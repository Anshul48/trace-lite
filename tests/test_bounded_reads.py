"""S2 bounded-reads tests (contract TL-QN-2026-09-12.1).

- Oracle equivalence under transition fuzz: materialized coverage ==
  exact recomputation after every step.
- Hole cases: deferred lone atom, failed/retried jobs, retracts,
  supersedes, version changes.
- Boundedness proof: EXPLAIN QUERY PLAN over every query-path read —
  no full-table SCAN of any projection base table.
- Perf regression: 10k-pending fixture, query-path reads under budget.
"""

from __future__ import annotations

import random
import time

from trace_lite.api import app as api
from trace_lite.source import coverage_state as cov
from trace_lite.source import project as proj
from trace_lite.source import service as svc
from trace_lite.store import migrate as migrate_mod
from trace_lite.store import outbox as outbox_mod
from trace_lite.store.database import Database

TEXTS = [
    "# Alpha\n\nAlpha bravo charlie delta echo foxtrot.\n",
    "# Beta\n\nGolf hotel india juliet kilo lima.\n",
    "# Gamma\n\nMike november oscar papa quebec romeo.\n",
    "# Delta\n\nSierra tango uniform victor whiskey xray.\n",
]


def _db(tmp_path, name="s2.db"):
    db = Database(tmp_path / name)
    cov.ensure_coverage_schema(db.conn)
    return db


def _drain(db):
    return proj.drain_outbox(db, None, owner="s2-test")


def _assert_equivalent(conn):
    report = cov.verify_against_oracle(conn)
    assert report["mismatches"] == [], report["mismatches"][:3]
    oracle = cov.recompute_pending_coverage(conn)
    mat, _total = cov.pending_from_state(conn, 10**9)
    assert mat == oracle


# -- oracle equivalence ----------------------------------------------------

def test_oracle_equivalence_fuzz(tmp_path):
    """Seeded transition fuzz: materialized == recompute after every step."""
    db = _db(tmp_path)
    try:
        rng = random.Random(20260912)
        sources = [f"doc-{i}.md" for i in range(4)]
        versions = {s: 0 for s in sources}
        live_jobs: list[str] = []

        def _check():
            _assert_equivalent(db.conn)
            fresh = api.freshness(db.conn)
            assert fresh["version_mismatch"] is False
            assert fresh["coverage_total"] == len(
                cov.recompute_pending_coverage(db.conn, limit=10**9)
            )

        for step in range(60):
            op = rng.choice(["publish", "edit", "retract", "fail", "drain"])
            src = rng.choice(sources)
            if op == "publish" and versions[src] == 0:
                versions[src] = 1
                svc.publish(db, src, TEXTS[0] + f"\nstep {step}\n")
            elif op == "edit":
                versions[src] += 1
                svc.publish(db, src, rng.choice(TEXTS) + f"\nrev {versions[src]}\n")
            elif op == "retract":
                if svc.get_current(db.conn, src) is not None:
                    svc.retract(db, src)
                    versions[src] = 0
            elif op == "fail":
                claim = outbox_mod.claim(db.conn, owner="s2-fuzz")
                if claim is not None:
                    live_jobs.append(claim.job_id)
                    outbox_mod.fail(
                        db.conn, claim.job_id, owner="s2-fuzz",
                        error=f"fuzz injected failure step {step}",
                    )
            else:
                _drain(db)
            if step % 3 == 0:
                _drain(db)
            _check()
    finally:
        db.close()


def test_lone_atom_defers_as_hole(tmp_path):
    """First lone atom: reconciled=False, explicit hole with reason."""
    db = _db(tmp_path)
    try:
        res = svc.publish(db, "solo.md", TEXTS[0])
        _drain(db)
        _assert_equivalent(db.conn)
        page, total = cov.pending_from_state(db.conn, 64)
        assert total == 1
        assert page[0]["revision_id"] == res.revision_id
        assert "reconciled" in page[0]["missing"]
        holes, htotal, truncated = cov.hole_page(db.conn)
        assert htotal == 1 and truncated is False
        assert "reconciled" in holes[0]["missing"]
        assert holes[0]["reason"], "holes must carry reasons"
        fresh = api.freshness(db.conn)
        assert fresh["semantic_status"] == "pending"
        assert fresh["coverage_holes"][0]["revision_id"] == res.revision_id
    finally:
        db.close()


