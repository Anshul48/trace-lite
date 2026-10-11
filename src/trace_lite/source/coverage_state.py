"""S2 materialized projection coverage (contract TL-QN-2026-09-12.1).

Query-path problem: ``freshness``/``pending_coverage`` ran correlated
``EXISTS`` scans over ``source_revision`` x segmentation/extraction/
grouping/job rows on EVERY query (~15 s tax at 10 k pending). S2 makes
every query-path read an indexed lookup over state maintained
incrementally on the write/drain paths.

Design (STEERING S2 row):

- ``revision_coverage``: one row per revision, per-stage checklist flags
  (seg/ext/rec/idx) + ``has_pending_job`` + ``in_scope`` + derived
  ``pending``/``missing``/``reason``. Maintained by SQLite triggers on
  the write path (revision insert, scope-pointer moves, stage-output
  inserts, job insert/update, grouping changes): each trigger touches
  only the affected revision row via indexed lookups — O(1) per write,
  never a recomputation.
- ``coverage_hole``: explicit hole set — every pending revision below
  the watermark with its reason (missing stages + job error). Synced by
  triggers on ``revision_coverage`` itself, so it can never disagree
  with the checklist. Hole enumeration on the read path is bounded
  (cap + truncated flag).
- ``coverage_meta``: ``projection_version`` + ``max_observed_at``
  watermark (single-row PK lookups on the read path).
- A high watermark NEVER implies completeness: ``freshness`` reports
  pending whenever the hole set is non-empty OR the materialized
  projection version differs from code (version changes surface as
  pending even with zero holes).
- ``recompute_pending_coverage`` is the exact pre-S2 scan, kept as the
  test oracle: materialized state must equal recomputation after every
  transition sequence (see tests/test_bounded_reads.py).
- Counts use partial indexes (``pending = 1``; live job states), so
  they are O(pending), drift-free, and need no counter triggers.

Write-path repair (drain start): ``ensure_coverage_schema`` is
idempotent (IF NOT EXISTS everywhere) and heals only pending rows plus
any rows missing since the last drain — bounded by pending/new, never
a full recompute. Full recompute happens only in migration/backfill
and on projection-version change (rare, write path).
"""

from __future__ import annotations

import sqlite3

#: Coverage-derivation version. Bump when the meaning of any stage flag
#: changes; a mismatch surfaces every freshness read as pending until a
#: drain-time backfill re-derives under the new version.
COVERAGE_VERSION = "s2v1"

#: Bound on hole rows enumerated per freshness read (cap + report).
HOLE_CAP = 64

META_VERSION_KEY = "projection_version"
META_WATERMARK_KEY = "max_observed_at"


# -- DDL ----------------------------------------------------------------

COVERAGE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS revision_coverage (
    revision_id     TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    seg             INTEGER NOT NULL DEFAULT 0,
    ext             INTEGER NOT NULL DEFAULT 0,
    rec             INTEGER NOT NULL DEFAULT 0,
    idx             INTEGER NOT NULL DEFAULT 0,
    has_pending_job INTEGER NOT NULL DEFAULT 0,
    in_scope        INTEGER NOT NULL DEFAULT 0,
    pending         INTEGER NOT NULL DEFAULT 0,
    missing         TEXT NOT NULL DEFAULT '',
    reason          TEXT NOT NULL DEFAULT '',
    job_error       TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cov_pending
    ON revision_coverage(pending, observed_at, revision_id)
    WHERE pending = 1;
CREATE INDEX IF NOT EXISTS idx_cov_scope
    ON revision_coverage(in_scope, pending);
CREATE TABLE IF NOT EXISTS coverage_hole (
    revision_id TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    missing     TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cov_hole_obs
    ON coverage_hole(observed_at, revision_id);
CREATE TABLE IF NOT EXISTS coverage_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: Partial index over live outbox rows: job counts on the read path are
#: O(live jobs), never a full-table scan. Additive; R01 semantics untouched.
JOB_LIVE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_cov_job_live"
    " ON projection_job(state, created_at)"
    " WHERE state IN ('pending', 'claimed')"
)

#: Point lookups used by triggers/grouping repair (additive).
SUPPORT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_cov_sr_atom ON source_revision(atom_id)"
)

#: Revision-scoped job lookups (additive). The job-state triggers
#: (``_job_flags_sql`` EXISTS subqueries, fired on every job INSERT/UPDATE)
#: and the per-revision job lookups filter on ``input_revision_id``, which
#: has no static index — without this they scan all done-jobs per write.
JOB_REV_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_cov_job_rev"
    " ON projection_job(input_revision_id, state)"
)

