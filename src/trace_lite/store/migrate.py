"""Legacy migration tool: R01 migration 001 (ARCH §9).

Snapshots the live database with ``VACUUM INTO`` (consistent backup) BEFORE any
schema write, applies the migration-001 DDL, then backfills exactly one legacy
``source_revision`` per ``doc_id`` in a single transaction:

- single-atom doc: revision text is that atom's text, byte-exact; the revision
  links the atom row (``atom_id``) so scoped lexical search keeps working.
- multi-atom doc: revision text is the atoms' texts joined by ``"\\n"`` in atom-id
  order; ``legacy_atom_map`` records per-atom ``(start_char, end_char)`` offsets
  so every legacy atom stays byte-exact recoverable.

Migrated revisions are labeled ``coordinate_space = 'legacy_atom_text'``: the
original raw offsets cannot be reconstructed from zero-based body offsets
(ARCH §2). The ``atom`` table and every existing ``Database`` method keep working
(compat shim, not rewrite). Reruns are safe: when ``schema_version >= 1`` and
every atom is mapped, the backfill is skipped and parity is re-verified.
Parity < 100% raises :class:`MigrationParityError` without committing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_VERSION = 1
SPANS_MIGRATION_VERSION = 2
GRAPH_MIGRATION_VERSION = 3
HOLON_MIGRATION_VERSION = 4
COVERAGE_MIGRATION_VERSION = 5
TOMBSTONE_MIGRATION_VERSION = 6
LEGACY_COORDINATE_SPACE = "legacy_atom_text"
LEGACY_JOINER = "\n"


class MigrationParityError(RuntimeError):
    """Raised when migrated text is not 100% faithful to the legacy atoms."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def backup_path_for(db_path: str | Path) -> Path:
    return Path(str(db_path) + ".bak-001")


def snapshot_backup(db_path: str | Path) -> Path:
    """Write a consistent snapshot of the live DB via ``VACUUM INTO``."""
    src = Path(db_path)
    backup = backup_path_for(src)
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("VACUUM INTO ?", (str(backup),))
    finally:
        conn.close()
    return backup


def _parity_report(conn: sqlite3.Connection) -> dict:
    atoms = conn.execute(
        "SELECT id, doc_id, text, content_hash FROM atom ORDER BY id"
    ).fetchall()
    total = len(atoms)
    matched = 0
    per_doc: dict[str, dict] = {}
    for atom_id, doc_id, text, content_hash in atoms:
        m = conn.execute(
            "SELECT revision_id, start_char, end_char, text_hash FROM legacy_atom_map"
            " WHERE atom_id = ?",
            (atom_id,),
        ).fetchone()
        ok = False
        if m is not None:
            rev = conn.execute(
                "SELECT text FROM source_revision WHERE revision_id = ?",
                (m[0],),
            ).fetchone()
            if rev is not None and rev[0][m[1] : m[2]] == text:
                ok = bytes(m[3]) == _sha(text) == bytes(content_hash)
        matched += 1 if ok else 0
        doc = per_doc.setdefault(
            str(doc_id), {"atoms": 0, "matched": 0, "revision_id": None}
        )
        doc["atoms"] += 1
        doc["matched"] += 1 if ok else 0
        if m is not None and doc["revision_id"] is None:
            doc["revision_id"] = str(m[0])
    revisions = conn.execute(
        "SELECT COUNT(*) FROM source_revision WHERE coordinate_space = ?",
        (LEGACY_COORDINATE_SPACE,),
    ).fetchone()[0]
    sources = conn.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    fidelity = (matched / total) if total else 1.0
    return {
        "legacy_atoms": total,
        "mapped_atoms": matched,
        "fidelity": fidelity,
        "legacy_revisions": int(revisions),
        "sources": int(sources),
        "per_doc": per_doc,
    }