def test_failed_job_surfaces_hole_with_reason(tmp_path):
    """Claimed-then-failed job: pending hole naming the job error."""
    db = _db(tmp_path)
    try:
        res = svc.publish(db, "wobbly.md", TEXTS[1])
        claim = outbox_mod.claim(db.conn, owner="s2-hole")
        assert claim is not None
        outbox_mod.fail(db.conn, claim.job_id, owner="s2-hole",
                         error="boom: extraction exploded")
        _assert_equivalent(db.conn)
        page, _total = cov.pending_from_state(db.conn, 64)
        assert len(page) == 1
        assert page[0]["has_pending_job"] is True
        row = db.conn.execute(
            "SELECT reason FROM revision_coverage WHERE revision_id = ?",
            (res.revision_id,),
        ).fetchone()
        assert "boom" in str(row[0])
        # A drain retries the projection; the lone atom still defers
        # honestly on reconciled (corpus needs >= 2 atoms), oracle agrees.
        _drain(db)
        _assert_equivalent(db.conn)
        page2, total2 = cov.pending_from_state(db.conn, 64)
        assert total2 == 1 and "reconciled" in page2[0]["missing"]
    finally:
        db.close()


def test_retract_and_supersede_leave_no_holes(tmp_path):
    """Retracted/superseded revisions go out of scope, never pending."""
    db = _db(tmp_path)
    try:
        first = svc.publish(db, "churn.md", TEXTS[0])
        _drain(db)
        second = svc.publish(db, "churn.md", TEXTS[2])
        _drain(db)
        _assert_equivalent(db.conn)
        old = db.conn.execute(
            "SELECT in_scope, pending FROM revision_coverage WHERE revision_id = ?",
            (first.revision_id,),
        ).fetchone()
        assert tuple(old) == (0, 0)
        svc.retract(db, "churn.md")
        _drain(db)
        _assert_equivalent(db.conn)
        cur = db.conn.execute(
            "SELECT in_scope, pending FROM revision_coverage WHERE revision_id = ?",
            (second.revision_id,),
        ).fetchone()
        assert tuple(cur) == (0, 0)
        fresh = api.freshness(db.conn)
        assert fresh["coverage_total"] == 0
        assert fresh["semantic_status"] == "complete"
        assert fresh["pending_revisions"] == []
    finally:
        db.close()


def test_grouping_membership_repairs_rec_flag(tmp_path):
    """Direct membership delete flips rec to 0; re-group flips it back."""
    from trace_lite.source import project as _proj

    db = _db(tmp_path)
    try:
        svc.publish(db, "g1.md", TEXTS[0])
        svc.publish(db, "g2.md", TEXTS[1])
        _drain(db)
        _assert_equivalent(db.conn)
        assert cov.pending_from_state(db.conn, 64)[1] == 0
        atom = db.conn.execute(
            "SELECT atom_id FROM source_revision WHERE source_id = 'g1.md'"
        ).fetchone()[0]
        db.conn.execute("DELETE FROM holon_members WHERE atom_id = ?", (atom,))
        db.conn.commit()
        pend, total = cov.pending_from_state(db.conn, 64)
        assert total == 1 and "reconciled" in pend[0]["missing"]
        _assert_equivalent(db.conn)
        _proj.ensure_grouped(db.conn, [int(atom)])
        db.conn.commit()
        assert cov.pending_from_state(db.conn, 64)[1] == 0
        _assert_equivalent(db.conn)
    finally:
        db.close()


# -- boundedness proof -------------------------------------------------------

BASE_TABLES = {
    "source_revision",
    "source",
    "projection_job",
    "segmentation_view",
    "evidence_span",
    "extraction_run",
    "holon_members",
    "holon_state",
    "atom",
    "facets",
    "memberships",
}


