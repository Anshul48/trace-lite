"""Backlog-depth ladder: separate corpus scaling from backlog coupling.

For each target backlog depth D in (0, 100, 300): fresh small-corpus DB
(200 docs, library-direct bulk ingest equivalent), then publish D backlog
docs library-direct WITHOUT draining, boot the served app, and time ONE
instrumented HTTP ingest (wall ms) plus depth before/after and immediate
lexical visibility. The mutation's own per-note cost is constant; any
growth with D is the synchronous drain coupling, isolated by construction.

Usage: .venv/bin/python evidence/overnight/coordinator-verify/mutprobe_2k/depth_ladder.py
Writes: depth_ladder.json next to this script. Bounded: <= ~5 min total.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parent
REPO = RUN_DIR.parents[3]
sys.path.insert(0, str(REPO / "src"))

os.environ["TRACE_LITE_SERVED_PERSISTENT"] = "1"
os.environ.pop("TRACE_LITE_WORKER", None)

from fastapi.testclient import TestClient  # noqa: E402

from trace_lite.api import create_app  # noqa: E402
from trace_lite.source import service as svc  # noqa: E402
from trace_lite.store import Database  # noqa: E402
from trace_lite.store import outbox as outbox_store  # noqa: E402


def build_corpus(db_path: Path, n: int) -> None:
    db = Database(db_path)
    try:
        for i in range(n):
            svc.publish(db, f"base-{i:04d}.md",
                        f"# Base {i}\n\nladder corpus document {i} with "
                        f"stable filler text quorum latch holds.\n")
    finally:
        db.close()


def backlog(db_path: Path, n: int) -> None:
    db = Database(db_path)
    try:
        for i in range(n):
            svc.publish(db, f"lag-{i:04d}.md",
                        f"# Lag {i}\n\nbacklog document {i} awaiting "
                        f"projection drain quorum latch holds.\n")
    finally:
        db.close()


def depth_of(db_path: Path) -> int:
    db = Database(db_path)
    try:
        return int(outbox_store.queue_stats(db.conn).get("depth", -1))
    finally:
        db.close()


def main() -> None:
    rows = []
    for depth in (0, 100, 300):
        work = Path(tempfile.mkdtemp(prefix=f"ladder-d{depth}-"))
        dbp = work / "serve.db"
        idx = work / "served"
        # Fixed 200-doc corpus built THROUGH the app (each ingest drains,
        # so the corpus carries ~zero backlog); backlog added after.
        os.environ["TRACE_LITE_SERVED_INDEX_DIR"] = str(idx)
        boot = TestClient(create_app(dbp))
        boot.__enter__()
        try:
            for i in range(200):
                boot.post("/api/ingest",
                          json={"document_name": f"base-{i:04d}.md",
                                "text": f"# Base {i}\n\nladder corpus "
                                        f"document {i} filler quorum.\n"})
        finally:
            boot.__exit__(None, None, None)
        backlog(dbp, depth)
        d0 = depth_of(dbp)
        os.environ["TRACE_LITE_SERVED_INDEX_DIR"] = str(idx)
        client = TestClient(create_app(dbp))
        client.__enter__()
        try:
            t0 = time.monotonic()
            r = client.post("/api/ingest",
                            json={"document_name": "ladder-probe.md",
                                  "text": "# Ladder probe\n\nprobevane0001 "
                                          "ladder probe under backlog.\n"})
            dt_ms = (time.monotonic() - t0) * 1000
            q = client.post("/api/query",
                            json={"query": "probevane0001 ladder",
                                  "limit": 5}).json()
            vis = any("ladder-probe.md" in str(
                it.get("source_artifact", {}).get("document_name", ""))
                for it in q.get("items", []))
        finally:
            client.__exit__(None, None, None)
        d1 = depth_of(dbp)
        rows.append({"target_depth": depth, "depth_before": d0,
                     "mut_ms": round(dt_ms, 1), "ok": r.json().get("ok"),
                     "visible": vis, "depth_after": d1,
                     "drained_in_request": d0 - d1})
        print(rows[-1], flush=True)
        shutil.rmtree(work, ignore_errors=True)
    (RUN_DIR / "depth_ladder.json").write_text(
        json.dumps({"rows": rows}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