def migrate_legacy(db_path: str | Path, *, backup: bool = True) -> dict:
    """Migrate a legacy store to migration 001. Returns the parity report dict."""
    from .database import SCHEMA_PATH

    src = Path(db_path)
    backup_file: str | None = None
    if backup:
        existing = backup_path_for(src)
        backup_file = str(existing if existing.exists() else snapshot_backup(src))

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())

        version_row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        version = int(version_row[0]) if version_row[0] is not None else 0
        unmapped = conn.execute(
            "SELECT COUNT(*) FROM atom WHERE id NOT IN (SELECT atom_id FROM legacy_atom_map)"
        ).fetchone()[0]
        if version >= MIGRATION_VERSION and unmapped == 0:
            report = _parity_report(conn)
            report.update(
                {
                    "migration_version": MIGRATION_VERSION,
                    "backup": backup_file,
                    "migrated": False,
                    "reason": "already at migration 001 with full mapping",
                }
            )
            if report["fidelity"] < 1.0:
                raise MigrationParityError(
                    f"existing migration parity {report['fidelity']:.4f} < 1.0"
                )
            return report

        now = _utcnow()
        conn.execute("BEGIN IMMEDIATE")
        try:
            doc_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT doc_id FROM atom ORDER BY doc_id"
                ).fetchall()
            ]
            for doc_id in doc_ids:
                rows = conn.execute(
                    "SELECT id, text, content_hash FROM atom WHERE doc_id = ? ORDER BY id",
                    (doc_id,),
                ).fetchall()
                texts = [str(r[1]) for r in rows]
                full = LEGACY_JOINER.join(texts)
                digest = _sha(full)
                revision_id = "legacy_" + hashlib.sha256(
                    str(doc_id).encode("utf-8")
                ).hexdigest()[:32]
                conn.execute(
                    "INSERT OR IGNORE INTO source (source_id, namespace, kind, locator,"
                    " current_revision_id, access_scope, created_at, updated_at)"
                    " VALUES (?, 'default', 'legacy', ?, NULL, 'default', ?, ?)",
                    (str(doc_id), str(doc_id), now, now),
                )
                exists = conn.execute(
                    "SELECT 1 FROM source_revision WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
                if exists is None:
                    atom_link = (
                        int(rows[0][0]) if len(rows) == 1 else None
                    )
                    conn.execute(
                        "INSERT INTO source_revision (revision_id, source_id, content_hash,"
                        " media, encoding, coordinate_space, observed_at, supersedes,"
                        " status, text, byte_len, atom_id, namespace)"
                        " VALUES (?, ?, ?, 'legacy_atom_text', 'utf-8', ?, ?, NULL,"
                        " 'current', ?, ?, ?, 'default')",
                        (
                            revision_id,
                            str(doc_id),
                            digest,
                            LEGACY_COORDINATE_SPACE,
                            now,
                            full,
                            len(full.encode("utf-8")),
                            atom_link,
                        ),
                    )
                offset = 0
                for (atom_id, text, _content_hash), piece in zip(rows, texts):
                    start, end = offset, offset + len(piece)
                    conn.execute(
                        "INSERT OR IGNORE INTO legacy_atom_map"
                        " (atom_id, revision_id, start_char, end_char, text_hash)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (int(atom_id), revision_id, start, end, _sha(piece)),
                    )
                    offset = end + len(LEGACY_JOINER)
                conn.execute(
                    "UPDATE source SET current_revision_id = ?, updated_at = ?"
                    " WHERE source_id = ?",
                    (revision_id, now, str(doc_id)),
                )
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (MIGRATION_VERSION, now),
            )
            report = _parity_report(conn)
            if report["fidelity"] < 1.0 or report["mapped_atoms"] != report["legacy_atoms"]:
                raise MigrationParityError(
                    f"migration parity {report['fidelity']:.4f}:"
                    f" {report['mapped_atoms']}/{report['legacy_atoms']} atoms mapped"
                )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        report.update(
            {
                "migration_version": MIGRATION_VERSION,
                "backup": backup_file,
                "migrated": True,
            }
        )
        return report
    finally:
        conn.close()


def backup_path_for_spans(db_path: str | Path) -> Path:
    return Path(str(db_path) + ".bak-002")