def _assert_no_base_scan(conn, sql, params=()):
    plan = cov.explain_plan(conn, sql, params)
    for line in plan:
        upper = line.upper()
        assert "SCAN" not in upper or "USING" in upper, f"full scan: {line} :: {sql}"
        for table in BASE_TABLES:
            if f"SCAN {table}" in line:
                raise AssertionError(f"base-table scan: {line} :: {sql}")
    return plan


def test_query_path_reads_are_indexed(tmp_path):
    """EXPLAIN: freshness/pending/hole reads never scan a base table."""
    db = _db(tmp_path)
    try:
        svc.publish(db, "q1.md", TEXTS[0])
        svc.publish(db, "q2.md", TEXTS[1])
        _assert_no_base_scan(db.conn, cov.SQL_PENDING_PAGE, (64,))
        _assert_no_base_scan(db.conn, cov.SQL_PENDING_COUNT)
        _assert_no_base_scan(db.conn, cov.SQL_HOLE_PAGE, (64,))
        _assert_no_base_scan(db.conn, cov.SQL_HOLE_COUNT)
        _assert_no_base_scan(db.conn, cov.SQL_META_GET, ("projection_version",))
        _assert_no_base_scan(db.conn, cov.SQL_LIVE_JOBS)
        plan = cov.explain_plan(db.conn, cov.SQL_PENDING_PAGE, (64,))
        assert any("idx_cov_pending" in line for line in plan), plan
        live = cov.explain_plan(db.conn, cov.SQL_LIVE_JOBS)
        assert any("idx_cov_job_live" in line for line in live), live
    finally:
        db.close()


def test_served_relations_single_query(tmp_path):
    """Dossier relation scope check is one batched query, not N."""
    db = _db(tmp_path)
    try:
        svc.publish(db, "r1.md", TEXTS[0])
        svc.publish(db, "r2.md", TEXTS[1])
        _drain(db)
        atoms = [
            int(r[0])
            for r in db.conn.execute("SELECT id FROM atom ORDER BY id").fetchall()
        ]
        cands = [
            {"candidate_id": f"c{i}", "atom_id": a} for i, a in enumerate(atoms)
        ]
        calls: list[str] = []
        db.conn.set_trace_callback(calls.append)
        try:
            out = api._served_relations(db.conn, cands)
        finally:
            db.conn.set_trace_callback(None)
        assert isinstance(out, list)
        scope_selects = [
            s for s in calls if "source_revision" in s and "atom_id" in s
        ]
        assert len(scope_selects) <= 1, scope_selects
    finally:
        db.close()


# -- perf regression (10k pending) -------------------------------------------

#: Query-path budget at 10k pending revisions (seconds, wall clock).
#: The pre-S2 scan pays ~seconds here; the materialized path must stay
#: two orders of magnitude below the Praxist sub-minute eval tick.
QUERY_PATH_BUDGET_S = 1.0

FIXTURE_N = 10_000


def _build_10k_pending(db):
    now = "2026-09-12T00:00:00+00:00"
    sources = [
        (f"s{i:05d}", "default", "doc", f"s{i:05d}", f"r{i:05d}",
         "default", now, now)
        for i in range(FIXTURE_N)
    ]
    db.conn.executemany(
        "INSERT INTO source (source_id, namespace, kind, locator,"
        " current_revision_id, access_scope, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        sources,
    )
    revs = [
        (f"r{i:05d}", f"s{i:05d}", b"\x00" * 32, now, "current",
         f"pending payload {i}", 16 + len(str(i)), "default")
        for i in range(FIXTURE_N)
    ]
    db.conn.executemany(
        "INSERT INTO source_revision (revision_id, source_id, content_hash,"
        " observed_at, status, text, byte_len, namespace)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        revs,
    )
    jobs = [
        (f"j{i:05d}", f"key-{i:05d}", f"r{i:05d}", "projection.refresh",
         "pending", now, now)
        for i in range(FIXTURE_N)
    ]
    db.conn.executemany(
        "INSERT INTO projection_job (job_id, idempotency_key,"
        " input_revision_id, job_type, state, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        jobs,
    )
    db.conn.commit()


