"""INT-1 write-path integration: served writes drive the full pipeline.

Acceptance demo (packet core evidence): ingest -> query -> edit -> query ->
delete -> query -> restart -> query over a small real dataset with a real
``TestClient`` + real DB files. After each step the test asserts persisted
effects (revision rows, span counts, relation counts, holon memberships,
dossier citations) and logs them to
``evidence/query-native/_integration/INT-1/lifecycle_demo.{log,json}``.

R06-F4 proof: a premise edit through ``POST /api/sync`` (file change)
auto-marks a dependent persisted synthesis stale with NO
``invalidate_dependents`` call anywhere in this file.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from trace_lite.api import create_app
from trace_lite.filing import HolonStore
from trace_lite.store import Database

REPO_ROOT = Path(__file__).resolve().parents[1]
INT1_DIR = REPO_ROOT / "evidence" / "query-native" / "_integration" / "INT-1"

PREMISE_V1 = (
    "# Premise\n\nBatching improves throughput by 30% under low contention.\n"
)
PREMISE_V2 = (
    "# Premise\n\nBatching degrades throughput under high contention.\n"
)
SUPPORT = (
    "# Support\n\nUnder high contention, the batching benefit disappears; "
    "throughput matches the unbatched path.\n"
)
CONTEXT = (
    "# Context\n\nWAL mode allows one writer with concurrent readers.\n"
)


def _counts(db: Database) -> dict:
    conn = db.conn
    get = lambda t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    jobs = [tuple(r) for r in conn.execute(
        "SELECT job_type, state FROM projection_job ORDER BY created_at").fetchall()]
    return {
        "atoms": get("atom"),
        "source": get("source"),
        "source_revision": get("source_revision"),
        "current_revisions": int(conn.execute(
            "SELECT COUNT(*) FROM source_revision WHERE status = 'current'").fetchone()[0]),
        "evidence_span": get("evidence_span"),
        "segmentation_view": get("segmentation_view"),
        "extraction_run": get("extraction_run"),
        "assertion": get("assertion"),
        "relation": get("relation"),
        "holon_members": get("holon_members"),
        "dependency": get("dependency"),
        "jobs": [{"type": t, "state": s} for t, s in jobs],
        "filing_generations": int(conn.execute(
            "SELECT COUNT(*) FROM projection_generation WHERE kind = 'filing'").fetchone()[0]),
    }


def _recorder():
    steps: list[dict] = []
    lines: list[str] = []

    def record(step: str, checks: dict) -> None:
        failed = sorted(k for k, v in checks.items() if not v)
        steps.append({"step": step, "checks": checks,
                      "ok": not failed, "failed": failed})
        lines.append(f"[{step}] " + ("OK " if not failed else f"FAIL {failed} ")
                     + json.dumps(checks, sort_keys=True))
        assert not failed, f"demo step {step!r} failed: {failed}"

    return steps, lines, record


def _drain(db_path, *, owner: str = "int-1-test", timeout_s: float = 120.0) -> dict:
    """Drive the shared outbox to rest like the background worker would.

    G1: HTTP writes only queue jobs; semantic assertions below wait for
    worker-equivalent convergence through the SAME shared drain the
    ``MaintenanceWorker`` runs (not a synchronous write-path drain).
    """
    from trace_lite.filing.workers import MaintenanceWorker

    worker = MaintenanceWorker(db_path)
    try:
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            last = worker.run_until_drained(max_batches=200)
            if last.get("drained_all"):
                return last
            time.sleep(0.2)
        raise AssertionError(f"outbox never drained: {last!r}")
    finally:
        worker.close()


def test_acceptance_lifecycle_demo(tmp_path):
    """ingest -> query -> edit -> query -> delete -> query -> restart -> query."""
    steps, lines, record = _recorder()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "premise.md").write_text(PREMISE_V1, encoding="utf-8")
    (vault / "support.md").write_text(SUPPORT, encoding="utf-8")
    (vault / "context.md").write_text(CONTEXT, encoding="utf-8")
    db_path = tmp_path / "int1.db"

    client = TestClient(create_app(db_path, vault))
    with client:
        # -- step 1: ingest (vault sync) ---------------------------------
        synced = client.post("/api/sync", json={}).json()
        # G1: the write ACK queues jobs (pending) instead of draining them.
        assert synced["coverage_pending"] == 3, synced
        _drain(db_path)
        db = Database(db_path)
        try:
            c = _counts(db)
            revs = {r["source_id"]: r["revision_id"] for r in db.conn.execute(
                "SELECT source_id, revision_id FROM source_revision"
                " WHERE status = 'current'").fetchall()}
            corpus = db.conn.execute(
                "SELECT holon_id, status, member_count FROM holon_state"
                " WHERE kind = 'thematic_cluster'").fetchall()
            status = client.get("/api/status").json()
        finally:
            db.close()
        record("ingest", {
            "synced==3": synced["synced"] == 3,
            "atoms==3": synced["atoms"] == 3,
            "revisions==3": c["source_revision"] == 3 and len(revs) == 3,
            "spans>0": c["evidence_span"] > 0,
            "views==3": c["segmentation_view"] == 3,
            "runs==3": c["extraction_run"] == 3,
            "assertions>0": c["assertion"] > 0,
            "relations>0": c["relation"] > 0,
            "corpus_active": len(corpus) == 1 and corpus[0]["status"] == "active",
            "corpus_members==3": len(corpus) == 1 and int(corpus[0]["member_count"]) == 3,
            "jobs_drained": all(j["state"] == "done" for j in c["jobs"]) and len(c["jobs"]) == 3,
            "coverage_complete": status["coverage_pending"] == 0,
            "index_trusted": status["index_trusted"] is True,
        })

        # -- step 2: query + dossier cite the retained revisions ---------
        answer = client.post(
            "/api/query", json={"query": "batching throughput"}).json()
        dossier = client.post(
            "/api/dossier", json={"query": "batching throughput", "limit": 5}).json()
        cited = {s.get("doc_id", "") for s in dossier.get("selections", [])}
        record("query", {
            "answerable": answer["sufficiency_state"] == "answerable",
            "premise_anchor": any(a["doc_id"] == "premise.md" for a in answer["anchors"]),
            "dossier_selections": len(dossier.get("selections", [])) >= 1,
            "dossier_cites_premise": "premise.md" in cited,
            "dossier_cites_served_docs": bool(cited) and cited <= {"premise.md", "support.md", "context.md"},
        })

        # -- step 3: dependent synthesis over the premise atom ------------
        db = Database(db_path)
        try:
            premise_atom = db.conn.execute(
                "SELECT id FROM atom WHERE doc_id = 'premise.md'").fetchone()[0]
            store = HolonStore(db.conn)
            synthesis = store.create_holon(
                [int(premise_atom)], kind="derived_synthesis",
                derivation="int-1 demo: persisted synthesis over premise")
            syn_status = store.get_holon(synthesis).status
        finally:
            db.close()
        record("synthesis_setup", {
            "synthesis_active": syn_status == "active",
        })

        # -- step 4: edit the premise through /api/sync -------------------
        (vault / "premise.md").write_text(PREMISE_V2, encoding="utf-8")
        resync = client.post("/api/sync", json={}).json()
        # G1: visibility is synchronous (new text served at once) while
        # semantic coverage converges on the worker.
        assert resync["coverage_pending"] == 1, resync
        _drain(db_path)
        edited = client.post(
            "/api/query", json={"query": "batching throughput"}).json()
        premise_snippets = " ".join(
            a.get("snippet", "") for a in edited["anchors"]
            if a.get("doc_id") == "premise.md")
        db = Database(db_path)
        try:
            c2 = _counts(db)
            hist = [tuple(r) for r in db.conn.execute(
                "SELECT status FROM source_revision WHERE source_id = 'premise.md'"
                " ORDER BY observed_at").fetchall()]
            syn_after = db.conn.execute(
                "SELECT status FROM holon_state WHERE holon_id = ?",
                (synthesis,)).fetchone()[0]
            corpus_after = db.conn.execute(
                "SELECT status, member_count FROM holon_state"
                " WHERE kind = 'thematic_cluster'").fetchall()
            new_atom = db.conn.execute(
                "SELECT id FROM atom WHERE doc_id = 'premise.md'").fetchone()[0]
            corpus_holds_new = db.conn.execute(
                "SELECT 1 FROM holon_members m JOIN holon_state s"
                " ON s.holon_id = m.holon_id WHERE m.atom_id = ?"
                " AND s.kind = 'thematic_cluster' AND s.status != 'deleted'",
                (int(new_atom),)).fetchone() is not None
            status2 = client.get("/api/status").json()
        finally:
            db.close()
        record("edit", {
            "history_retained": (len(hist) == 2
                                 and hist[0][0] == "superseded" and hist[1][0] == "current"),
            "revisions_grew": c2["source_revision"] == 4,
            "synthesis_stale_no_manual_call": syn_after == "stale",
            "corpus_still_active": len(corpus_after) == 1 and corpus_after[0][0] == "active",
            "corpus_holds_new_atom": corpus_holds_new,
            "query_serves_new_text": "degrades" in premise_snippets,
            "old_text_gone": "improves throughput by 30%" not in premise_snippets,
            "coverage_complete": status2["coverage_pending"] == 0,
            "index_trusted": status2["index_trusted"] is True,
        })

        # -- step 5: replay changes nothing -------------------------------
        db = Database(db_path)
        try:
            views_before = db.conn.execute(
                "SELECT COUNT(*) FROM segmentation_view").fetchone()[0]
            revs_before = db.conn.execute(
                "SELECT COUNT(*) FROM source_revision").fetchone()[0]
        finally:
            db.close()
        replay = client.post("/api/sync", json={}).json()
        db = Database(db_path)
        try:
            views_after = db.conn.execute(
                "SELECT COUNT(*) FROM segmentation_view").fetchone()[0]
            revs_after = db.conn.execute(
                "SELECT COUNT(*) FROM source_revision").fetchone()[0]
        finally:
            db.close()
        record("replay", {
            "no_new_revisions": revs_after == revs_before,
            "no_duplicated_projections": views_after == views_before,
            "still_complete": replay["coverage_pending"] == 0,
        })

        # -- step 6: delete via the served watcher remove path ------------
        (vault / "support.md").unlink()
        gone = False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            time.sleep(0.3)
            probe = client.post(
                "/api/query", json={"query": "unbatched path benefit disappears"}).json()
            if all(a["doc_id"] != "support.md" for a in probe["anchors"]):
                gone = True
                break
        _drain(db_path)  # G1: retract job converges on the worker
        db = Database(db_path)
        try:
            c3 = _counts(db)
            support_atoms = int(db.conn.execute(
                "SELECT COUNT(*) FROM atom WHERE doc_id = 'support.md'").fetchone()[0])
            support_revs = int(db.conn.execute(
                "SELECT COUNT(*) FROM source_revision WHERE source_id = 'support.md'").fetchone()[0])
            status3 = client.get("/api/status").json()
        finally:
            db.close()
        record("delete", {
            "watcher_removed_note": gone,
            "lexical_rows_gone": support_atoms == 0,
            "revision_history_retained": support_revs >= 1,
            "coverage_complete": status3["coverage_pending"] == 0,
            "atoms==2": c3["atoms"] == 2,
        })

    # -- step 7: restart on the same DB file ------------------------------
    client2 = TestClient(create_app(db_path, vault))
    with client2:
        restarted = client2.post(
            "/api/query", json={"query": "batching contention"}).json()
        dossier2 = client2.post(
            "/api/dossier", json={"query": "batching contention", "limit": 5}).json()
        db = Database(db_path)
        try:
            c4 = _counts(db)
            syn_restart = db.conn.execute(
                "SELECT status FROM holon_state WHERE holon_id = ?",
                (synthesis,)).fetchone()[0]
            status4 = client2.get("/api/status").json()
        finally:
            db.close()
        record("restart", {
            "query_served": restarted["total_results"] >= 1,
            "premise_survived": any(a["doc_id"] == "premise.md" for a in restarted["anchors"]),
            "revisions_survived": c4["source_revision"] >= 4,
            "spans_survived": c4["evidence_span"] > 0,
            "relations_survived": c4["relation"] > 0,
            "synthesis_still_stale": syn_restart == "stale",
            "dossier_cites": len(dossier2.get("selections", [])) >= 1,
            "coverage_complete": status4["coverage_pending"] == 0,
        })

    INT1_DIR.mkdir(parents=True, exist_ok=True)
    (INT1_DIR / "lifecycle_demo.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (INT1_DIR / "lifecycle_demo.json").write_text(
        json.dumps({"steps": steps,
                    "ok": all(s["ok"] for s in steps)}, indent=2),
        encoding="utf-8")


def test_ingest_reports_revision_and_coverage(tmp_path):
    """POST /api/ingest is additive (revision_id + coverage) and replays clean."""
    db_path = tmp_path / "ing.db"
    client = TestClient(create_app(db_path))
    with client:
        first = client.post("/api/ingest", json={
            "text": "# Solo\n\nBatching improves throughput under low contention.\n",
            "document_name": "solo.md",
        }).json()
        assert first["ok"] is True and first["atoms"] == 1
        assert first["revision_id"], "ingest must report its revision"
        assert set(first["coverage"]) == {"segmented", "extracted", "reconciled", "indexed"}
        # G1: the ACK queues the job (honest pending) instead of draining it.
        assert first["pending"] is True
        assert first["coverage"]["indexed"] is False
        status = client.get("/api/status").json()
        assert status["semantic_pending"] == 1
        assert status["pending_revisions"] == [first["revision_id"]]

        second = client.post("/api/ingest", json={
            "text": "# Second\n\nWAL mode allows one writer with concurrent readers.\n",
            "document_name": "second.md",
        }).json()
        assert second["pending"] is True
        # Worker convergence heals coverage (the >= 2-atom corpus forms).
        _drain(db_path)
        healed = client.get("/api/status").json()
        assert healed["semantic_pending"] == 0
        assert healed["index_trusted"] is True

        # Replay: same key + payload returns the prior revision, no duplicates.
        db = Database(tmp_path / "ing.db")
        try:
            views_before = db.conn.execute(
                "SELECT COUNT(*) FROM segmentation_view").fetchone()[0]
        finally:
            db.close()
        again = client.post("/api/ingest", json={
            "text": "# Solo\n\nBatching improves throughput under low contention.\n",
            "document_name": "solo.md",
        }).json()
        assert again["revision_id"] == first["revision_id"]
        db = Database(tmp_path / "ing.db")
        try:
            assert db.conn.execute(
                "SELECT COUNT(*) FROM segmentation_view").fetchone()[0] == views_before
            assert db.conn.execute(
                "SELECT COUNT(*) FROM source_revision").fetchone()[0] == 2
        finally:
            db.close()


def test_ingest_reuses_dimension_roots(tmp_path):
    """Repeated ingests share one dimension root; re-ingest adds no facets."""
    client = TestClient(create_app(tmp_path / "roots.db"))
    with client:
        for i in range(3):
            r = client.post("/api/ingest", json={
                "text": f"# Doc {i}\n\nBatching improves throughput {i}.\n",
                "document_name": f"doc{i}.md",
            })
            assert r.status_code == 200, r.text
        db = Database(tmp_path / "roots.db")
        try:
            dupes = db.conn.execute(
                "SELECT dimension, name, COUNT(*) FROM facets"
                " GROUP BY dimension, name HAVING COUNT(*) > 1").fetchall()
            assert [tuple(r) for r in dupes] == [], f"duplicate facets: {dupes}"
            facets_before = db.conn.execute(
                "SELECT COUNT(*) FROM facets").fetchone()[0]
        finally:
            db.close()
        # Re-ingest identical payload: replay path adds no facets.
        r = client.post("/api/ingest", json={
            "text": "# Doc 0\n\nBatching improves throughput 0.\n",
            "document_name": "doc0.md",
        })
        assert r.status_code == 200, r.text
        db = Database(tmp_path / "roots.db")
        try:
            assert db.conn.execute(
                "SELECT COUNT(*) FROM facets").fetchone()[0] == facets_before
        finally:
            db.close()


def test_decoupled_ingest_fast_ack_under_backlog(tmp_path):
    """G1: POST /api/ingest ACKs in P95 <= 35ms with 500 queued jobs (no sync drain)."""
    import math

    from trace_lite.source import service as svc

    db_path = tmp_path / "backlog.db"
    db = Database(db_path)
    try:
        for i in range(500):
            svc.publish(
                db, f"bulk/doc{i:04d}.md",
                f"# Bulk {i}\n\nThroughput memo payload {i} with quorum filler text.\n",
            )
        depth = db.conn.execute(
            "SELECT COUNT(*) FROM projection_job"
            " WHERE state IN ('pending', 'claimed')").fetchone()[0]
        assert depth == 500
    finally:
        db.close()
    client = TestClient(create_app(db_path))
    with client:
        # Post-startup depth: the bounded startup-resume pass may have
        # drained up to 64 jobs; the write path itself must drain zero.
        depth0 = client.get("/api/status").json()["queue_stats"]["depth"]
        assert depth0 >= 500 - 64
        lat: list[float] = []
        for i in range(50):
            t0 = time.perf_counter()
            r = client.post("/api/ingest", json={
                "text": f"# Probe {i}\n\nFresh probe note {i} quorum alpha.\n",
                "document_name": f"probe-{i}.md",
            })
            lat.append((time.perf_counter() - t0) * 1000.0)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body.get("atom_id"), "ACK must allocate an atom id"
            assert body.get("revision_id"), "ACK must expose durable revision identity"
            assert body.get("pending") is True, \
                "fresh revision must report honest pending state"
        ordered = sorted(lat)
        p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
        assert p95 <= 35.0, f"P95 {p95:.2f}ms exceeds 35ms budget"
        depth = client.get("/api/status").json()["queue_stats"]["depth"]
        assert depth == depth0 + 50, \
            "each ingest must enqueue exactly one job and drain zero"


def test_served_vectors_nonblocking_during_maintenance(tmp_path, monkeypatch):
    """G1: 200 queries stay vector-pinned while maintenance is in flight.

    Zero maintenance-induced detachments: with a valid baseline snapshot,
    every query during an active catch-up pass keeps its vector pin instead
    of falling back to lexical-only search.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from trace_lite.api.app import ServedRuntime

    monkeypatch.setenv("TRACE_LITE_SERVED_PERSISTENT", "1")
    monkeypatch.setenv("TRACE_LITE_SERVED_INDEX_DIR", str(tmp_path / "served"))
    monkeypatch.setenv("TRACE_LITE_WORKER_POLL_S", "0.05")
    db_path = tmp_path / "nb.db"
    client = TestClient(create_app(db_path))
    with client:
        for name, text in (("a.md", PREMISE_V1), ("b.md", SUPPORT),
                           ("c.md", CONTEXT)):
            r = client.post("/api/ingest",
                            json={"text": text, "document_name": name})
            assert r.status_code == 200, r.text
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            sp = client.get("/api/status").json().get("served_persistent")
            if sp and sp.get("servable") and sp.get("served_atoms") == 3:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("baseline snapshot never became servable")
        # Gate the next catch-up pass open so queries deterministically
        # overlap active maintenance.
        gate = threading.Event()
        entered = threading.Event()
        real = ServedRuntime._run_catchup

        def _gated(self):
            entered.set()
            assert gate.wait(timeout=60.0), "maintenance gate never released"
            return real(self)

        monkeypatch.setattr(ServedRuntime, "_run_catchup", _gated)
        try:
            r = client.post("/api/ingest", json={
                "text": "# Delta\n\nDelta arrival quorum harbor inlet.\n",
                "document_name": "d.md",
            })
            assert r.status_code == 200, r.text
            assert entered.wait(timeout=60.0), "catch-up never started"
            # Maintenance is provably in flight: 200 concurrent queries must
            # all keep their vector pin (no maintenance-induced detach).
            def _one(_: int) -> dict:
                resp = client.post(
                    "/api/query",
                    json={"query": "batching throughput contention", "limit": 5})
                assert resp.status_code == 200, resp.text
                return resp.json()

            with ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(_one, range(200)))
            assert len(results) == 200
            unpinned = [q for q in results
                        if not q.get("vector_generation_id")
                        or q["vector_generation_id"] != q["generation_id"]]
            assert unpinned == [], \
                f"{len(unpinned)}/200 queries lost the vector pin during maintenance"
        finally:
            gate.set()
        # Convergence afterwards: the gated pass completes and the worker
        # drains projection jobs autonomously.
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            st = client.get("/api/status").json()
            sp = st.get("served_persistent", {})
            if sp.get("served_atoms") == 4 and st["queue_stats"]["depth"] == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("post-maintenance convergence timed out")


