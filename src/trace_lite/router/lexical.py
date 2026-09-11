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


def lexical_search(
    conn: sqlite3.Connection, query: str, limit: int = 10
) -> list[dict]:
    """FTS5-backed keyword search with LIKE fallback. Returns atom dicts + score."""
    terms = extract_terms(query)
    if not terms:
        return []
    try:
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = conn.execute(
            "SELECT atom.*, fts_atoms.rank AS r FROM fts_atoms JOIN atom ON atom.id = fts_atoms.rowid"
            " WHERE fts_atoms MATCH ? ORDER BY r LIMIT ?",
            (match, limit * 3),
        ).fetchall()
        return _score_rows([dict(r) for r in rows], terms, limit)
    except sqlite3.OperationalError:
        return _like_fallback(conn, terms, limit)


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
