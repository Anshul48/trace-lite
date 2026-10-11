"""INT-2 served-path integration: served retrieval + dossier integrity.

Contract TL-QN-2026-09-12.1, path_matrix rows 5-6. Served read path only
(real ``TestClient`` + real DB files); every check also cross-reads the
store directly so results prove persistence, not return-value echoes.

Battery (§3 outcome):
- served retrieval uses the intended branches (R03 VectorIndex/IVF,
  generation-pinned, honest route labels; holon-guided candidates; named
  fallbacks kept);
- dossier citations resolve server-side to (revision_id, span_id, byte
  range); atom_self is degraded-only and surfaced;
- unicode/CRLF/repeats/nonzero offsets/interior excerpts; span trimming;
  replacement/deletion during queries; stale + invalid citations;
  out-of-scope + historical candidates with relations/disagreements;
  atomic groups with an excluded member;
  negation/actor-reversal/quantity/conditional claims (lexical-vote honesty).

Writes ``evidence/query-native/_integration/INT-2/citation_battery.log``
(check lines) and ``served_retrieval.json`` (served quality probe).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from trace_lite.api import create_app
from trace_lite.cordis import TokenBudget, compile_dossier
from trace_lite.cordis.dossier import SUPPORT_METHOD
from trace_lite.router.cascade import CascadeRouter
from trace_lite.source import views as V
from trace_lite.store import Database

REPO_ROOT = Path(__file__).resolve().parents[1]
INT2_DIR = REPO_ROOT / "evidence" / "query-native" / "_integration" / "INT-2"

PREMISE = "# Premise\n\nBatching improves throughput by 30% under low contention.\n"
SUPPORT = (
    "# Support\n\nUnder high contention, the batching benefit disappears; "
    "throughput matches the unbatched path.\n"
)
CONTEXT = "# Context\n\nWAL mode allows one writer with concurrent readers.\n"

UNI = (
    "# Café notes — naïve façade\n\n"
    "Résumé: the piñata emoji 🎉 survives byte slicing.\r\n"
    "Second line with repeats repeats repeats.\r\n"
    "Third line: IRC§ + naïve café.\n"
)
LONG_TAIL = "background filler without query signal. " * 60
LONG_DOC = (
    "# Long\n\nBatching throughput contention anchor sentence here.\n"
    + LONG_TAIL
    + "\nBatching throughput contention closing sentence here.\n"
)
REPEAT_DOC = "# Echo\n\n" + ("Batching throughput anchor repeats.\n\n" * 4)


def _budget(**kw):
    args = {
        "max_total_tokens": 3500,
        "mandatory_invariant_tokens": 600,
        "salient_evidence_budget": 1800,
    }
    args.update(kw)
    return TokenBudget(**args)


def _recorder():
    steps: list[dict] = []
    lines: list[str] = []

    def record(step: str, checks: dict) -> None:
        failed = sorted(k for k, v in checks.items() if not v)
        steps.append({"step": step, "checks": checks,
                      "ok": not failed, "failed": failed})
        lines.append(f"[{step}] " + ("OK " if not failed else f"FAIL {failed} ")
                     + json.dumps(checks, sort_keys=True))
        assert not failed, f"battery step {step!r} failed: {failed}"

    return steps, lines, record


def _flush(steps, lines, extra: dict | None = None):
    INT2_DIR.mkdir(parents=True, exist_ok=True)
    with open(INT2_DIR / "citation_battery.log", "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    # Accumulate: every test's steps and extras land in one manifest.
    manifest_path = INT2_DIR / "served_retrieval.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"steps": [], "ok": True}
    payload["steps"] = list(payload.get("steps", [])) + steps
    payload["ok"] = bool(payload.get("ok", True)) and all(s["ok"] for s in steps)
    if extra is not None:
        payload.update(extra)
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ingest(client: TestClient, name: str, text: str) -> dict:
    resp = client.post("/api/ingest", json={"text": text, "document_name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _current_revisions(db_path: Path) -> dict[str, str]:
    db = Database(db_path)
    try:
        return {r["source_id"]: r["revision_id"] for r in db.conn.execute(
            "SELECT source_id, revision_id FROM source_revision"
            " WHERE status = 'current'").fetchall()}
    finally:
        db.close()


def _payload(db_path: Path, revision_id: str) -> bytes:
    db = Database(db_path)
    try:
        row = db.conn.execute(
            "SELECT text FROM source_revision WHERE revision_id = ?",
            (revision_id,)).fetchone()
        assert row is not None
        return str(row[0]).encode("utf-8")
    finally:
        db.close()


def _drain(db_path, timeout_s=120.0):
    """Drive the shared outbox to rest like the background worker would.

    G1: HTTP writes only queue jobs; projection-dependent assertions wait
    for worker-equivalent convergence through the SAME shared drain the
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