#: Partial index so the unindexed-facet honesty count stays index-backed.
FACET_NULL_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_cov_facet_nullblob"
    " ON facets(facet_id) WHERE centroid_blob IS NULL"
)

#: Index so the stale-holon honesty count stays index-backed (additive).
HOLON_STATUS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_cov_holostatus ON holon_state(status)"
)


def _derive_sql(scope: str) -> str:
    """Recompute derived columns from flags for the touched rows only.

    Two ordered statements (derived, then reason) so no statement reads
    a column assigned earlier in the same statement.
    """
    return (
        "UPDATE revision_coverage SET"
        " missing = ltrim("
        " (CASE WHEN seg THEN '' ELSE ',segmented' END)"
        " || (CASE WHEN ext THEN '' ELSE ',extracted' END)"
        " || (CASE WHEN rec THEN '' ELSE ',reconciled' END)"
        " || (CASE WHEN idx THEN '' ELSE ',indexed' END), ','),"
        " pending = (in_scope AND ("
        " (CASE WHEN seg THEN 0 ELSE 1 END)"
        " + (CASE WHEN ext THEN 0 ELSE 1 END)"
        " + (CASE WHEN rec THEN 0 ELSE 1 END)"
        " + (CASE WHEN idx THEN 0 ELSE 1 END)) > 0),"
        " updated_at = datetime('now')"
        f" WHERE {scope};"
        "UPDATE revision_coverage SET"
        " reason = CASE WHEN pending = 0 THEN ''"
        " WHEN in_scope = 0 THEN 'out of scope (superseded/retracted)'"
        " ELSE 'missing: ' || missing"
        " || CASE WHEN job_error IS NOT NULL THEN ' :: job: '"
        " || substr(job_error, 1, 200) ELSE '' END END,"
        " updated_at = datetime('now')"
        f" WHERE {scope};"
    )


def _ensure_row_sql(rev_expr: str) -> str:
    """Ensure a coverage row exists for a revision known only by id."""
    return (
        "INSERT OR IGNORE INTO revision_coverage"
        " (revision_id, source_id, observed_at, rec, in_scope, updated_at)"
        f" SELECT {rev_expr}, sr.source_id, sr.observed_at,"
        " CASE WHEN sr.atom_id IS NULL THEN 1 ELSE 0 END,"
        " CASE WHEN s.current_revision_id = sr.revision_id THEN 1 ELSE 0 END,"
        " datetime('now') FROM source_revision sr"
        " LEFT JOIN source s ON s.source_id = sr.source_id"
        f" WHERE sr.revision_id = {rev_expr}"
    )


def _job_flags_sql(rev_expr: str) -> str:
    """Refresh idx/has_pending_job/job_error for one revision's jobs."""
    return (
        "UPDATE revision_coverage SET"
        f" idx = EXISTS(SELECT 1 FROM projection_job"
        f" WHERE input_revision_id = {rev_expr} AND state = 'done'),"
        " has_pending_job = EXISTS(SELECT 1 FROM projection_job"
        f" WHERE input_revision_id = {rev_expr}"
        " AND state IN ('pending', 'claimed')),"
        " job_error = (SELECT error FROM projection_job"
        f" WHERE input_revision_id = {rev_expr}"
        " AND state IN ('pending', 'claimed') AND error IS NOT NULL"
        " ORDER BY created_at LIMIT 1),"
        " updated_at = datetime('now')"
        f" WHERE revision_id = {rev_expr}"
    )


def _rec_from_grouping_sql(scope: str) -> str:
    """Recompute rec from live grouping for the touched rows only."""
    return (
        "UPDATE revision_coverage SET"
        " rec = COALESCE((SELECT CASE WHEN sr.atom_id IS NULL THEN 1"
        " ELSE EXISTS(SELECT 1 FROM holon_members m JOIN holon_state s"
        " ON s.holon_id = m.holon_id WHERE m.atom_id = sr.atom_id"
        " AND s.status != 'deleted') END"
        " FROM source_revision sr"
        " WHERE sr.revision_id = revision_coverage.revision_id), rec),"
        " updated_at = datetime('now')"
        f" WHERE {scope}"
    )