def migrate_spans_002(db_path: str | Path, *, backup: bool = True) -> dict:
    """Apply R02 migration 002: add ``evidence_span`` + ``segmentation_view``.

    Purely additive: no existing table is altered or dropped, so rollback is
    restoring the ``.bak-002`` snapshot (or simply staying on the pre-002 file).
    The ``schema_version = 2`` row is written ONLY here — never by the static
    DDL — so migration-001 callers keep observing ``MAX(schema_version) == 1``
    until they opt into 002. Reruns are safe (idempotent ``IF NOT EXISTS`` DDL
    plus ``INSERT OR IGNORE`` of the version row).
    """
    from .database import SCHEMA_PATH

    src = Path(db_path)
    backup_file: str | None = None
    if backup:
        existing = backup_path_for_spans(src)
        if existing.exists():
            backup_file = str(existing)
        else:
            conn0 = sqlite3.connect(str(src))
            try:
                conn0.execute("VACUUM INTO ?", (str(existing),))
            finally:
                conn0.close()
            backup_file = str(existing)

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at)"
            " VALUES (?, ?)",
            (SPANS_MIGRATION_VERSION, _utcnow()),
        )
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        missing = {"evidence_span", "segmentation_view"} - tables
        if missing:
            raise MigrationParityError(f"migration 002 tables missing: {sorted(missing)}")
        version = int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
        return {
            "migration_version": SPANS_MIGRATION_VERSION,
            "observed_version": version,
            "backup": backup_file,
            "migrated": True,
            "tables": sorted(tables & {"evidence_span", "segmentation_view"}),
        }
    finally:
        conn.close()


GRAPH_TABLES = frozenset(
    {
        "extraction_run",
        "entity_mention",
        "assertion",
        "assertion_argument",
        "relation",
        "dependency",
    }
)


def backup_path_for_graph(db_path: str | Path) -> Path:
    return Path(str(db_path) + ".bak-003")


def migrate_graph_003(db_path: str | Path, *, backup: bool = True) -> dict:
    """Apply R04 migration 003: add local-graph tables (ARCH §2 records).

    Purely additive: no existing table is altered or dropped, so rollback is
    restoring the ``.bak-003`` snapshot (or simply staying on the pre-003
    file). The ``schema_version = 3`` row is written ONLY here — never by the
    static DDL — so migration-001/002 callers keep observing their
    ``MAX(schema_version)`` values until they opt into 003. Reruns are safe
    (idempotent ``IF NOT EXISTS`` DDL plus ``INSERT OR IGNORE`` of the
    version row). This contract is frozen for R05: later packets may add
    tables but must not alter these six.
    """
    from .database import SCHEMA_PATH

    src = Path(db_path)
    backup_file: str | None = None
    if backup:
        existing = backup_path_for_graph(src)
        if existing.exists():
            backup_file = str(existing)
        else:
            conn0 = sqlite3.connect(str(src))
            try:
                conn0.execute("VACUUM INTO ?", (str(existing),))
            finally:
                conn0.close()
            backup_file = str(existing)

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at)"
            " VALUES (?, ?)",
            (GRAPH_MIGRATION_VERSION, _utcnow()),
        )
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        missing = GRAPH_TABLES - tables
        if missing:
            raise MigrationParityError(f"migration 003 tables missing: {sorted(missing)}")
        version = int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
        return {
            "migration_version": GRAPH_MIGRATION_VERSION,
            "observed_version": version,
            "backup": backup_file,
            "migrated": True,
            "tables": sorted(tables & GRAPH_TABLES),
        }
    finally:
        conn.close()


HOLON_TABLES = frozenset(
    {
        "holon_state",
        "holon_version",
        "holon_containment",
        "holon_link",
        "holon_canonical",
    }
)


def backup_path_for_holon(db_path: str | Path) -> Path:
    return Path(str(db_path) + ".bak-004")


def migrate_holon_004(db_path: str | Path, *, backup: bool = True) -> dict:
    """Apply R05 migration 004: cross-source holon + reconciliation tables.

    Purely additive: no existing table is altered (``holon_members`` gains
    columns only via ``ADD COLUMN`` with defaults) or dropped, so rollback is
    restoring the ``.bak-004`` snapshot (or simply staying on the pre-004
    file). Legacy holon IDs and memberships are preserved byte-for-byte;
    every legacy holon gains a ``holon_state`` row (kind ``discourse_span``).
    The ``schema_version = 4`` row is written ONLY here — never by the static
    DDL — so migration-001/002/003 callers keep observing their
    ``MAX(schema_version)`` values until they opt into 004. Reruns are safe.
    This contract is frozen for R06/R07: later packets may add tables but
    must not alter these five.
    """
    from ..filing.holon import ensure_holon_aux
    from .database import SCHEMA_PATH

    src = Path(db_path)
    backup_file: str | None = None
    if backup:
        existing = backup_path_for_holon(src)
        if existing.exists():
            backup_file = str(existing)
        else:
            conn0 = sqlite3.connect(str(src))
            try:
                conn0.execute("VACUUM INTO ?", (str(existing),))
            finally:
                conn0.close()
            backup_file = str(existing)

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        aux = ensure_holon_aux(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at)"
            " VALUES (?, ?)",
            (HOLON_MIGRATION_VERSION, _utcnow()),
        )
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        missing = HOLON_TABLES - tables
        if missing:
            raise MigrationParityError(f"migration 004 tables missing: {sorted(missing)}")
        holons = conn.execute("SELECT COUNT(*) FROM holons").fetchone()[0]
        stateless = conn.execute(
            "SELECT COUNT(*) FROM holons WHERE holon_id NOT IN"
            " (SELECT holon_id FROM holon_state)").fetchone()[0]
        if stateless:
            raise MigrationParityError(
                f"migration 004 left {stateless} holons without state")
        version = int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
        return {
            "migration_version": HOLON_MIGRATION_VERSION,
            "observed_version": version,
            "backup": backup_file,
            "migrated": True,
            "tables": sorted(tables & HOLON_TABLES),
            "legacy_holons_preserved": int(holons),
            "member_columns_added": aux["member_columns_added"],
            "states_backfilled": aux["states_backfilled"],
        }
    finally:
        conn.close()