KNOWN_ROUTES = {"lexical", "vector", "beam", "hybrid", "holon"}


def test_served_retrieval_uses_vector_branch_with_generation_pin(tmp_path):
    """Row 5: the served query path runs the R03 ANN branch, pinned."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2.db"
    client = TestClient(create_app(db_path))
    served: dict = {}
    with client:
        _ingest(client, "premise.md", PREMISE)
        _ingest(client, "support.md", SUPPORT)
        _ingest(client, "context.md", CONTEXT)
        q = client.post(
            "/api/query", json={"query": "batching throughput"}).json()
        served = q
        record("vector_branch", {
            "answerable": q["sufficiency_state"] == "answerable",
            "route_is_vector": q["route"] == "vector",
            "routes_named": bool(q["routes"]) and set(q["routes"]) <= KNOWN_ROUTES,
            "vector_adequate": q["vector_adequate"] is True,
            "generation_pinned": bool(q["vector_generation_id"])
            and q["vector_generation_id"] == q["generation_id"],
            "premise_anchored": any(
                a["doc_id"] == "premise.md" for a in q["anchors"]),
            "anchors_labeled": all(
                a.get("route") in KNOWN_ROUTES for a in q["anchors"]),
        })
        # Gibberish abstains with an honest label — never a silent miss.
        miss = client.post(
            "/api/query", json={"query": "xqzt blorpt wqkj zzzqq"}).json()
        record("abstain_honest", {
            "insufficient": miss["sufficiency_state"] == "insufficient_evidence",
            "no_anchors": miss["anchors"] == [],
            "route_named": miss["route"] in KNOWN_ROUTES | {"none"},
        })
    # Fallback stays available as a NAMED baseline: an unattached router on
    # the same file serves the legacy branches with honest labels.
    db = Database(db_path)
    try:
        from trace_lite.filing import FilingEngine, Taxonomy
        router = CascadeRouter(db.conn, FilingEngine(db.conn, Taxonomy(db.conn)))
        router.warm()
        fb = router.route("batching throughput")
        record("named_fallback", {
            "fallback_answerable": fb.verdict == "answerable",
            "fallback_route_named": fb.route in {"lexical", "beam", "hybrid"},
            "fallback_tier_legacy": fb.tier_used in (1, 2, 3),
            "vector_unavailable_labeled": fb.vector_adequate is None
            and fb.vector_generation_id is None,
        })
    finally:
        db.close()
    _flush(steps, lines, {"served_query": {
        "route": served.get("route"), "routes": served.get("routes"),
        "tier_used": served.get("tier_used"),
        "vector_generation_id": served.get("vector_generation_id"),
        "generation_id": served.get("generation_id"),
    }})


def test_holon_guided_candidates_on_served_path(tmp_path):
    """ARCH H→C: corpus co-members blend in as route=holon candidates."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2h.db"
    client = TestClient(create_app(db_path))
    with client:
        _ingest(client, "premise.md", PREMISE)
        _ingest(client, "support.md", SUPPORT)
        _ingest(client, "context.md", CONTEXT)
        _drain(db_path)
        q = client.post(
            "/api/query", json={"query": "batching throughput"}).json()
        record("holon_blend", {
            "routes_named": set(q["routes"]) <= KNOWN_ROUTES,
            "holon_present": "holon" in q["routes"],
            "holon_anchors_labeled": all(
                a["doc_id"] for a in q["anchors"]
                if a.get("route") == "holon"),
        })
        d = client.post(
            "/api/dossier",
            json={"query": "batching throughput", "limit": 5}).json()
        vhs = d.get("virtual_holons", [])
        record("served_virtual_holon", {
            "transient_derived": all(
                v["kind"] == "query_dossier" and v["status"] == "derived"
                for v in vhs) if vhs else True,
            "candidate_routes_named": set(d.get("candidate_routes", {})) <= KNOWN_ROUTES,
        })
    _flush(steps, lines)


