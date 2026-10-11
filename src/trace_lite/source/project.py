"""INT-1 projection pipeline: bounded synchronous drain over R01/R02/R04/R05.

Wires the verified library components into one per-write pipeline:

``segment`` (R02 ``views``) -> ``extract`` (R04 extractor + graph store)
-> ``reconcile`` (R05 ``HolonStore`` corpus grouping) -> ``index``
(job drained; the served lexical publication is the ``filing`` generation
bump owned by the caller in ``api/app.py``).

Single-node, bounded, synchronous: every served write drains the outbox
under the writer lock before ack, so readers never observe silent lag.
Each revision carries a coverage checklist
``[segmented, extracted, reconciled, indexed]`` computed live from existing
rows (no migration): ``freshness`` reports revisions with incomplete
coverage even when the outbox queue is empty (never vacuous).

Idempotency: every stage checks for existing output before writing, so a
replay (same key + payload, ``created=False``) or a resume drain never
duplicates spans, runs, or holon memberships.

Reconciliation policy (minimal, honest): the pipeline groups each
revision's lexical atom into one pipeline-maintained ``thematic_cluster``
corpus holon (cross-source grouping; membership never implies agreement,
and no similarity score ever mints a typed link — R05). Typed links and
canonical entities stay manual. Corpus creation needs >= 2 atoms, so a
first lone revision reports ``reconciled=False`` with an explicit reason
until a second atom arrives (backfilled on the next drain). The corpus
holon is pipeline-maintained (state refreshed synchronously by
``add_members``), so invalidation callers exclude it: only downstream
syntheses go stale on premise edits.

Obsolete-worker rule (ARCH section 3 step 5): a refresh job whose revision
is no longer current is completed WITHOUT projecting (late output never
overwrites current projections); retract jobs carry no projections (the
caller invalidates synchronously from its pre-write snapshot).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from trace_lite.filing.holon import HolonStore

from . import coverage_state as _cov
from . import service as _svc
from . import views as _views

try:
    from trace_lite.graph.extractor import extract_spans
    from trace_lite.graph.store import create_extraction_run, persist_graph
except ImportError:  # pragma: no cover - packaging guard, venv provides graph
    extract_spans = None  # type: ignore[assignment]
    create_extraction_run = None  # type: ignore[assignment]
    persist_graph = None  # type: ignore[assignment]

try:
    from trace_lite.store import outbox as _outbox
except ImportError:  # pragma: no cover
    _outbox = None  # type: ignore[assignment]

#: Per-revision projection coverage checklist (ordered pipeline stages).
COVERAGE_STAGES = ("segmented", "extracted", "reconciled", "indexed")

#: Pipeline-maintained corpus grouping: kind + derivation marker.
CORPUS_KIND = "thematic_cluster"
CORPUS_DERIVATION = "int-1:corpus grouping (pipeline-maintained thematic_cluster)"

#: Bound on jobs drained per call (single-node first; no unbounded scans).
DRAIN_LIMIT = 64
#: Bound on backfill atoms grouped per drain (keeps corpus catch-up O(1)-ish).
BACKFILL_LIMIT = 16
#: Cap on revision ids exposed through freshness payloads.
PENDING_REVISIONS_CAP = 64

#: Contention discipline (G2 repair cycle 2): atoms grouped per write commit.
#: R1's single-group-per-pass amortizes the O(corpus) fold scan; chunking
#: keeps that amortization (a few calls per pass, not one per job) while no
#: single write txn spans a full 64-job projection. 8 keeps the per-txn
#: over-1s fraction near ~2% under continuous drift (each full recompute
#: is diluted across many fold commits) at ~0.3 s/pass extra scan cost at
#: 10 k — well inside the drain budget. Override:
#: ``TRACE_LITE_DRAIN_GROUP_CHUNK``.
GROUP_COMMIT_CHUNK = 8
#: Yield ``DRAIN_PACE_S`` every ``DRAIN_PACE_EVERY`` claimed jobs so a
#: back-to-back drain never starves a concurrent writer's BEGIN IMMEDIATE
#: (SQLite busy handlers need ms-scale gaps; the sub-ms gaps between
#: back-to-back short txns are not acquisition windows). Override:
#: ``TRACE_LITE_DRAIN_PACE_S``
#: (0 disables); ``TRACE_LITE_DRAIN_PACE_EVERY``.
DRAIN_PACE_EVERY = 8
DRAIN_PACE_S = 0.005
#: Minimum seconds between coverage heals (``ensure_coverage_schema`` walks
#: every pending row; at 10 k pending that is a ~0.5 s write txn, twice per
#: pass unthrottled). Triggers maintain the state incrementally, so the heal
#: only repairs drift; the first call per process and the grouping-table
#: creation transition always heal. Override: ``TRACE_LITE_DRAIN_HEAL_S``
#: (0 = every pass, the pre-repair behavior).
HEAL_MIN_INTERVAL_S = 30.0
#: Minimum drain passes between corpus groupings while membership drift
#: is detected (a cascaded atom delete flips the next grouping from a ~30 ms
#: fold into a full O(corpus) recompute INSIDE one write txn — measured
#: 2.95 s at 4 k members, ~8 s projected at 10 k — which beats every
#: concurrent writer's 5 s busy timeout). Deferral batches cascades: drifted
#: passes stage cheaply (idempotent reuse) and release to pending; the next
#: grouping pays ONE full recompute for all batched cascades. Pass-count
#: (not wall time) adapts to the driver: a tight backfill loop and the
#: 1 s-polled served worker both group every few passes. Small corpuses
#: never defer (the full recompute is sub-second there). Override:
#: ``TRACE_LITE_GROUP_MIN_PASSES`` (0 disables deferral);
#: ``TRACE_LITE_GROUP_DEFER_MIN_MEMBERS``.
GROUP_MIN_PASSES = 6
GROUP_DEFER_MIN_MEMBERS = 500

#: Attempts/backoff for drain-side transient-busy retries (each attempt also
#: waits up to the connection's busy_timeout inside SQLite).
DRAIN_BUSY_ATTEMPTS = 3
DRAIN_BUSY_BASE_S = 0.05


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _group_chunk() -> int:
    return max(1, _env_int("TRACE_LITE_DRAIN_GROUP_CHUNK", GROUP_COMMIT_CHUNK))


def _pace_every() -> int:
    return max(1, _env_int("TRACE_LITE_DRAIN_PACE_EVERY", DRAIN_PACE_EVERY))


def _pace_s() -> float:
    return max(0.0, _env_float("TRACE_LITE_DRAIN_PACE_S", DRAIN_PACE_S))


def _heal_interval_s() -> float:
    return max(0.0, _env_float("TRACE_LITE_DRAIN_HEAL_S", HEAL_MIN_INTERVAL_S))


def _group_min_passes() -> int:
    return max(0, _env_int("TRACE_LITE_GROUP_MIN_PASSES", GROUP_MIN_PASSES))


def _group_defer_min_members() -> int:
    return max(0, _env_int("TRACE_LITE_GROUP_DEFER_MIN_MEMBERS",
                           GROUP_DEFER_MIN_MEMBERS))


#: Throttle clocks keyed by connection identity (the drain is
#: single-driver per file, but one process may serve several files across
#: its lifetime — e.g. tests — and each file needs its own DDL ensure and
#: drift window). Entries are small ints/floats; the maps are capped.
_heal_ts_by_conn: dict[int, float] = {}
_heal_holon_by_conn: dict[int, bool] = {}
_passes_since_group_by_conn: dict[int, int] = {}


def reset_contention_state() -> None:
    """Reset drain-throttle clocks (tests/probes only; production never calls)."""
    _heal_ts_by_conn.clear()
    _heal_holon_by_conn.clear()
    _passes_since_group_by_conn.clear()


def _throttle_key(conn: sqlite3.Connection) -> int:
    if len(_heal_ts_by_conn) > 1024:
        reset_contention_state()
    return id(conn)


def _is_busy_error(exc: BaseException) -> bool:
    """True for transient SQLite lock contention (SQLITE_BUSY / locked)."""
    return (isinstance(exc, sqlite3.OperationalError)
            and ("locked" in str(exc).lower() or "busy" in str(exc).lower()))


def _rollback_quiet(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def _retry_on_busy(conn: sqlite3.Connection, fn, *, attempts: int,
                   base_s: float):
    """Run idempotent ``fn`` with bounded busy retry (rollback before retry).

    Non-busy errors propagate immediately. When attempts exhaust, the last
    busy error propagates (callers convert to held/deferred, never fatal).
    """
    err: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:
            if not _is_busy_error(exc):
                raise
            err = exc
            _rollback_quiet(conn)
            if i + 1 < attempts:
                time.sleep(base_s * (2 ** i))
    assert err is not None
    raise err


def _has_holon_tables(conn: sqlite3.Connection) -> bool:
    """Whether grouping tables exist on this file yet.

    ``holons``/``holon_members`` come from the filing ``HOLON_SCHEMA``
    (ensured lazily by ``HolonStore``), not the static ``schema.sql`` — so a
    file that never ran a grouping has no such tables. Coverage then reports
    ``reconciled=False`` honestly instead of raising; the write path
    ensures the DDL via ``HolonStore`` before grouping.
    """
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('holons', 'holon_members')")}
    except Exception:
        return False
    return names == {"holons", "holon_members"}


class ProjectionError(RuntimeError):
    """A revision's projection failed; source retained, pending explicit."""


