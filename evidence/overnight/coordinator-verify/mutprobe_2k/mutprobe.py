"""Coordinator mutation probe vs the 2k regime (corrective pass, critique 2).

Reopens a COPY of the qualified 2k run artifact (serve.db + served/) and
issues a few online POST /api/ingest mutations with cProfile attribution,
separating: HTTP total, sync projection drain, publish txn, facet repair,
publication, queue wait. Read-only w.r.t. the original run dir.

Usage: .venv/bin/python evidence/overnight/coordinator-verify/mutprobe_2k/mutprobe.py
Writes: mutprobe.json + console summary next to this script.
"""
from __future__ import annotations

import cProfile
import json
import os
import pstats
import sys
import time
from io import StringIO
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parent
REPO = RUN_DIR.parents[3]
sys.path.insert(0, str(REPO / "src"))

os.environ["TRACE_LITE_SERVED_PERSISTENT"] = "1"
os.environ["TRACE_LITE_SERVED_INDEX_DIR"] = str(RUN_DIR / "served")
os.environ.pop("TRACE_LITE_WORKER", None)

from fastapi.testclient import TestClient  # noqa: E402

from trace_lite.api import create_app  # noqa: E402

WANT = ["ingest_note", "drain_outbox", "publish_filing_generation",
        "_refresh_state", "_repair_facet_state", "request_maintenance",
        "served_after_write", "ensure_grouped", "add_members",
        "text_vector", "embed", "centroid", "reseed",
        "sqlite3", "execute"]


def top_functions(prof, names, limit=16):
    sio = StringIO()
    ps = pstats.Stats(prof, stream=sio).sort_stats("cumulative")
    rows = []
    for func, (cc, nc, tt, ct, callers) in ps.stats.items():
        label = f"{func[0].split('/')[-1]}:{func[1]}:{func[2]}"
        rows.append((ct, tt, cc, label))
    rows.sort(reverse=True)
    picked = []
    for ct, tt, cc, label in rows:
        if any(w in label for w in names):
            picked.append({"fn": label, "cum_s": round(ct, 3),
                           "self_s": round(tt, 3), "calls": cc})
        if len(picked) >= limit:
            break
    return picked, [{"fn": l, "cum_s": round(c, 3)} for c, t, n, l in rows[:8]]


def main() -> None:
    client = TestClient(create_app(RUN_DIR / "serve.db"))
    client.__enter__()
    try:
        st0 = client.get("/api/status").json()
        q0 = (st0.get("queue_stats") or {})
        out = {"before": {"depth": q0.get("depth"),
                          "lag_s": q0.get("queue_lag_s")},
               "mutations": []}
        for i in range(5):
            name = f"mutprobe-{i}.md"
            tok = f"probevane{i:04d}"
            text = (f"# Probe {i}\n\n{tok} mutation probe under backlog "
                    f"{tok} measures write path.\n")
            prof = cProfile.Profile()
            t0 = time.monotonic()
            prof.enable()
            r = client.post("/api/ingest",
                            json={"document_name": name, "text": text})
            prof.disable()
            dt_ms = (time.monotonic() - t0) * 1000
            body = r.json()
            tq0 = time.monotonic()
            q = client.post("/api/query",
                            json={"query": f"{tok} probe", "limit": 5})
            q_ms = (time.monotonic() - tq0) * 1000
            qdocs = [str(it.get("source_artifact", {}).get(
                "document_name", "")) for it in q.json().get("items", [])]
            picked, top = top_functions(prof, WANT)
            out["mutations"].append({
                "name": name, "http_ms": round(dt_ms, 1),
                "ok": body.get("ok") is True,
                "query_ms": round(q_ms, 1),
                "visible_top5": name in qdocs,
                "attribution": picked, "top_cumulative": top,
            })
            print(f"{name}: http={dt_ms:.0f}ms ok={body.get('ok')} "
                  f"q={q_ms:.0f}ms visible={name in qdocs}", flush=True)
        st1 = client.get("/api/status").json()
        q1 = (st1.get("queue_stats") or {})
        out["after"] = {"depth": q1.get("depth"), "lag_s": q1.get("lag_s")}
        (RUN_DIR / "mutprobe.json").write_text(
            json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps({k: out[k] for k in ("before", "after")}))
    finally:
        client.__exit__(None, None, None)


if __name__ == "__main__":
    main()