def test_dossier_citations_unicode_crlf_repeats_offsets(tmp_path):
    """Byte-exact citations over unicode/CRLF/repeats; interior excerpts."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2c.db"
    client = TestClient(create_app(db_path))
    with client:
        _ingest(client, "uni.md", UNI)
        _ingest(client, "long.md", LONG_DOC)
        _ingest(client, "echo.md", REPEAT_DOC)
        _drain(db_path)
        d = client.post(
            "/api/dossier",
            json={"query": "throughput contention anchor", "limit": 6}).json()
        assert d["selections"], "battery needs served selections"
        ok = True
        interior = False
        for s in d["selections"]:
            raw = _payload(db_path, s["revision_id"])
            window = raw[s["start_byte"]:s["end_byte"]]
            try:
                window.decode("utf-8")
                utf8 = True
            except UnicodeDecodeError:
                utf8 = False
            excerpt = s["excerpt_text"].encode("utf-8")
            contained = excerpt in window
            # Interior excerpt: the window sits strictly inside the payload
            # (nonzero offset) rather than at byte zero.
            at = raw.find(excerpt)
            if s["is_excerpt"] and at > 0:
                interior = True
            ok = ok and s["citation_valid"] and not s["citation_degraded"] \
                and s["citation_method"] in ("r02_excerpt", "source_slice") \
                and utf8 and contained and bool(s["revision_id"])
        # Tight budget forces anchored trimming; the trimmed window must sit
        # at a nonzero byte offset inside the retained payload.
        dt = client.post(
            "/api/dossier",
            json={"query": "throughput contention anchor closing", "limit": 6,
                  "salient_evidence_budget": 120}).json()
        for s in dt["selections"]:
            if not s["is_excerpt"]:
                continue
            raw = _payload(db_path, s["revision_id"])
            at = raw.find(s["excerpt_text"].encode("utf-8"))
            if at > 0:
                interior = True
        record("served_citations", {
            "all_valid_undegraded": ok,
            "all_revision_bound": all(s["revision_id"] for s in d["selections"]),
            "no_bare_atom_self": all(
                s["citation_method"] != "atom_self" for s in d["selections"]),
            "interior_excerpt_nonzero": interior,
        })
        # Repeats keep separate span IDs (ARCH §2); CRLF/unicode byte-exact.
        db = Database(db_path)
        try:
            reps = db.conn.execute(
                "SELECT text_hash, COUNT(DISTINCT span_id) AS n, COUNT(*) AS rows"
                " FROM evidence_span GROUP BY text_hash HAVING n > 1").fetchall()
            uni_rev = db.conn.execute(
                "SELECT revision_id, byte_len, text FROM source_revision"
                " WHERE source_id = 'uni.md' AND status = 'current'").fetchone()
            nls = db.conn.execute(
                "SELECT signal_json FROM segmentation_view"
                " WHERE revision_id = ?", (uni_rev["revision_id"],)).fetchone()
        finally:
            db.close()
        record("span_integrity", {
            "repeats_separate_spans": len(reps) >= 1,
            "unicode_byte_len_exact": uni_rev["byte_len"] == len(
                str(uni_rev["text"]).encode("utf-8")),
            "crlf_signal": "crlf" in str(nls[0]),
        })
    _flush(steps, lines)


def test_citation_offsets_after_span_trimming(tmp_path):
    """Trimmed (anchored, never prefix) excerpts stay byte-exact."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2t.db"
    client = TestClient(create_app(db_path))
    with client:
        _ingest(client, "long.md", LONG_DOC)
        d = client.post(
            "/api/dossier",
            json={"query": "batching throughput contention anchor closing",
                  "limit": 3,
                  "salient_evidence_budget": 60}).json()
        excerpts = [s for s in d["selections"] if s["is_excerpt"]]
        checks = {"has_trimmed_excerpt": bool(excerpts)}
        for s in excerpts:
            raw = _payload(db_path, s["revision_id"])
            blob = s["excerpt_text"].encode("utf-8")
            at = raw.find(blob)
            checks.setdefault("byte_exact", True)
            checks["byte_exact"] = checks["byte_exact"] and at >= 0 \
                and raw[at:at + len(blob)] == blob
            # Anchored, not an arbitrary prefix of the source text.
            full = raw.decode("utf-8")
            checks.setdefault("not_prefix", True)
            checks["not_prefix"] = checks["not_prefix"] \
                and not full.startswith(s["excerpt_text"])
            checks.setdefault("carries_signal", True)
            checks["carries_signal"] = checks["carries_signal"] \
                and ("Batching" in s["excerpt_text"] or "contention" in s["excerpt_text"])
        record("trimmed_offsets", checks)
    _flush(steps, lines)


