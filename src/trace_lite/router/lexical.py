"""Tier 1: lexical short-circuit for syntax-dense queries (<= 5ms, no vector math)."""

from __future__ import annotations

import re
import sqlite3

_SYNTAX_RES = (
    re.compile(r"[`\"']"),            # quotes / code spans
    re.compile(r"[A-Z]+_[A-Z_]+"),    # UPPER_SNAKE
    re.compile(r"[a-z]+_[a-z_]+"),    # snake_case
    re.compile(r"[a-z]+[A-Z]"),       # camelCase
    re.compile(r"[/\\:.]{1}"),        # paths, module separators, symbols
    re.compile(r"\(\)|::|->|=="),     # code operators
)
_TERM_RE = re.compile(r"[A-Za-z0-9_]{2,}")
_HAY_RE = re.compile(r"[a-z0-9]{2,}")


def _porter_types(word: str) -> list[bool]:
    """Per-char consonant flags (True = consonant). y is a vowel iff preceded
    by a consonant, a consonant otherwise — the canonical Porter convention."""
    types: list[bool] = []
    for ch in word:
        if ch in "aeiou":
            types.append(False)
        elif ch != "y":
            types.append(True)
        else:
            types.append(False if (types and types[-1]) else True)
    return types


def _porter_m(word: str) -> int:
    """Measure m: number of vowel-consonant (VC) sequences in the word."""
    types = _porter_types(word)
    return sum(1 for a, b in zip(types, types[1:]) if not a and b)


def _porter_vowel(word: str) -> bool:
    return not all(_porter_types(word)) if word else False