def _core_triggers_sql() -> str:
    stmts: list[str] = []
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_sr_ins"
        " AFTER INSERT ON source_revision BEGIN "
        + _ensure_row_sql("NEW.revision_id")
        + "; UPDATE revision_coverage SET"
        " in_scope = COALESCE((SELECT CASE WHEN s.current_revision_id"
        " = NEW.revision_id THEN 1 ELSE 0 END FROM source s"
        " WHERE s.source_id = NEW.source_id), in_scope),"
        " updated_at = datetime('now')"
        " WHERE revision_id = NEW.revision_id;"
        + _derive_sql("revision_id = NEW.revision_id")
        + " INSERT INTO coverage_meta(key, value) VALUES ('"
        + META_WATERMARK_KEY
        + "', NEW.observed_at) ON CONFLICT(key) DO UPDATE"
        " SET value = max(value, excluded.value); END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_sr_atom"
        " AFTER UPDATE OF atom_id ON source_revision"
        " WHEN NEW.atom_id IS NULL AND OLD.atom_id IS NOT NULL BEGIN"
        " UPDATE revision_coverage SET rec = 1,"
        " updated_at = datetime('now')"
        " WHERE revision_id = NEW.revision_id;"
        + _derive_sql("revision_id = NEW.revision_id")
        + " END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_src_ins"
        " AFTER INSERT ON source"
        " WHEN NEW.current_revision_id IS NOT NULL BEGIN "
        + _ensure_row_sql("NEW.current_revision_id")
        + "; UPDATE revision_coverage SET in_scope = 1,"
        " updated_at = datetime('now')"
        " WHERE revision_id = NEW.current_revision_id;"
        + _derive_sql("revision_id = NEW.current_revision_id")
        + " END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_src_scope"
        " AFTER UPDATE OF current_revision_id ON source"
        " WHEN NOT (OLD.current_revision_id IS NEW.current_revision_id)"
        " BEGIN"
        " UPDATE revision_coverage SET in_scope = 0,"
        " updated_at = datetime('now')"
        " WHERE revision_id = OLD.current_revision_id;"
        + _ensure_row_sql("NEW.current_revision_id")
        + "; UPDATE revision_coverage SET in_scope = 1,"
        " updated_at = datetime('now')"
        " WHERE revision_id = NEW.current_revision_id;"
        + _derive_sql(
            "revision_id = OLD.current_revision_id"
            " OR revision_id = NEW.current_revision_id"
        )
        + " END"
    )
    for ev in ("INSERT", "UPDATE"):
        stmts.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_cov_job_{ev.lower()}"
            f" AFTER {ev} ON projection_job BEGIN "
            + _ensure_row_sql("NEW.input_revision_id")
            + "; "
            + _job_flags_sql("NEW.input_revision_id")
            + "; "
            + _derive_sql("revision_id = NEW.input_revision_id")
            + " END"
        )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_seg"
        " AFTER INSERT ON segmentation_view"
        " WHEN NEW.status = 'complete' BEGIN"
        " UPDATE revision_coverage SET seg = 1,"
        " updated_at = datetime('now')"
        " WHERE revision_id = NEW.revision_id;"
        + _derive_sql("revision_id = NEW.revision_id")
        + " END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_ext"
        " AFTER INSERT ON extraction_run"
        " WHEN NEW.status = 'complete' BEGIN"
        " UPDATE revision_coverage SET ext = 1,"
        " updated_at = datetime('now')"
        " WHERE revision_id = NEW.revision_id;"
        + _derive_sql("revision_id = NEW.revision_id")
        + " END"
    )
    for ev in ("INSERT", "UPDATE"):
        stmts.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_cov_hole_{ev.lower()}"
            f" AFTER {ev} ON revision_coverage BEGIN"
            " DELETE FROM coverage_hole WHERE revision_id = NEW.revision_id;"
            " INSERT INTO coverage_hole"
            " (revision_id, source_id, observed_at, missing, reason)"
            " SELECT revision_id, source_id, observed_at, missing, reason"
            " FROM revision_coverage"
            " WHERE revision_id = NEW.revision_id AND pending = 1;"
            " END"
        )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_hole_del"
        " AFTER DELETE ON revision_coverage BEGIN"
        " DELETE FROM coverage_hole WHERE revision_id = OLD.revision_id; END"
    )
    return ";\n".join(stmts) + ";"