COVERAGE_TABLES = frozenset(
    {"revision_coverage", "coverage_hole", "coverage_meta"}
)


def backup_path_for_coverage(db_path: str | Path) -> Path:
    return Path(str(db_path) + ".bak-005")


def migrate_coverage_005(db_path: str | Path, *, backup: bool = True) -> dict:
    """Apply S2 migration 005: materialized coverage state (ARCH §2 records).

    Purely additive: three new tables, partial/support indexes, and
    triggers — no existing table is altered or dropped, so rollback is
    restoring the ``.bak-005`` snapshot (or staying on the pre-005 file).
    The ``schema_version = 5`` row is written ONLY here, so earlier
    callers keep observing their ``MAX(schema_version)`` values until
    they opt into 005. Reruns are safe (idempotent ``IF NOT EXISTS``
    DDL plus ``INSERT OR IGNORE`` of the version row; an already-current
    file is re-verified, not rewritten).

    Equivalence protocol (S2 oracle rule): the backfill is first applied
    on a scratch ``VACUUM INTO`` copy and checked against the exact
    recomputation; only then is the live file backfilled and re-checked.
    Any disagreement raises :class:`MigrationParityError` without
    writing the version row.
    """
    import tempfile

    from ..source import coverage_state as _cov

    src = Path(db_path)
    backup_file: str | None = None
    if backup:
        existing = backup_path_for_coverage(src)
        if existing.exists():
            backup_file = str(existing)
        else:
            conn0 = sqlite3.connect(str(src))
            try:
                conn0.execute("VACUUM INTO ?", (str(existing),))
            finally:
                conn0.close()
            backup_file = str(existing)

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        version_row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        version = int(version_row[0]) if version_row[0] is not None else 0
        if version >= COVERAGE_MIGRATION_VERSION:
            report = _verify_coverage_live(conn)
            report.update(
                {
                    "migration_version": COVERAGE_MIGRATION_VERSION,
                    "backup": backup_file,
                    "migrated": False,
                    "reason": "already at migration 005; re-verified",
                }
            )
            return report

        with tempfile.TemporaryDirectory(prefix="s2cov-") as tmpdir:
            scratch = Path(tmpdir) / "scratch.db"
            conn.execute("VACUUM INTO ?", (str(scratch),))
            sconn = sqlite3.connect(str(scratch))
            sconn.row_factory = sqlite3.Row
            try:
                backfilled = _cov.backfill_from_recompute(sconn)
                equivalence = _cov.verify_against_oracle(sconn)
                if equivalence["mismatches"]:
                    raise MigrationParityError(
                        "migration 005 scratch equivalence failed: "
                        f"{equivalence['mismatches'][:5]}"
                    )
            finally:
                sconn.close()

        backfilled = _cov.backfill_from_recompute(conn)
        equivalence = _cov.verify_against_oracle(conn)
        if equivalence["mismatches"]:
            raise MigrationParityError(
                "migration 005 live equivalence failed: "
                f"{equivalence['mismatches'][:5]}"
            )
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at)"
            " VALUES (?, ?)",
            (COVERAGE_MIGRATION_VERSION, _utcnow()),
        )
        conn.commit()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        missing = COVERAGE_TABLES - tables
        if missing:
            raise MigrationParityError(
                f"migration 005 tables missing: {sorted(missing)}"
            )
        observed = int(
            conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        )
        return {
            "migration_version": COVERAGE_MIGRATION_VERSION,
            "observed_version": observed,
            "backup": backup_file,
            "migrated": True,
            "tables": sorted(tables & COVERAGE_TABLES),
            "backfilled_revisions": backfilled["revisions"],
            "scratch_checked": equivalence["checked"],
            "scratch_mismatches": 0,
            "live_mismatches": 0,
        }
    finally:
        conn.close()


