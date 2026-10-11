"""R09 G2 ladder gate semantics (PACKET-G2-R09-10K, contract .3).

Pure-function tests over the qualification harness verdict logic:
no servers, no corpus, milliseconds. Pins the G2 contract —
autonomous drain gates, frozen SciFact floor at 10k, memory ceiling,
vector stability, honest PASS/FAIL/INCOMPLETE, capability-hook
INCOMPLETE wiring, proxy-stripped child env, and rung-invariant
query schedules — plus legacy sampled-drain backcompat for reading
pre-G2 ladder entries.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

from run_integrated_qualification import (  # noqa: E402
    CAPABILITY_CASES_REQUIRED,
    HARD_GATES,
    MUT_BENCH_FLOOR,
    build_latency_schedule,
    build_mutation_plan,
    capability_cases_path,
    child_env,
    hard_verdict,
    load_capability_cases,
    queue_healthy_ok,
    readiness_gate,
    run_verdict,
    schedule_sha256,
    scifact_floor_ok,
    summarize_ops,
    vector_serving_summary,
)


def _passing_rung() -> dict:
    return {
        "scale_sources": 200,
        "lifecycle": {"replay_same_revision": True,
                      "replay_no_new_revisions": True},
        "crash_restart": {"integrity_post": "ok", "counts_stable": True},
        "parity": {"agree": True},
        "queue_drain": {"mode": "autonomous", "converged": True,
                        "within_deadline": True, "worker_enabled": True},
        "setup": {"within_cap": True},
        "memory": {"within_ceiling": True},
        "vector_serving": {"detachments": 0},
        "capability": {"status": "PASS"},
    }


def test_all_hard_gates_pass_on_healthy_autonomous_rung():
    v = hard_verdict(_passing_rung())
    assert v["pass"] is True and v["halt_ladder"] is False
    assert set(v["checks"]) == set(HARD_GATES)


def test_each_failing_gate_halts():
    base = _passing_rung()
    cases = [
        ({"crash_restart": {"integrity_post": "corrupt",
                            "counts_stable": True}}, "integrity_ok"),
        ({"crash_restart": {"integrity_post": "ok",
                            "counts_stable": False}}, "counts_stable_post_crash"),
        ({"lifecycle": {"replay_same_revision": False,
                        "replay_no_new_revisions": True}}, "replay_ok"),
        ({"parity": {"agree": False}}, "parity_agree"),
        ({"queue_drain": {"mode": "autonomous", "converged": False,
                          "within_deadline": True,
                          "worker_enabled": True}}, "queue_healthy"),
        ({"queue_drain": {"mode": "autonomous", "converged": True,
                          "within_deadline": False,
                          "worker_enabled": True}}, "queue_healthy"),
        ({"queue_drain": {"mode": "autonomous", "converged": True,
                          "within_deadline": True,
                          "worker_enabled": False}}, "queue_healthy"),
        ({"setup": {"within_cap": False}}, "setup_within_cap"),
        ({"memory": {"within_ceiling": False}}, "memory_within_ceiling"),
        ({"vector_serving": {"detachments": 2}}, "vector_stable"),
    ]
    for override, gate in cases:
        rung = {**base, **override}
        v = hard_verdict(rung)
        assert v["checks"][gate] is False, gate
        assert v["pass"] is False and v["halt_ladder"] is True, gate


def test_legacy_sampled_drain_still_reads_healthy():
    rung = _passing_rung()
    rung["queue_drain"] = {"mode": "sampled", "converged": False,
                           "sampled_ok": True, "mutated_corpus": False}
    assert hard_verdict(rung)["checks"]["queue_healthy"] is True
    rung["queue_drain"] = {"mode": "sampled", "converged": True,
                           "sampled_ok": False, "mutated_corpus": False}
    assert hard_verdict(rung)["checks"]["queue_healthy"] is True
    rung["queue_drain"] = {"mode": "sampled", "converged": False,
                           "sampled_ok": False, "mutated_corpus": False}
    assert hard_verdict(rung)["checks"]["queue_healthy"] is False


def test_queue_healthy_rejects_unknown_mode():
    assert queue_healthy_ok({"mode": "bogus", "converged": True}) is False
    assert queue_healthy_ok({}) is False


def test_scifact_floor_strict_only_at_10k_full_corpus():
    # Small rungs: floor not applicable even without quality.
    assert scifact_floor_ok(_passing_rung()) is True
    # 10k without full-corpus eval fails (no vacuous pass).
    rung = _passing_rung()
    rung["scale_sources"] = 10000
    assert scifact_floor_ok(rung) is False
    rung["quality"] = {"scifact": {"full_corpus": False,
                                   "clears_frozen_floor": True}}
    assert scifact_floor_ok(rung) is False
    # 10k full-corpus below floor fails; at/above passes.
    rung["quality"] = {"scifact": {"full_corpus": True,
                                   "clears_frozen_floor": False}}
    assert scifact_floor_ok(rung) is False
    assert hard_verdict(rung)["checks"]["scifact_floor"] is False
    rung["quality"] = {"scifact": {"full_corpus": True,
                                   "clears_frozen_floor": True}}
    assert scifact_floor_ok(rung) is True
    assert hard_verdict(rung)["checks"]["scifact_floor"] is True


def test_run_verdict_pass_fail_incomplete():
    ok = _passing_rung()
    v = run_verdict(ok)
    assert v["verdict"] == "PASS" and v["halt_ladder"] is False
    bad = _passing_rung()
    bad["parity"] = {"agree": False}
    v = run_verdict(bad)
    assert v["verdict"] == "FAIL" and v["halt_ladder"] is True
    assert "parity_agree" in v["reason"]
    for cap in ({"status": "INCOMPLETE", "reason": "cases file missing"},
                {"status": "FAIL"}, {}):
        inc = _passing_rung()
        inc["capability"] = cap
        v = run_verdict(inc)
        assert v["verdict"] == "INCOMPLETE", cap
        assert v["halt_ladder"] is False, cap


def test_capability_missing_file_is_explicit_incomplete(tmp_path):
    loaded = load_capability_cases(tmp_path)
    assert loaded["status"] == "INCOMPLETE"
    assert "missing" in loaded["reason"]
    assert loaded["n_cases"] == 0
    assert loaded["required"] == CAPABILITY_CASES_REQUIRED
    assert str(capability_cases_path(tmp_path)) in loaded["reason"]
    assert loaded["cases_path"] == str(capability_cases_path(tmp_path))


def test_capability_pending_and_bad_mix_are_incomplete(tmp_path):
    p = capability_cases_path(tmp_path)
    p.write_text(json.dumps({"status": "PENDING", "cases": []}))
    loaded = load_capability_cases(tmp_path)
    assert loaded["status"] == "INCOMPLETE"
    assert "not frozen" in loaded["reason"]
    # Frozen with 20 but without the required negation/multi mix.
    cases = [{"id": f"cap-{i:02d}", "query": "q",
              "has_negation_or_condition": False,
              "multi_sentence": False} for i in range(20)]
    p.write_text(json.dumps({"status": "FROZEN", "cases": cases}))
    loaded = load_capability_cases(tmp_path)
    assert loaded["status"] == "INCOMPLETE"
    assert "required mix" in loaded["reason"]


def test_capability_frozen_ready(tmp_path):
    cases = [{"id": f"cap-{i:02d}", "query": "q",
              "has_negation_or_condition": i < 10,
              "multi_sentence": i >= 10} for i in range(20)]
    capability_cases_path(tmp_path).write_text(
        json.dumps({"status": "FROZEN", "cases": cases}))
    loaded = load_capability_cases(tmp_path)
    assert loaded["status"] == "READY"
    assert loaded["n_cases"] == 20
    assert len(loaded["cases_sha256"]) == 64


def test_child_env_served_flags_and_proxy_strip():
    env = child_env({"http_proxy": "http://dead:1",
                     "HTTPS_PROXY": "http://dead:1",
                     "ALL_PROXY": "http://dead:1",
                     "NODE_USE_ENV_PROXY": "1",
                     "PATH": "/bin"})
    assert env["TRACE_LITE_SERVED_PERSISTENT"] == "1"
    assert env["TRACE_LITE_WORKER"] == "1"
    for var in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "NODE_USE_ENV_PROXY"):
        assert var not in env, var
    assert "127.0.0.1" in env["no_proxy"]
    assert "localhost" in env["NO_PROXY"]
    assert env["PATH"] == "/bin"


def test_latency_schedule_deterministic_and_rung_invariant():
    sci = [(f"s{i}", f"sci query {i}") for i in range(10)]
    nfc = [(f"n{i}", f"nfc query {i}") for i in range(10)]
    a = build_latency_schedule(25, sci, nfc)
    b = build_latency_schedule(25, sci, nfc)
    assert a == b and len(a) == 25
    assert schedule_sha256(a) == schedule_sha256(b)
    # Prefix-stable: the first 12 of a 25-schedule equal the 12-schedule.
    assert build_latency_schedule(12, sci, nfc) == a[:12]
    # Independent of indexed-subset size: same pools, same schedule.
    assert build_latency_schedule(25, sci[:5], nfc[:5]) != a  # pools differ
    assert build_latency_schedule(25, sci, nfc) == a  # pools same


def test_vector_serving_counts_events_not_cold():
    lat = [{"vector_adequate": {"true": 8, "false": 1, "none": 3},
            "vector_route_count": 5},
           {"vector_adequate": {"true": 10, "false": 0, "none": 2},
            "vector_route_count": 7}]
    s = vector_serving_summary(lat, [True, True, False, False])
    assert s["detachments"] == 2  # 1 inadequate + 1 True->False transition
    assert s["cold_unavailable_responses"] == 5
    assert s["vector_route_responses"] == 12
    s2 = vector_serving_summary(lat, [None, False, False])
    assert s2["detachments"] == 1  # cold start is not a detachment
    assert s2["servable_transitions_to_false"] == 0


def test_mutation_plan_exact_mix_and_guards():
    plan = build_mutation_plan(4811)
    assert len(plan) == 120
    kinds = [op["op"] for op in plan]
    assert kinds.count("insert") == 60
    assert kinds.count("edit") == 40
    assert kinds.count("retract") == 20
    assert [op["slot"] for op in plan] == list(range(120))
    assert plan == build_mutation_plan(4811)  # deterministic
    edits = {op["doc"] for op in plan if op["op"] == "edit"}
    retracts = {op["doc"] for op in plan if op["op"] == "retract"}
    inserts = [op["doc"] for op in plan if op["op"] == "insert"]
    assert len(set(inserts)) == 60 and all(d.startswith("mut-load-") for d in inserts)
    assert edits.isdisjoint(retracts)
    for doc in edits | retracts:
        assert doc.startswith("bench-"), doc
        idx = int(doc[len("bench-"):-len(".md")])
        assert idx >= MUT_BENCH_FLOOR, doc  # parity sample + lifecycle safe
    assert "bench-000000.md" not in edits | retracts


def test_mutation_plan_fallback_on_tiny_rung():
    plan = build_mutation_plan(10)
    assert len(plan) == 120
    retracts = [op for op in plan if op["op"] == "retract"]
    edits = [op for op in plan if op["op"] == "edit"]
    inserts = [op for op in plan if op["op"] == "insert"]
    assert all(op["doc"].startswith("mut-load-") for op in retracts)
    assert all(op["doc"].startswith("mut-load-") for op in edits)
    # Every retract/edit target was inserted earlier in the same plan.
    by_slot = {op["doc"]: op["slot"] for op in inserts}
    for op in retracts + edits:
        assert op["doc"] in by_slot
        assert by_slot[op["doc"]] < op["slot"]
    # Retract targets are distinct from edit targets (no wasted op pairs).
    assert {op["doc"] for op in retracts}.isdisjoint({op["doc"] for op in edits})
    # Dry-run quotas stay proportional and disjoint.
    small = build_mutation_plan(144, 3, 2, 1)
    assert [op["op"] for op in small].count("insert") == 3
    assert [op["op"] for op in small].count("edit") == 2
    assert [op["op"] for op in small].count("retract") == 1


def test_summarize_ops_offered_achieved_and_overlap():
    recs = [
        {"issued": True, "ok": True, "e2e_ms": 5.0, "overlapped": True,
         "slip_s": 0.01},
        {"issued": True, "ok": True, "e2e_ms": 7.0, "overlapped": False,
         "slip_s": 0.02},
        {"issued": True, "ok": False, "e2e_ms": 10000.0, "overlapped": True,
         "slip_s": 0.0, "error": "timeout"},
        {"issued": False, "ok": False, "e2e_ms": None, "overlapped": False,
         "slip_s": 0.0, "error": "skipped"},
    ]
    s = summarize_ops(recs)
    assert (s["offered"], s["achieved"]) == (4, 3)
    assert (s["succeeded"], s["failures"]) == (2, 2)
    # Errors join the distribution (never dropped) with ok-only alongside.
    assert s["latency_incl_errors_ms"]["max"] == 10000.0
    assert s["latency_ok_only_ms"]["max"] == 7.0
    assert s["latency_ok_only_ms"]["n"] == 2
    assert s["overlap"]["fraction"] == 0.5
    assert s["overlap"]["latency_incl_errors_ms"]["n"] == 2
    assert s["errors"] == {"timeout": 1, "skipped": 1}
    assert s["schedule_slip_s"]["max"] == 0.02


def test_readiness_gate_2x_rule():
    admit = readiness_gate(1000, 400.0, 8000.0)
    assert set(admit) >= {"fits_ram_2x", "fits_disk_2x", "admit",
                          "projected_server_rss_mb"}
    # Absurd scale must refuse on this host: 1e12 sources cannot fit 2x.
    refuse = readiness_gate(10**12, 400.0, 8000.0)
    assert refuse["admit"] is False
    assert refuse["fits_ram_2x"] is False or refuse["fits_disk_2x"] is False