def _holon_triggers_sql() -> str:
    stmts: list[str] = []
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_hm_ins"
        " AFTER INSERT ON holon_members BEGIN"
        " UPDATE revision_coverage SET rec = 1,"
        " updated_at = datetime('now')"
        " WHERE revision_id IN (SELECT revision_id FROM source_revision"
        " WHERE atom_id = NEW.atom_id);"
        + _derive_sql(
            "revision_id IN (SELECT revision_id FROM source_revision"
            " WHERE atom_id = NEW.atom_id)"
        )
        + " END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_hm_del"
        " AFTER DELETE ON holon_members BEGIN "
        + _rec_from_grouping_sql(
            "revision_id IN (SELECT revision_id FROM source_revision"
            " WHERE atom_id = OLD.atom_id)"
        )
        + "; "
        + _derive_sql(
            "revision_id IN (SELECT revision_id FROM source_revision"
            " WHERE atom_id = OLD.atom_id)"
        )
        + " END"
    )
    stmts.append(
        "CREATE TRIGGER IF NOT EXISTS trg_cov_hs_status"
        " AFTER UPDATE OF status ON holon_state"
        " WHEN NOT (OLD.status IS NEW.status) BEGIN "
        + _rec_from_grouping_sql(
            "revision_id IN (SELECT sr.revision_id FROM source_revision sr"
            " JOIN holon_members m ON m.atom_id = sr.atom_id"
            " WHERE m.holon_id = NEW.holon_id)"
        )
        + "; "
        + _derive_sql(
            "revision_id IN (SELECT sr.revision_id FROM source_revision sr"
            " JOIN holon_members m ON m.atom_id = sr.atom_id"
            " WHERE m.holon_id = NEW.holon_id)"
        )
        + " END"
    )
    return ";\n".join(stmts) + ";"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def _has_holon_tables(conn: sqlite3.Connection) -> bool:
    try:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE name IN ('holons', 'holon_members')"
            )
        }
    except Exception:
        return False
    return names == {"holons", "holon_members"}


# -- oracle (exact pre-S2 recomputation) ----------------------------------

def recompute_flags_for_revision(
    conn: sqlite3.Connection, revision_id: str
) -> dict | None:
    """Exact per-revision flags from base rows (test oracle, write path only).

    Mirrors the pre-S2 ``coverage_for_revision``/``pending_coverage``
    stage semantics plus the in-scope bit. Never called on the query path.
    """
    rev = conn.execute(
        "SELECT revision_id, source_id, observed_at, atom_id"
        " FROM source_revision WHERE revision_id = ?",
        (revision_id,),
    ).fetchone()
    if rev is None:
        return None
    seg = (
        conn.execute(
            "SELECT 1 FROM segmentation_view WHERE revision_id = ?"
            " AND status = 'complete' LIMIT 1",
            (revision_id,),
        ).fetchone()
        is not None
    )
    ext = (
        conn.execute(
            "SELECT 1 FROM extraction_run WHERE revision_id = ?"
            " AND status = 'complete' LIMIT 1",
            (revision_id,),
        ).fetchone()
        is not None
    )
    atom_id = rev["atom_id"] if "atom_id" in rev.keys() else rev[3]
    if atom_id is None:
        rec = True
    elif not _has_holon_tables(conn):
        rec = False
    else:
        rec = (
            conn.execute(
                "SELECT 1 FROM holon_members m JOIN holon_state s"
                " ON s.holon_id = m.holon_id WHERE m.atom_id = ?"
                " AND s.status != 'deleted' LIMIT 1",
                (int(atom_id),),
            ).fetchone()
            is not None
        )
    idx = (
        conn.execute(
            "SELECT 1 FROM projection_job WHERE input_revision_id = ?"
            " AND state = 'done' LIMIT 1",
            (revision_id,),
        ).fetchone()
        is not None
    )
    hasjob = (
        conn.execute(
            "SELECT 1 FROM projection_job WHERE input_revision_id = ?"
            " AND state IN ('pending', 'claimed') LIMIT 1",
            (revision_id,),
        ).fetchone()
        is not None
    )
    job_error = conn.execute(
        "SELECT error FROM projection_job WHERE input_revision_id = ?"
        " AND state IN ('pending', 'claimed') AND error IS NOT NULL"
        " ORDER BY created_at LIMIT 1",
        (revision_id,),
    ).fetchone()
    cur = conn.execute(
        "SELECT current_revision_id FROM source WHERE source_id = ?",
        (str(rev["source_id"] if "source_id" in rev.keys() else rev[1]),),
    ).fetchone()
    in_scope = cur is not None and str(cur[0]) == revision_id
    missing = [
        stage
        for stage, ok in (
            ("segmented", seg),
            ("extracted", ext),
            ("reconciled", rec),
            ("indexed", idx),
        )
        if not ok
    ]
    return {
        "revision_id": revision_id,
        "source_id": str(rev["source_id"] if "source_id" in rev.keys() else rev[1]),
        "observed_at": str(
            rev["observed_at"] if "observed_at" in rev.keys() else rev[2]
        ),
        "seg": int(bool(seg)),
        "ext": int(bool(ext)),
        "rec": int(bool(rec)),
        "idx": int(bool(idx)),
        "has_pending_job": int(bool(hasjob)),
        "in_scope": int(bool(in_scope)),
        "pending": int(bool(in_scope and missing)),
        "missing": ",".join(missing),
        "job_error": str(job_error[0]) if job_error is not None else None,
    }