def _verify_coverage_live(conn: sqlite3.Connection) -> dict:
    """Re-verify an already-migrated file against the oracle (no rewrite)."""
    from ..source import coverage_state as _cov

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    missing = COVERAGE_TABLES - tables
    if missing:
        raise MigrationParityError(
            f"migration 005 tables missing: {sorted(missing)}"
        )
    equivalence = _cov.verify_against_oracle(conn)
    if equivalence["mismatches"]:
        raise MigrationParityError(
            "migration 005 re-verification failed: "
            f"{equivalence['mismatches'][:5]}"
        )
    version = int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
    return {
        "observed_version": version,
        "tables": sorted(tables & COVERAGE_TABLES),
        "checked": equivalence["checked"],
        "mismatches": 0,
    }


TOMBSTONE_TABLES = frozenset({"atom_tombstone"})

#: Fixed FTS probe terms for the 006 equivalence oracle (A1 §5.3.ii).
#: Plain single tokens only (valid bare FTS5 MATCH queries).
_TOMBSTONE_FTS_PROBES = ("the", "a", "test", "atom", "legacy", "note", "zzyzxqy")


def backup_path_for_tombstones(db_path: str | Path) -> Path:
    return Path(str(db_path) + ".bak-006")


def _atom_state(conn: sqlite3.Connection) -> tuple[int, str]:
    rows = conn.execute(
        "SELECT id, doc_id, content_hash, start_byte, end_byte, text"
        " FROM atom ORDER BY id"
    ).fetchall()
    digest = hashlib.sha256()
    for r in rows:
        digest.update(repr((r[0], r[1], bytes(r[2]), r[3], r[4], r[5])).encode("utf-8"))
    return len(rows), digest.hexdigest()


def _fts_probe_state(conn: sqlite3.Connection) -> dict[str, list[int]]:
    return {
        term: [
            int(r[0])
            for r in conn.execute(
                "SELECT rowid FROM fts_atoms WHERE fts_atoms MATCH ? ORDER BY rowid",
                (term,),
            ).fetchall()
        ]
        for term in _TOMBSTONE_FTS_PROBES
    }


def _tombstone_dump(conn: sqlite3.Connection) -> list[tuple] | None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    if "atom_tombstone" not in tables:
        return None
    return [
        (r[0], bytes(r[1]), r[2], r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT atom_id, content_hash, doc_id, retire_gen, reason, created_at"
            " FROM atom_tombstone ORDER BY atom_id"
        ).fetchall()
    ]


def _snapshot_006(conn: sqlite3.Connection) -> dict:
    count, digest = _atom_state(conn)
    version_row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return {
        "atom": (count, digest),
        "fts": _fts_probe_state(conn),
        "max_version": int(version_row[0]) if version_row[0] is not None else 0,
        "tombs": _tombstone_dump(conn),
    }


def _check_006_equivalence(pre: dict, post: dict) -> list[str]:
    """Oracle diff for 006 (A1 §5.3): empty-table migration must disturb nothing.

    (i) atom count + full-table hash identical; (ii) fixed FTS probe set
    returns identical rowids; (iii) tombstone table exists post-DDL — empty
    when absent pre-DDL, byte-identical when the file already carried rows
    (Database open auto-applies schema.sql, so live files may); (iv) DDL
    alone moves no version row.
    """
    mismatches = []
    if post["atom"] != pre["atom"]:
        mismatches.append(f"atom table changed: {pre['atom']} -> {post['atom']}")
    if post["fts"] != pre["fts"]:
        mismatches.append("FTS probe rowids changed")
    if post["tombs"] is None:
        mismatches.append("atom_tombstone missing after DDL")
    elif pre["tombs"] is None:
        if post["tombs"] != []:
            mismatches.append("fresh atom_tombstone is not empty")
    elif post["tombs"] != pre["tombs"]:
        mismatches.append("existing atom_tombstone rows disturbed")
    if post["max_version"] != pre["max_version"]:
        mismatches.append(
            f"MAX(schema_version) moved under DDL: {pre['max_version']}"
            f" -> {post['max_version']}"
        )
    return mismatches


