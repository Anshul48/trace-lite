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
    conn: sqlite3.Connection, query: str, limit: int = 10, ranked: bool = False
) -> list[dict]:
    """FTS5-backed keyword search with LIKE fallback. Returns atom dicts + score.

    AND-first: conjunctive matches rank cheapest (indexed intersection); OR only
    supplements when AND yields fewer than `limit` rows. Same recall, far less sorting.
    Unranked by default: BM25 ORDER BY scores every match (25k+ rows for common
    terms); match-fraction rerank already provides the ordering. Pass ranked=True
    only for rare-term queries where the match set is small.
    """
    terms = extract_terms(query)
    if not terms:
        return []
    try:
        quoted = [f'"{t}"' for t in terms]
        rows = _fts_rows(conn, " AND ".join(quoted), limit * 3, ranked)
        if len(rows) < limit and len(quoted) > 1:
            seen = {r["id"] for r in rows}
            rows += [r for r in _fts_rows(conn, " OR ".join(quoted), limit * 3, ranked)
                     if r["id"] not in seen]
        return _score_rows(rows, terms, limit)
    except sqlite3.OperationalError:
        return _like_fallback(conn, terms, limit)


def has_lexical_support(conn: sqlite3.Connection, query: str) -> bool:
    """True when at least one query term is indexed anywhere (single FTS EXISTS).

    Corroboration signal for the abstention gate: dense-only matches with zero
    lexical support are hash-collision noise in this substrate, not evidence.
    """
    terms = extract_terms(query)
    if not terms:
        return False
    try:
        match = " OR ".join(f'"{t}"' for t in terms)
        return conn.execute(
            "SELECT 1 FROM fts_atoms WHERE fts_atoms MATCH ? LIMIT 1", (match,)
        ).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _score_rows(rows: list[dict], terms: list[str], limit: int) -> list[dict]:
    scored = []
    for row in rows:
        hay = row["text"].lower()
        matched = sum(1 for t in terms if t in hay)
        if matched:
            scored.append({**row, "score": matched / len(terms)})
    scored.sort(key=lambda r: (-r["score"], r["id"]))
    return scored[:limit]


def _like_fallback(conn: sqlite3.Connection, terms: list[str], limit: int) -> list[dict]:
    cond = " OR ".join("atom.text LIKE ?" for _ in terms)
    rows = conn.execute(
        f"SELECT * FROM atom WHERE {cond} LIMIT ?",
        tuple(f"%{t}%" for t in terms) + (limit * 3,),
    ).fetchall()
    return _score_rows([dict(r) for r in rows], terms, limit)