def _porter_doublec(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _porter_types(word)[-1]


def _porter_cvc(word: str) -> bool:
    # *o: consonant-vowel-consonant ending, last letter not w/x/y.
    if len(word) < 3 or word[-1] in "wxyaeiou":
        return False
    t = _porter_types(word)
    return bool(t[-3] and not t[-2] and t[-1])


def porter_stem(word: str) -> str:
    """Standard Porter stemmer (single term). Mirrors the FTS5 porter tokenizer.

    Retrieval stems but coverage scoring did not: a doc FTS-matched via shared
    stems ("inhibition" for query "inhibitor") scored a substring miss, so
    gate thresholds saw weaker evidence than the index actually found.
    """
    w = word.lower()
    if len(w) <= 2:
        return w
    # Step 1a
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]
    # Step 1b
    flag = False
    if w.endswith("eed"):
        if _porter_m(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed"):
        if _porter_vowel(w[:-2]):
            w = w[:-2]
            flag = True
    elif w.endswith("ing"):
        if _porter_vowel(w[:-3]):
            w = w[:-3]
            flag = True
    if flag:
        if w.endswith(("at", "bl", "iz")):
            w += "e"
        elif _porter_doublec(w) and w[-1] not in "lsz":
            w = w[:-1]
        elif _porter_m(w) == 1 and _porter_cvc(w):
            w += "e"
    # Step 1c
    if w.endswith("y") and _porter_vowel(w[:-1]):
        w = w[:-1] + "i"
    # Step 2
    step2 = {
        "ational": "ate", "tional": "tion", "enci": "ence", "anci": "ance",
        "izer": "ize", "bli": "ble", "alli": "al", "entli": "ent", "eli": "e",
        "ousli": "ous", "ization": "ize", "ation": "ate", "ator": "ate",
        "alism": "al", "iveness": "ive", "fulness": "ful", "ousness": "ous",
        "aliti": "al", "iviti": "ive", "biliti": "ble", "logi": "log",
    }
    for end, rep in step2.items():
        if w.endswith(end):
            if _porter_m(w[: -len(end)]) > 0:
                w = w[: -len(end)] + rep
            break
    # Step 3
    step3 = {
        "icate": "ic", "ative": "", "alize": "al", "iciti": "ic",
        "ical": "ic", "ful": "", "ness": "",
    }
    for end, rep in step3.items():
        if w.endswith(end):
            if _porter_m(w[: -len(end)]) > 0:
                w = w[: -len(end)] + rep
            break
    # Step 4
    for end in ("al", "ance", "ence", "er", "ic", "able", "ible", "ant",
                "ement", "ment", "ent", "ion", "ou", "ism", "ate", "iti",
                "ous", "ive", "ize"):
        if w.endswith(end):
            base = w[: -len(end)]
            if _porter_m(base) > 1:
                if end == "ion" and base and base[-1] not in "st":
                    break
                w = base
            break
    # Step 5a / 5b
    if w.endswith("e"):
        m = _porter_m(w[:-1])
        if m > 1 or (m == 1 and not _porter_cvc(w[:-1])):
            w = w[:-1]
    if w.endswith("ll") and _porter_m(w) > 1:
        w = w[:-1]
    return w


def is_syntax_dense(query: str) -> bool:
    return any(rx.search(query) for rx in _SYNTAX_RES)


def extract_terms(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for tok in _TERM_RE.findall(query):
        seen.setdefault(tok.lower())
    return list(seen)


def _fts_rows(conn: sqlite3.Connection, match: str, n: int, ranked: bool = False) -> list[dict]:
    # Two-step fetch: JOINing atom against an external-content FTS5 table costs
    # ~1.5ms/row at scale (300x slower than rowid walk + PK fetch — measured).
    # ORDER BY rank additionally scores EVERY match; only rank small match sets.
    cols = "rowid, rank" if ranked else "rowid"
    order = "ORDER BY rank " if ranked else ""
    hits = conn.execute(
        f"SELECT {cols} FROM fts_atoms WHERE fts_atoms MATCH ? {order}LIMIT ?",
        (match, n),
    ).fetchall()
    if not hits:
        return []
    ids = [r[0] for r in hits]
    ranks = {r[0]: r[1] for r in hits} if ranked else {}
    marks = ",".join("?" for _ in ids)
    by_id = {
        r[0]: dict(r)
        for r in conn.execute(
            f"SELECT * FROM atom WHERE id IN ({marks})", tuple(ids)
        ).fetchall()
    }
    rows = []
    for atom_id in ids:
        row = by_id.get(atom_id)
        if row is None:  # FTS/row store skew: never crash the query path
            continue
        row["r"] = ranks.get(atom_id, 0.0)
        rows.append(row)
    return rows


def lexical_search(
    conn: sqlite3.Connection, query: str, limit: int = 10, ranked: bool = False,
    pool: int | None = None,
) -> list[dict]:
    """FTS5-backed keyword search with LIKE fallback. Returns atom dicts + score.

    AND-first: conjunctive matches rank cheapest (indexed intersection); OR only
    supplements when AND yields fewer than `limit` rows. Same recall, far less sorting.
    Unranked by default: BM25 ORDER BY scores every match (25k+ rows for common
    terms); match-fraction order then provides the ranking. Pass ranked=True
    for the quality profile: BM25 order wins outright on prose claims.
    `pool` overrides the default limit*3 candidate depth per clause.
    """
    terms = extract_terms(query)
    if not terms:
        return []
    depth = pool or limit * 3
    # Supplement gate tracks depth, not limit: a deeper AND fetch (quality
    # profile) must not starve the OR clause that carries prose recall —
    # conjunctive pools hold ~5% of relevant docs (pool diagnostic, BEIR).
    # Unpooled callers (tests, latency profile) keep the original gate exactly.
    bar = depth if pool is not None else limit
    try:
        quoted = [f'"{t}"' for t in terms]
        rows = _fts_rows(conn, " AND ".join(quoted), depth, ranked)
        if len(rows) < bar and len(quoted) > 1:
            seen = {r["id"] for r in rows}
            rows += [r for r in _fts_rows(conn, " OR ".join(quoted), depth, ranked)
                     if r["id"] not in seen]
        return _score_rows(rows, terms, limit, by_rank=ranked, stem=ranked)
    except sqlite3.OperationalError:
        return _like_fallback(conn, terms, limit)


def has_lexical_support(
    conn_or_query: sqlite3.Connection | str | None = None,
    query_or_anchor: str | None = None,
    anchor_text: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    query: str | None = None,
) -> bool:
    """True when query terms (or stems) appear in candidate anchor text.

    Corroboration signal for the abstention gate: dense-only matches with zero
    lexical support are hash-collision noise in this substrate, not evidence.
    Checking the candidate anchor text specifically prevents database-wide leakage
    where an unrelated note's terms incorrectly corroborate a false anchor.

    Signatures supported:
      has_lexical_support(conn, query, anchor_text=...)
      has_lexical_support(query, anchor_text)
      has_lexical_support(conn, query)  # legacy db-wide fallback
      has_lexical_support(query="...", anchor_text="...")
      has_lexical_support(conn=..., query="...", anchor_text="...")
    """
    c: sqlite3.Connection | None = conn
    q: str = query or ""
    target_text: str | None = anchor_text

    if isinstance(conn_or_query, sqlite3.Connection):
        c = conn_or_query
        if query_or_anchor is not None:
            q = query_or_anchor
    elif isinstance(conn_or_query, str):
        q = conn_or_query
        if target_text is None:
            target_text = query_or_anchor
    elif query_or_anchor is not None and not q:
        q = query_or_anchor

    terms = extract_terms(q)
    if not terms:
        return False

    if target_text is not None:
        hay_tokens = set(_TERM_RE.findall(target_text.lower()))
        if not hay_tokens:
            return False
        # Exact token match
        term_set = set(terms)
        if term_set & hay_tokens:
            return True
        # Porter stem match
        query_stems = {porter_stem(t) for t in terms}
        hay_stems = {porter_stem(tok) for tok in hay_tokens}
        return bool(query_stems & hay_stems)

    if c is not None:
        try:
            match = " OR ".join(f'"{t}"' for t in terms)
            return c.execute(
                "SELECT 1 FROM fts_atoms WHERE fts_atoms MATCH ? LIMIT 1", (match,)
            ).fetchone() is not None
        except sqlite3.OperationalError:
            return False
    return False


def _score_rows(
    rows: list[dict], terms: list[str], limit: int, by_rank: bool = False,
    stem: bool = False,
) -> list[dict]:
    # "score" is always the match fraction: Tier-1 confidence gates
    # (LEXICAL_MIN_SCORE/HITS) are calibrated on it. Ordering differs by
    # profile: ranked fetches (quality) follow BM25 directly — coverage-first
    # rerank measurably loses to raw BM25 on prose claims (0.59 vs 0.69 nDCG
    # on SciFact: substring coverage counts glue-word matches as heavily as
    # content terms, burying BM25's tf/length-norm signal). Unranked fetches
    # (latency profile, r=0.0 throughout) keep coverage order.
    # stem=True (quality only): a term also matches via its Porter stem, so
    # coverage sees what the porter-tokenized index actually matched
    # ("activates" for query "activation"). Query-side stemming is O(terms);
    # hay-side stays C-speed substring checks — no per-doc Python tokenizing.
    keys = [(t, porter_stem(t)) for t in terms] if stem else [(t, t) for t in terms]
    scored = []
    for row in rows:
        hay = row["text"].lower()
        matched = sum(1 for t, s in keys if t in hay or (s != t and s in hay))
        if matched:
            scored.append({**row, "score": matched / len(terms) if terms else 0.0})
    if by_rank:
        scored.sort(key=lambda r: (r.get("r", 0.0), r["id"]))
    else:
        scored.sort(key=lambda r: (-r["score"], r["id"]))
    return scored[:limit]


def _like_fallback(conn: sqlite3.Connection, terms: list[str], limit: int) -> list[dict]:
    cond = " OR ".join("atom.text LIKE ?" for _ in terms)
    rows = conn.execute(
        f"SELECT * FROM atom WHERE {cond} LIMIT ?",
        tuple(f"%{t}%" for t in terms) + (limit * 3,),
    ).fetchall()
    return _score_rows([dict(r) for r in rows], terms, limit)
