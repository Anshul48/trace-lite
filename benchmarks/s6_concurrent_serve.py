#!/usr/bin/env python3
"""S6 concurrent-serve falsifier: queries stay correct while backlog drains.

Lane-C qualification harness. Replaces (supersedes) ``s6_envelope.py`` and
``s6_serve_under_drain.py`` for qualification purposes — those probes drain
before serving / serialize work beside a second writer, so they cannot
falsify the S3/S4 non-blocking claim. This harness:

* uses a persistent explicitly-identified run dir + manifest (no globs);
* prepares a valid served baseline, then creates a KNOWN pending backlog
  WITHOUT fully draining it (backlog published library-direct while the
  served app is already running, so lifespan startup cannot drain it);
* verifies document / revision / atom / job counts before timing;
* exercises ONLY the app-owned maintenance path (``ServedRuntime``
  seed / catch-up, auto-scheduled by ``POST /api/ingest`` via
  ``served_after_write``) concurrently with independent query threads
  plus online ``POST /api/ingest`` mutations — NO manual
  ``MaintenanceWorker`` (a second writer would change the architecture
  under test);
* records timestamps proving query/maintenance overlap and worker
  progress; separates setup / cold start / steady-state / shutdown;
* freezes budgets + stop rules up front (see FROZEN below);
* preserves partial failure evidence (manifest + events first, verdict last).

FROZEN budgets (S6, from RECONCILIATION.md section 1):
  lag 60s | disk headroom 10x | query freshness 1.0s | shutdown join 30s.

Harness bounds (frozen here, recorded in every manifest):
  setup <= 300s | cold start (time-to-servable) <= 600s |
  per-request timeout 10s (queries AND mutations) |
  observation window fixed by --window-s |
  shutdown join <= 30s.
Stop rules: rolling error rate > 10% over 20 requests stops clients early
(verdict false); any unhandled exception aborts the phase and writes
failure.json with series collected so far; the observation window is never
extended; the backlog is never force-drained (progress, not empty, is the
predicate).
v4 repairs (cycle 2): per-request timeout gates the verdict for mutations
too (v3 green-lit a 10.4 s mutation); query clients are paced evenly
across the window (--pace even; v3 burst 360 queries into 5.5 s of a 40 s
window, serving a single frozen generation); new span_ok (>=80% of the
window covered) and gens_observed (>=2 distinct response generations)
predicates; mutation latency summary + per-mutation queue-depth brackets;
visibility probe records route/vgen/gen/served_gen; manifest paths are
absolute; stop-rule double error counting fixed.

Count semantics (exact, never relabeled):
  documents = COUNT(*) FROM source
  revisions_total / revisions_current = COUNT(*) FROM source_revision
    (total / WHERE status='current')
  atoms = COUNT(*) FROM atom
  jobs_pending / jobs_claimed / jobs_done / jobs_total = job_progress()
  queue depth / lag = queue_stats()  (depth counts JOBS, never docs)
  vectors/encodes = served seed/catch-up receipts (cache encodes)

Usage (smoke; bounded 2k run waits for coordinator approval):
  .venv/bin/python benchmarks/s6_concurrent_serve.py \\
      --baseline 40 --backlog 120 --window-s 45 --queries-per-thread 200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue as _queue
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HARNESS = Path(__file__).resolve()
REPO = HARNESS.parents[1]

# Frozen budgets (S6).
LAG_BOUND_S = 60.0
DISK_HEADROOM_MIN_RATIO = 10.0
FRESHNESS_BUDGET_S = 1.0
SHUTDOWN_JOIN_S = 30.0
# Harness bounds (frozen).
SETUP_BUDGET_S = 300.0
COLD_START_BUDGET_S = 600.0
PER_REQUEST_TIMEOUT_S = 10.0
LATENCY_PREDICATE_P95_MS = 1000.0
STOP_ERROR_RATE = 0.10
STOP_ERROR_WINDOW = 20
# v4.2 (coordinator corrective pass): exact byte-span citation on matching
# items (anchor + visibility); content-bound candidate manifest (executed
# module hashes + running-app resolved config); maint pass-edge signal.
# v4 checked top-1 content (recall/integrity conflated); v4.1 fixed
# visibility only and left anchors top-1.
HARNESS_VERSION = "4.2"

TOPICS = [
    "batching throughput contention", "wal concurrency readers",
    "lexical dense fusion", "generation pinning snapshots",
    "projection drain backpressure",
]
POOL = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
        "kilo lima mike november oscar papa quebec romeo sierra tango "
        "uniform victor whiskey xray yankee zulu").split()


def _doc(i: int, tag: str) -> tuple[str, str]:
    topic = TOPICS[i % len(TOPICS)]
    w = " ".join(POOL[(i * 7 + j * 11) % len(POOL)] for j in range(12))
    return (f"{tag}-{i:05d}.md",
            f"# Note {tag} {i}\n\n{topic} under load: {w} measurements {i}.\n")


def _planted(i: int) -> tuple[str, str]:
    tok = f"zephyrquorum{i:02d}"
    return (f"anchor-c-{i:02d}.md",
            f"# Anchor {i}\n\n{tok} quorum latch {tok} holds the line.\n")


def _online(i: int) -> tuple[str, str]:
    tok = f"onlinevane{i:04d}"
    return (f"online-c-{i:04d}.md",
            f"# Online {i}\n\n{tok} live mutation {tok} lands during drain.\n")


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _git_identity(app_repo: Path) -> dict:
    def _run(*a: str) -> str:
        try:
            return subprocess.run(
                list(a), cwd=str(app_repo), capture_output=True, text=True,
                timeout=30).stdout.strip()
        except Exception:
            return "unknown"

    head = _run("git", "rev-parse", "HEAD")
    try:
        diff = subprocess.run(
            ["git", "diff", "--stat", "HEAD"], cwd=str(app_repo),
            capture_output=True, text=True, timeout=60).stdout
        diff_hash = hashlib.sha256(diff.encode()).hexdigest()[:16]
    except Exception:
        diff, diff_hash = "", "unknown"
    try:
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(app_repo), capture_output=True, text=True,
            timeout=60).stdout.split()
    except Exception:
        untracked = []
    return {"head": head, "diff_stat_sha16": diff_hash,
            "diff_stat": diff[:4000], "untracked": sorted(untracked)}


def _module_identity() -> dict:
    """Content binding for EXECUTED code (v4.2).

    A diff-stat hash cannot bind source contents (identical stats can
    cover different code; untracked content was unhashed). This hashes
    every loaded ``trace_lite`` module file, i.e. the code that actually
    ran, plus the interpreter search path. Runs before v4.2 whose
    manifests lack ``code`` are candidate-IDENTIFIED (head + diffstat),
    not candidate-bound.
    """
    mods = {}
    for name in sorted(sys.modules):
        if not (name == "trace_lite"
                or name.startswith("trace_lite.")):
            continue
        f = getattr(sys.modules[name], "__file__", None)
        if not f or not str(f).endswith(".py"):
            continue
        try:
            digest = hashlib.sha256(
                Path(str(f)).read_bytes()).hexdigest()[:16]
        except OSError:
            digest = "unreadable"
        mods[name] = {"file": str(f), "sha16": digest}
    total = hashlib.sha256(
        "".join(f"{n}={m['sha16']}" for n, m in mods.items()
                ).encode()).hexdigest()[:16]
    return {"modules": mods, "digest": total,
            "sys_path_head": sys.path[:4]}


def _dep_versions() -> dict:
    try:
        from importlib import metadata as _md
    except ImportError:
        return {}
    out = {}
    for dist in ("fastapi", "starlette", "httpx", "numpy", "pydantic",
                 "pytest"):
        try:
            out[dist] = _md.version(dist)
        except Exception:
            out[dist] = "unknown"
    return out


def _counts(db_path: Path) -> dict:
    con = sqlite3.connect(str(db_path))
    try:
        docs = con.execute("SELECT COUNT(*) FROM source").fetchone()[0]
        revs = con.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0]
        cur = con.execute("SELECT COUNT(*) FROM source_revision "
                          "WHERE status='current'").fetchone()[0]
        atoms = con.execute("SELECT COUNT(*) FROM atom").fetchone()[0]
        return {"documents": int(docs), "revisions_total": int(revs),
                "revisions_current": int(cur), "atoms": int(atoms)}
    finally:
        con.close()


def _revision_text(db_path: Path, source_id: str) -> str | None:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT text FROM source_revision "
                          "WHERE source_id=? AND status='current'",
                          (source_id,)).fetchone()
        return str(row[0]) if row else None
    finally:
        con.close()


def cite_exact(db_path: Path, atom_id: object,
               source_id: str) -> dict:
    """Exact byte-span citation (v4.2): served atom bytes must equal the
    current revision bytes at the atom's recorded span. Fail-closed with a
    machine-checkable reason. Replaces whitespace-normalized substring
    matching (v3/v4), which could not distinguish stale text, shifted
    spans, or wrong revisions that share vocabulary.
    """
    try:
        aid = int(str(atom_id))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "bad-atom-id"}
    con = sqlite3.connect(str(db_path))
    try:
        arow = con.execute(
            "SELECT text, start_byte, end_byte FROM atom WHERE id=?",
            (aid,)).fetchone()
        if arow is None:
            return {"ok": False, "reason": "atom-missing"}
        rrow = con.execute(
            "SELECT text FROM source_revision "
            "WHERE source_id=? AND status='current'",
            (source_id,)).fetchone()
        if rrow is None:
            return {"ok": False, "reason": "revision-missing"}
        try:
            atom_b = str(arow[0]).encode("utf-8")
            rev_b = str(rrow[0]).encode("utf-8")
            start, end = int(arow[1]), int(arow[2])
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad-span"}
        if start < 0 or end > len(rev_b) or start >= end:
            return {"ok": False, "reason": "span-out-of-range"}
        if rev_b[start:end] != atom_b:
            return {"ok": False, "reason": "span-bytes-mismatch"}
        return {"ok": True, "reason": "exact",
                "bytes": len(atom_b)}
    finally:
        con.close()


def overlap_verdict(ok_reqs: list[dict], mut_done: list[dict],
                   status_series: list[dict]) -> dict:
    """Pure overlap analysis with split verdicts (v4.2).

    Three signals: (a) query covers a maintenance_running=true sample;
    (b) query intersects a mutation request interval; (c) query falls in
    a bin with proven worker progress (receipt change or jobs.done up).
    Gate = (a) or (c): background-worker overlap ONLY. Signal (b) spans
    synchronous drain inside ingest (ingest_note drains the outbox
    in-request), so it is REPORTED as mut_overlap, never gated: it
    cannot evidence the decoupled background path.
    """
    def _inter(a0: float, a1: float, b0: float, b1: float) -> bool:
        return a0 <= b1 and a1 >= b0

    run_samples = [s["t"] for s in status_series if s["maint"] is True]
    hits_a = [r for r in ok_reqs
              for t in run_samples
              if _inter(r["t0"], r["t1"], t - 0.5, t + 0.5)]
    mut_spans = [(m["t0"], m["t1"]) for m in mut_done
                 if "t0" in m and "t1" in m]
    hits_b = [r for r in ok_reqs
              for (m0, m1) in mut_spans
              if _inter(r["t0"], r["t1"], m0, m1)]
    bins: list[tuple[float, float]] = []
    for prev, cur in zip(status_series, status_series[1:]):
        progressed = (
            json.dumps(cur.get("receipt"), sort_keys=True)
            != json.dumps(prev.get("receipt"), sort_keys=True)
            or (cur.get("jobs") or {}).get("done", 0)
            > (prev.get("jobs") or {}).get("done", 0))
        if progressed:
            bins.append((prev["t"], cur["t"]))
    hits_c = [r for r in ok_reqs
              for (b0, b1) in bins
              if _inter(r["t0"], r["t1"], b0, b1)]
    maint_passes = sum(
        1 for prev, cur in zip(status_series, status_series[1:])
        if prev.get("maint") is True and cur.get("maint") is not True)
    overlap_maint = bool(hits_a or hits_c)
    overlap_mut = bool(hits_b)
    return {
        "maint_overlap": overlap_maint,
        "mut_overlap": overlap_mut,
        "detail": {
            "maint_samples": len(run_samples),
            "maint_passes": maint_passes,
            "signal_a_maint_queries": len(hits_a),
            "signal_b_mut_overlaps": len(hits_b),
            "signal_c_progress_bins": len(bins),
            "signal_c_bin_queries": len(hits_c),
            "maint_overlap": overlap_maint,
            "mut_overlap": overlap_mut,
        },
    }


def stop_fired(err_window: list[bool]) -> bool:
    """Pure stop-rule evaluator (v4: single error appender is the caller).

    v3 double-counted failures (inline append + a second append inside
    the evaluator); the evaluator now only reads the window.
    """
    return (len(err_window) >= STOP_ERROR_WINDOW
            and sum(err_window[-STOP_ERROR_WINDOW:])
            / STOP_ERROR_WINDOW > STOP_ERROR_RATE)


def pace_interval_s(window_s: float, queries_per_thread: int) -> float:
    """Per-thread inter-query issue interval for --pace even (v4).

    Spreads each thread's budget uniformly over the observation window
    so latency samples cover the drain curve instead of bursting in the
    first seconds (v3 runs: 360 queries in 5.5 s of a 40 s window).
    """
    return window_s / max(1, int(queries_per_thread))


def mut_stats(mut_done: list[dict]) -> dict:
    """Pure online-mutation summary (v4: timeouts are first-class).

    v3 recorded per-mutation timeout flags but the verdict ignored them
    (run s6c_20260912-190447: 10447 ms mutation, ok=True). Callers must
    gate on ``mut_timeouts == 0`` via the ``timeout_ok`` predicate.
    """
    lat = [float(m.get("ms", 0.0)) for m in mut_done if m.get("ok")]
    return {
        "mut_p50_ms": round(_pct(lat, 0.50), 3),
        "mut_p95_ms": round(_pct(lat, 0.95), 3),
        "mut_max_ms": round(max(lat), 3) if lat else 0.0,
        "mut_timeouts": sum(1 for m in mut_done if m.get("timeout")),
    }


def query_coverage(req_log: list[dict], status_series: list[dict],
                   window_s: float, span_frac: float,
                   min_gens: int) -> dict:
    """Pure steady-state coverage analysis (v4).

    span_ok: ok-query issue span covers span_frac of the window.
    gens_observed: ok queries span >= min_gens distinct response
      generations (proves serving ACROSS a publication; v3 run
      s6c_20260912-190049 served all 360 queries at one frozen gen).
    vgen_differs_from_served: informational count of ok vector queries
      whose vgen != nearest poller served_gen (served-view staleness
      signal; overlay delta serves fresh atoms under a frozen gen, so
      this is reported, not gated — Lane B owns the semantics).
    """
    ok_reqs = [r for r in req_log if r.get("ok")]
    t0s = [float(r["t0"]) for r in ok_reqs if "t0" in r]
    span = (max(t0s) - min(t0s)) if len(t0s) >= 2 else 0.0
    gens = {r.get("gen") for r in ok_reqs if r.get("gen") is not None}
    differs = 0
    total = 0
    if status_series:
        pts = sorted(status_series, key=lambda s: float(s.get("t", 0.0)))
        for r in ok_reqs:
            if r.get("route") != "vector" or r.get("vgen") is None:
                continue
            total += 1
            served = pts[0].get("served_gen")
            for s in pts:
                if float(s.get("t", 0.0)) <= float(r.get("t0", 0.0)):
                    served = s.get("served_gen")
                else:
                    break
            if r.get("vgen") != served:
                differs += 1
    return {
        "query_span_s": round(span, 2),
        "span_ok": span >= span_frac * window_s,
        "distinct_gens": len(gens),
        "gens_observed": len(gens) >= min_gens,
        "vgen_differs_from_served": differs,
        "vgen_compared": total,
    }


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        root = (args.run_root or
                (REPO / "evidence" / "query-native" / "_integration"
                 / "S6" / "runs"))
        self.run_dir = (Path(root) / f"s6c_{stamp}").resolve()  # v4: absolute
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.db_path = self.run_dir / "serve.db"
        self.index_dir = self.run_dir / "served"
        self.events_path = self.run_dir / "events.jsonl"
        self._events_lock = threading.Lock()
        self.result: dict = {"run_id": self.run_dir.name}
        self.stop_flag = threading.Event()

    def emit(self, kind: str, **fields: object) -> None:
        rec = {"t_wall": datetime.now(timezone.utc).isoformat(),
               "t_mono": time.monotonic(), "kind": kind}
        rec.update(fields)
        with self._events_lock:
            with open(self.events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    def manifest(self, app_repo: Path, extra: dict) -> dict:
        man = {
            "run_id": self.run_dir.name,
            "harness_version": HARNESS_VERSION,
            "cwd": str(Path.cwd().resolve()),
            "app_repo": str(Path(app_repo).resolve()),
            "harness": {"file": str(HARNESS),
                        "sha256": hashlib.sha256(
                            HARNESS.read_bytes()).hexdigest()[:16]},
            "candidate": _git_identity(app_repo),
            "code": _module_identity(),
            "deps": _dep_versions(),
            "python": sys.version.split()[0],
            "backend": "TestClient(shared, threaded) + sqlite",
            "config": {
                "TRACE_LITE_SERVED_PERSISTENT": os.environ.get(
                    "TRACE_LITE_SERVED_PERSISTENT"),
                "TRACE_LITE_SERVED_INDEX_DIR": str(self.index_dir),
                "TRACE_LITE_WORKER": os.environ.get("TRACE_LITE_WORKER"),
                "TRACE_LITE_QUEUE_LAG_BOUND_S": os.environ.get(
                    "TRACE_LITE_QUEUE_LAG_BOUND_S"),
            },
            "paths": {"db": str(self.db_path), "index": str(self.index_dir),
                      "run_dir": str(self.run_dir)},
            "budgets": {"lag_s": LAG_BOUND_S,
                        "disk_headroom_ratio": DISK_HEADROOM_MIN_RATIO,
                        "freshness_s": FRESHNESS_BUDGET_S,
                        "shutdown_join_s": SHUTDOWN_JOIN_S,
                        "setup_s": SETUP_BUDGET_S,
                        "cold_start_s": COLD_START_BUDGET_S,
                        "per_request_timeout_s": PER_REQUEST_TIMEOUT_S,
                        "latency_p95_ms": LATENCY_PREDICATE_P95_MS},
            "stop_rules": {
                "rolling_error_rate": STOP_ERROR_RATE,
                "rolling_window": STOP_ERROR_WINDOW,
                "no_window_extension": True, "no_force_drain": True},
        }
        man.update(extra)
        (self.run_dir / "manifest.json").write_text(
            json.dumps(man, indent=2), encoding="utf-8")
        return man

    def manifest_update(self, extra: dict) -> None:
        """Merge late-resolved evidence (v4.2): config read from the
        RUNNING app, not the parent environment (the pre-v4.2 manifest
        recorded TRACE_LITE_SERVED_PERSISTENT=null because it was written
        before the harness set it in-process)."""
        p = self.run_dir / "manifest.json"
        try:
            man = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            man = {"run_id": self.run_dir.name}
        man.update(extra)
        p.write_text(json.dumps(man, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S6 concurrent-serve falsifier")
    ap.add_argument("--baseline", type=int, default=40)
    ap.add_argument("--backlog", type=int, default=120)
    ap.add_argument("--planted", type=int, default=6)
    ap.add_argument("--window-s", type=float, default=45.0)
    ap.add_argument("--query-threads", type=int, default=4)
    ap.add_argument("--queries-per-thread", type=int, default=200)
    ap.add_argument("--mut-budget", type=int, default=15)
    ap.add_argument("--mut-period-s", type=float, default=3.0)
    ap.add_argument("--run-root", type=Path, default=None)
    ap.add_argument("--app-repo", type=Path, default=None)
    ap.add_argument("--pace", choices=("even", "burst"), default="even",
                    help="v4: even spreads each thread's budget over the "
                    "window; burst keeps v3 closed-loop issue")
    ap.add_argument("--min-gens", type=int, default=2,
                    help="v4: distinct response gens required (span a "
                    "publication)")
    ap.add_argument("--span-frac", type=float, default=0.8,
                    help="v4: required query-span fraction of the window")
    args = ap.parse_args(argv)

    total_new = args.baseline + args.backlog + args.planted
    if total_new > 200 and args.window_s <= 60.0 and os.environ.get(
            "S6C_ALLOW_LARGE_SMOKE") != "1":
        print(f"REFUSED: {total_new} docs exceeds smoke bound (200) for a "
              f"short window; bounded 2k run needs coordinator approval.",
              flush=True)
        return 2

    app_repo = args.app_repo or REPO
    sys.path.insert(0, str(Path(app_repo) / "src"))
    from trace_lite.api import create_app  # noqa: E402
    from trace_lite.store import Database  # noqa: E402
    from trace_lite.store import outbox as outbox_store  # noqa: E402
    from trace_lite.source import service as svc  # noqa: E402

    h = Harness(args)
    t_setup0 = time.monotonic()
    h.manifest(app_repo, {
        "corpus": {"baseline": args.baseline, "backlog": args.backlog,
                   "planted": args.planted, "mut_budget": args.mut_budget,
                   "query_threads": args.query_threads,
                   "queries_per_thread": args.queries_per_thread,
                   "window_s": args.window_s, "pace": args.pace,
                   "min_gens": args.min_gens,
                   "span_frac": args.span_frac},
        "note": "setup budget clock starts at manifest write",
    })
    h.emit("run_start", **h.result)

    try:
        # ---- Phase 0: setup — baseline via library-direct publish ----
        base_docs = ([_doc(i, "base") for i in range(args.baseline)]
                     + [_planted(i) for i in range(args.planted)])
        db = Database(h.db_path)
        try:
            t0 = time.monotonic()
            for name, text in base_docs:
                res = svc.publish(db, name, text)
                assert res.created is True
            setup_ingest_s = time.monotonic() - t0
            pre = outbox_store.queue_stats(db.conn)
        finally:
            db.close()
        setup_s = time.monotonic() - t_setup0
        h.result.update({
            "setup_s": round(setup_s, 2),
            "setup_ingest_s": round(setup_ingest_s, 2),
            "setup_within_budget": setup_s <= SETUP_BUDGET_S,
            "baseline_enqueued_depth": pre["depth"],
        })
        h.emit("setup_done", **{k: h.result[k] for k in
                                ("setup_s", "baseline_enqueued_depth")})
        if setup_s > SETUP_BUDGET_S:
            raise TimeoutError(f"setup {setup_s:.1f}s exceeded bound")

        # ---- Phase 1: cold start — boot served app, seed is app-owned ----
        old_env = dict(os.environ)
        os.environ["TRACE_LITE_SERVED_PERSISTENT"] = "1"
        os.environ["TRACE_LITE_SERVED_INDEX_DIR"] = str(h.index_dir)
        os.environ.pop("TRACE_LITE_WORKER", None)  # app-owned ServedRuntime only
        from fastapi.testclient import TestClient

        # Explicit enter/exit (not `with`) so the lifespan shutdown —
        # bounded join + residual drain — is really timed in Phase 4.
        client = TestClient(create_app(h.db_path))
        client.__enter__()
        try:
            t_app = time.monotonic()
            deadline = t_app + COLD_START_BUDGET_S
            st: dict = {}
            while time.monotonic() < deadline:
                st = client.get("/api/status").json()
                sp = st.get("served_persistent", {})
                if (sp.get("seed", {}).get("state") == "done"
                        and sp.get("servable") is True):
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("seed never servable")
            time_to_servable = time.monotonic() - t_app
            seed = st["served_persistent"]["seed"]["receipt"] or {}
            h.result.update({
                "time_to_servable_s": round(time_to_servable, 2),
                "cold_start_within_budget":
                    time_to_servable <= COLD_START_BUDGET_S,
                "seed_encodes": (seed.get("cache") or {}).get("encodes"),
                "seed_atoms": seed.get("atoms"),
            })
            h.emit("cold_start_done",
                   time_to_servable_s=h.result["time_to_servable_s"])
            # v4.2: resolve config from the RUNNING application.
            h.manifest_update({
                "config_resolved": {
                    "TRACE_LITE_SERVED_PERSISTENT_effective":
                        os.environ.get("TRACE_LITE_SERVED_PERSISTENT"),
                    "TRACE_LITE_WORKER_effective": os.environ.get(
                        "TRACE_LITE_WORKER"),
                    "served_persistent_enabled": sp.get("enabled"),
                    "served_generation_id": sp.get("generation_id"),
                    "served_servable": sp.get("servable"),
                },
            })

            # ---- Phase 2: KNOWN backlog while app runs (no drain) ----
            backlog_docs = [_doc(i, "lag") for i in range(args.backlog)]
            wdb = Database(h.db_path)
            try:
                for name, text in backlog_docs:
                    res = svc.publish(wdb, name, text)
                    assert res.created is True
                pre_q = outbox_store.queue_stats(wdb.conn)
                pre_prog = outbox_store.job_progress(wdb.conn)
            finally:
                wdb.close()
            pre_counts = _counts(h.db_path)
            h.emit("backlog_created", depth=pre_q["depth"],
                   lag_s=pre_q["queue_lag_s"], counts=pre_counts,
                   job_progress=pre_prog)
            if pre_q["depth"] < args.backlog:
                raise AssertionError(
                    f"backlog assumption falsified: depth "
                    f"{pre_q['depth']} < docs {args.backlog} "
                    f"(something drained during setup)")
            h.result["pre_timing"] = {
                "counts": pre_counts,
                "queue_depth_jobs": pre_q["depth"],
                "queue_lag_s": pre_q["queue_lag_s"],
                "job_progress": pre_prog,
                "status_snapshot": {
                    "generation_id": st.get("generation_id"),
                    "coverage_total": st.get("coverage_total"),
                    "coverage_pending": st.get("coverage_pending"),
                    "served_generation": st.get(
                        "served_persistent", {}).get("generation_id"),
                },
            }

            # ---- Phase 3: steady state — overlap queries + mutations --
            t_obs0 = time.monotonic()
            t_end = t_obs0 + args.window_s
            req_log: list[dict] = []
            req_lock = threading.Lock()
            err_window: list[bool] = []
            mut_done: list[dict] = []
            status_series: list[dict] = []
            queries = ([f"how does {t} behave under load"
                        for t in TOPICS]
                       + [f"{t} measurements" for t in TOPICS]
                       + [f"zephyrquorum{i:02d} quorum latch"
                          for i in range(args.planted)])

            def note_error() -> None:
                # v4: callers append exactly once; evaluator only reads.
                if stop_fired(err_window):
                    h.emit("stop_rule_fired",
                           reason="rolling error rate exceeded")
                    h.stop_flag.set()

            nq = len(queries)

            interval = pace_interval_s(args.window_s,
                                         args.queries_per_thread)

            def query_worker(wid: int) -> None:
                qi = wid
                next_due = time.monotonic() + wid * interval / max(
                    1, args.query_threads)
                while (not h.stop_flag.is_set()
                       and time.monotonic() < t_end):
                    q = queries[qi % nq]
                    qidx = qi % nq
                    qi += 1
                    t0 = time.monotonic()
                    try:
                        # NOTE: starlette TestClient ignores the timeout
                        # kwarg (Kludex/starlette#1108), so timeouts are
                        # measured on our own wall clock (ms field).
                        r = client.post(
                            "/api/query", json={"query": q, "limit": 5})
                        dt_ms = (time.monotonic() - t0) * 1000
                        body = r.json()
                        ok = r.status_code == 200
                        items = body.get("items", []) if ok else []
                        rec = {"t0": t0, "t1": time.monotonic(),
                               "wid": wid, "qidx": qidx, "ok": ok,
                               "status": r.status_code,
                               "ms": round(dt_ms, 2),
                               "route": body.get("route") if ok else None,
                               "vgen": body.get("vector_generation_id")
                               if ok else None,
                               "gen": body.get("generation_id")
                               if ok else None,
                               # bounded payload: per-item doc + atom id for
                               # matching-item citation verdicts (v4.2), plus
                               # legacy doc names + top-1 content
                               "docs": [str(it.get("source_artifact",
                                                   {}).get(
                                                   "document_name", ""))
                                        for it in items[:5]] if ok else [],
                               "tops": [{"doc": str(it.get(
                                   "source_artifact", {}).get(
                                       "document_name", "")),
                                   "atom_id": str((it.get("atom") or {})
                                                  .get("atom_id", ""))}
                                   for it in items[:5]] if ok else [],
                               "top": str((items[0].get("atom") or {})
                                          .get("content", ""))[:500]
                               if ok and items else "",
                               "timeout": bool(
                                   dt_ms > PER_REQUEST_TIMEOUT_S * 1000),
                               "error": None if ok else r.text[:200]}
                    except Exception as exc:  # noqa: BLE001
                        rec = {"t0": t0, "t1": time.monotonic(),
                               "wid": wid, "ok": False, "status": None,
                               "ms": round(
                                   (time.monotonic() - t0) * 1000, 2),
                               "route": None, "vgen": None, "gen": None,
                               "timeout": "timeout" in type(
                                   exc).__name__.lower(),
                               "error": f"{type(exc).__name__}: "
                               f"{str(exc)[:200]}"}
                    with req_lock:
                        req_log.append(rec)
                        err_window.append(not rec["ok"])
                    h.emit("query", **rec)
                    if not rec["ok"]:
                        note_error()
                    if args.pace == "even":
                        next_due += interval
                        delay = next_due - time.monotonic()
                        if delay > 0:
                            h.stop_flag.wait(delay)
                    if sum(1 for x in req_log
                           if x["wid"] == wid) >= args.queries_per_thread:
                        break

            def mut_worker() -> None:
                for i in range(args.mut_budget):
                    if (h.stop_flag.is_set()
                            or time.monotonic() >= t_end):
                        break
                    name, text = _online(i)
                    try:  # v4: queue depth bracketing the mutation
                        depth_before = client.get("/api/status").json().get(
                            "queue_stats", {}).get("depth")
                    except Exception:  # noqa: BLE001
                        depth_before = None
                    t0 = time.monotonic()
                    try:
                        # Real app-owned maintenance trigger:
                        # ingest -> served_after_write -> request_maint.
                        r = client.post(
                            "/api/ingest",
                            json={"document_name": name, "text": text})
                        dt_ms = (time.monotonic() - t0) * 1000
                        ok = r.status_code == 200
                        try:
                            depth_after = client.get(
                                "/api/status").json().get(
                                "queue_stats", {}).get("depth")
                        except Exception:  # noqa: BLE001
                            depth_after = None
                        rec = {"i": i, "name": name, "ok": ok,
                               "t0": t0, "t1": time.monotonic(),
                               "status": r.status_code,
                               "ms": round(dt_ms, 2),
                               "depth_before": depth_before,
                               "depth_after": depth_after,
                               "timeout": bool(
                                   dt_ms > PER_REQUEST_TIMEOUT_S * 1000),
                               "error": None if ok else r.text[:200]}
                    except Exception as exc:  # noqa: BLE001
                        rec = {"i": i, "name": name, "ok": False,
                               "t0": t0, "t1": time.monotonic(),
                               "status": None,
                               "ms": round(
                                   (time.monotonic() - t0) * 1000, 2),
                               "depth_before": depth_before,
                               "depth_after": None,
                               "timeout": "timeout" in type(
                                   exc).__name__.lower(),
                               "error": f"{type(exc).__name__}: "
                               f"{str(exc)[:200]}"}
                    mut_done.append(rec)
                    h.emit("mutate", **rec)
                    if not rec["ok"]:
                        with req_lock:
                            err_window.append(True)
                        note_error()
                    time.sleep(args.mut_period_s)

            threads = [threading.Thread(target=query_worker,
                                        args=(w,), daemon=True)
                       for w in range(args.query_threads)]
            threads.append(threading.Thread(target=mut_worker,
                                            daemon=True))
            for t in threads:
                t.start()
            # status poller: maintenance + worker-progress timestamps
            while any(t.is_alive() for t in threads):
                try:
                    s = client.get("/api/status").json()
                    sp = s.get("served_persistent", {})
                    status_series.append({
                        "t": time.monotonic(),
                        "maint": sp.get("maintenance_running"),
                        "seed": (sp.get("seed") or {}).get("state"),
                        "catchup": (sp.get("catchup") or {}).get("state"),
                        "receipt": (sp.get("catchup") or {}).get(
                            "receipt"),
                        "served_gen": sp.get("generation_id"),
                        "gen": s.get("generation_id"),
                        "depth": (s.get("queue_stats") or {}).get(
                            "depth"),
                        "lag_s": (s.get("queue_stats") or {}).get(
                            "queue_lag_s"),
                        "jobs": s.get("job_progress"),
                    })
                except Exception as exc:  # noqa: BLE001
                    h.emit("status_poll_error",
                           error=f"{type(exc).__name__}: {str(exc)[:150]}")
                time.sleep(1.0)
            for t in threads:
                t.join(timeout=30.0)
            hung = [t.name for t in threads if t.is_alive()]
            if hung:
                h.emit("threads_hung", threads=hung)
                h.stop_flag.set()
            obs_s = time.monotonic() - t_obs0
            end_status = client.get("/api/status").json()
            h.emit("observation_done", obs_s=round(obs_s, 2),
                   queries=len(req_log), muts=len(mut_done))

            # visibility probe while the app is still up: last
            # successful online mutation queryable on the live path
            mut_ok_pre = [m for m in mut_done if m["ok"]]
            vis_ok_pre: object = False
            vis_detail_pre: dict = {"mut_ok": len(mut_ok_pre)}
            if mut_ok_pre:
                last = mut_ok_pre[-1]
                tok = f"onlinevane{last['i']:04d}"
                try:
                    vr = client.post(
                        "/api/query",
                        json={"query": f"{tok} live mutation",
                              "limit": 5}).json()
                    vdocs = [str(it.get("source_artifact", {}).get(
                        "document_name", ""))
                        for it in vr.get("items", [])]
                    # Citation integrity is checked on the MATCHING item, not
                    # top-1: top-1 rank under a stale backlog is a recall
                    # observation (recorded as rank/top1), while byte
                    # fidelity of what IS returned is the integrity gate.
                    # (2k run s6c_20260912-201811 falsified the old form:
                    # unique-token doc found in top-5, top-1 elsewhere.)
                    match_rank = (vdocs.index(last["name"])
                                  if last["name"] in vdocs else None)
                    match_atom_id: object = None
                    if match_rank is not None:
                        match_atom_id = (
                            ((vr.get("items") or [])[match_rank].get(
                                "atom") or {}).get("atom_id"))
                    top1doc = vdocs[0] if vdocs else ""
                    try:  # v4: served generation at probe time
                        probe_served = client.get("/api/status").json().get(
                            "served_persistent", {}).get("generation_id")
                    except Exception:  # noqa: BLE001
                        probe_served = None
                    # v4.2: exact byte-span citation on the matching item.
                    cite = (cite_exact(h.db_path, match_atom_id,
                                       last["name"])
                            if match_rank is not None
                            else {"ok": False, "reason": "not-found"})
                    vis_ok_pre = bool(cite["ok"])
                    vis_detail_pre.update(
                        {"name": last["name"],
                         "found": last["name"] in vdocs,
                         "rank": match_rank,
                         "top1": top1doc,
                         "route": vr.get("route"),
                         "vgen": vr.get("vector_generation_id"),
                         "gen": vr.get("generation_id"),
                         "served_gen": probe_served,
                         "cite": bool(cite["ok"]),
                         "cite_reason": cite["reason"]})
                except Exception as exc:  # noqa: BLE001
                    vis_detail_pre["error"] = (
                        f"{type(exc).__name__}: {str(exc)[:150]}")
            h.emit("visibility", **vis_detail_pre,
                   ok=bool(vis_ok_pre))
        finally:
            # ---- Phase 4: shutdown (bounded join) ----
            # __exit__ runs lifespan shutdown: bounded maintenance
            # join (30 s) + residual projection drain under the lock.
            t_sd0 = time.monotonic()
            try:
                client.__exit__(None, None, None)
            finally:
                shutdown_s = time.monotonic() - t_sd0
        os.environ.clear()
        os.environ.update(old_env)

        # ---- verdict ----
        ok_reqs = [r for r in req_log if r["ok"]]
        lat = [r["ms"] for r in ok_reqs]
        routes: dict[str, int] = {}
        for r in ok_reqs:
            routes[r["route"]] = routes.get(r["route"], 0) + 1
        timeouts = sum(1 for r in req_log if r["timeout"])
        errors = sum(1 for r in req_log if not r["ok"])
        mut_errs = sum(1 for m in mut_done if not m["ok"])
        mstats = mut_stats(mut_done)  # v4: mut timeouts gate the verdict
        cov = query_coverage(req_log, status_series, args.window_s,
                             args.span_frac, args.min_gens)  # v4

        ov = overlap_verdict(ok_reqs, mut_done, status_series)
        run_samples = [s["t"] for s in status_series if s["maint"] is True]
        overlap = ov["maint_overlap"]
        overlap_detail = ov["detail"]

        pre_prog = h.result["pre_timing"]["job_progress"]
        end_prog = (end_status.get("job_progress") or {})
        done_delta = (end_prog.get("done", 0) - pre_prog.get("done", 0))
        depth0 = h.result["pre_timing"]["queue_depth_jobs"]
        depth1 = (end_status.get("queue_stats") or {}).get("depth")
        progress = done_delta > 0 or (
            depth1 is not None and depth1 < depth0)

        gen_bad = [r for r in ok_reqs
                   if r["route"] == "vector" and r["vgen"] != r["gen"]]

        # retrieval: planted anchors hit + EXACT byte-span citation
        # (v4.2: served atom bytes == current revision bytes at the
        # recorded span, checked on the matching item with rank recorded).
        anchor_qbase = 2 * len(TOPICS)
        anchor_hits = 0
        anchor_cite_ok = 0
        anchor_ranks: list = []
        for i in range(args.planted):
            want = f"anchor-c-{i:02d}.md"
            cands = [r for r in ok_reqs
                     if r.get("qidx") == anchor_qbase + i
                     and want in (r.get("docs") or [])]
            if not cands:
                continue
            anchor_hits += 1
            # matching item (rank recorded): top-1 rank under a stale
            # backlog is a recall observation, not a citation verdict.
            match = next(
                (t for t in (cands[0].get("tops") or [])
                 if t.get("doc") == want), None)
            if match is None:
                continue
            anchor_ranks.append(
                (cands[0].get("tops") or []).index(match))
            if cite_exact(h.db_path, match.get("atom_id"),
                          want)["ok"]:
                anchor_cite_ok += 1
        retrieval_ok = (anchor_hits == args.planted
                        and anchor_cite_ok == args.planted)

        # visibility verdict reuses the in-app probe taken pre-shutdown
        mut_ok = [m for m in mut_done if m["ok"]]
        vis_ok = bool(vis_ok_pre)
        vis_detail = dict(vis_detail_pre)
        h.emit("retrieval", anchor_hits=anchor_hits,
               anchor_cite_ok=anchor_cite_ok, anchor_ranks=anchor_ranks,
               visibility=vis_detail)

        disk = (end_status.get("served_persistent") or {}).get("disk") or {}
        disk_ok = bool(disk.get("within_budget", False))
        servable_end = bool((end_status.get("served_persistent") or {}).get(
            "servable"))
        post_counts = _counts(h.db_path)

        pred = {
            "overlap_proven": overlap,
            "backlog_progress": progress,
            "latency_ok": (len(lat) > 0
                           and _pct(lat, 0.95) <= LATENCY_PREDICATE_P95_MS),
            "retrieval_ok": retrieval_ok,
            "generation_ok": len(gen_bad) == 0 and len(ok_reqs) > 0,
            "visibility_ok": vis_ok,
            "servable_at_end": servable_end,
            "disk_ok": disk_ok,
            "shutdown_ok": shutdown_s <= SHUTDOWN_JOIN_S,
            "errors_zero": (errors == 0 and mut_errs == 0
                            and not hung),
            "timeout_ok": (timeouts == 0
                           and mstats["mut_timeouts"] == 0),
            "span_ok": cov["span_ok"],
            "gens_observed": cov["gens_observed"],
        }
        h.result.update({
            "observation_s": round(obs_s, 2),
            "queries": len(req_log), "queries_ok": len(ok_reqs),
            "query_errors": errors, "query_timeouts": timeouts,
            "mut_requests": len(mut_done), "mut_ok": len(mut_ok),
            "mut_errors": mut_errs,
            "mut_p50_ms": mstats["mut_p50_ms"],
            "mut_p95_ms": mstats["mut_p95_ms"],
            "mut_max_ms": mstats["mut_max_ms"],
            "mut_timeouts": mstats["mut_timeouts"],
            "query_span_s": cov["query_span_s"],
            "distinct_gens": cov["distinct_gens"],
            "vgen_differs_from_served": cov["vgen_differs_from_served"],
            "vgen_compared": cov["vgen_compared"],
            "lat_p50_ms": round(_pct(lat, 0.50), 3),
            "lat_p95_ms": round(_pct(lat, 0.95), 3),
            "lat_p99_ms": round(_pct(lat, 0.99), 3),
            "lat_max_ms": round(max(lat), 3) if lat else 0.0,
            "routes": routes,
            "overlap": overlap_detail,
            "jobs_done_delta": done_delta,
            "queue_depth_first": depth0, "queue_depth_last": depth1,
            "gen_mismatches": len(gen_bad),
            "anchor_hits": anchor_hits, "anchor_cite_ok": anchor_cite_ok,
            "anchor_ranks": anchor_ranks,
            "visibility": vis_ok,
            "shutdown_s": round(shutdown_s, 3),
            "post_counts": post_counts,
            "served_gen_first": (status_series[0].get("served_gen")
                                 if status_series else None),
            "served_gen_last": (status_series[-1].get("served_gen")
                                if status_series else None),
            "catchup_receipts": [s.get("receipt") for s in status_series
                                 if s.get("receipt")],
            "status_series": status_series,
            "threads_hung": hung,
            "predicates": pred,
        })
        h.result["ok"] = all(pred.values())
        (h.run_dir / "result.json").write_text(
            json.dumps(h.result, indent=2), encoding="utf-8")
        print(json.dumps({"run": h.run_dir.name, "ok": h.result["ok"],
                          **{k: h.result[k] for k in
                             ("queries", "queries_ok", "lat_p50_ms",
                              "lat_p95_ms", "lat_max_ms", "routes",
                              "mut_max_ms", "mut_timeouts",
                              "query_span_s", "distinct_gens",
                              "jobs_done_delta", "queue_depth_first",
                              "queue_depth_last", "shutdown_s")},
                          "predicates": pred}, indent=2))
        return 0 if h.result["ok"] else 1
    except Exception as exc:  # noqa: BLE001 - partial evidence must survive
        h.result["failure"] = {
            "type": type(exc).__name__, "message": str(exc)[:500],
            "traceback": traceback.format_exc()[-3000:]}
        h.result["ok"] = False
        h.emit("harness_failure", **h.result["failure"])
        (h.run_dir / "failure.json").write_text(
            json.dumps(h.result, indent=2), encoding="utf-8")
        print(json.dumps({"run": h.run_dir.name, "ok": False,
                          "failure": h.result["failure"]}, indent=2))
        try:
            os.environ.clear()
            os.environ.update(old_env)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