def migrate_tombstones_006(db_path: str | Path, *, backup: bool = True) -> dict:
    """Apply A1 migration 006: add ``atom_tombstone`` + ``idx_tombstone_gen``.

    Purely additive: no existing table is altered or dropped, so rollback is
    restoring the ``.bak-006`` snapshot (or staying on the pre-006 file).
    The ``schema_version = 6`` row is written ONLY here — never by the static
    DDL — so migration-001..005 callers keep observing their
    ``MAX(schema_version)`` values until they opt into 006. Reruns are safe
    (an already-current file is re-verified, not rewritten).

    Equivalence protocol (005 pattern): the DDL is first applied on a
    scratch ``VACUUM INTO`` copy and checked against the oracle; only then is
    the live file migrated and re-checked. Any disagreement raises
    :class:`MigrationParityError` without writing the version row.

    Known limitation (A1 §5.5, documented not fixed): atoms physically
    deleted BEFORE 006 leave no tombstone; their ids resolve as unknown,
    never false-retired.
    """
    import tempfile

    from .database import SCHEMA_PATH

    src = Path(db_path)
    backup_file: str | None = None
    if backup:
        existing = backup_path_for_tombstones(src)
        if existing.exists():
            backup_file = str(existing)
        else:
            conn0 = sqlite3.connect(str(src))
            try:
                conn0.execute("VACUUM INTO ?", (str(existing),))
            finally:
                conn0.close()
            backup_file = str(existing)

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        version_row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        version = int(version_row[0]) if version_row[0] is not None else 0
        if version >= TOMBSTONE_MIGRATION_VERSION:
            report = _verify_tombstones_live(conn)
            report.update(
                {
                    "migration_version": TOMBSTONE_MIGRATION_VERSION,
                    "backup": backup_file,
                    "migrated": False,
                    "reason": "already at migration 006; re-verified",
                }
            )
            return report

        pre = _snapshot_006(conn)

        with tempfile.TemporaryDirectory(prefix="a1ts-") as tmpdir:
            scratch = Path(tmpdir) / "scratch.db"
            conn.execute("VACUUM INTO ?", (str(scratch),))
            sconn = sqlite3.connect(str(scratch))
            sconn.row_factory = sqlite3.Row
            try:
                with open(SCHEMA_PATH, encoding="utf-8") as fh:
                    sconn.executescript(fh.read())
                mismatches = _check_006_equivalence(pre, _snapshot_006(sconn))
                if mismatches:
                    raise MigrationParityError(
                        "migration 006 scratch equivalence failed: "
                        f"{mismatches[:5]}"
                    )
            finally:
                sconn.close()

        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        post = _snapshot_006(conn)
        mismatches = _check_006_equivalence(pre, post)
        if mismatches:
            raise MigrationParityError(
                "migration 006 live equivalence failed: "
                f"{mismatches[:5]}"
            )
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at)"
            " VALUES (?, ?)",
            (TOMBSTONE_MIGRATION_VERSION, _utcnow()),
        )
        conn.commit()
        observed = int(
            conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        )
        return {
            "migration_version": TOMBSTONE_MIGRATION_VERSION,
            "observed_version": observed,
            "pre_version": pre["max_version"],
            "backup": backup_file,
            "migrated": True,
            "tables": sorted(TOMBSTONE_TABLES),
            "atom_count": post["atom"][0],
            "atom_hash": post["atom"][1],
            "fts_probes_checked": len(_TOMBSTONE_FTS_PROBES),
            "scratch_mismatches": 0,
            "live_mismatches": 0,
        }
    finally:
        conn.close()


def _verify_tombstones_live(conn: sqlite3.Connection) -> dict:
    """Re-verify an already-migrated file (no rewrite)."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    missing = TOMBSTONE_TABLES - tables
    if missing:
        raise MigrationParityError(
            f"migration 006 tables missing: {sorted(missing)}"
        )
    probes = _fts_probe_state(conn)  # index must stay queryable
    version = int(conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0])
    tombs = _tombstone_dump(conn) or []
    return {
        "observed_version": version,
        "tables": sorted(tables & TOMBSTONE_TABLES),
        "atom_count": _atom_state(conn)[0],
        "tombstone_count": len(tombs),
        "fts_probes_checked": len(probes),
        "mismatches": 0,
    }