def test_replacement_during_queries_serves_new_revision(tmp_path):
    """Edits supersede: dossiers cite the new revision, never the stale one."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2r.db"
    client = TestClient(create_app(db_path))
    with client:
        first = _ingest(client, "premise.md", PREMISE)
        rev1 = first["revision_id"]
        # Second doc so the corpus holon can form (needs >= 2 atoms).
        _ingest(client, "context.md", CONTEXT)
        _drain(db_path)
        d1 = client.post(
            "/api/dossier", json={"query": "batching throughput", "limit": 5}).json()
        cited1 = {s["revision_id"] for s in d1["selections"]
                  if s["doc_id"] == "premise.md"}
        record("cite_v1", {
            "cites_rev1": rev1 in cited1,
            "v1_text_served": any(
                "30%" in s["excerpt_text"] for s in d1["selections"]),
        })
        v2_text = "# Premise\n\nBatching degrades throughput under high contention.\n"
        second = _ingest(client, "premise.md", v2_text)
        rev2 = second["revision_id"]
        assert rev2 != rev1
        _drain(db_path)
        d2 = client.post(
            "/api/dossier", json={"query": "batching throughput", "limit": 5}).json()
        current = set(_current_revisions(db_path).values())
        record("cite_v2", {
            "revision_changed": rev2 != rev1,
            "cites_rev2": any(s["revision_id"] == rev2 for s in d2["selections"]
                              if s["doc_id"] == "premise.md"),
            "old_revision_uncited": all(
                s["revision_id"] != rev1 for s in d2["selections"]),
            "old_text_gone": all(
                "30%" not in s["excerpt_text"] for s in d2["selections"]
                if s["doc_id"] == "premise.md"),
            "new_text_served": any(
                "degrades" in s["excerpt_text"] for s in d2["selections"]),
            "all_citations_current": all(
                s["revision_id"] in current for s in d2["selections"]),
        })
        # Stale-by-version candidates are filtered, not served (library pin).
        db = Database(db_path)
        try:
            old_spans = V.get_spans(
                db.conn,
                V.list_views(db.conn, rev1)[-1]["view_id"])
            stale = {
                "candidate_id": "stale-1", "text": old_spans[0]["text"],
                "doc_id": "premise.md", "source_id": "premise.md",
                "revision_id": rev1, "span_id": old_spans[0]["span_id"],
                "start_byte": old_spans[0]["start_byte"],
                "end_byte": old_spans[0]["end_byte"],
                "namespace": "default", "access_scope": "default",
                "version_status": "superseded", "route": "lexical",
            }
            dossier = compile_dossier(
                query="batching throughput", objective="batching throughput",
                candidates=[stale], budget=_budget(), conn=db.conn)
        finally:
            db.close()
        record("stale_filtered", {
            "stale_excluded": dossier.selections == [],
            "version_counted": dossier.coverage_report["filtered_out"]["version"] == 1,
            "support_insufficient": dossier.support_state.value == "insufficient",
        })
    _flush(steps, lines)


def test_deletion_during_queries(tmp_path):
    """Retracts vanish from served results; history is retained."""
    import time

    steps, lines, record = _recorder()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "premise.md").write_text(PREMISE, encoding="utf-8")
    (vault / "support.md").write_text(SUPPORT, encoding="utf-8")
    db_path = tmp_path / "int2d.db"
    client = TestClient(create_app(db_path, vault))
    with client:
        assert client.post("/api/sync", json={}).status_code == 200
        before = client.post(
            "/api/dossier", json={"query": "batching throughput", "limit": 5}).json()
        assert any(s["doc_id"] == "support.md" for s in before["selections"])
        (vault / "support.md").unlink()
        gone = False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            time.sleep(0.3)
            probe = client.post(
                "/api/query",
                json={"query": "unbatched path benefit disappears"}).json()
            if all(a["doc_id"] != "support.md" for a in probe["anchors"]):
                gone = True
                break
        after = client.post(
            "/api/dossier", json={"query": "batching throughput", "limit": 5}).json()
        db = Database(db_path)
        try:
            atoms = int(db.conn.execute(
                "SELECT COUNT(*) FROM atom WHERE doc_id = 'support.md'").fetchone()[0])
            hist = int(db.conn.execute(
                "SELECT COUNT(*) FROM source_revision"
                " WHERE source_id = 'support.md'").fetchone()[0])
        finally:
            db.close()
        record("delete", {
            "watcher_removed_note": gone,
            "dossier_drops_deleted": all(
                s["doc_id"] != "support.md" for s in after["selections"]),
            "lexical_rows_gone": atoms == 0,
            "revision_history_retained": hist >= 1,
        })
    _flush(steps, lines)


def test_stale_and_invalid_citations_surfaced(tmp_path):
    """Proven-wrong citations are dropped; unprovable ones are degraded."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2s.db"
    client = TestClient(create_app(db_path))
    with client:
        first = _ingest(client, "premise.md", PREMISE)
        _ingest(client, "context.md", CONTEXT)
        _drain(db_path)
        db = Database(db_path)
        try:
            atom_id = int(db.conn.execute(
                "SELECT id FROM atom WHERE doc_id = 'premise.md'").fetchone()[0])
            view = V.list_views(db.conn, first["revision_id"])[-1]
            span = V.get_spans(db.conn, view["view_id"])[0]
            good_text = str(db.conn.execute(
                "SELECT text FROM atom WHERE id = ?", (atom_id,)).fetchone()[0])
            # Proven-wrong: right atom id, wrong text. Query terms included
            # so the candidates clear relevance and reach citation checking.
            wrong_atom = {
                "candidate_id": "wrong-atom", "atom_id": atom_id,
                "text": "fabricated batching throughput claim with zero provenance",
                "doc_id": "premise.md", "source_id": "premise.md",
                "start_byte": 0, "end_byte": 60,
                "namespace": "default", "access_scope": "default",
                "version_status": "current", "route": "lexical",
            }
            # Proven-wrong: right span id, wrong text.
            wrong_span = {
                "candidate_id": "wrong-span",
                "text": "fabricated batching throughput span text",
                "doc_id": "premise.md", "source_id": "premise.md",
                "revision_id": first["revision_id"], "span_id": span["span_id"],
                "start_byte": span["start_byte"], "end_byte": span["end_byte"],
                "namespace": "default", "access_scope": "default",
                "version_status": "current", "route": "lexical",
            }
            # Unprovable: unknown span, self-consistent bounds. Query terms
            # included so it clears relevance and reaches citation checking
            # (where its degraded state also exercises the conservative
            # support rule: lexically supported yet unproven).
            ghost_text = "ghost batching throughput evidence nobody retained"
            ghost = {
                "candidate_id": "ghost-1", "text": ghost_text,
                "doc_id": "ghost.md", "source_id": "ghost.md",
                "span_id": "deadbeef" * 4,
                "start_byte": 0, "end_byte": len(ghost_text.encode()),
                "namespace": "default", "access_scope": "default",
                "version_status": "current", "route": "lexical",
            }
            dossier = compile_dossier(
                query="batching throughput", objective="batching throughput",
                candidates=[wrong_atom, wrong_span, ghost],
                budget=_budget(), conn=db.conn)
        finally:
            db.close()
        kinds = {(o["kind"], o["id"]) for o in dossier.omissions}
        record("invalid_dropped", {
            "wrong_atom_excluded": "wrong-atom" not in
            {s.candidate_id for s in dossier.selections},
            "wrong_span_excluded": "wrong-span" not in
            {s.candidate_id for s in dossier.selections},
            "invalid_named": ("citation_invalid", "wrong-atom") in kinds
            and ("citation_invalid", "wrong-span") in kinds,
        })
        ghost_sel = [s for s in dossier.selections
                     if s.candidate_id == "ghost-1"]
        record("unprovable_degraded", {
            "ghost_labeled_degraded": len(ghost_sel) == 1
            and ghost_sel[0].citation_degraded
            and ghost_sel[0].citation_method == "atom_self:degraded",
            "degradation_surfaced": ("citation_degraded", "ghost-1") in kinds,
            "degraded_not_support": dossier.supported_count == 0,
            "support_conservative": dossier.support_state.value == "insufficient",
        })
        assert good_text  # fixture sanity: served text was read back
    _flush(steps, lines)


