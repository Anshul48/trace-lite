#!/usr/bin/env python3
"""S6 release-envelope runner: native rungs with persistent serving cut over.

Drives the REAL served path (``TestClient`` app + real DB files, gate
``TRACE_LITE_SERVED_PERSISTENT=1``) at 2k then 10k sources. Each rung
records ingest rate, edit/delete cost, freshness, queue drain + lag,
background-seed time/encodes, cold open (0 encodes), first-query and
warm p50/p95/p99 latency, planted-anchor hit rate, edit-overlay cost,
restart recovery, and the disk-budget verdict — then writes one JSON
row per rung. 10k is admitted only when the 2k rung is ok; 50k is NOT
attempted here (admission needs 10k curves + headroom, decided from
the recorded numbers, never assumed).

DBs live under an explicit root (/tmp/s6_env_<stamp>/), never the repo.
Usage:
  .venv/bin/python benchmarks/s6_envelope.py --rungs 2000,10000 \\
      --output evidence/query-native/_integration/S6
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from trace_lite.api import create_app  # noqa: E402
from trace_lite.api.app import (  # noqa: E402
    check_disk_budget,
    filing_watermark,
    freshness,
    publish_filing_generation,
)
from trace_lite.filing import FilingEngine, Taxonomy  # noqa: E402
from trace_lite.filing.workers import MaintenanceWorker, WorkerConfig  # noqa: E402
from trace_lite.router import ensure_served_persistent  # noqa: E402
from trace_lite.router.cascade import CascadeRouter  # noqa: E402
from trace_lite.router.vector_store import (  # noqa: E402
    EmbeddingCache,
    ServedVectors,
)
from trace_lite.source import service as svc  # noqa: E402
from trace_lite.store import Database, outbox as outbox_store  # noqa: E402

TOPICS = [
    "batching throughput contention", "wal concurrency readers",
    "lexical dense fusion", "generation pinning snapshots",
    "projection drain backpressure", "holon membership grouping",
    "span anchoring offsets", "idempotency replay keys",
    "compaction delta bounds", "retrieval latency budgets",
]
POOL = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
        "kilo lima mike november oscar papa quebec romeo sierra tango "
        "uniform victor whiskey xray yankee zulu").split()


def _doc(i: int) -> tuple[str, str]:
    topic = TOPICS[i % len(TOPICS)]
    w = " ".join(POOL[(i * 7 + j * 11) % len(POOL)] for j in range(12))
    return (f"doc-{i:05d}.md",
            f"# Note {i}\n\n{topic} under load: {w} measurements {i}.\n")


def _planted(i: int) -> tuple[str, str]:
    tok = f"zephyrnine{i:02d}"
    return (f"anchor-{i:02d}.md",
            f"# Anchor {i}\n\n{tok} quorum latch {tok} holds the line.\n")


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _db_files(db_path: Path) -> dict:
    out = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(db_path) + suffix)
        try:
            out[p.name] = p.stat().st_size
        except OSError:
            continue
    return out


def _wait_status(client, pred, timeout_s: float, what: str) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get("/api/status").json()
        if pred(last):
            return last
        time.sleep(0.5)
    raise TimeoutError(f"s6 envelope: timed out waiting for {what}: "
                       f"{json.dumps(last.get('served_persistent', {}))[:400]}")


def run_rung(n: int, root: Path, log) -> dict:
    from fastapi.testclient import TestClient

    rung_dir = root / f"rung_{n}"
    rung_dir.mkdir(parents=True, exist_ok=True)
    db_path = rung_dir / "env.db"
    index_dir = rung_dir / "served"
    t_rung = time.monotonic()
    row: dict[str, object] = {"n": n}

    # -- ingest (in-process publish; served drain happens below) --
    db = Database(db_path)
    try:
        docs = [_doc(i) for i in range(max(0, n - 10))]
        planted = [_planted(i) for i in range(min(10, n))]
        t0 = time.monotonic()
        for name, text in docs + planted:
            res = svc.publish(db, name, text)
            assert res.created is True
        ingest_s = time.monotonic() - t0
        row.update({"ingest_s": round(ingest_s, 2),
                    "ingest_per_s": round(n / max(ingest_s, 1e-9), 1),
                    "db_files_post_ingest": _db_files(db_path)})
        log(f"[rung {n}] ingested {n} in {ingest_s:.1f}s")

        # -- edit + retract --
        t0 = time.monotonic()
        svc.publish(db, docs[0][0], docs[0][1] + "\nEdited line.\n")
        svc.retract(db, docs[1][0])
        row["edit_delete_s"] = round(time.monotonic() - t0, 2)

        # -- freshness + queue state before drain --
        t0 = time.monotonic()
        fresh = freshness(db.conn)
        row.update({"freshness_ms": round((time.monotonic() - t0) * 1000, 1),
                    "freshness_pending": fresh["pending_jobs"]})
        stats = outbox_store.queue_stats(db.conn)
        row.update({"queue_depth": stats["depth"],
                    "queue_lag_s": stats["queue_lag_s"]})

        # -- finite backfill drain (S3 primitive), then generation --
        worker = MaintenanceWorker(
            db=db, config=WorkerConfig(owner="s6-envelope"))
        try:
            t0 = time.monotonic()
            final = worker.run_until_drained()
        finally:
            worker.close()
        row.update({"drain_s": round(time.monotonic() - t0, 2),
                    "drained_all": bool(final["drained_all"])})
        gen_id = publish_filing_generation(db.conn)
    finally:
        db.close()
    log(f"[rung {n}] drained={row['drained_all']} gen={gen_id}")

    # -- served app with the cutover gate on --
    old = dict(os.environ)
    os.environ["TRACE_LITE_SERVED_PERSISTENT"] = "1"
    os.environ["TRACE_LITE_SERVED_INDEX_DIR"] = str(index_dir)
    try:
        with TestClient(create_app(db_path)) as client:
            t_app = time.monotonic()
            st = _wait_status(
                client,
                lambda s: (s.get("served_persistent", {}).get("seed", {})
                           .get("state") == "done")
                and s.get("served_persistent", {}).get("servable") is True,
                timeout_s=900.0, what="background seed servable")
            block = st["served_persistent"]
            row.update({
                "time_to_servable_s": round(time.monotonic() - t_app, 2),
                "seed_s": None,  # app owns the seed thread; wall time above
                "seed_encodes": block["seed"]["receipt"]["cache"]["encodes"],
                "seed_atoms": block["seed"]["receipt"]["atoms"],
            })
            log(f"[rung {n}] servable atoms={row['seed_atoms']} "
                f"encodes={row['seed_encodes']}")

            # -- cold open at the layer: 0 encodes by construction --
            t0 = time.monotonic()
            cache = EmbeddingCache()
            snap = ServedVectors.open(index_dir, cache=cache)
            cold_ms = (time.monotonic() - t0) * 1000
            assert snap.servable
            enc0 = cache.stats()["encodes"]
            rdb = Database(db_path)
            try:
                router = CascadeRouter(
                    rdb.conn, FilingEngine(rdb.conn, Taxonomy(rdb.conn)))
                ensure_served_persistent(router, snap, gen_id)
            finally:
                rdb.close()
            row.update({"cold_open_ms": round(cold_ms, 2),
                        "open_encodes": cache.stats()["encodes"] - enc0})

            # -- first + warm queries over HTTP --
            def _q(text: str):
                t = time.monotonic()
                r = client.post("/api/query",
                                json={"query": text, "limit": 5})
                assert r.status_code == 200, r.text
                return r.json(), (time.monotonic() - t) * 1000

            first, first_ms = _q(f"{TOPICS[0]} zephyrnine00")
            row.update({"first_query_ms": round(first_ms, 2),
                        "first_query_route": first["route"]})
            queries = ([f"how does {t} behave under load" for t in TOPICS]
                       + [f"{t} measurements" for t in TOPICS]
                       + [f"zephyrnine{i:02d} quorum latch" for i in range(10)])
            lat, pins, pins_tot, hits = [], 0, 0, 0
            for qi, qtext in enumerate(queries):
                body, ms = _q(qtext)
                lat.append(ms)
                if body["route"] == "vector":
                    pins_tot += 1
                    pins += (body["vector_generation_id"]
                             == body["generation_id"])
                if qi >= len(queries) - 10:
                    want = f"anchor-{qi - (len(queries) - 10):02d}.md"
                    names = [it["source_artifact"]["document_name"]
                             for it in body["items"][:5]]
                    hits += want in names
            row.update({"warm_p50_ms": round(_pct(lat, 0.50), 3),
                        "warm_p95_ms": round(_pct(lat, 0.95), 3),
                        "warm_p99_ms": round(_pct(lat, 0.99), 3),
                        "pin_match_rate": (pins / pins_tot) if pins_tot else None,
                        "planted_hit_rate": round(hits / 10, 3)})
            log(f"[rung {n}] p50={row['warm_p50_ms']}ms "
                f"p95={row['warm_p95_ms']}ms hit={row['planted_hit_rate']}")

            # -- edit overlay through the served catch-up --
            base_catchup = dict(block["catchup"].get("receipt") or {})
            r = client.post("/api/ingest", json={
                "document_name": "doc-overlay.md",
                "text": "# Overlay\n\nOverlay quorum delta8361 holds.\n"})
            assert r.status_code == 200, r.text
            st2 = _wait_status(
                client,
                lambda s: (s.get("served_persistent", {}).get("catchup", {})
                           .get("state") == "done")
                and (s.get("served_persistent", {}).get("catchup", {})
                     .get("receipt") or {}).get("new", 0) >= 1,
                timeout_s=600.0, what="catch-up overlay")
            receipt = (st2["served_persistent"]["catchup"]["receipt"]
                       or {})
            row.update({"edit_overlay_encodes": receipt.get("encoded", 0),
                        "edit_overlay_delta": receipt.get("delta_size"),
                        "edit_overlay_new": receipt.get("new")})

            # -- disk verdict on the live footprint --
            disk = check_disk_budget(index_dir, db_path,
                                     served=None, budget_bytes=None)
            row["disk"] = disk
            row["disk_check"] = dict(disk, within_budget=bool(
                disk["within_budget"]))
            row["footprint_bytes"] = disk["footprint_bytes"]

        # -- restart: fresh app, same DB+index --
        with TestClient(create_app(db_path)) as client2:
            st3 = _wait_status(
                client2,
                lambda s: bool(s.get("served_persistent")),
                timeout_s=120.0, what="restart status")
            r = client2.post("/api/query", json={
                "query": f"{TOPICS[1]} measurements", "limit": 5})
            assert r.status_code == 200, r.text
            q = r.json()
            row.update({"restart_route": q["route"],
                        "restart_servable": bool(
                            st3["served_persistent"]["servable"])})
    finally:
        os.environ.clear()
        os.environ.update(old)

    row.update({"ok": bool(
        row.get("drained_all")
        and row.get("restart_servable")
        and (row.get("open_encodes") == 0)
        and bool(row.get("disk_check", {}).get("within_budget")))})
    row["rung_s"] = round(time.monotonic() - t_rung, 1)
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S6 release-envelope runner")
    ap.add_argument("--rungs", default="2000,10000")
    ap.add_argument("--output", type=Path,
                    default=REPO / "evidence" / "query-native"
                    / "_integration" / "S6")
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    root = args.root or Path(f"/tmp/s6_env_{stamp}")
    root.mkdir(parents=True, exist_ok=True)
    run_log = out / "envelope_run.log"
    data_path = out / "envelope.json"

    def log(msg: str):
        line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(run_log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _git() -> str:
        try:
            return subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(REPO),
                capture_output=True, text=True,
                timeout=30).stdout.strip()
        except Exception:
            return "unknown"

    def _df() -> str:
        try:
            return subprocess.run(
                ["df", "-h", "/tmp"], capture_output=True, text=True,
                timeout=30).stdout
        except Exception:
            return ""

    data: dict = {"meta": {"git": _git(), "df_tmp": _df(),
                           "started": stamp}, "rungs": {}}
    if data_path.exists():
        try:
            prior = json.loads(data_path.read_text(encoding="utf-8"))
            if isinstance(prior, dict) and "rungs" in prior:
                data["meta"]["prior_superseded"] = (
                    "envelope_prior_partial.json")
                (out / "envelope_prior_partial.json").write_text(
                    json.dumps(prior, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    log(f"S6 envelope start head={data['meta']['git']}")

    ok_all = True
    for token in args.rungs.split(","):
        n = int(token.strip())
        if n > 2000 and not data["rungs"].get("2000", {}).get("ok"):
            log(f"--- rung n={n} REFUSED: 2k rung not ok (admission) ---")
            ok_all = False
            continue
        log(f"--- rung n={n} ---")
        try:
            row = run_rung(n, root, log)
        except Exception as exc:  # noqa: BLE001 - evidence must survive
            log(f"rung n={n} FAILED: {type(exc).__name__}: {str(exc)[:300]}")
            row = {"n": n, "ok": False,
                   "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            ok_all = False
        data["rungs"][str(n)] = row
        data_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log(f"rung n={n} ok={row.get('ok')} "
            f"rung_s={row.get('rung_s')} "
            f"ingest/s={row.get('ingest_per_s')} "
            f"p50={row.get('warm_p50_ms')}ms "
            f"hit={row.get('planted_hit_rate')} "
            f"foot={row.get('footprint_bytes')}")
        if row.get("ok") is not True:
            ok_all = False
    # 50k admission decided from numbers, never assumed.
    r10 = data["rungs"].get("10000", {})
    if r10.get("ok") is True:
        data["admission_50k"] = {
            "admitted": False,
            "reason": ("10k ok; 50k needs a provisioned bench window: "
                       "refused by policy here, not by measurement."),
        }
    else:
        data["admission_50k"] = {
            "admitted": False,
            "reason": "10k rung not ok; ladder halts per steering line 89.",
        }
    data_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