# -- coverage -----------------------------------------------------------

def coverage_for_revision(conn: sqlite3.Connection, revision_id: str) -> dict:
    """Live coverage checklist for one revision from existing rows."""
    rev = _svc.get_revision(conn, revision_id)
    if rev is None:
        return {
            "revision_id": revision_id,
            "stages": dict.fromkeys(COVERAGE_STAGES, False),
            "missing": list(COVERAGE_STAGES),
            "reason": "unknown revision",
        }
    segmented = (
        conn.execute(
            "SELECT 1 FROM segmentation_view WHERE revision_id = ?"
            " AND status = 'complete' LIMIT 1",
            (revision_id,),
        ).fetchone()
        is not None
    )
    extracted = (
        conn.execute(
            "SELECT 1 FROM extraction_run WHERE revision_id = ?"
            " AND status = 'complete' LIMIT 1",
            (revision_id,),
        ).fetchone()
        is not None
    )
    atom_id = rev["atom_id"]
    if atom_id is None:
        reconciled = True
        rec_reason = "no lexical atom (retracted/superseded link cleared)"
    elif not _has_holon_tables(conn):
        reconciled = False
        rec_reason = "grouping tables absent; no projection has grouped this file yet"
    else:
        reconciled = (
            conn.execute(
                "SELECT 1 FROM holon_members m JOIN holon_state s"
                " ON s.holon_id = m.holon_id WHERE m.atom_id = ?"
                " AND s.status != 'deleted' LIMIT 1",
                (int(atom_id),),
            ).fetchone()
            is not None
        )
        rec_reason = "" if reconciled else "atom not grouped into any live holon"
    job = _svc.job_for_revision(conn, revision_id)
    indexed = job is not None and str(job["state"]) == "done"
    stages = {
        "segmented": bool(segmented),
        "extracted": bool(extracted),
        "reconciled": bool(reconciled),
        "indexed": bool(indexed),
    }
    missing = [stage for stage, ok in stages.items() if not ok]
    reasons = []
    if not segmented:
        reasons.append("no complete segmentation_view")
    if not extracted:
        reasons.append("no complete extraction_run")
    if not reconciled:
        reasons.append(rec_reason)
    if not indexed:
        reasons.append(
            "no drained projection job" if job is None
            else f"job {job['job_id']} is {job['state']}"
        )
    return {
        "revision_id": revision_id,
        "source_id": str(rev["source_id"]),
        "atom_id": int(atom_id) if atom_id is not None else None,
        "stages": stages,
        "missing": missing,
        "reason": "; ".join(reasons),
        "has_pending_job": job is not None and str(job["state"]) in ("pending", "claimed"),
    }