def recompute_pending_coverage(
    conn: sqlite3.Connection, limit: int = HOLE_CAP
) -> list[dict]:
    """Byte-for-byte pre-S2 ``pending_coverage`` scan (oracle only)."""
    if _has_holon_tables(conn):
        rec_expr = (
            " CASE WHEN sr.atom_id IS NULL THEN 1"
            " ELSE EXISTS(SELECT 1 FROM holon_members m JOIN holon_state s"
            " ON s.holon_id = m.holon_id WHERE m.atom_id = sr.atom_id"
            " AND s.status != 'deleted') END AS rec,"
        )
    else:
        rec_expr = " CASE WHEN sr.atom_id IS NULL THEN 1 ELSE 0 END AS rec,"
    rows = conn.execute(
        "SELECT sr.revision_id, sr.source_id,"
        " EXISTS(SELECT 1 FROM segmentation_view v WHERE v.revision_id = sr.revision_id"
        " AND v.status = 'complete') AS seg,"
        " EXISTS(SELECT 1 FROM extraction_run r WHERE r.revision_id = sr.revision_id"
        " AND r.status = 'complete') AS ext," + rec_expr + " EXISTS(SELECT 1 FROM projection_job j"
        " WHERE j.input_revision_id = sr.revision_id AND j.state = 'done') AS idx,"
        " EXISTS(SELECT 1 FROM projection_job j"
        " WHERE j.input_revision_id = sr.revision_id"
        " AND j.state IN ('pending', 'claimed')) AS hasjob"
        " FROM source_revision sr JOIN source s"
        " ON s.current_revision_id = sr.revision_id"
        " ORDER BY sr.observed_at, sr.revision_id"
    ).fetchall()
    pending = []
    for row in rows:
        missing = [
            stage
            for stage, flag in (
                ("segmented", row["seg"]),
                ("extracted", row["ext"]),
                ("reconciled", row["rec"]),
                ("indexed", row["idx"]),
            )
            if not flag
        ]
        if missing:
            pending.append(
                {
                    "revision_id": str(row["revision_id"]),
                    "source_id": str(row["source_id"]),
                    "missing": missing,
                    "has_pending_job": bool(row["hasjob"]),
                }
            )
        if len(pending) >= limit:
            break
    return pending


# -- write-path maintenance -----------------------------------------------

def coverage_tables_present(conn: sqlite3.Connection) -> bool:
    return _has_table(conn, "revision_coverage") and _has_table(
        conn, "coverage_hole"
    ) and _has_table(conn, "coverage_meta")


def ensure_coverage_schema(conn: sqlite3.Connection) -> dict:
    """Idempotent DDL + bounded heal (write/drain path only, never queries).

    Creates tables, partial indexes, and triggers (holon triggers only
    once grouping tables exist), defaults the meta rows, then heals:
    backfills rows for revisions missing since the last call (gated by
    counts, so the common case is two O(1) counts) and re-derives flags
    for pending rows only (bounded by pending, via indexed point reads).
    """
    conn.executescript(COVERAGE_TABLES_SQL)
    for stmt in (JOB_LIVE_INDEX_SQL, SUPPORT_INDEX_SQL, FACET_NULL_INDEX_SQL,
                 JOB_REV_INDEX_SQL):
        conn.execute(stmt)
    if _has_table(conn, "holon_state"):
        conn.execute(HOLON_STATUS_INDEX_SQL)
    conn.executescript(_core_triggers_sql())
    holon_triggers = False
    if _has_holon_tables(conn):
        conn.executescript(_holon_triggers_sql())
        holon_triggers = True
    conn.execute(
        "INSERT OR IGNORE INTO coverage_meta(key, value) VALUES (?, ?)",
        (META_VERSION_KEY, COVERAGE_VERSION),
    )
    wm = conn.execute(
        "SELECT COALESCE(MAX(observed_at), '') FROM source_revision"
    ).fetchone()
    if wm is not None and wm[0]:
        conn.execute(
            "INSERT INTO coverage_meta(key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = max(value, excluded.value)",
            (META_WATERMARK_KEY, str(wm[0])),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO coverage_meta(key, value) VALUES (?, '')",
            (META_WATERMARK_KEY,),
        )
    healed = _heal_coverage(conn)
    healed["holon_triggers"] = holon_triggers
    # Leave the connection as found: callers (drain, migrate) run outside
    # an explicit transaction, but never inherit a half-open one from here.
    if conn.in_transaction:
        conn.commit()
    return healed