def test_remove_note_clears_lexical_keeps_history(tmp_path):
    """remove_note retracts the served revision and invalidates dependents."""
    from trace_lite.api.app import remove_note
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "rm.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        engine.bind_database(db)
        from trace_lite.api.app import ingest_note
        ingest_note(db, taxonomy, engine, "a.md",
                    "# A\n\nBatching improves throughput under low contention.\n")
        ingest_note(db, taxonomy, engine, "b.md",
                    "# B\n\nWAL mode allows one writer with concurrent readers.\n")
        store = HolonStore(db.conn)
        aid = db.conn.execute(
            "SELECT id FROM atom WHERE doc_id = 'a.md'").fetchone()[0]
        syn = store.create_holon([int(aid)], kind="derived_synthesis",
                                 derivation="rm test synthesis")
        assert remove_note(db, engine, "a.md") == 1
        assert db.conn.execute(
            "SELECT COUNT(*) FROM atom WHERE doc_id = 'a.md'").fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT COUNT(*) FROM source_revision WHERE source_id = 'a.md'").fetchone()[0] == 1
        assert db.conn.execute(
            "SELECT status FROM holon_state WHERE holon_id = ?",
            (syn,)).fetchone()[0] == "stale"
        assert remove_note(db, engine, "a.md") == 0
        # G1: writes queue jobs; convergence is worker-driven.
        from trace_lite.api.app import freshness
        from trace_lite.source import project as projection
        assert freshness(db.conn)["semantic_pending"] >= 1
        projection.drain_outbox(db, None, owner="int-1-test")
        retract = db.conn.execute(
            "SELECT state FROM projection_job"
            " WHERE job_type = 'projection.retract'").fetchone()
        assert retract is not None and retract[0] == "done"
        # b is now a lone live atom: honestly pending until a corpus
        # partner arrives (same as a fresh single-note database).
        assert freshness(db.conn)["semantic_pending"] == 1
        ingest_note(db, taxonomy, engine, "c.md",
                    "# C\n\nWAL mode allows one writer with concurrent readers quorum.\n")
        _drain(tmp_path / "rm.db")
        fresh = freshness(db.conn)
        assert fresh["semantic_pending"] == 0, fresh
    finally:
        db.close()