def _timeit(fn, repeats=3):
    best = min((lambda t0: (fn(), time.perf_counter() - t0)[1])(time.perf_counter())
               for _ in range(repeats))
    return best


def test_perf_10k_pending_under_budget(tmp_path):
    """10k-pending fixture: materialized reads under budget, oracle agrees."""
    db = _db(tmp_path, "perf10k.db")
    try:
        _build_10k_pending(db)
        oracle = cov.recompute_pending_coverage(db.conn, limit=10**9)
        assert len(oracle) == FIXTURE_N
        mat, total = cov.pending_from_state(db.conn, 10**9)
        assert total == FIXTURE_N
        assert [m["revision_id"] for m in mat] == [
            o["revision_id"] for o in oracle
        ]
        # Boundedness on the loaded fixture (no base-table scans).
        _assert_no_base_scan(db.conn, cov.SQL_PENDING_PAGE, (64,))
        _assert_no_base_scan(db.conn, cov.SQL_PENDING_COUNT)
        _assert_no_base_scan(db.conn, cov.SQL_LIVE_JOBS)
        before = _timeit(
            lambda: cov.recompute_pending_coverage(db.conn, limit=10**9)
        )
        after_page = _timeit(lambda: cov.pending_from_state(db.conn, 64))
        after_fresh = _timeit(lambda: api.freshness(db.conn))
        assert after_page < QUERY_PATH_BUDGET_S, after_page
        assert after_fresh < QUERY_PATH_BUDGET_S, after_fresh
        fresh = api.freshness(db.conn)
        assert fresh["coverage_total"] == FIXTURE_N
        assert fresh["coverage_truncated"] is True
        assert fresh["holes_truncated"] is True
        assert len(fresh["pending_revisions"]) == 64
        assert len(fresh["coverage_holes"]) == 64
        print(
            f"\n[S2-perf] 10k pending: scan={before:.3f}s"
            f" page={after_page:.4f}s freshness={after_fresh:.4f}s"
            f" speedup={before / max(after_fresh, 1e-9):.0f}x"
        )
    finally:
        db.close()


# -- version gate ------------------------------------------------------------

def test_version_mismatch_surfaces_pending(tmp_path):
    """Stale derivation version forces pending; a drain heals it."""
    db = _db(tmp_path)
    try:
        svc.publish(db, "v1.md", TEXTS[0])
        svc.publish(db, "v2.md", TEXTS[1])
        _drain(db)
        assert api.freshness(db.conn)["semantic_status"] == "complete"
        db.conn.execute(
            "UPDATE coverage_meta SET value = 'ancient' WHERE key = ?",
            (cov.META_VERSION_KEY,),
        )
        db.conn.commit()
        fresh = api.freshness(db.conn)
        assert fresh["version_mismatch"] is True
        assert fresh["semantic_status"] == "pending"
        assert fresh["index_trusted"] is False
        _drain(db)
        healed = api.freshness(db.conn)
        assert healed["version_mismatch"] is False
        _assert_equivalent(db.conn)
    finally:
        db.close()


# -- migration -----------------------------------------------------------------

def test_migrate_coverage_005(tmp_path):
    """Migration 005: additive, scratch-verified, rerun-safe, backed up."""
    path = tmp_path / "mig.db"
    db = Database(path)
    try:
        svc.publish(db, "m1.md", TEXTS[0])
        svc.publish(db, "m2.md", TEXTS[1])
        proj.drain_outbox(db, None, owner="s2-mig")
    finally:
        db.close()
    report = migrate_mod.migrate_coverage_005(path)
    assert report["migrated"] is True
    assert report["migration_version"] == 5
    assert report["observed_version"] == 5
    assert report["live_mismatches"] == 0
    assert report["backup"] is not None and report["backup"].endswith(".bak-005")
    db = Database(path)
    try:
        _assert_equivalent(db.conn)
    finally:
        db.close()
    rerun = migrate_mod.migrate_coverage_005(path)
    assert rerun["migrated"] is False
    assert rerun["mismatches"] == 0
    assert rerun["observed_version"] == 5
    # Earlier migrations still observe their own versions on this file.
    db = Database(path)
    try:
        assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 5
    finally:
        db.close()