def revisions_in_scope(conn: sqlite3.Connection) -> list[dict]:
    """Currently served revisions: the ones source pointers select.

    Only pointed-to revisions gate readiness (current-mode results are
    version-filtered). Superseded history and unpointed ad-hoc rows (e.g. a
    manually injected outbox probe with no source pointer) never block it.
    """
    return [
        dict(r)
        for r in conn.execute(
            "SELECT sr.revision_id, sr.source_id FROM source_revision sr"
            " JOIN source s ON s.current_revision_id = sr.revision_id"
            " ORDER BY sr.observed_at, sr.revision_id"
        ).fetchall()
    ]


def pending_coverage(conn: sqlite3.Connection, limit: int = PENDING_REVISIONS_CAP) -> list[dict]:
    """In-scope revisions missing >= 1 coverage stage (bounded, one query).

    S2: served from the incrementally maintained ``revision_coverage``
    state (partial-index page, never a pending-table scan). Contract
    unchanged: same order, same dict shape, same default cap. Files
    whose coverage state was never ensured (pre-S2 library use) fall
    back to the exact recomputation oracle.
    """
    if _cov.coverage_tables_present(conn):
        page, _total = _cov.pending_from_state(conn, limit)
        return page
    if _has_holon_tables(conn):
        rec_expr = (" CASE WHEN sr.atom_id IS NULL THEN 1"
                    " ELSE EXISTS(SELECT 1 FROM holon_members m JOIN holon_state s"
                    " ON s.holon_id = m.holon_id WHERE m.atom_id = sr.atom_id"
                    " AND s.status != 'deleted') END AS rec,")
    else:
        rec_expr = " CASE WHEN sr.atom_id IS NULL THEN 1 ELSE 0 END AS rec,"
    rows = conn.execute(
        "SELECT sr.revision_id, sr.source_id,"
        " EXISTS(SELECT 1 FROM segmentation_view v WHERE v.revision_id = sr.revision_id"
        " AND v.status = 'complete') AS seg,"
        " EXISTS(SELECT 1 FROM extraction_run r WHERE r.revision_id = sr.revision_id"
        " AND r.status = 'complete') AS ext,"
        + rec_expr +
        " EXISTS(SELECT 1 FROM projection_job j"
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


# -- reconciliation (corpus grouping) ------------------------------------

def find_corpus_holon(conn: sqlite3.Connection) -> str | None:
    """Id of the pipeline-maintained corpus holon (None when absent)."""
    if not _has_holon_tables(conn):
        return None
    row = conn.execute(
        "SELECT holon_id FROM holon_state WHERE kind = ? AND derivation = ?"
        " AND status != 'deleted' ORDER BY updated_at LIMIT 1",
        (CORPUS_KIND, CORPUS_DERIVATION),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _grouped_atom_ids(conn: sqlite3.Connection, atom_ids: list[int]) -> set[int]:
    if not atom_ids:
        return set()
    marks = ",".join("?" * len(atom_ids))
    return {
        int(r[0])
        for r in conn.execute(
            "SELECT m.atom_id FROM holon_members m JOIN holon_state s"
            " ON s.holon_id = m.holon_id WHERE m.atom_id IN (%s)"
            " AND s.status != 'deleted'" % marks,
            [int(a) for a in atom_ids],
        ).fetchall()
    }


def _live_atom(atom_id: int, conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM atom WHERE id = ?", (int(atom_id),)).fetchone()
        is not None
    )


def _backfill_candidates(conn: sqlite3.Connection, exclude: set[int],
                         limit: int = BACKFILL_LIMIT) -> list[int]:
    """Other served atoms still ungrouped (oldest first, bounded)."""
    rows = conn.execute(
        "SELECT sr.atom_id FROM source_revision sr JOIN source s"
        " ON s.current_revision_id = sr.revision_id"
        " WHERE sr.atom_id IS NOT NULL ORDER BY sr.observed_at, sr.revision_id"
        f" LIMIT {int(limit * 4)}"
    ).fetchall()
    cands = [int(r[0]) for r in rows if int(r[0]) not in exclude]
    grouped = _grouped_atom_ids(conn, cands)
    return [aid for aid in cands if aid not in grouped][:limit]


def ensure_grouped(conn: sqlite3.Connection, atom_ids: list[int]) -> dict:
    """Group atoms into the corpus holon. Returns grouped/deferred + reason.

    Idempotent: already-grouped atoms are skipped, never duplicated (the
    store raises on duplicate membership, so the check-before-add is the
    replay guard). A lone first atom defers honestly: corpus creation
    needs >= 2 atoms.
    """
    store = HolonStore(conn)  # idempotent DDL ensure (commits when needed)
    live = [int(a) for a in atom_ids if _live_atom(int(a), conn)]
    if not live:
        return {"grouped": [], "deferred": [int(a) for a in atom_ids],
                "corpus": find_corpus_holon(conn),
                "reason": "no live atoms to group"}
    grouped_now = _grouped_atom_ids(conn, live)
    ungrouped = [aid for aid in live if aid not in grouped_now]
    if not ungrouped:
        return {"grouped": [], "deferred": [], "already_grouped": sorted(live),
                "corpus": find_corpus_holon(conn), "reason": ""}
    corpus = find_corpus_holon(conn)
    if corpus is None:
        cands = list(dict.fromkeys(ungrouped + _backfill_candidates(
            conn, set(ungrouped), BACKFILL_LIMIT)))
        cands = [aid for aid in cands if _live_atom(aid, conn)]
        if len(cands) < 2:
            conn.execute(
                "INSERT INTO events (event_id, stream_id, stream_seq, event_type,"
                " occurred_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, "projection:corpus",
                 _next_event_seq(conn, "projection:corpus"),
                 "projection.reconcile_deferred",
                 datetime.now(timezone.utc).isoformat(),
                 json.dumps(
                     {"deferred_atoms": cands,
                      "reason": "thematic_cluster needs >= 2 atoms"}),
                 ),
            )
            conn.commit()
            return {"grouped": [], "deferred": ungrouped, "corpus": None,
                    "reason": "thematic_cluster needs >= 2 atoms;"
                              " deferred until a second atom arrives"}
        corpus = store.create_holon(
            cands, kind=CORPUS_KIND, derivation=CORPUS_DERIVATION)
        return {"grouped": sorted(cands), "deferred": [], "corpus": corpus,
                "reason": ""}
    fresh = [aid for aid in ungrouped
             if aid not in _grouped_atom_ids(conn, [aid])]
    if not fresh:
        return {"grouped": [], "deferred": [], "already_grouped": sorted(ungrouped),
                "corpus": corpus, "reason": ""}
    store.add_members(corpus, sorted(fresh))
    return {"grouped": sorted(fresh), "deferred": [],
            "already_grouped": sorted(set(ungrouped) - set(fresh)),
            "corpus": corpus, "reason": ""}


def _next_event_seq(conn: sqlite3.Connection, stream_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(stream_seq), -1) FROM events WHERE stream_id = ?",
        (stream_id,),
    ).fetchone()
    return int(row[0]) + 1


