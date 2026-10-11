#!/usr/bin/env python3
"""S6 serve-under-drain probe: queries stay fast while the backlog drains.

Bounded window (default 600s) on a DB with a large pending projection
queue: one thread alternates a bounded worker batch with served HTTP
queries through the gated app. Proves (or refutes) the S3/S4
non-blocking claim at a scale whose full drain is NOT admitted:
every query must answer fast with honest routes while depth/lag move.

Usage:
  .venv/bin/python benchmarks/s6_serve_under_drain.py \\
      --db /tmp/s6_env_.../rung_10000/env.db \\
      --index-dir /tmp/s6_env_.../rung_10000/served \\
      --output evidence/query-native/_integration/S6/under_drain_10k.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from trace_lite.api import create_app  # noqa: E402
from trace_lite.api.app import check_disk_budget  # noqa: E402
from trace_lite.filing.workers import MaintenanceWorker, WorkerConfig  # noqa: E402
from trace_lite.store import Database  # noqa: E402

QUERIES = ([f"how does {t} behave under load" for t in
            ["batching throughput contention", "wal concurrency readers",
             "lexical dense fusion", "generation pinning snapshots",
             "projection drain backpressure"]]
           + ["zephyrnine00 quorum latch", "zephyrnine05 quorum latch"])


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--index-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--window-s", type=float, default=600.0)
    ap.add_argument("--batch-limit", type=int, default=16)
    args = ap.parse_args(argv)

    row: dict = {"db": str(args.db), "window_s": args.window_s,
                 "started": datetime.now(timezone.utc).isoformat()}
    old = dict(os.environ)
    os.environ["TRACE_LITE_SERVED_PERSISTENT"] = "1"
    os.environ["TRACE_LITE_SERVED_INDEX_DIR"] = str(args.index_dir)
    try:
        from fastapi.testclient import TestClient

        lat: list[float] = []
        routes: dict[str, int] = {}
        lag_samples: list = []
        depth_samples: list = []
        ticks = 0
        drained_total = 0
        with TestClient(create_app(args.db)) as client:
            deadline = time.monotonic() + 900.0
            while time.monotonic() < deadline:
                st = client.get("/api/status").json()
                sp = st.get("served_persistent", {})
                if (sp.get("seed", {}).get("state") == "done"
                        and sp.get("servable") is True):
                    break
                time.sleep(1.0)
            else:
                raise TimeoutError("seed never servable")
            row["seed_atoms"] = sp["seed"]["receipt"]["atoms"]
            row["seed_encodes"] = sp["seed"]["receipt"]["cache"]["encodes"]
            n_sources = client.get("/api/status").json()["atoms"]

            db = Database(args.db)
            try:
                worker = MaintenanceWorker(
                    db=db, config=WorkerConfig(owner="s6-under-drain",
                                               batch_limit=args.batch_limit))
                try:
                    t_end = time.monotonic() + args.window_s
                    qi = 0
                    while time.monotonic() < t_end:
                        rep = worker.run_once()
                        drained_total += int(rep["drained"])
                        ticks += 1
                        for _ in range(6):
                            q = QUERIES[qi % len(QUERIES)]
                            qi += 1
                            t0 = time.monotonic()
                            r = client.post("/api/query", json={
                                "query": q, "limit": 5})
                            dt = (time.monotonic() - t0) * 1000
                            assert r.status_code == 200, r.text
                            body = r.json()
                            lat.append(dt)
                            routes[body["route"]] = routes.get(
                                body["route"], 0) + 1
                        st = client.get("/api/status").json()
                        lag_samples.append(
                            st["served_persistent"]["lag"]["queue_lag_s"])
                        depth_samples.append(st["queue_stats"]["depth"])
                finally:
                    worker.close()
            finally:
                db.close()
            row["disk"] = check_disk_budget(
                args.index_dir, args.db, served=None)
            end = client.get("/api/status").json()
            row["servable_at_end"] = bool(
                end.get("served_persistent", {}).get("servable"))
    finally:
        os.environ.clear()
        os.environ.update(old)

    row.update({
        "sources": n_sources, "ticks": ticks, "queries": len(lat),
        "drained_during_window": drained_total,
        "lat_p50_ms": round(_pct(lat, 0.50), 3),
        "lat_p95_ms": round(_pct(lat, 0.95), 3),
        "lat_p99_ms": round(_pct(lat, 0.99), 3),
        "lat_max_ms": round(max(lat), 3) if lat else 0.0,
        "routes": routes,
        "lag_first_s": lag_samples[0] if lag_samples else None,
        "lag_last_s": lag_samples[-1] if lag_samples else None,
        "depth_first": depth_samples[0] if depth_samples else None,
        "depth_last": depth_samples[-1] if depth_samples else None,
        "disk_within_budget": bool(row["disk"]["within_budget"]),
        "ok": bool(row.get("servable_at_end")
                   and row["disk"]["within_budget"] and lat),
    })
    args.output.write_text(json.dumps(row, indent=2), encoding="utf-8")
    print(json.dumps({k: row[k] for k in
                      ("sources", "ticks", "queries", "lat_p50_ms",
                       "lat_p95_ms", "lat_max_ms", "routes",
                       "depth_first", "depth_last", "lag_first_s",
                       "lag_last_s", "ok")}, indent=2))
    return 0 if row["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