def _heal_coverage(conn: sqlite3.Connection) -> dict:
    """Bounded repair: missing rows + pending-row re-derivation."""
    n_rev = int(conn.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0])
    n_cov = int(conn.execute("SELECT COUNT(*) FROM revision_coverage").fetchone()[0])
    backfilled = 0
    if n_cov < n_rev:
        for (rev_id,) in conn.execute(
            "SELECT sr.revision_id FROM source_revision sr"
            " LEFT JOIN revision_coverage c ON c.revision_id = sr.revision_id"
            " WHERE c.revision_id IS NULL LIMIT 5000"
        ).fetchall():
            _backfill_one(conn, str(rev_id))
            backfilled += 1
    rederived = 0
    for (rev_id,) in conn.execute(
        "SELECT revision_id FROM revision_coverage WHERE pending = 1"
    ).fetchall():
        if _rederive_one(conn, str(rev_id)):
            rederived += 1
    return {"backfilled": backfilled, "rederived": rederived}


def _backfill_one(conn: sqlite3.Connection, revision_id: str) -> bool:
    flags = recompute_flags_for_revision(conn, revision_id)
    if flags is None:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO revision_coverage"
        " (revision_id, source_id, observed_at, seg, ext, rec, idx,"
        " has_pending_job, in_scope, pending, missing, reason, job_error,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
        " datetime('now'))",
        (
            flags["revision_id"],
            flags["source_id"],
            flags["observed_at"],
            flags["seg"],
            flags["ext"],
            flags["rec"],
            flags["idx"],
            flags["has_pending_job"],
            flags["in_scope"],
            flags["pending"],
            flags["missing"],
            _reason_for(flags),
            flags["job_error"],
        ),
    )
    return True


def _rederive_one(conn: sqlite3.Connection, revision_id: str) -> bool:
    """Re-derive one row from the oracle; True when the row changed."""
    flags = recompute_flags_for_revision(conn, revision_id)
    if flags is None:
        return False
    cur = conn.execute(
        "SELECT seg, ext, rec, idx, has_pending_job, in_scope, pending,"
        " missing FROM revision_coverage WHERE revision_id = ?",
        (revision_id,),
    ).fetchone()
    if cur is None:
        return _backfill_one(conn, revision_id)
    want = (
        flags["seg"],
        flags["ext"],
        flags["rec"],
        flags["idx"],
        flags["has_pending_job"],
        flags["in_scope"],
        flags["pending"],
        flags["missing"],
    )
    if tuple(cur) == want:
        return False
    conn.execute(
        "UPDATE revision_coverage SET seg = ?, ext = ?, rec = ?, idx = ?,"
        " has_pending_job = ?, in_scope = ?, pending = ?, missing = ?,"
        " reason = ?, job_error = ?, updated_at = datetime('now')"
        " WHERE revision_id = ?",
        (
            flags["seg"],
            flags["ext"],
            flags["rec"],
            flags["idx"],
            flags["has_pending_job"],
            flags["in_scope"],
            flags["pending"],
            flags["missing"],
            _reason_for(flags),
            flags["job_error"],
            revision_id,
        ),
    )
    return True


def _reason_for(flags: dict) -> str:
    """Python mirror of the trigger reason text (must stay identical)."""
    if not flags["pending"]:
        return ""
    if not flags["in_scope"]:
        return "out of scope (superseded/retracted)"
    text = "missing: " + flags["missing"]
    if flags["job_error"]:
        text += " :: job: " + str(flags["job_error"])[:200]
    return text


# -- backfill / verify / version ------------------------------------------