# -- single-revision pipeline ---------------------------------------------

def _project_segment_extract(conn: sqlite3.Connection, revision_id: str,
                             report: dict) -> str | None:
    """Run the segment -> extract stages for one revision (idempotent).

    Shared by :func:`project_revision` and the batched
    :func:`drain_outbox` path so both observe identical stage semantics.
    Returns the complete segmentation ``view_id`` on success, else None
    (the failure is already recorded + noted on ``report``).
    """
    # -- segment (R02) --
    existing_views = [
        v for v in _views.list_views(conn, revision_id) if v["status"] == "complete"
    ]
    if existing_views:
        view_id = str(existing_views[-1]["view_id"])
        report["segmented"] = True
        report["notes"].append(f"reused segmentation view {view_id}")
    else:
        try:
            view = _views.create_segmentation_view(conn, revision_id)
        except Exception as exc:  # storage-level failure: explicit partial
            _record_failure(conn, revision_id, "segmented", str(exc))
            report["notes"].append(f"segmentation failed: {exc}")
            return None
        if view["status"] != "complete":
            _record_failure(conn, revision_id, "segmented",
                            "view recorded partial status")
            report["notes"].append("segmentation partial; spans withheld")
            return None
        view_id = str(view["view_id"])
        report["segmented"] = True
        report["notes"].append(
            f"segmented view {view_id} ({view['span_count']} spans)")
    # -- extract (R04) --
    run_row = conn.execute(
        "SELECT run_id FROM extraction_run WHERE revision_id = ? AND view_id = ?"
        " AND status = 'complete' ORDER BY created_at LIMIT 1",
        (revision_id, view_id),
    ).fetchone()
    if run_row is not None:
        report["extracted"] = True
        report["notes"].append(f"reused extraction run {run_row[0]}")
    else:
        if extract_spans is None:
            _record_failure(conn, revision_id, "extracted", "graph stack unavailable")
            report["notes"].append("graph stack unavailable")
            return None
        try:
            spans = _views.get_spans(conn, view_id)
            run_id = uuid.uuid4().hex
            result = extract_spans(spans, run_id=run_id)
            assert create_extraction_run is not None and persist_graph is not None
            create_extraction_run(conn, revision_id, view_id, run_id=run_id)
            counts = persist_graph(conn, result, run_id=run_id,
                                   revision_id=revision_id)
        except Exception as exc:
            _record_failure(conn, revision_id, "extracted", str(exc))
            report["notes"].append(f"extraction failed: {exc}")
            return None
        report["extracted"] = True
        report["notes"].append(
            f"extracted run {run_id} "
            f"(assertions={counts['assertions']} relations={counts['relations']})")
    return view_id