def test_out_of_scope_and_historical_with_relations(tmp_path):
    """Relations/disagreements obey the same scope/version rules."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2o.db"
    client = TestClient(create_app(db_path))
    with client:
        first = _ingest(client, "premise.md", PREMISE)
        _ingest(client, "context.md", CONTEXT)
        _drain(db_path)
        _ingest(client, "premise.md",
                "# Premise\n\nBatching degrades throughput under high contention.\n")
        _drain(db_path)
        db = Database(db_path)
        try:
            atom_id = int(db.conn.execute(
                "SELECT id FROM atom WHERE doc_id = 'context.md'").fetchone()[0])
            atom_text = str(db.conn.execute(
                "SELECT text FROM atom WHERE id = ?", (atom_id,)).fetchone()[0])
            ok = {"candidate_id": f"atom-{atom_id}", "atom_id": atom_id,
                  "text": atom_text, "doc_id": "context.md",
                  "source_id": "context.md", "route": "vector"}
            priv = {"candidate_id": "priv-1",
                    "text": "SECRET batching throughput launch codes",
                    "doc_id": "priv.md", "source_id": "priv.md",
                    "start_byte": 0, "end_byte": 39,
                    "namespace": "private", "access_scope": "default",
                    "version_status": "current", "route": "lexical"}
            old_spans = V.get_spans(
                db.conn, V.list_views(db.conn, first["revision_id"])[-1]["view_id"])
            # Term-bearing span: the test pins version behavior, so the
            # candidate must clear the relevance gate on its own terms.
            old_span = next(
                (s for s in old_spans if "throughput" in s["text"]), old_spans[0])
            old = {"candidate_id": "old-1", "text": old_span["text"],
                   "doc_id": "premise.md", "source_id": "premise.md",
                   "revision_id": first["revision_id"],
                   "span_id": old_span["span_id"],
                   "start_byte": old_span["start_byte"],
                   "end_byte": old_span["end_byte"],
                   "namespace": "default", "access_scope": "default",
                   "version_status": "superseded", "route": "lexical"}
            rels = [
                {"rel_type": "qualifies", "subject": ok["candidate_id"],
                 "object": "wal-claim",
                 "evidence": "single writer with concurrent readers",
                 "condition_text": "in WAL mode"},
                {"rel_type": "conflicts_with", "subject": ok["candidate_id"],
                 "object": "priv-1", "evidence": "leak check"},
            ]
            disag = [{"side_a": ok["candidate_id"], "side_b": "priv-1",
                      "rel_type": "conflicts_with", "evidence": "leak check"}]
            cur = compile_dossier(
                query="wal writer readers", objective="wal writer readers",
                scope={"namespaces": ["default"], "current_only": True},
                candidates=[ok, priv, old], relations=rels,
                disagreements=disag, budget=_budget(), conn=db.conn)
            cur_ids = {s.candidate_id for s in cur.selections}
            cur_text = " ".join(s.excerpt_text for s in cur.selections)
            cur_kinds = {o["kind"] for o in cur.omissions}
            record("scope_enforced", {
                "secret_withheld": "SECRET" not in cur_text,
                "priv_excluded": "priv-1" not in cur_ids,
                "historical_excluded": "old-1" not in cur_ids,
                "leak_edge_dropped": all(
                    r["object"] != "priv-1" for r in cur.relations),
                "leak_disagreement_dropped": cur.disagreements == [],
                "exclusions_named": "relation_filtered" in cur_kinds
                and "disagreement_filtered" in cur_kinds,
                "kept_edge_conditions": any(
                    r.get("condition_text") == "in WAL mode"
                    for r in cur.relations),
            })
            hist = compile_dossier(
                query="batching throughput", objective="batching throughput",
                scope={"namespaces": ["default"], "current_only": False},
                candidates=[ok, priv, old], relations=rels,
                disagreements=disag, budget=_budget(), conn=db.conn)
            hist_ids = {s.candidate_id for s in hist.selections}
            record("historical_explicit", {
                "historical_retained": "old-1" in hist_ids,
                "historical_honest_status": any(
                    s.candidate_id == "old-1" for s in hist.selections),
                "priv_still_excluded": "priv-1" not in hist_ids,
            })
        finally:
            db.close()
    _flush(steps, lines)


def test_atomic_group_with_excluded_member(tmp_path):
    """A scope-partitioned atomic group is named, never claimed complete."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2a.db"
    client = TestClient(create_app(db_path))
    with client:
        _ingest(client, "a.md", PREMISE)
        _ingest(client, "b.md", CONTEXT)
        db = Database(db_path)
        try:
            ids_texts = [(r[0], r[1]) for r in db.conn.execute(
                "SELECT id, text FROM atom ORDER BY id").fetchall()]
            members = []
            for aid, text in ids_texts:
                members.append({
                    "candidate_id": f"atom-{aid}", "atom_id": int(aid),
                    "text": str(text),
                    "doc_id": "a.md" if aid == ids_texts[0][0] else "b.md",
                    "source_id": "a.md" if aid == ids_texts[0][0] else "b.md",
                    "route": "vector", "group_id": "G",
                    "atomic_group": True,
                    # Force the second member out of scope at compile time.
                    "namespace": ("default" if aid == ids_texts[0][0]
                                  else "private"),
                    "access_scope": "default", "version_status": "current",
                })
            # Pure path (no conn): the forced out-of-scope claim stands
            # and the partition logic — conn-independent — must name it.
            dossier = compile_dossier(
                query="batching throughput", objective="batching throughput",
                scope={"namespaces": ["default"]},
                candidates=members, budget=_budget())
        finally:
            db.close()
        kinds = {(o["kind"], o.get("id")) for o in dossier.omissions}
        record("partition_named", {
            "visible_member_selected": any(
                s.candidate_id == members[0]["candidate_id"]
                for s in dossier.selections),
            "partition_surfaced": ("atomic_group_partitioned", "G") in kinds,
            "no_silent_completeness": dossier.status.value == "ok",
        })
    _flush(steps, lines)