def backfill_from_recompute(conn: sqlite3.Connection) -> dict:
    """Full re-derivation from base rows (migration/backfill path only).

    Rebuilds every coverage row from the oracle, rebuilds the hole set,
    and stamps the watermark + version. O(revisions); never on queries.
    """
    ensure_coverage_schema(conn)
    rev_ids = [
        str(r[0])
        for r in conn.execute(
            "SELECT revision_id FROM source_revision"
            " ORDER BY observed_at, revision_id"
        ).fetchall()
    ]
    for rev_id in rev_ids:
        _backfill_one(conn, rev_id)
    wm = conn.execute(
        "SELECT COALESCE(MAX(observed_at), '') FROM source_revision"
    ).fetchone()
    conn.execute(
        "INSERT INTO coverage_meta(key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (META_WATERMARK_KEY, str(wm[0]) if wm else ""),
    )
    conn.execute(
        "INSERT INTO coverage_meta(key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (META_VERSION_KEY, COVERAGE_VERSION),
    )
    if conn.in_transaction:
        conn.commit()
    return {"revisions": len(rev_ids), "version": COVERAGE_VERSION}


def verify_against_oracle(conn: sqlite3.Connection) -> dict:
    """Equivalence check: materialized state vs exact recomputation.

    Returns ``{"mismatches": [...], "checked": N}``. Empty mismatches
    means the incremental triggers agree with the full scan exactly.
    Compares stage flags, scope, pending bit, missing set, and the hole
    set against the oracle pending list. Write/test path only.
    """
    if not coverage_tables_present(conn):
        raise RuntimeError("coverage state absent: run ensure_coverage_schema first")
    mismatches: list[dict] = []
    mat = {
        str(r["revision_id"]): r
        for r in conn.execute("SELECT * FROM revision_coverage").fetchall()
    }
    checked = 0
    for rev_id in [
        str(r[0]) for r in conn.execute("SELECT revision_id FROM source_revision")
    ]:
        checked += 1
        want = recompute_flags_for_revision(conn, rev_id)
        assert want is not None
        got = mat.get(rev_id)
        if got is None:
            mismatches.append({"revision_id": rev_id, "kind": "missing_row"})
            continue
        for key in (
            "seg",
            "ext",
            "rec",
            "idx",
            "has_pending_job",
            "in_scope",
            "pending",
            "missing",
        ):
            if int(got[key]) != want[key] if key != "missing" else (
                str(got[key]) != str(want[key])
            ):
                mismatches.append(
                    {
                        "revision_id": rev_id,
                        "kind": f"flag_{key}",
                        "materialized": got[key],
                        "oracle": want[key],
                    }
                )
    oracle_pending = {
        u["revision_id"]: u
        for u in recompute_pending_coverage(conn, limit=10**9)
    }
    mat_pending = {
        str(r["revision_id"]): r
        for r in conn.execute(
            "SELECT * FROM revision_coverage WHERE pending = 1"
        ).fetchall()
    }
    if set(oracle_pending) != set(mat_pending):
        mismatches.append(
            {
                "kind": "pending_set",
                "oracle_only": sorted(set(oracle_pending) - set(mat_pending)),
                "materialized_only": sorted(
                    set(mat_pending) - set(oracle_pending)
                ),
            }
        )
    else:
        for rev_id, want in oracle_pending.items():
            got = mat_pending[rev_id]
            want_missing = ",".join(want["missing"])
            if str(got["missing"]) != want_missing or bool(
                got["has_pending_job"]
            ) != bool(want["has_pending_job"]):
                mismatches.append(
                    {
                        "revision_id": rev_id,
                        "kind": "pending_row",
                        "materialized": {
                            "missing": got["missing"],
                            "has_pending_job": bool(got["has_pending_job"]),
                        },
                        "oracle": want,
                    }
                )
    holes = {
        str(r["revision_id"]): r
        for r in conn.execute("SELECT * FROM coverage_hole").fetchall()
    }
    if set(holes) != set(mat_pending):
        mismatches.append(
            {
                "kind": "hole_set",
                "holes_only": sorted(set(holes) - set(mat_pending)),
                "pending_only": sorted(set(mat_pending) - set(holes)),
            }
        )
    return {"mismatches": mismatches, "checked": checked}


def stored_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM coverage_meta WHERE key = ?", (META_VERSION_KEY,)
    ).fetchone()
    return str(row[0]) if row is not None else None