def _apply_grouping_outcome(conn: sqlite3.Connection, revision_id: str,
                            atom_id: int, grouping: dict,
                            report: dict) -> None:
    """Fold one batched ``ensure_grouped`` outcome into a revision report.

    Same per-revision semantics as the reconcile tail of
    :func:`project_revision`: grouped/already-grouped atoms reconcile, a
    deferred atom records a ``projection.failed`` event for the
    ``reconciled`` stage and stays pending for the next pass.
    """
    if atom_id in grouping.get("grouped", []) or atom_id in grouping.get(
            "already_grouped", []):
        report["reconciled"] = True
        report["notes"].append(f"grouped into corpus {grouping['corpus']}")
    else:
        _record_failure(conn, revision_id, "reconciled",
                        grouping.get("reason", "deferred"))
        report["notes"].append(
            f"reconciliation deferred: {grouping.get('reason', '')}")


def project_revision(db, engine, revision_id: str, *, owner: str = "int-1") -> dict:
    """Run segment -> extract -> reconcile for one revision (idempotent).

    Each stage reuses existing output when present. Returns a stage report;
    a failed stage records a ``projection.failed`` event and stops the
    pipeline with the source retained (partial coverage stays explicit via
    :func:`coverage_for_revision`). Never raises on data errors.
    """
    del engine  # reserved: facet/centroid maintenance lives with the caller
    del owner
    conn = db.conn
    report: dict = {"revision_id": revision_id, "segmented": False,
                    "extracted": False, "reconciled": False, "notes": []}
    rev = _svc.get_revision(conn, revision_id)
    if rev is None:
        report["notes"].append("unknown revision; nothing projected")
        return report
    if _project_segment_extract(conn, revision_id, report) is None:
        return report
    # -- reconcile (R05 corpus grouping) --
    atom_id = _svc.atom_id_for_revision(conn, revision_id)
    if atom_id is None:
        report["reconciled"] = True
        report["notes"].append("no lexical atom; nothing to group")
    else:
        try:
            grouping = ensure_grouped(conn, [atom_id])
        except Exception as exc:
            _record_failure(conn, revision_id, "reconciled", str(exc))
            report["notes"].append(f"reconciliation failed: {exc}")
            return report
        _apply_grouping_outcome(conn, revision_id, atom_id, grouping, report)
    return report


def _record_failure(conn: sqlite3.Connection, revision_id: str,
                    stage: str, error: str) -> None:
    conn.execute(
        "INSERT INTO events (event_id, stream_id, stream_seq, event_type,"
        " occurred_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, f"projection:{revision_id}",
         _next_event_seq(conn, f"projection:{revision_id}"),
         "projection.failed",
         datetime.now(timezone.utc).isoformat(),
         json.dumps({"stage": stage, "error": error[:500]})),
    )
    conn.commit()


# -- bounded synchronous drain ---------------------------------------------