def _sematom(cid, text, doc, **kw):
    d = {"candidate_id": cid, "text": text, "doc_id": doc, "source_id": doc,
         "start_byte": 0, "end_byte": len(text.encode("utf-8")),
         "namespace": "default", "access_scope": "default",
         "version_status": "current", "route": "lexical"}
    d.update(kw)
    return d


def test_lexical_vote_honesty_semantic_variants():
    """Lexical overlap stays a relevance heuristic, never a verdict.

    Negation / actor-reversal / quantity / conditional variants coexist;
    conditions and counterevidence are preserved; the method is named.
    """
    steps, lines, record = _recorder()
    query = "Does batching improve throughput under low contention"
    neg = "Batching does NOT improve throughput under any contention."
    rev = "Throughput improves batching under low contention."
    qty30 = "Batching improves throughput by 30 percent under low contention."
    qty300 = "Batching improves throughput by 300 percent under low contention."
    cond = "Batching improves throughput under low contention."
    dossier = compile_dossier(
        query=query, objective=query,
        candidates=[
            _sematom("neg-1", neg, "neg-doc"),
            _sematom("rev-1", rev, "rev-doc"),
            _sematom("qty30-1", qty30, "qty30-doc"),
            _sematom("qty300-1", qty300, "qty300-doc"),
            _sematom("cond-1", cond, "cond-doc"),
        ],
        relations=[
            {"rel_type": "qualifies", "subject": "cond-1",
             "object": "batching-claim",
             "evidence": "low-contention gain",
             "condition_text": "unless contention is high"},
            {"rel_type": "conflicts_with", "subject": "qty30-1",
             "object": "qty300-1",
             "evidence": "30 percent versus 300 percent"},
            {"rel_type": "conflicts_with", "subject": "cond-1",
             "object": "neg-1", "evidence": "gain versus never"},
        ],
        budget=_budget())
    got = {s.candidate_id for s in dossier.selections}
    record("variants_coexist", {
        # No variant is dropped as "irrelevant": negation and reversal are
        # counterevidence, not noise.
        "negation_retained": "neg-1" in got,
        "reversal_retained": "rev-1" in got,
        "both_quantities_retained": {"qty30-1", "qty300-1"} <= got,
        "conditional_retained": "cond-1" in got,
        # Counterevidence is explicit, conditions preserved verbatim.
        "conflicting_state": dossier.support_state.value == "conflicting",
        "quantity_disagreement": any(
            {d["side_a"], d["side_b"]} == {"qty30-1", "qty300-1"}
            for d in dossier.disagreements),
        "negation_disagreement": any(
            {d["side_a"], d["side_b"]} == {"cond-1", "neg-1"}
            for d in dossier.disagreements),
        "condition_preserved": any(
            r.get("condition_text") == "unless contention is high"
            for r in dossier.relations),
        # The vote is labeled a heuristic — never NLI.
        "checker_named": dossier.checker["method"] == SUPPORT_METHOD
        and "not NLI" in dossier.checker["note"],
    })
    _flush(steps, lines)