def maybe_heal_version(conn: sqlite3.Connection) -> dict:
    """Drain-start version gate: stale derivation re-derives, then heals.

    When the materialized version differs from code, every freshness
    read already reports pending (see ``freshness_snapshot``); the drain
    repairs by full backfill so the next read is exact again. Returns
    ``{"healed": bool}``.
    """
    if not coverage_tables_present(conn):
        return {"healed": False, "reason": "coverage tables absent"}
    if stored_version(conn) == COVERAGE_VERSION:
        return {"healed": False}
    backfill_from_recompute(conn)
    return {"healed": True}


# -- query-path reads (indexed only) --------------------------------------

#: Exact statements served on the query path (kept as constants so tests
#: can EXPLAIN them and assert no full-table scans).
SQL_PENDING_PAGE = (
    "SELECT revision_id, source_id, missing, has_pending_job"
    " FROM revision_coverage WHERE pending = 1"
    " ORDER BY observed_at, revision_id LIMIT ?"
)
SQL_PENDING_COUNT = "SELECT COUNT(*) FROM revision_coverage WHERE pending = 1"
SQL_HOLE_PAGE = (
    "SELECT revision_id, source_id, observed_at, missing, reason"
    " FROM coverage_hole ORDER BY observed_at, revision_id LIMIT ?"
)
SQL_HOLE_COUNT = "SELECT COUNT(*) FROM coverage_hole"
SQL_META_GET = "SELECT value FROM coverage_meta WHERE key = ?"
SQL_LIVE_JOBS = (
    "SELECT COUNT(*) FROM projection_job WHERE state IN ('pending', 'claimed')"
)


def pending_from_state(conn: sqlite3.Connection, limit: int) -> tuple[list[dict], int]:
    """Bounded pending page + exact total (partial-index count)."""
    total = int(conn.execute(SQL_PENDING_COUNT).fetchone()[0])
    rows = conn.execute(SQL_PENDING_PAGE, (int(limit),)).fetchall()
    page = [
        {
            "revision_id": str(r["revision_id"]),
            "source_id": str(r["source_id"]),
            "missing": str(r["missing"]).split(",") if r["missing"] else [],
            "has_pending_job": bool(r["has_pending_job"]),
        }
        for r in rows
    ]
    return page, total


def hole_page(
    conn: sqlite3.Connection, limit: int = HOLE_CAP
) -> tuple[list[dict], int, bool]:
    """Bounded hole enumeration + total + truncated flag."""
    total = int(conn.execute(SQL_HOLE_COUNT).fetchone()[0])
    rows = conn.execute(SQL_HOLE_PAGE, (int(limit),)).fetchall()
    holes = [
        {
            "revision_id": str(r["revision_id"]),
            "source_id": str(r["source_id"]),
            "missing": str(r["missing"]).split(",") if r["missing"] else [],
            "reason": str(r["reason"]),
        }
        for r in rows
    ]
    return holes, total, total > len(holes)


def freshness_snapshot(conn: sqlite3.Connection, limit: int = HOLE_CAP) -> dict:
    """All materialized inputs a freshness read needs (indexed lookups).

    No base-table scans: two partial-index counts, one meta PK lookup
    each for version + watermark, one bounded hole page.
    """
    live_jobs = int(conn.execute(SQL_LIVE_JOBS).fetchone()[0])
    pending_page, pending_total = pending_from_state(conn, limit)
    holes, _hole_total, truncated = hole_page(conn, limit)
    version = conn.execute(SQL_META_GET, (META_VERSION_KEY,)).fetchone()
    watermark = conn.execute(SQL_META_GET, (META_WATERMARK_KEY,)).fetchone()
    version_ok = version is not None and str(version[0]) == COVERAGE_VERSION
    return {
        "live_jobs": live_jobs,
        "pending_page": pending_page,
        "pending_total": pending_total,
        "holes": holes,
        "holes_truncated": truncated,
        "version_ok": version_ok,
        "stored_version": str(version[0]) if version is not None else None,
        "watermark": str(watermark[0]) if watermark is not None else "",
    }


def explain_plan(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[str]:
    """EXPLAIN QUERY PLAN detail strings for one read statement."""
    cur = conn.execute("EXPLAIN QUERY PLAN " + sql, params)
    try:
        names = [d[0] for d in cur.description or []]
        idx = names.index("detail") if "detail" in names else 3
    except Exception:
        idx = 3
    return [str(r[idx]) for r in cur.fetchall()]