def _maybe_heal_coverage(conn: sqlite3.Connection) -> dict:
    """Throttled coverage-schema ensure (drain path only, never queries).

    The DDL ensure is idempotent and the triggers maintain the state
    incrementally; the expensive part (walking every pending row) only
    repairs drift, so it runs at most every ``HEAL_MIN_INTERVAL_S`` per
    file — plus always when the tables are absent, on the first call per
    file, and on the grouping-table creation transition (when the holon
    triggers can first exist). A persistently busy heal is SKIPPED
    (triggers keep the state fresh; the next pass retries) — never fatal
    to the drain.
    """
    key = _throttle_key(conn)
    holon_now = _has_holon_tables(conn)
    now = time.monotonic()
    interval = _heal_interval_s()
    last = _heal_ts_by_conn.get(key, 0.0)
    # DDL ensure is never throttled: a file without coverage tables needs
    # it unconditionally (fresh files, per-file independence).
    need_ddl = not _cov.coverage_tables_present(conn)
    first = last == 0.0
    prev_holon = _heal_holon_by_conn.get(key)
    transition = prev_holon is not None and holon_now != prev_holon
    if not (need_ddl or first or transition or (now - last) >= interval):
        return {"healed": False, "reason": "throttled",
                "age_s": round(now - last, 3)}
    t0 = time.perf_counter()
    try:
        healed = _retry_on_busy(
            conn, lambda: _cov.ensure_coverage_schema(conn),
            attempts=2, base_s=DRAIN_BUSY_BASE_S)
    except Exception as exc:
        if _is_busy_error(exc):
            return {"healed": False, "reason": f"busy; skipped ({exc})",
                    "elapsed_s": round(time.perf_counter() - t0, 3)}
        raise
    _heal_ts_by_conn[key] = now
    _heal_holon_by_conn[key] = holon_now
    out = {"healed": True, "elapsed_s": round(time.perf_counter() - t0, 3)}
    out.update(healed)
    return out


def _group_defer_reason(conn: sqlite3.Connection, group_atoms: list[int]) -> str:
    """Why this pass must skip grouping ("" = group now).

    A cascaded atom delete (R01 edit/retract) invalidates the corpus fold
    gate, flipping the next grouping into a full O(corpus) recompute inside
    ONE write txn (measured 2.95 s at 4 k members). While drift is detected
    on a large corpus, grouping runs at most every ``GROUP_MIN_PASSES``
    passes so one full recompute absorbs every batched cascade instead of
    one per pass. The check is two indexed point reads (no member scan).
    Small corpuses and the corpus-creation path never defer.
    """
    if not group_atoms or _group_min_passes() <= 0:
        return ""
    corpus = find_corpus_holon(conn)
    if corpus is None:
        return ""
    try:
        row = conn.execute(
            "SELECT member_count FROM holon_state WHERE holon_id = ?",
            (corpus,)).fetchone()
        live = conn.execute(
            "SELECT COUNT(*) FROM holon_members WHERE holon_id = ?",
            (corpus,)).fetchone()[0]
    except Exception:
        return ""
    if row is None or int(live) == int(row[0]):
        return ""
    if int(live) < _group_defer_min_members():
        return ""
    since = _passes_since_group_by_conn.get(_throttle_key(conn))
    if since is None:
        return ""
    if since < _group_min_passes():
        return ("corpus drift (%s live vs %s stored members) and last grouping"
                " %d passes ago (< %d); deferred to batch cascades"
                % (live, row[0], since, _group_min_passes()))
    return ""


def _is_current(conn: sqlite3.Connection, revision_id: str) -> tuple[bool, str | None]:
    """Obsolete-worker check: is this revision still the served one?"""
    rev = _svc.get_revision(conn, revision_id)
    if rev is None:
        return False, None
    cur = _svc.get_current(conn, str(rev["source_id"]))
    if cur is None or str(cur["revision_id"]) != revision_id:
        return False, str(rev["source_id"])
    return True, str(rev["source_id"])