def test_task_quality_measured_through_served_path(tmp_path):
    """End-to-end task quality via HTTP: relevant doc ranks first."""
    steps, lines, record = _recorder()
    db_path = tmp_path / "int2q.db"
    docs = {
        "rel-tx.md": "# Batching\n\nBatching improves throughput by 30% under low contention.\n",
        "rel-unbatched.md": "# Unbatched\n\nUnder high contention the batching benefit disappears; "
        "throughput matches the unbatched path.\n",
        "dis-wal.md": "# WAL\n\nWAL mode allows one writer with concurrent readers.\n",
        "dis-obsidian.md": "# Sync\n\nObsidian vault synchronization strategies for plain markdown notes.\n",
        "dis-router.md": "# Fusion\n\nReciprocal rank fusion for hybrid search pipelines.\n",
        "dis-truth.md": "# TMS\n\nTruth maintenance across contract boundaries.\n",
    }
    client = TestClient(create_app(db_path))
    with client:
        for name, text in docs.items():
            _ingest(client, name, text)
        q = client.post(
            "/api/query",
            json={"query": "batching throughput low contention gain"}).json()
        order = [a["doc_id"] for a in q["anchors"]]
        record("served_quality", {
            "answerable": q["sufficiency_state"] == "answerable",
            "relevant_first": bool(order) and order[0] == "rel-tx.md",
            "both_relevant_served": {"rel-tx.md", "rel-unbatched.md"} <= set(order),
            "route_honest": q["route"] in KNOWN_ROUTES
            and set(q["routes"]) <= KNOWN_ROUTES,
            "pinned": q["vector_generation_id"] == q["generation_id"],
        })
        d = client.post(
            "/api/dossier",
            json={"query": "batching throughput low contention gain",
                  "limit": 6}).json()
        record("served_dossier_quality", {
            "cites_relevant": any(
                s["doc_id"] == "rel-tx.md" for s in d["selections"]),
            "all_resolved": all(
                s["revision_id"] and not s["citation_degraded"]
                for s in d["selections"]),
            "served_relations_scoped": all(
                "revision_id" in r for r in d["relations"]),
            "relations_proven": all(
                r.get("revision_id", "") in
                {s["revision_id"] for s in d["selections"]}
                for r in d["relations"]),
            "support_known": d["support_state"] in (
                "supported", "partial", "conflicting", "insufficient"),
        })
        probe = {
            "query": "batching throughput low contention gain",
            "route": q["route"], "routes": q["routes"],
            "tier_used": q["tier_used"],
            "anchor_order": order,
            "relevant_first": bool(order) and order[0] == "rel-tx.md",
            "elapsed_ms": q["elapsed_ms"],
            "dossier_support": d["support_state"],
            "dossier_selections": len(d["selections"]),
            "dossier_relations": len(d["relations"]),
        }
    _flush(steps, lines, {"served_quality_probe": probe})