def drain_outbox(db, engine, *, owner: str = "int-1",
                 limit: int = DRAIN_LIMIT) -> dict:
    """Claim -> project -> complete/failed over pending jobs (bounded).

    - refresh job, revision still current: run segment -> extract, then
      group the whole pass's atoms with ONE :func:`ensure_grouped` call
      (R1 batching: the holon fold re-scans all member texts per call, so
      per-job grouping is O(N) per job and O(N^2) per drain; one call per
      pass amortizes it by the batch size). Complete on full coverage,
      fail (retryable, error recorded) otherwise.
    - refresh job, revision superseded: complete without projecting (late
      output never overwrites current projections).
    - retract job: complete without projecting (the write path invalidates
      synchronously from its pre-write snapshot).
    - poison guard: a job seen twice in one call is left pending.
    - G1 fairness: a failing job HOLDS its claim while the pass continues
      to later jobs (a lone-atom deferral must never head-of-line block a
      retract behind it); held claims release to pending at pass end.
    - contention discipline (G2 cycle 2): the pass's atoms group in
      ``GROUP_COMMIT_CHUNK``-atom commits with yields between (no single
      write txn spans a full 64-job projection); the claim loop paces
      itself so concurrent writers find acquisition gaps; the coverage
      heal is throttled (triggers maintain the state); grouping defers
      under large-corpus drift so one full recompute absorbs batched
      cascades instead of one per pass. Deferred jobs release to pending
      (reported under ``deferred``, never ``failed``) and converge on a
      later pass. Obsolete-skip, idempotency, and return shape preserved
      (new keys are additive).
    """
    del engine
    if _outbox is None:  # pragma: no cover
        return {"drained": 0, "completed": [], "failed": [], "skipped": []}
    conn = db.conn
    # S2 drain-path maintenance (write path only): throttled coverage heal
    # (triggers maintain the state; the heal only repairs drift), then the
    # cheap derivation-version gate (a single SELECT unless stale).
    heal_report = _maybe_heal_coverage(conn)
    _cov.maybe_heal_version(conn)
    _tkey = _throttle_key(conn)
    if _tkey in _passes_since_group_by_conn:
        _passes_since_group_by_conn[_tkey] += 1
    pace_every = _pace_every()
    pace_s = _pace_s()
    completed: list[str] = []
    failed: list[dict] = []
    skipped: list[dict] = []
    deferred: list[dict] = []
    held: list[tuple[str, str]] = []
    seen: set[str] = set()
    # R1: current refresh jobs whose segment/extract stages ran this pass;
    # their atoms group together below in chunked ensure_grouped commits.
    staged: list[dict] = []
    group_calls = 0
    txn_holds: list[float] = []
    drained = 0
    while drained < limit:
        if pace_s > 0 and drained > 0 and drained % pace_every == 0:
            time.sleep(pace_s)
        try:
            claim = _retry_on_busy(
                conn, lambda: _outbox.claim(conn, owner=owner),
                attempts=DRAIN_BUSY_ATTEMPTS, base_s=DRAIN_BUSY_BASE_S)
        except Exception as exc:
            if _is_busy_error(exc):
                # Transient writer pressure: resolve staged work below and
                # let the next pass reclaim (never fail the pass on this).
                break
            raise
        if claim is None:
            break
        if claim.job_id in seen:
            # Poison guard: this job already failed once in this call. The
            # claim above re-acquired our own lease and cleared its error,
            # so restore it to pending (retryable) before leaving it alone —
            # otherwise the drain itself would leak a live claim.
            try:
                _outbox.fail(conn, claim.job_id, owner=owner,
                             error="left pending after bounded drain retry")
            except Exception:
                pass
            break
        seen.add(claim.job_id)
        drained += 1
        job_type = str(claim.job_type)
        rev_id = str(claim.input_revision_id)
        try:
            if job_type == _svc.RETRACT_JOB_TYPE:
                _outbox.complete(conn, claim.job_id, owner=owner)
                completed.append(claim.job_id)
                continue
            current, _source = _is_current(conn, rev_id)
            if not current:
                conn.execute(
                    "INSERT INTO events (event_id, stream_id, stream_seq,"
                    " event_type, occurred_at, payload_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, f"projection:{rev_id}",
                     _next_event_seq(conn, f"projection:{rev_id}"),
                     "projection.skipped_superseded",
                     datetime.now(timezone.utc).isoformat(),
                     json.dumps({"job_id": claim.job_id})),
                )
                conn.commit()
                _outbox.complete(conn, claim.job_id, owner=owner)
                skipped.append({"job_id": claim.job_id, "reason": "superseded"})
                continue
            report: dict = {"revision_id": rev_id, "segmented": False,
                            "extracted": False, "reconciled": False,
                            "notes": []}
            rev = _svc.get_revision(conn, rev_id)
            if rev is None:
                report["notes"].append("unknown revision; nothing projected")
                staged.append({"job_id": claim.job_id, "rev_id": rev_id,
                               "atom_id": None, "report": report,
                               "stages_ok": False})
                continue
            ok = _project_segment_extract(conn, rev_id, report) is not None
            atom_id = _svc.atom_id_for_revision(conn, rev_id)
            if atom_id is None and ok:
                report["reconciled"] = True
                report["notes"].append("no lexical atom; nothing to group")
            staged.append({"job_id": claim.job_id, "rev_id": rev_id,
                           "atom_id": atom_id, "report": report,
                           "stages_ok": bool(ok)})
        except Exception as exc:  # never break the drain on one bad job
            held.append((claim.job_id, str(exc)[:500]))
            failed.append({"job_id": claim.job_id, "error": str(exc)[:200]})
    # R1 batched grouping, contention-chunked: the pass's staged atoms
    # group in GROUP_COMMIT_CHUNK-atom commits with yields between (group
    # then complete). Jobs whose stages failed or which have no atom skip
    # the calls but still resolve below via coverage.
    group_atoms = sorted({s["atom_id"] for s in staged
                          if s["stages_ok"] and s["atom_id"] is not None})
    grouping: dict | None = None
    grouping_error: str | None = None
    errored_atoms: set[int] = set()
    group_deferred_reason = ""
    deferred_ids: set[str] = set()
    if group_atoms:
        group_deferred_reason = _group_defer_reason(conn, group_atoms)
    if group_atoms and not group_deferred_reason:
        merged: dict = {"grouped": [], "already_grouped": [], "deferred": [],
                        "corpus": None, "reason": ""}
        chunk = _group_chunk()
        pieces = [group_atoms[lo:lo + chunk]
                  for lo in range(0, len(group_atoms), chunk)]
        for idx, piece in enumerate(pieces):
            t0 = time.perf_counter()
            try:
                one = _retry_on_busy(
                    conn, lambda p=piece: ensure_grouped(conn, list(p)),
                    attempts=DRAIN_BUSY_ATTEMPTS, base_s=DRAIN_BUSY_BASE_S)
            except Exception as exc:
                txn_holds.append(time.perf_counter() - t0)
                grouping_error = str(exc)[:500]
                # Only the failed chunk and the never-attempted later ones
                # are errored; earlier chunks grouped and resolve normally.
                for rest in pieces[idx:]:
                    errored_atoms.update(rest)
                break
            txn_holds.append(time.perf_counter() - t0)
            group_calls += 1
            merged["grouped"].extend(one.get("grouped", []))
            merged["already_grouped"].extend(one.get("already_grouped", []))
            merged["deferred"].extend(one.get("deferred", []))
            if one.get("corpus") is not None:
                merged["corpus"] = one["corpus"]
            if one.get("reason"):
                prev = merged["reason"]
                merged["reason"] = ((prev + "; " if prev else "")
                                    + str(one["reason"]))
            if pace_s > 0:
                time.sleep(pace_s)
        if group_calls:
            _passes_since_group_by_conn[_throttle_key(conn)] = 0
            grouping = merged
    elif group_deferred_reason:
        deferred_ids = {s["job_id"] for s in staged
                        if s["stages_ok"] and s["atom_id"] is not None}
    for s in staged:
        job_id, rev_id, atom_id = s["job_id"], s["rev_id"], s["atom_id"]
        report = s["report"]
        try:
            if (s["stages_ok"] and atom_id is not None and grouping_error
                    is not None and atom_id in errored_atoms):
                _record_failure(conn, rev_id, "reconciled", grouping_error)
                report["notes"].append(
                    f"reconciliation failed: {grouping_error[:200]}")
            elif (s["stages_ok"] and atom_id is not None
                    and grouping is not None):
                _apply_grouping_outcome(conn, rev_id, atom_id, grouping,
                                        report)
            coverage = coverage_for_revision(conn, rev_id)
            # "indexed" IS this job's completion (no chicken-and-egg: the
            # job completes once every other stage holds, which in turn
            # flips indexed to true).
            missing = [m for m in coverage["missing"] if m != "indexed"]
            if not missing:
                _outbox.complete(conn, job_id, owner=owner)
                completed.append(job_id)
            elif job_id in deferred_ids:
                # Contention deferral (not a failure): release to pending at
                # pass end; the next grouping pass completes it. Excluded
                # from failed[] so supervisors never back off on policy.
                held.append((job_id, "grouping deferred: "
                             + group_deferred_reason[:300]))
                deferred.append({"job_id": job_id, "missing": missing,
                                 "reason": group_deferred_reason[:200]})
            else:
                # Hold the claim: release to pending only at pass end so a
                # persistently failing job cannot starve later siblings.
                held.append((job_id,
                             "incomplete coverage: " + ",".join(missing)
                             + " :: " + " | ".join(report["notes"])[:400]))
                failed.append({"job_id": job_id, "missing": missing})
        except Exception as exc:  # never break the drain on one bad job
            held.append((job_id, str(exc)[:500]))
            failed.append({"job_id": job_id, "error": str(exc)[:200]})
    for job_id, error in held:
        try:
            _outbox.fail(conn, job_id, owner=owner, error=error)
        except Exception:
            pass
    # S2 post-drain ensure (throttled, same gate as the pre-drain heal):
    # grouping tables are created lazily mid-drain by the first corpus
    # grouping; the creation transition forces this heal so their triggers
    # exist from here on and mid-drain flag drift is repaired.
    post_heal_report = _maybe_heal_coverage(conn)
    holds = sorted(txn_holds)
    if holds:
        p95 = holds[min(len(holds) - 1, int(len(holds) * 0.95))]
        hold_stats: dict = {"max_s": round(holds[-1], 4),
                            "p95_s": round(p95, 4),
                            "n": len(holds),
                            "n_over_1s": sum(1 for h in holds if h > 1.0)}
    else:
        hold_stats = {"max_s": 0.0, "p95_s": 0.0, "n": 0, "n_over_1s": 0}
    return {"drained": drained, "completed": completed, "failed": failed,
            "skipped": skipped, "deferred": deferred, "limit": limit,
            "owner": owner, "group_calls": group_calls,
            "grouped_atoms": group_atoms,
            "group_deferred_reason": group_deferred_reason,
            "txn_hold_s": hold_stats, "heal": heal_report,
            "post_heal": post_heal_report}
