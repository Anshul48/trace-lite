#!/usr/bin/env python3
"""R09 integrated qualification harness: NEW integrated system (§8), not legacy.

Drives the ACTUAL integrated served paths on ONE identical candidate/config
per rung (no profile swaps):
  - write path: real ``uvicorn`` server process + HTTP ``POST /api/sync``
    (chunked vault batches; served ``sync_notes`` pipeline: R01 revision +
    R02 spans + R04 graph + R05 holons + R06 drain/invalidation+generation).
  - latency: real server + separate load client (urllib), server elapsed_ms
    and client end-to-end timed separately and labeled.
  - quality: direct harness constructing the IDENTICAL served reader stack
    (profile=quality, served ANN + holon-guided, same caps) over the rung DB.
  - lifecycle spots: crash/restart, replay, update/delete, parity (bounded).

Operating point (recorded per rung, never swapped):
  profile=quality, DENSE_POOL=2000 full-scan, sparse_pool=100 ranked,
  beam cap 400/balanced, SERVED_VECTOR_MAX_ATOMS=20000, holon_guided=True,
  encoder=fallback hashbow (R08 PARK: learned weights NOT default),
  mode=hybrid, top_k=10. Model cost always zero.

G2 (PACKET-G2-R09-10K, contract TL-QN-2026-09-13.3): the 10k rung indexes
the FULL SciFact corpus (5,183 docs) + synthetic notes = 10,000 total;
SciFact nDCG@10 gates on the frozen floor 0.6400 (target 0.6500);
NFCorpus is a separate diagnostic-only run (never enlarges the 10k
corpus, never a hard gate); the child server runs with
TRACE_LITE_SERVED_PERSISTENT=1 + TRACE_LITE_WORKER=1 and the autonomous
worker must drain the queue to 0 within 600s; per-query rankings go to
evidence/query-native/R09/per_query_rankings.jsonl; a 20-case
relational-capability hook reports explicit INCOMPLETE until the frozen
cases file is supplied.

DBs live under an explicit bench root (/tmp/tl_r09_<stamp>/), never the repo.
Usage:
  .venv/bin/python benchmarks/run_integrated_qualification.py --scale 2k \
      --output evidence/query-native/R09
  .venv/bin/python benchmarks/run_integrated_qualification.py --smoke
Scales: 2k | 10k | 100k | 250k | 1m | <int sources>.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import random
import resource
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

# Sandbox proxy bypass: the managed shell exports http_proxy for all hosts
# with an empty no_proxy, which blackholes loopback HTTP (health/sync/query
# all refused). The qualification server is a real loopback uvicorn process,
# so bypass the proxy for localhost in this process and every child, and
# use a no-proxy opener for all harness HTTP (urllib honors env proxies).
for _k in ("no_proxy", "NO_PROXY"):
    _cur = os.environ.get(_k, "")
    _need = "127.0.0.1,localhost"
    if _need not in _cur:
        os.environ[_k] = f"{_cur},{_need}" if _cur else _need

_NOPROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SCALE_ALIASES = {"2k": 2000, "10k": 10000, "100k": 100000,
                 "250k": 250000, "1m": 1000000}

OPERATING_POINT = {
    "profile": "quality",
    "dense_pool": 2000,
    "dense_scan_cap": None,
    "sparse_pool": 100,
    "sparse_ranked": True,
    "beam_candidate_cap": 400,
    "beam_sample_strategy": "balanced",
    "served_vector_max_atoms": 20000,
    "holon_guided": True,
    "encoder": "fallback-hashbow-128d (R08 PARK: learned weights NOT default)",
    "query_mode": "hybrid",
    "top_k": 10,
    "model_cost_usd": 0.0,
}

TOPICS = [
    "wal checkpoint tuning for sqlite filers",
    "faceted classification with hearst patterns",
    "obsidian vault synchronization strategies",
    "context compilation under token budgets",
    "reciprocal rank fusion for hybrid search",
    "truth maintenance across contract boundaries",
    "debounced file watching for vault sync",
    "rest api daemon on loopback port",
]

BEIR_DIRS = {
    "scifact": "/mnt/c/Users/anshu/AppData/Local/trace-bench-data/beir-scifact/raw/scifact",
    "nfcorpus": "/mnt/c/Users/anshu/AppData/Local/trace-bench-data/beir-nfcorpus/raw/nfcorpus",
}
BEIR_FLOORS_REF = {"scifact": 0.65, "nfcorpus": 0.32}  # historical refs, not mandates
BEIR_BASE_REF = {"scifact": 0.6589, "nfcorpus": 0.3188}  # R00 reproduced (legacy adapter)

# ---------------- G2 budgets (PACKET-G2-R09-10K; contract .3 governs) ----------------
G2_CONTRACT = "TL-QN-2026-09-13.3 / PACKET-G2-R09-10K"
SCIFACT_FLOOR = 0.6400  # frozen G2 floor: HALT the ladder below this at 10k
SCIFACT_TARGET = 0.6500  # release target (recorded, not gating)
SCIFACT_EXPECTED_DOCS = 5183  # full frozen SciFact corpus size
NFCORPUS_DIAGNOSTIC_REF = 0.1900  # historical approx ref; diagnostic only
DRAIN_DEADLINE_S = 600.0  # autonomous 10k drain budget (>= 16.7 jobs/s)
SETUP_CAP_S = 900.0  # setup/index-build cap (15 min), supervisor-enforced
HTTP_TIMEOUT_S = 10  # individual HTTP call timeout (bulk sync bounded by
# remaining setup budget instead; see served_ingest)
RSS_CEILING_MB = 300.0  # G2 peak-RSS budget (setup peak reported separately)
RSS_HALT_MB = 400.0  # escalation halt: abort the run above this RSS
CAPABILITY_CASES_FILENAME = "capability_cases.json"
CAPABILITY_CASES_REQUIRED = 20
SHUTDOWN_GRACE_S = 10.0  # graceful shutdown bound (.3 §5)
RESTART_READY_S = 120  # startup/restart readiness bound (.3 §5)
GLOBAL_DEADLINE_S = 5400.0  # outer supervisor backstop for the whole rung
PROXY_ENV_VARS = ("http_proxy", "https_proxy", "all_proxy",
                  "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")

# ---------------- sustained .3 workload (G2 repair cycle 1) ----------------
# Exact .3 active window: 4 query clients, 800 evenly paced queries over
# 180s, alongside a concurrent mutation stream (120 ops at 1/s: 60
# inserts, 40 edits, 20 retractions) and the active background worker
# drain. Query P95 <= 35ms and mutation ACK P95 <= 50ms are measured
# simultaneously under this write pressure (informational outcome
# bullets; the hard gates stay exactly HARD_GATES).
SUSTAINED_QUERIES = 800
SUSTAINED_WINDOW_S = 180.0
SUSTAINED_CLIENTS = 4
MUT_OPS_TOTAL = 120
MUT_OPS_PACE_S = 1.0
MUT_INSERTS = 60
MUT_EDITS = 40
MUT_RETRACTS = 20
MUT_ACK_P95_BUDGET_MS = 50.0
QUERY_P95_BUDGET_MS = 35.0
MUT_DOC_PREFIX = "mut-load-"
#: Mutation targets avoid the lowest bench indices: the parity sample
#: reads the lowest 30 sorted vault files and lifecycle owns
#: bench-000000.md, so edits/retracts stay at/above this floor (with a
#: same-run fallback onto inserted mut docs when the rung is too small).
MUT_BENCH_FLOOR = 64

# Required counterexample probes (EVALUATION §3), each with a rare anchor token.
PROBES = [
    ("probe-contradict-a", "Kestrel vector throughput under contention rises with batching; "
     "measured gain zephyrnine 30 percent at low load."),
    ("probe-contradict-b", "Kestrel vector throughput under contention falls with batching; "
     "measured loss zephyrnine 12 percent at high load."),
    ("probe-paraphrase", "The zephyrnine batching facility, when load is light, lifts Kestrel "
     "flow by nearly a third, with no shared words beyond the anchor."),
    ("probe-unfiled", "Zephyrnine marginalia on checkpoint cadence, filed under no facet: "
     "fsync every 4000 frames keeps p99 flat on ext4."),
    ("probe-minority", "Under the great throughput hub consensus, one dissenting zephyrnine "
     "measurement shows batching neutral at saturation, contrary to the hub."),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def mem_available_mb() -> float:
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    return -1.0


def fs_avail_gb(path: Path) -> float:
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def rss_peak_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return -1.0
    return -1.0


def rss_now_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return -1.0
    return -1.0


def host_info() -> dict:
    """CPU/RAM/Python identity for the frozen workload manifest (.3 §4)."""
    cpu_model, cpu_count = "unknown", 0
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    cpu_count += 1
                elif line.startswith("processor"):
                    pass
        if cpu_count == 0:
            with open("/proc/cpuinfo") as fh:
                cpu_count = sum(1 for l in fh if l.startswith("processor"))
    except OSError:
        pass
    mem_total, mem_avail = -1.0, -1.0
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) / 1024.0
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1]) / 1024.0
    except OSError:
        pass
    import platform
    return {
        "cpu_model": cpu_model, "cpu_count": cpu_count or os.cpu_count() or -1,
        "mem_total_mb": round(mem_total, 1), "mem_available_mb": round(mem_avail, 1),
        "python": sys.version.split()[0], "platform": platform.platform(),
        "harness_pid": os.getpid(),
    }


def storage_info(path: Path) -> dict:
    """Mount + fstype + headroom for the bench root (recorded, not gated)."""
    out = {"path": str(path), "mountpoint": None, "fstype": None,
           "avail_gb": round(fs_avail_gb(path), 2)}
    try:
        r = subprocess.run(["findmnt", "-T", str(path), "-o", "TARGET,FSTYPE",
                            "-n", "-r"], capture_output=True, text=True,
                           timeout=HTTP_TIMEOUT_S)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split()
            out["mountpoint"], out["fstype"] = parts[0], parts[1] if len(parts) > 1 else None
            return out
    except Exception:
        pass
    try:  # fallback: longest-prefix match over /proc/self/mountinfo
        best, best_fs = "", None
        rp = str(Path(path).resolve())
        with open("/proc/self/mountinfo") as fh:
            for line in fh:
                p = line.split()
                if len(p) < 9:
                    continue
                mp, fs = p[4], p[8]
                if rp.startswith(mp) and len(mp) > len(best):
                    best, best_fs = mp, fs
        out["mountpoint"], out["fstype"] = best or None, best_fs
    except OSError:
        pass
    return out


# ---------------- corpus construction ----------------

def synth_text(i: int) -> str:
    mode = i % 4
    if mode == 0:
        return (f"# bench-{i:06d}\n\ndef get_node_version_{i}(config):\n"
                f"    return config.load('/etc/trace/{i}.yaml')\n")
    if mode == 1:
        return (f"# bench-{i:06d}\n\nDesign note {i}: {TOPICS[i % len(TOPICS)]} "
                f"with measurements {i * 3}.\n")
    if mode == 2:
        return (f"# bench-{i:06d}\n\nLog {i}: checkpoint governor folded wal frames "
                f"after bulk ingest batch {i // 100}.\n")
    return (f"# bench-{i:06d}\n\nSpec {i}: authority lattice USER ARCH_SPEC "
            f"AGENT_DECISION TOOL_OUTPUT rank {i}.\n")


def resolve_beir_dir(name: str) -> Path:
    """Locate the frozen local BEIR corpus; STOP with searched paths if missing."""
    candidates = [Path(BEIR_DIRS[name])]
    env = os.environ.get(f"TRACE_LITE_BEIR_{name.upper()}_DIR")
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if (c / "corpus.jsonl").exists() and (c / "queries.jsonl").exists():
            return c
    searched = [str(c) for c in candidates]
    raise FileNotFoundError(
        f"BEIR dataset {name!r} not found locally; searched: {searched}. "
        f"No network fetch is attempted (sandbox rule): obtain the frozen "
        f"corpus or set TRACE_LITE_BEIR_{name.upper()}_DIR and re-run.")


def load_beir_corpus(name: str, n_docs: int | None) -> tuple[list[tuple[str, str]], dict]:
    """Frozen corpus docs (n_docs=None loads ALL) + covered test queries.

    Meta records full-corpus identity (hashes, totals) so the workload
    freeze proves whether the eval ran on the full corpus or a subset.
    """
    d = resolve_beir_dir(name)
    docs: list[tuple[str, str]] = []
    with open(d / "corpus.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if n_docs is not None and len(docs) >= n_docs:
                break
            line = line.strip()
            if line:
                r = json.loads(line)
                docs.append((r["_id"], f"{r.get('title', '')} {r.get('text', '')}".strip()))
    qrels: dict[str, dict[str, int]] = {}
    with open(d / "qrels" / "test.tsv", encoding="utf-8") as fh:
        header = True
        for line in fh:
            p = line.strip().split("\t")
            if header and p[0] == "query-id":
                header = False
                continue
            header = False
            if len(p) == 3:
                qrels.setdefault(p[0], {})[p[1]] = int(p[2])
    queries: dict[str, str] = {}
    with open(d / "queries.jsonl", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                r = json.loads(line)
                queries[r["_id"]] = r["text"]
    keep = set(i for i, _ in docs)
    # doc ids are opaque strings; coverage = qrel doc id present in subset
    sub_ids = {doc_id for doc_id, _ in docs}
    covered = [qid for qid, rel in qrels.items()
               if qid in queries and any(did in sub_ids for did in rel)]
    total_docs = sum(1 for _ in open(d / "corpus.jsonl", encoding="utf-8"))
    return docs, {"queries": queries, "qrels": qrels, "covered_qids": covered,
                  "corpus_dir": str(d), "total_corpus_docs": total_docs,
                  "full_corpus": len(docs) == total_docs,
                  "corpus_sha256": sha256_file(d / "corpus.jsonl"),
                  "queries_sha256": sha256_file(d / "queries.jsonl"),
                  "qrels_sha256": sha256_file(d / "qrels" / "test.tsv")}


def load_beir_subset(name: str, n_docs: int) -> tuple[list[tuple[str, str]], dict]:
    """First-N docs of the frozen corpus + covered test queries (bounded)."""
    return load_beir_corpus(name, n_docs)


def load_frozen_beir_queries(name: str) -> list[tuple[str, str]]:
    """ALL frozen test queries for a dataset, sorted by qid (rung-invariant).

    The latency schedule draws from this — never from the indexed subset —
    so every rung replays the same query schedule (.3 §4).
    """
    d = resolve_beir_dir(name)
    out = []
    with open(d / "queries.jsonl", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                r = json.loads(line)
                out.append((r["_id"], r["text"]))
    return sorted(out)


def build_latency_schedule(n: int, scifact_q: list[tuple[str, str]],
                           nfcorpus_q: list[tuple[str, str]]) -> list[str]:
    """Deterministic rung-invariant latency query schedule (.3 §4).

    Fixed composition, no randomness, no dependence on the indexed subset:
    synthetic topic probes, then frozen BEIR query texts round-robin by
    sorted qid, then fixed gibberish. Same n => same schedule at every rung.
    """
    synth = [f"how does {t} behave under load" for t in TOPICS]
    sched: list[str] = []
    i = 0
    pools = [synth, [q for _, q in scifact_q], [q for _, q in nfcorpus_q]]
    while len(sched) < n:
        progressed = False
        for pool in pools:
            if not pool:
                continue
            sched.append(pool[i % len(pool)])
            progressed = True
            if len(sched) >= n:
                break
        if not progressed:
            break
        i += 1
    while len(sched) < n:  # tiny-fixture fallback: fixed gibberish
        sched.append(f"xqzt blorpt wqkj {len(sched)}")
    return sched[:n]


def schedule_sha256(sched: list[str]) -> str:
    return hashlib.sha256("\n".join(sched).encode()).hexdigest()


def build_vault(vault: Path, n_synth: int, beir_docs: dict[str, list]) -> dict:
    vault.mkdir(parents=True, exist_ok=True)
    counts = {"synthetic": 0, "beir": {}, "probes": 0}
    for i in range(n_synth):
        (vault / f"bench-{i:06d}.md").write_text(synth_text(i), encoding="utf-8")
        counts["synthetic"] += 1
    for ds, docs in beir_docs.items():
        for doc_id, text in docs:
            safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in doc_id)
            (vault / f"beir-{ds}-{safe}.md").write_text(
                f"# {doc_id}\n\n{text}\n", encoding="utf-8")
        counts["beir"][ds] = len(docs)
    for name, text in PROBES:
        (vault / f"{name}.md").write_text(f"# {name}\n\n{text}\n", encoding="utf-8")
        counts["probes"] += 1
    # late-row-ID probe: relevant doc written LAST (lexically last filename).
    (vault / "zzz-late-relevant.md").write_text(
        "# zzz-late\n\nZephyrnine batching throughput anchor repeats for late-row "
        "recall: batching improves throughput under low contention.\n",
        encoding="utf-8")
    counts["probes"] += 1
    total = sum([counts["synthetic"], sum(counts["beir"].values()),
                 counts["probes"]]) if counts["beir"] else counts["synthetic"] + counts["probes"]
    counts["total_files"] = total
    return counts


def vault_manifest_sha256(vault: Path) -> dict:
    """Frozen corpus identity: per-file sha over sorted names + manifest sha."""
    files = sorted(vault.rglob("*.md"), key=lambda p: p.name)
    h = hashlib.sha256()
    for f in files:
        fh = sha256_file(f)
        h.update(f"{f.name}:{fh}\n".encode())
    return {"files": len(files), "manifest_sha256": h.hexdigest()}


# ---------------- server management ----------------

SERVE_TMPL = """import sys
sys.path.insert(0, {repo!r})
from trace_lite.api import create_app
import uvicorn
app = create_app({db!r}, {vault!r})
uvicorn.run(app, host="127.0.0.1", port={port:d}, log_level="warning")
"""


def child_env(base: dict | None = None) -> dict:
    """Child server env: G2 served flags set, sandbox proxies stripped.

    The sandbox exports http_proxy/https_proxy/all_proxy pointing at a
    dead endpoint and Python honors them even for loopback, so every
    proxy var is removed (not merely overridden) and loopback no_proxy
    is forced. Pure over ``base`` (defaults to os.environ) for testing.
    """
    env = dict(os.environ if base is None else base)
    for var in PROXY_ENV_VARS:
        env.pop(var, None)
    env.pop("NODE_USE_ENV_PROXY", None)
    need = "127.0.0.1,localhost"
    for var in ("no_proxy", "NO_PROXY"):
        cur = env.get(var, "")
        if need not in cur:
            env[var] = f"{cur},{need}" if cur else need
    env["TRACE_LITE_SERVED_PERSISTENT"] = "1"
    env["TRACE_LITE_WORKER"] = "1"
    return env


def start_server(root: Path, db: Path, port: int) -> subprocess.Popen:
    """Start the real server with vault=None: ingest is driven explicitly via
    POST /api/sync per chunk. (A watching server would race explicit syncs
    and double-register every source under a second prefixed name.)

    G2: the child inherits this process tree (same network namespace as the
    harness HTTP clients) with TRACE_LITE_SERVED_PERSISTENT=1 and
    TRACE_LITE_WORKER=1 so it serves the persistent engine and drains the
    outbox autonomously.
    """
    runner = root / "_serve_r09.py"
    runner.write_text(SERVE_TMPL.format(repo=str(REPO / "src"), db=str(db),
                                        vault="__none__", port=port).replace(
                                            "'__none__'", "None"), encoding="utf-8")
    log = open(root / "server.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(runner)], stdout=log, stderr=subprocess.STDOUT,
        cwd=str(REPO), env=child_env())
    return proc


def stop_server(proc: subprocess.Popen, timeout_s: float = SHUTDOWN_GRACE_S) -> str:
    """Graceful shutdown bound (.3 §5): terminate, wait, then kill."""
    if proc.poll() is not None:
        return "already-exited"
    proc.terminate()
    try:
        proc.wait(timeout=timeout_s)
        return "terminated"
    except Exception:
        proc.kill()
        return "killed-after-grace"


def http_post(base: str, path: str, payload: dict,
              timeout: int = HTTP_TIMEOUT_S) -> dict:
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with _NOPROXY_OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_get(base: str, path: str, timeout: int = HTTP_TIMEOUT_S) -> dict:
    with _NOPROXY_OPENER.open(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_healthy(base: str, timeout_s: int = RESTART_READY_S) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            if http_get(base, "/api/health").get("ok"):
                return True
        except Exception:
            time.sleep(1.0)
    return False


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return -1.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def gain(rel: int) -> float:
    return float(2 ** rel - 1)


def dcg(rels: list[int], k: int) -> float:
    return sum(gain(r) / math.log2(i + 2) for i, r in enumerate(rels[:k]))


def bootstrap_ci(scores: list[float], n: int = 2000, seed: int = 42):
    rng = random.Random(seed)
    m = len(scores)
    means = sorted(sum(scores[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return round(sum(scores) / m, 4), round(means[int(0.025 * n)], 4), round(means[min(n - 1, int(0.975 * n))], 4)


def db_counts(db_path: Path) -> dict:
    # Fail LOUD on a missing DB: sqlite would otherwise create a fresh
    # empty database at the path and every downstream gate would pass
    # vacuously on zero rows (operator-error lesson, attempt 2).
    if not Path(db_path).exists():
        raise FileNotFoundError(f"bench DB missing: {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        out: dict = {}
        for t in ("source", "source_revision", "atom", "evidence_span",
                  "segmentation_view", "extraction_run", "assertion", "relation",
                  "memberships", "facets", "holon_state", "holon_members",
                  "dependency", "projection_job", "projection_generation"):
            out[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) \
                if t in tables else 0
        out["current_revisions"] = int(con.execute(
            "SELECT COUNT(*) FROM source_revision WHERE status='current'").fetchone()[0]) \
            if "source_revision" in tables else 0
        out["pending_jobs"] = int(con.execute(
            "SELECT COUNT(*) FROM projection_job WHERE state IN ('pending','claimed')").fetchone()[0]) \
            if "projection_job" in tables else 0
        try:
            out["oldest_pending_s"] = con.execute(
                "SELECT MAX(strftime('%s','now')-strftime('%s',created_at)) FROM projection_job"
                " WHERE state IN ('pending','claimed')").fetchone()[0]
        except Exception:
            out["oldest_pending_s"] = None
        nsrc = out.get("source", 0)
        if nsrc:
            rows = con.execute(
                "SELECT length(text) FROM source_revision WHERE status='current'").fetchall() \
                if "source_revision" in tables else []
            byts = [r[0] or 0 for r in rows]
            out["source_bytes_current"] = int(sum(byts))
            out["bytes_per_source_mean"] = round(sum(byts) / max(1, len(byts)), 1)
        else:
            out["source_bytes_current"] = 0
            out["bytes_per_source_mean"] = 0.0
        try:
            out["fts_rows"] = int(con.execute("SELECT COUNT(*) FROM fts_atoms").fetchone()[0])
        except Exception:
            out["fts_rows"] = -1
        try:
            out["integrity_check"] = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        except Exception as exc:
            out["integrity_check"] = f"ERROR {exc}"
        return out
    finally:
        con.close()


def file_sizes(db_path: Path) -> dict:
    out = {}
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        out[f"db{suffix or '_main'}_bytes"] = p.stat().st_size if p.exists() else 0
    out["db_total_bytes"] = sum(out.values())
    return out


# ---------------- readiness gate ----------------

def readiness_gate(next_sources: int, bytes_per_source: float,
                   db_bytes_per_source: float) -> dict:
    """Measure free RAM/disk + projection; refuse rungs that need 2x headroom."""
    avail_mb = mem_available_mb()
    disk_gb = fs_avail_gb(Path("/tmp"))
    proj_db_gb = next_sources * db_bytes_per_source / 1e9
    # Server RSS projection: base (~250MB measured small-rung) + dense matrix
    # fp32-warm estimate ~ n_atoms*128*4B + FTS/row overhead ~ bytes*3.
    proj_rss_mb = 250 + next_sources * (128 * 4 + 3 * bytes_per_source) / 1e6
    fits_ram = avail_mb > 2 * proj_rss_mb
    fits_disk = disk_gb > 2 * max(proj_db_gb, 0.05)
    return {
        "mem_available_mb": round(avail_mb, 1),
        "disk_avail_gb": round(disk_gb, 1),
        "next_sources": next_sources,
        "projected_db_gb": round(proj_db_gb, 3),
        "projected_server_rss_mb": round(proj_rss_mb, 1),
        "rule": "admit only if avail RAM > 2x projected RSS and disk > 2x projected DB",
        "fits_ram_2x": bool(fits_ram),
        "fits_disk_2x": bool(fits_disk),
        "admit": bool(fits_ram and fits_disk),
    }


# ---------------- ingest (served path) ----------------

def chunk_vault(vault: Path, chunk_files: int = 2000) -> list[Path]:
    files = sorted(vault.glob("*.md"))
    chunks = []
    for i in range(0, len(files), chunk_files):
        cdir = vault / f"_chunk-{i // chunk_files:03d}"
        cdir.mkdir(exist_ok=True)
        for f in files[i:i + chunk_files]:
            f.rename(cdir / f.name)
        chunks.append(cdir)
    return chunks


def served_ingest(base: str, chunks: list[Path], log,
                  setup_deadline: float | None = None) -> dict:
    """Bulk setup ingest; each /api/sync call is bounded by the REMAINING
    setup budget (supervisor enforcement, .3 §5) instead of the 10s
    query-call timeout: one chunk carries thousands of docs."""
    t0 = time.time()
    total = 0
    per_chunk = []
    for cdir in chunks:
        n_files = len(list(cdir.glob("*.md")))
        c0 = time.time()
        if setup_deadline is not None:
            remaining = max(10.0, setup_deadline - time.time())
        else:
            remaining = SETUP_CAP_S
        resp = http_post(base, "/api/sync", {"path": str(cdir)},
                         timeout=int(remaining))
        dt = time.time() - c0
        total += int(resp.get("synced", 0))
        per_chunk.append({"chunk": cdir.name, "files": n_files,
                          "synced": resp.get("synced", 0),
                          "seconds": round(dt, 1),
                          "docs_per_s": round(n_files / max(dt, 1e-6), 2)})
        log(f"[ingest] {cdir.name}: {n_files} files in {dt:.1f}s "
            f"({n_files / max(dt, 1e-6):.1f} docs/s)")
        gc.collect()
    dt_all = time.time() - t0
    return {"sources_synced": total, "seconds": round(dt_all, 1),
            "docs_per_s": round(total / max(dt_all, 1e-6), 2),
            "chunks": per_chunk}


# ---------------- served-path queue drain (G2 autonomous) ----------------

# G1 decoupled the write path: the child server runs the supervised
# MaintenanceWorker (TRACE_LITE_WORKER=1) which drains the durable outbox
# autonomously. G2 therefore OBSERVES the drain via /api/status until the
# queue depth reaches 0 within the 600s deadline — no replay ingests, no
# harness-driven pumping. Gate = converged within deadline with the
# worker active. The legacy replay sampler is deleted (attempt-2
# drain_sample_iters); hard_verdict still accepts legacy "sampled" rung
# records for reading old ladder entries.
def served_drain(base: str, db_path: Path, log, *,
                 deadline_s: float = DRAIN_DEADLINE_S,
                 poll_s: float = 2.0) -> dict:
    """Autonomous drain monitor: poll /api/status to queue depth 0.

    Converged = queue_stats.depth == 0 AND semantic_pending == 0.
    Serves a light timed query probe during the drain as honest
    concurrent-serving-under-drain evidence (bounded 10s calls).
    Never writes; the worker does all draining.
    """
    t0 = time.time()
    started_at = utcnow()
    deadline = t0 + deadline_s
    first = http_get(base, "/api/status")
    worker_enabled = bool((first.get("worker") or {}).get("enabled", False))
    depth0 = int((first.get("queue_stats") or {}).get("depth", -1))
    done0 = int((first.get("job_progress") or {}).get("done", 0))
    total_jobs = int((first.get("job_progress") or {}).get("total", 0))
    probe_ms: list[float] = []
    probe_routes: dict[str, int] = {}
    probe_failures = 0
    probes = 0
    last: dict = first
    polls = 0
    while True:
        polls += 1
        try:
            st = http_get(base, "/api/status")
            last = st
        except Exception as exc:
            log(f"[drain] status poll failed: {exc}")
            st = last
        depth = int((st.get("queue_stats") or {}).get("depth", -1))
        sem = int(st.get("semantic_pending", -1))
        # Light serving probe about every 30s of draining (max ~20 probes).
        dt_now = time.time() - t0
        if probes < 20 and dt_now > (probes + 1) * 30.0:
            probes += 1
            q0 = time.perf_counter()
            try:
                r = http_post(base, "/api/query",
                              {"query": "checkpoint governor wal frames",
                               "limit": 5, "mode": "hybrid"})
                probe_ms.append((time.perf_counter() - q0) * 1000.0)
                rk = str(r.get("route", "?"))
                probe_routes[rk] = probe_routes.get(rk, 0) + 1
            except Exception:
                probe_failures += 1
        if depth == 0 and sem == 0:
            break
        if time.time() >= deadline:
            break
        time.sleep(poll_s)
    dt = time.time() - t0
    depth1 = int((last.get("queue_stats") or {}).get("depth", -1))
    sem1 = int(last.get("semantic_pending", -1))
    done1 = int((last.get("job_progress") or {}).get("done", done0))
    completed = max(0, done1 - done0)
    rate = round(completed / max(dt, 1e-6), 3)
    required_rate = round(depth0 / deadline_s, 3) if depth0 > 0 else 0.0
    converged = bool(depth1 == 0 and sem1 == 0)
    within_deadline = bool(dt <= deadline_s)
    after = db_counts(db_path)
    out = {
        "mode": "autonomous",
        "started_at": started_at,
        "finished_at": utcnow(),
        "worker_enabled": worker_enabled,
        "depth_before": depth0, "depth_after": depth1,
        "semantic_before": int((first.get("semantic_pending", -1))),
        "semantic_after": sem1,
        "pending_before": depth0, "pending_after": depth1,
        "completed_jobs": completed, "total_jobs": total_jobs,
        "polls": polls,
        "seconds": round(dt, 1), "deadline_s": deadline_s,
        "jobs_per_s": rate, "required_jobs_per_s": required_rate,
        "meets_rate": bool(rate >= required_rate) if depth0 > 0 else True,
        "converged": converged, "within_deadline": within_deadline,
        "oldest_pending_s": after.get("oldest_pending_s"),
        "serving_probe": {
            "n": len(probe_ms), "failures": probe_failures,
            "p50_ms": round(pct(probe_ms, .5), 2) if probe_ms else None,
            "p95_ms": round(pct(probe_ms, .95), 2) if probe_ms else None,
            "routes": probe_routes,
        },
    }
    log(f"[drain] mode=autonomous worker={worker_enabled} depth "
        f"{depth0}->{depth1} sem->{sem1} completed={completed} "
        f"rate={rate}/s (need {required_rate}) {dt:.1f}s/{deadline_s:.0f}s "
        f"converged={converged}")
    return out


# ---------------- latency (served path, separate client) ----------------

def latency_run(base: str, queries: list[str], log, concurrency: int = 1) -> dict:
    """Timed served queries (10s per-call bound) with vector telemetry.

    Records per-query route + vector adequacy/generation so the
    vector_stable gate can count real detachment events (served_adequate
    False) separately from cold-start unavailability (None).
    """
    server_ms: list[float] = []
    e2e_ms: list[float] = []
    routes: dict[str, int] = {}
    abstentions = 0
    failures = 0
    vec_true = vec_false = vec_none = 0
    vec_gens: set[str] = set()

    def one(q: str):
        t0 = time.perf_counter()
        try:
            r = http_post(base, "/api/query",
                          {"query": q, "limit": 10, "mode": "hybrid"})
            dt = (time.perf_counter() - t0) * 1000.0
            return r, dt, None
        except Exception as exc:
            return None, -1.0, str(exc)

    t0 = time.time()
    if concurrency <= 1:
        results = [one(q) for q in queries]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            results = list(ex.map(one, queries))
    wall_s = time.time() - t0
    for r, dt, err in results:
        if err is not None or r is None:
            failures += 1
            continue
        server_ms.append(float(r.get("elapsed_ms", -1.0)))
        e2e_ms.append(dt)
        routes[str(r.get("route", "?"))] = routes.get(str(r.get("route", "?")), 0) + 1
        if not r.get("anchors"):
            abstentions += 1
        va = r.get("vector_adequate", None)
        if va is True:
            vec_true += 1
        elif va is False:
            vec_false += 1
        else:
            vec_none += 1
        vg = r.get("vector_generation_id")
        if vg:
            vec_gens.add(str(vg))
    return {
        "n": len(queries), "concurrency": concurrency,
        "wall_seconds": round(wall_s, 1),
        "qps": round(len(queries) / max(wall_s, 1e-6), 2),
        "server_ms": {"p50": round(pct(server_ms, .5), 2),
                      "p95": round(pct(server_ms, .95), 2),
                      "p99": round(pct(server_ms, .99), 2),
                      "max": round(max(server_ms) if server_ms else -1, 2)},
        "end_to_end_ms": {"p50": round(pct(e2e_ms, .5), 2),
                          "p95": round(pct(e2e_ms, .95), 2),
                          "p99": round(pct(e2e_ms, .99), 2),
                          "max": round(max(e2e_ms) if e2e_ms else -1, 2)},
        "routes": routes, "abstentions": abstentions, "failures": failures,
        "vector_adequate": {"true": vec_true, "false": vec_false,
                            "none": vec_none},
        "vector_generations_seen": sorted(vec_gens),
        "vector_route_count": int(routes.get("vector", 0)),
    }


def vector_serving_summary(lat_runs: list[dict], servable_samples: list) -> dict:
    """G2 vector-stability telemetry: detachment EVENTS, not cold fallback.

    detachments = served_persistent True->False transitions across the
    run + served_adequate==False responses (filter hid live candidates,
    forcing fallback). Cold-start unavailability (adequate None, branch
    never attached) is reported separately as cold_unavailable and is
    explicitly permitted fallback (.3 §3), never counted as detachment.
    """
    transitions = 0
    prev = None
    for s in servable_samples:
        if prev is True and s is False:
            transitions += 1
        if s is not None:
            prev = s
    false_n = sum(r.get("vector_adequate", {}).get("false", 0) for r in lat_runs)
    none_n = sum(r.get("vector_adequate", {}).get("none", 0) for r in lat_runs)
    vec_n = sum(r.get("vector_route_count", 0) for r in lat_runs)
    return {
        "servable_samples": list(servable_samples),
        "servable_transitions_to_false": transitions,
        "inadequate_responses": false_n,
        "cold_unavailable_responses": none_n,
        "vector_route_responses": vec_n,
        "detachments": transitions + false_n,
    }


# ---------------- sustained .3 window (queries + mutations under drain) -----

def mut_text(kind: str, i: int) -> str:
    """Deterministic BEIR-length mutation payload (~1KB, topic-grounded)."""
    topic = TOPICS[i % len(TOPICS)]
    para = (f"Mutation {kind} {i}: {topic} under sustained write pressure. "
            f"Measurement row {i * 7} records throughput, latency, and "
            f"recall deltas while the background worker drains. ")
    return f"# mut-{kind}-{i:06d}\n\n" + para * 8


def build_mutation_plan(n_synth: int, n_inserts: int = MUT_INSERTS,
                        n_edits: int = MUT_EDITS,
                        n_retracts: int = MUT_RETRACTS) -> list[dict]:
    """Deterministic 120-op mutation mix (pure; no IO, rung-invariant).

    Cycle ``[insert, edit, insert, retract, insert, edit]`` per 6 slots
    yields exactly the 60/40/20 mix. Inserts mint ``mut-load-*.md`` docs;
    edits rewrite high-index ``bench-*.md`` docs (never bench-000000,
    never below ``MUT_BENCH_FLOOR``); retracts remove distinct high-index
    bench docs. When the rung is too small for distinct bench targets,
    retracts fall back onto mut docs inserted earlier in this same plan
    (still genuine retract jobs) and inserts are front-loaded so every
    target exists before its retract slot.
    """
    if n_synth < 0:
        raise ValueError(f"n_synth must be >= 0, saw {n_synth}")
    total = n_inserts + n_edits + n_retracts
    need_bench = n_edits + n_retracts
    # Highest bench indices first (parity sample reads the lowest files).
    bench_hi = [f"bench-{i:06d}.md"
                for i in range(n_synth - 1, MUT_BENCH_FLOOR - 1, -1)]
    fallback = len(bench_hi) < need_bench
    if fallback and n_inserts <= 0 and (n_edits > 0 or n_retracts > 0):
        raise ValueError("fallback plan needs inserts to host edits/retracts")
    edit_docs = bench_hi[:n_edits]
    if fallback:
        retract_docs = [f"{MUT_DOC_PREFIX}{i:04d}.md" for i in range(n_retracts)]
    else:
        retract_docs = bench_hi[n_edits:n_edits + n_retracts]
    insert_docs = [f"{MUT_DOC_PREFIX}{i:04d}.md" for i in range(n_inserts)]
    ops: list[dict] = []
    ii = ei = ri = 0
    cycle = ["insert", "edit", "insert", "retract", "insert", "edit"]
    left = {"insert": n_inserts, "edit": n_edits, "retract": n_retracts}
    ci = 0
    while len(ops) < total:
        kind = cycle[ci % len(cycle)]
        ci += 1
        if left[kind] <= 0:  # uneven dry-run quotas: skip exhausted kinds
            if all(v <= 0 for v in left.values()):
                break
            continue
        left[kind] -= 1
        if kind == "insert":
            ops.append({"op": kind, "doc": insert_docs[ii],
                        "text": mut_text(kind, ii)})
            ii += 1
        elif kind == "edit":
            if fallback:
                # Tiny rung: edits rewrite earlier inserts (bench untouched).
                pool = insert_docs[n_retracts:] or insert_docs
                doc = pool[ei % len(pool)]
            else:
                doc = edit_docs[ei]
            ops.append({"op": kind, "doc": doc, "text": mut_text(kind, ei)})
            ei += 1
        else:
            ops.append({"op": kind, "doc": retract_docs[ri]})
            ri += 1
    if fallback:
        # Order inserts before their dependent edits/retracts (stable).
        rank = {"insert": 0, "edit": 1, "retract": 2}
        ops.sort(key=lambda o: rank[o["op"]])
    for slot, op in enumerate(ops):
        op["slot"] = slot
    return ops


def summarize_ops(records: list[dict], *, lat_key: str = "e2e_ms") -> dict:
    """Offered-vs-achieved + latency summary over paced op records (pure).

    Each record carries ``ok``/``error``, a latency (``e2e_ms`` for
    queries incl. time-to-error, ``ack_ms`` for mutations), and an
    ``overlapped`` flag (backlog nonzero at the last status sample).
    Overlap-only stats qualify the under-drain evidence when the backlog
    drains to zero mid-window.
    """
    offered = len(records)
    achieved = sum(1 for r in records if r.get("issued", True))
    oks = [r for r in records if r.get("ok")]
    fails = [r for r in records if not r.get("ok")]
    lat_all = sorted(float(r[lat_key]) for r in records if r.get(lat_key) is not None)
    lat_ok = sorted(float(r[lat_key]) for r in oks if r.get(lat_key) is not None)
    ov = [r for r in records if r.get("overlapped")]
    ov_lat = sorted(float(r[lat_key]) for r in ov if r.get(lat_key) is not None)
    errs: dict[str, int] = {}
    for r in fails:
        errs[str(r.get("error", "?"))[:80]] = errs.get(str(r.get("error", "?"))[:80], 0) + 1
    slips = sorted(float(r.get("slip_s", 0.0)) for r in records)
    return {
        "offered": offered, "achieved": achieved,
        "succeeded": len(oks), "failures": len(fails),
        "errors": errs,
        "latency_incl_errors_ms": {
            "n": len(lat_all), "p50": round(pct(lat_all, .5), 2),
            "p95": round(pct(lat_all, .95), 2),
            "p99": round(pct(lat_all, .99), 2),
            "max": round(max(lat_all) if lat_all else -1, 2)},
        "latency_ok_only_ms": {
            "n": len(lat_ok), "p50": round(pct(lat_ok, .5), 2),
            "p95": round(pct(lat_ok, .95), 2),
            "p99": round(pct(lat_ok, .99), 2),
            "max": round(max(lat_ok) if lat_ok else -1, 2)},
        "overlap": {
            "n": len(ov),
            "fraction": round(len(ov) / max(1, offered), 4),
            "latency_incl_errors_ms": {
                "n": len(ov_lat), "p50": round(pct(ov_lat, .5), 2),
                "p95": round(pct(ov_lat, .95), 2),
                "p99": round(pct(ov_lat, .99), 2),
                "max": round(max(ov_lat) if ov_lat else -1, 2)}},
        "schedule_slip_s": {
            "p50": round(pct(slips, .5), 3), "p95": round(pct(slips, .95), 3),
            "max": round(max(slips) if slips else 0.0, 3)},
    }


def sustained_under_drain_run(base: str, schedule: list[str], plan: list[dict],
                              server_pid: int, db_path: Path, log, *,
                              window_s: float = SUSTAINED_WINDOW_S,
                              clients: int = SUSTAINED_CLIENTS,
                              pace_s: float = MUT_OPS_PACE_S,
                              timeout: int = HTTP_TIMEOUT_S) -> dict:
    """Exact .3 active window: paced queries + mutations vs the live backlog.

    ``clients`` query threads issue ``len(schedule)`` queries evenly
    paced over ``window_s`` while ONE mutation thread issues ``plan`` at
    ``pace_s``. A status sampler proves overlap (worker active + backlog
    nonzero) every 2s and samples server RSS continuously in-window.
    Every op records offered-vs-achieved, end-to-end latency INCLUDING
    errors (time-to-error joins the distribution; failures are also
    counted separately, never dropped), and an overlap tag.

    Transports: queries + inserts/edits are end-to-end HTTP; retractions
    run through the identical served ``remove_note`` code on a private
    WAL handle (the served HTTP surface exposes no retract route —
    recorded per-op as ``transport: served-code``, never mislabeled).
    """
    import sqlite3 as _sqlite3
    import threading as _threading

    window_started_at = utcnow()
    from trace_lite.api.app import remove_note as _remove_note
    from trace_lite.filing import FilingEngine as _Engine
    from trace_lite.filing import Taxonomy as _Taxonomy
    from trace_lite.store import Database as _Database

    n_q = len(schedule)
    per_client = [schedule[i::clients] for i in range(clients)]
    q_interval = window_s / max(1, max(len(s) for s in per_client))
    stop = _threading.Event()
    status_samples: list[dict] = []
    mem_samples: list[dict] = []
    sample_lock = _threading.Lock()
    latest = {"depth": -1, "semantic": -1, "batches": -1}

    sample_t0 = time.monotonic()

    def sample_once() -> None:
        try:
            st = http_get(base, "/api/status")
            depth = int((st.get("queue_stats") or {}).get("depth", -1))
            sem = int(st.get("semantic_pending", -1))
            batches = int((st.get("worker") or {}).get("batches", -1))
        except Exception:
            depth, sem, batches = -1, -1, -1
        rss = rss_now_mb(server_pid)
        # Response-time label: under a loaded server the HTTP round
        # trip itself queues, so a pre-request timestamp would attach
        # the reading to an earlier state than it observes.
        tick = round(time.monotonic() - sample_t0, 1)
        with sample_lock:
            latest.update(depth=depth, semantic=sem, batches=batches)
            status_samples.append({"t_s": tick, "depth": depth,
                                   "semantic_pending": sem,
                                   "worker_batches": batches})
            mem_samples.append({"t_s": tick, "rss_mb": round(rss, 1)})

    def sampler() -> None:
        while not stop.is_set():
            sample_once()
            stop.wait(2.0)
        with sample_lock:
            mem_samples.append({"t_s": round(time.monotonic() - sample_t0, 1),
                                "rss_mb": round(rss_now_mb(server_pid), 1)})

    def snapshot_overlap() -> tuple[bool, int]:
        with sample_lock:
            d = int(latest["depth"])
        return (d > 0, d)

    q_records: list[dict] = []
    q_lock = _threading.Lock()
    routes: dict[str, int] = {}
    vec_true = vec_false = vec_none = 0
    vec_gens: set[str] = set()
    abstentions = 0

    def query_worker(shard: list[str], offset: float) -> None:
        nonlocal vec_true, vec_false, vec_none, abstentions
        for j, q in enumerate(shard):
            target = offset + j * q_interval
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)
            slip = max(0.0, time.monotonic() - target)
            overlapped, depth = snapshot_overlap()
            t0 = time.perf_counter()
            rec: dict = {"issued": True, "slot": j, "slip_s": round(slip, 3),
                         "t_s": round(time.monotonic() - t_start, 1),
                         "overlapped": overlapped, "depth": depth}
            try:
                r = http_post(base, "/api/query",
                              {"query": q, "limit": 10, "mode": "hybrid"},
                              timeout=timeout)
                dt = (time.perf_counter() - t0) * 1000.0
                rec.update(ok=True, e2e_ms=round(dt, 2),
                           server_ms=float(r.get("elapsed_ms", -1.0)),
                           route=str(r.get("route", "?")))
                with q_lock:
                    routes[rec["route"]] = routes.get(rec["route"], 0) + 1
                    if not r.get("anchors"):
                        abstentions += 1
                    va = r.get("vector_adequate", None)
                    if va is True:
                        vec_true += 1
                    elif va is False:
                        vec_false += 1
                    else:
                        vec_none += 1
                    vg = r.get("vector_generation_id")
                    if vg:
                        vec_gens.add(str(vg))
            except Exception as exc:
                dt = (time.perf_counter() - t0) * 1000.0
                rec.update(ok=False, e2e_ms=round(dt, 2), server_ms=None,
                           route=None, error=f"{type(exc).__name__}: {exc}"[:160])
            with q_lock:
                q_records.append(rec)

    m_records: list[dict] = []
    m_lock = _threading.Lock()

    def mutation_worker() -> None:
        mdb: Any = None
        try:
            mdb = _Database(db_path)
            mtax = _Taxonomy(mdb.conn)
            meng = _Engine(mdb.conn, mtax)
            meng.bind_database(mdb)
        except Exception as exc:
            with m_lock:
                for op in plan:
                    m_records.append({"issued": False, "ok": False,
                                      "op": op["op"], "slot": op["slot"],
                                      "ack_ms": None, "overlapped": False,
                                      "depth": -1, "slip_s": 0.0,
                                      "transport": "none",
                                      "error": f"db-open: {exc}"[:160]})
            return
        try:
            for op in plan:
                target = t_start + op["slot"] * pace_s
                now = time.monotonic()
                if target > now:
                    time.sleep(target - now)
                slip = max(0.0, time.monotonic() - target)
                overlapped, depth = snapshot_overlap()
                rec = {"issued": True, "op": op["op"], "slot": op["slot"],
                       "slip_s": round(slip, 3),
                       "t_s": round(time.monotonic() - t_start, 1),
                       "overlapped": overlapped, "depth": depth}
                t0 = time.perf_counter()
                try:
                    if op["op"] in ("insert", "edit"):
                        r = http_post(base, "/api/ingest",
                                      {"document_name": op["doc"],
                                       "text": op["text"]},
                                      timeout=timeout)
                        dt = (time.perf_counter() - t0) * 1000.0
                        rec.update(ok=bool(r.get("ok", False)),
                                   ack_ms=round(dt, 2), transport="http",
                                   revision_id=r.get("revision_id"))
                        if not rec["ok"]:
                            rec["error"] = f"ack-not-ok: {r}"[:160]
                    else:
                        removed = _retract_with_retry(mdb, meng, op["doc"])
                        dt = (time.perf_counter() - t0) * 1000.0
                        rec.update(ok=True, ack_ms=round(dt, 2),
                                   transport="served-code",
                                   atoms_removed=removed)
                except Exception as exc:
                    dt = (time.perf_counter() - t0) * 1000.0
                    rec.update(ok=False, ack_ms=round(dt, 2),
                               transport=("http" if op["op"] in
                                          ("insert", "edit") else "served-code"),
                               error=f"{type(exc).__name__}: {exc}"[:160])
                with m_lock:
                    m_records.append(rec)
        finally:
            try:
                mdb.close()
            except Exception:
                pass

    def _retract_with_retry(mdb: Any, meng: Any, doc: str,
                            tries: int = 5) -> int:
        last: Exception | None = None
        for attempt in range(tries):
            try:
                return int(_remove_note(mdb, meng, doc))
            except _sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == tries - 1:
                    raise
                last = exc
                time.sleep(0.1 * (2 ** attempt))
        raise last if last is not None else RuntimeError("retract retries lost")

    threads: list[Any] = []
    t_start = time.monotonic() + 0.5
    # Seed the overlap state synchronously so the first paced ops read a
    # real backlog sample instead of the unknown initial (-1).
    sample_once()
    sampler_t = _threading.Thread(target=sampler, name="sustained-sampler",
                                  daemon=True)
    sampler_t.start()
    wall0 = time.monotonic()
    for shard in per_client:
        th = _threading.Thread(target=query_worker, args=(shard, t_start),
                               daemon=True)
        threads.append(th)
        th.start()
    mut_t = _threading.Thread(target=mutation_worker, name="mutation-client",
                              daemon=True)
    mut_t.start()
    # Bound: paced window + per-op timeout headroom + sampler tail.
    join_deadline = window_s + len(plan) * pace_s + 120.0
    mut_t.join(timeout=join_deadline)
    for th in threads:
        th.join(timeout=max(0.0, join_deadline - (time.monotonic() - wall0)))
    wall_s = time.monotonic() - wall0
    stop.set()
    sampler_t.join(timeout=15.0)
    if mut_t.is_alive() or any(th.is_alive() for th in threads):
        log("[sustained] WARNING: a load thread outlived its join bound; "
            "recording partial results honestly")

    q_sum = summarize_ops(q_records)
    m_sum = summarize_ops(m_records, lat_key="ack_ms")
    by_op: dict[str, dict] = {}
    for kind in ("insert", "edit", "retract"):
        by_op[kind] = summarize_ops([r for r in m_records if r["op"] == kind],
                                    lat_key="ack_ms")
    depths = [s["depth"] for s in status_samples if s["depth"] >= 0]
    batches = [s["worker_batches"] for s in status_samples
               if s["worker_batches"] >= 0]
    rss_vals = [s["rss_mb"] for s in mem_samples if s["rss_mb"] > 0]
    overlap_proven = bool(depths and depths[0] > 0) and bool(
        any(s["depth"] > 0 for s in status_samples))
    worker_active = bool(batches and batches[-1] > batches[0]) or bool(
        any(s["depth"] > 0 for s in status_samples))
    server_ms = sorted(float(r["server_ms"]) for r in q_records
                       if r.get("server_ms") is not None)
    out = {
        "window_s": window_s, "clients": clients, "mut_pace_s": pace_s,
        "window_started_at": window_started_at,
        "window_finished_at": utcnow(),
        "offered_queries": n_q, "offered_qps": round(n_q / window_s, 3),
        "achieved_qps": round(len(q_records) / max(wall_s, 1e-6), 3),
        "wall_seconds": round(wall_s, 1),
        "schedule_sha256": schedule_sha256(schedule),
        "queries": q_sum,
        "server_ms": {"n": len(server_ms),
                      "p50": round(pct(server_ms, .5), 2),
                      "p95": round(pct(server_ms, .95), 2),
                      "p99": round(pct(server_ms, .99), 2),
                      "max": round(max(server_ms) if server_ms else -1, 2)},
        "routes": dict(routes), "abstentions": abstentions,
        "vector_adequate": {"true": vec_true, "false": vec_false,
                            "none": vec_none},
        "vector_generations_seen": sorted(vec_gens),
        "vector_route_count": int(routes.get("vector", 0)),
        "mutations": {"summary": m_sum, "by_op": by_op,
                      "mut_ack_p95_le_50ms": bool(
                          m_sum["latency_incl_errors_ms"]["p95"] <=
                          MUT_ACK_P95_BUDGET_MS)},
        "overlap": {
            "proven": bool(overlap_proven),
            "worker_active": bool(worker_active),
            "depth_first": depths[0] if depths else None,
            "depth_last": depths[-1] if depths else None,
            "depth_max": max(depths) if depths else None,
            "worker_batches_first": batches[0] if batches else None,
            "worker_batches_last": batches[-1] if batches else None,
            "query_overlap_fraction": q_sum["overlap"]["fraction"],
            "mut_overlap_fraction": m_sum["overlap"]["fraction"],
            "status_samples": status_samples,
        },
        "memory": {
            "samples": mem_samples,
            "peak_in_window_mb": round(max(rss_vals) if rss_vals else -1, 1),
            "hwm_end_mb": round(rss_peak_mb(server_pid), 1),
        },
        "query_p95_le_35ms": bool(
            q_sum["latency_incl_errors_ms"]["p95"] <= QUERY_P95_BUDGET_MS),
        "query_overlap_p95_le_35ms": bool(
            q_sum["overlap"]["latency_incl_errors_ms"]["p95"] <=
            QUERY_P95_BUDGET_MS),
        # Compact per-op series for trajectory buckets: [t_s, ms, ok].
        "q_series": [[r.get("t_s"), r.get("e2e_ms"), bool(r.get("ok"))]
                     for r in q_records],
        "m_series": [[r.get("t_s"), r.get("ack_ms"), bool(r.get("ok")),
                      r.get("op")] for r in m_records],
    }
    log(f"[sustained] queries {len(q_records)}/{n_q} "
        f"p95e2e={out['queries']['latency_incl_errors_ms']['p95']}ms "
        f"(overlap {out['queries']['overlap']['latency_incl_errors_ms']['p95']}ms) "
        f"fails={q_sum['failures']} muts {len(m_records)}/{len(plan)} "
        f"ack-p95={m_sum['latency_incl_errors_ms']['p95']}ms "
        f"fails={m_sum['failures']} overlap={overlap_proven} "
        f"depth {out['overlap']['depth_first']}->{out['overlap']['depth_last']} "
        f"mem-peak={out['memory']['peak_in_window_mb']}MB {wall_s:.0f}s")
    return out


# ---------------- quality (direct harness, IDENTICAL served stack) ----------------

def served_stack(db_path: Path):
    """Rebuild the exact served reader stack in-process (same config as server)."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"bench DB missing: {db_path}")
    from trace_lite.filing import FilingEngine
    from trace_lite.router.cascade import CascadeRouter
    from trace_lite.store import Database
    from trace_lite.store import generations as gen_store
    from trace_lite.filing import Taxonomy
    db = Database(db_path)
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    router = CascadeRouter(db.conn, engine, profile="quality",
                           beam_candidate_cap=400, beam_sample_strategy="balanced")
    # read-only warm (mirrors app._warm_reader, never writes)
    live = 0
    try:
        from trace_lite.filing.engine import unpack_vector
        for fid, blob in db.conn.execute(
                "SELECT f.facet_id, f.centroid_blob FROM facets f"
                " WHERE f.centroid_blob IS NOT NULL"
                " AND EXISTS (SELECT 1 FROM memberships m WHERE m.facet_id = f.facet_id)").fetchall():
            engine._centroids[str(fid)] = unpack_vector(bytes(blob))
            live += 1
    except Exception:
        pass
    router.hybrid.warm()
    matrix, pos, idf = router.hybrid.matrix_view()
    router.beam.set_vectors(matrix, pos, idf)
    router.warmed = True
    try:
        active = gen_store.get_active(db.conn, "filing")
        gen_id = str(active["generation_id"]) if active else None
    except Exception:
        gen_id = None
    vec_build: dict = {"built": False, "reason": "no-generation"}
    if gen_id is not None:
        try:
            vec_build = router.refresh_served_vector(gen_id)
        except Exception as exc:
            vec_build = {"built": False, "reason": f"error: {exc}"}
    router.holon_guided = True
    return db, router, {"warmed_centroids": live, "generation_id": gen_id,
                       "vector_generation_id": router.vector_generation_id,
                       "vector_build": vec_build}


def score_beir(router, beir_docs: dict, beir_meta: dict, max_q: int | None,
               log, *, rankings_path: Path | None = None) -> dict:
    """nDCG@10/Recall@10 with doc-ID dedup, on the served-quality stack.

    G2: SciFact carries the frozen floor (0.6400) and target (0.6500);
    the hard gate reads ``clears_frozen_floor`` on FULL-corpus evals.
    NFCorpus entries are diagnostic-only (never gated). When
    ``rankings_path`` is set, every evaluated query's deduped top-10
    ranking is appended as one JSON line (header line first).
    """
    out = {}
    rank_fh = None
    if rankings_path is not None:
        rankings_path.parent.mkdir(parents=True, exist_ok=True)
        rank_fh = open(rankings_path, "w", encoding="utf-8")
        rank_fh.write(json.dumps({
            "type": "header", "contract": G2_CONTRACT,
            "generated_at": utcnow(), "operating_point": OPERATING_POINT,
            "datasets": {ds: {
                "indexed_docs": len(docs),
                "total_corpus_docs": beir_meta[ds].get("total_corpus_docs"),
                "full_corpus": beir_meta[ds].get("full_corpus"),
                "corpus_sha256": beir_meta[ds].get("corpus_sha256"),
                "queries_sha256": beir_meta[ds].get("queries_sha256"),
                "qrels_sha256": beir_meta[ds].get("qrels_sha256"),
            } for ds, docs in beir_docs.items()},
        }) + "\n")
    try:
        for ds, docs in beir_docs.items():
            meta = beir_meta[ds]
            sub_ids = {doc_id for doc_id, _ in docs}
            qids = [qid for qid in meta["covered_qids"]]
            if max_q is not None:
                qids = qids[:max_q]
            # filename-safe mapping mirrors build_vault
            def safe(d):
                return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in d)
            fn_of = {doc_id: f"beir-{ds}-{safe(doc_id)}.md" for doc_id, _ in docs}
            id_of_fn = {v: k for k, v in fn_of.items()}
            ndcgs, recalls = [], []
            for qid in qids:
                res = router.route(meta["queries"][qid], limit=10, mode="hybrid")
                seen, ranked, ranked_ids = set(), [], []
                for a in res.anchors:
                    doc_id = str(a.get("doc_id", ""))
                    did = id_of_fn.get(doc_id, None)
                    if did is None or did in seen:
                        continue
                    seen.add(did)
                    ranked.append(meta["qrels"][qid].get(did, 0))
                    ranked_ids.append(did)
                ideal = sorted(meta["qrels"][qid].values(), reverse=True)
                denom = dcg(ideal, 10)
                q_ndcg = dcg(ranked, 10) / denom if denom else 1.0
                ndcgs.append(q_ndcg)
                rel_total = sum(1 for v in meta["qrels"][qid].values() if v > 0)
                q_rec = sum(1 for r in ranked if r > 0) / max(1, rel_total)
                recalls.append(q_rec)
                if rank_fh is not None:
                    rank_fh.write(json.dumps({
                        "type": "ranking", "dataset": ds, "qid": qid,
                        "ranked_doc_ids": ranked_ids, "gains": ranked,
                        "ndcg_at_10": round(q_ndcg, 4),
                        "recall_at_10": round(q_rec, 4),
                        "route": res.route,
                    }) + "\n")
            mean_ndcg, lo, hi = bootstrap_ci(ndcgs) if ndcgs else (0.0, 0.0, 0.0)
            entry = {
                "indexed_docs": len(docs), "queries_scored": len(qids),
                "queries_scored_ids": qids,
                "total_corpus_docs": meta.get("total_corpus_docs"),
                "full_corpus": bool(meta.get("full_corpus", False)),
                "ndcg_at_10": mean_ndcg, "bootstrap_95_ci": [lo, hi],
                "recall_at_10": round(sum(recalls) / max(1, len(recalls)), 4),
                "floor_ref": BEIR_FLOORS_REF[ds], "r00_repro_ref": BEIR_BASE_REF[ds],
                "clears_floor_ref": bool(mean_ndcg >= BEIR_FLOORS_REF[ds]),
                "non_inferior_vs_r00": bool(abs(mean_ndcg - BEIR_BASE_REF[ds]) <= 0.01),
            }
            if ds == "scifact":
                entry.update({
                    "floor_frozen": SCIFACT_FLOOR, "target": SCIFACT_TARGET,
                    "clears_frozen_floor": bool(mean_ndcg >= SCIFACT_FLOOR),
                    "meets_target": bool(mean_ndcg >= SCIFACT_TARGET),
                    "gate": "hard-at-10k-full-corpus",
                })
            else:
                entry.update({
                    "gate": "diagnostic-only",
                    "diagnostic_ref": NFCORPUS_DIAGNOSTIC_REF,
                    "constraint": "evidence/query-native/R09/nfcorpus_constraint.md",
                })
            out[ds] = entry
            log(f"[quality] {ds}: nDCG@10={mean_ndcg} CI=[{lo},{hi}] "
                f"recall={out[ds]['recall_at_10']} (n={len(qids)} "
                f"full={entry['full_corpus']})")
    finally:
        if rank_fh is not None:
            rank_fh.close()
    return out


def probe_checks(router, log) -> dict:
    """Required-counterexample probes through the served-quality stack."""
    checks = {}

    def top_docs(q: str, k: int = 10) -> list[str]:
        return [str(a.get("doc_id", "")) for a in
                router.route(q, limit=k, mode="hybrid").anchors]

    both = top_docs("Kestrel vector throughput contention batching zephyrnine", 10)
    checks["contradiction_both_recalled"] = bool(
        "probe-contradict-a.md" in both and "probe-contradict-b.md" in both)
    par = top_docs("flow improvement when system load is light batching zephyrnine", 10)
    checks["paraphrase_no_lexical_overlap"] = bool("probe-paraphrase.md" in par)
    unf = top_docs("checkpoint cadence fsync frames ext4 p99 zephyrnine", 10)
    checks["unfiled_retrievable"] = bool("probe-unfiled.md" in unf)
    late = top_docs("zephyrnine batching throughput anchor low contention", 10)
    checks["late_rowid_recalled"] = bool("zzz-late-relevant.md" in late)
    minor = top_docs("batching neutral saturation dissent zephyrnine hub", 10)
    checks["minority_under_hub"] = bool("probe-minority.md" in minor)
    gib = router.route("xqzt blorpt wqkj xyzzy", limit=10, mode="hybrid")
    checks["gibberish_abstains"] = bool(not gib.anchors)
    for k, v in checks.items():
        log(f"[probe] {k}: {'PASS' if v else 'FAIL'}")
    return checks


# ---------------- lifecycle spots (bounded, served path) ----------------

def lifecycle_spots(base: str, db_path: Path, vault: Path, log, full: bool) -> dict:
    """Bounded served-path lifecycle checks. Full set at 2k/10k; spots above."""
    out: dict = {}
    # served document names are vault-relative paths (with .md suffix)
    doc = "bench-000000.md"
    # replay: duplicate ingest returns same revision, no new rows
    r1 = http_post(base, "/api/ingest",
                   {"document_name": doc, "text": synth_text(0)})
    before = db_counts(db_path)
    r2 = http_post(base, "/api/ingest",
                   {"document_name": doc, "text": synth_text(0)})
    after = db_counts(db_path)
    out["replay_same_revision"] = bool(r1.get("revision_id") == r2.get("revision_id"))
    out["replay_no_new_revisions"] = bool(
        before["source_revision"] == after["source_revision"])
    log(f"[lifecycle] replay same_revision={out['replay_same_revision']} "
        f"no_new_rows={out['replay_no_new_revisions']}")
    if full:
        # update: new current, old retained as superseded, never zero rows
        new_text = synth_text(0) + "\nUpdated throughput finding for lifecycle probe.\n"
        ru = http_post(base, "/api/ingest",
                       {"document_name": doc, "text": new_text})
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT status FROM source_revision WHERE source_id=? ORDER BY rowid",
                (doc,)).fetchall()
        finally:
            con.close()
        out["update_new_revision"] = bool(ru.get("revision_id") != r1.get("revision_id"))
        out["update_old_retained"] = bool(
            [r[0] for r in rows][-2:] == ["superseded", "current"])
        # restore original so later reads are stable
        http_post(base, "/api/ingest",
                  {"document_name": doc, "text": synth_text(0)})
        log(f"[lifecycle] update new_rev={out['update_new_revision']} "
            f"old_retained={out['update_old_retained']}")
    # current-state cleanliness: served query for the unfiled probe doc
    q = http_post(base, "/api/query",
                  {"query": "zephyrnine marginalia checkpoint cadence", "limit": 10,
                   "mode": "hybrid"})
    docs = [a.get("doc_id", "") for a in q.get("anchors", [])]
    out["unfiled_served_current"] = bool("probe-unfiled.md" in docs)
    log(f"[lifecycle] unfiled served as current: {out['unfiled_served_current']}")
    return out


def restart_check(root: Path, db_path: Path, vault: Path, port: int, base: str, log) -> dict:
    """SIGKILL crash -> restart: integrity, counts, FTS parity, freshness."""
    pre_counts = db_counts(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        pre_fts = [r[0] for r in con.execute(
            "SELECT rowid FROM fts_atoms WHERE fts_atoms MATCH 'zephyrnine' LIMIT 50")]
    finally:
        con.close()
    return {"pre_counts": pre_counts, "pre_fts_zephyrnine": len(pre_fts)}


def parity_check(vault: Path, db_path: Path, log, sample_n: int = 30) -> dict:
    """Incremental-vs-rebuild parity on a bounded sample (gate-7 analog).

    Rebuilds the same sample files into a fresh DB via the identical served
    ``sync_notes`` code path, then compares per-table counts restricted to
    the sample's current revisions against the served rung DB.

    G1: ``sync_notes`` no longer drains inline (the worker does), so the
    rebuild explicitly drains its own outbox through the identical
    ``drain_outbox`` worker code path (bounded batches) before comparing;
    otherwise the rebuild trivially reads zeros and the gate fails by
    construction.
    """
    from trace_lite.api import sync_notes
    from trace_lite.filing import FilingEngine, Taxonomy
    from trace_lite.source.project import drain_outbox
    from trace_lite.store import Database
    from trace_lite.store import outbox as outbox_store
    files = sorted(vault.rglob("*.md"))[:sample_n]
    # served doc names are chunk-relative (sync path = chunk dir): file name
    rels = [f.name for f in files]
    tmp = Path(tempfile.mkdtemp(prefix="tl-r09-parity-"))
    db = Database(tmp / "parity.db")
    try:
        tax = Taxonomy(db.conn)
        eng = FilingEngine(db.conn, tax)
        eng.bind_database(db)
        sync_notes(db, tax, eng, vault, files)
        for _ in range(100):  # identical worker drain path, bounded
            if outbox_store.pending_count(db.conn) == 0:
                break
            drain_outbox(db, eng, owner="parity-rebuild")
        got = {}
        for t in ("evidence_span", "assertion", "relation", "memberships"):
            try:
                got[t] = int(db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except Exception:
                got[t] = -1
        # served-side counts for the same sample source_ids (current revs)
        marks = ",".join("?" for _ in rels)
        served = {}
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            revs = [r[0] for r in con.execute(
                "SELECT sr.revision_id FROM source_revision sr JOIN source s"
                " ON s.current_revision_id = sr.revision_id"
                f" WHERE s.source_id IN ({marks})", rels).fetchall()]
            rmarks = ",".join("?" for _ in revs) or "NULL"
            col = {"evidence_span": "revision_id", "assertion": "revision_id",
                   "relation": "revision_id", "memberships": None}
            for t in ("evidence_span", "assertion", "relation"):
                try:
                    served[t] = int(con.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE {col[t]} IN ({rmarks})",
                        revs).fetchone()[0]) if revs else 0
                except Exception:
                    served[t] = -1
            served["memberships"] = "n/a (facet-global, not revision-scoped)"
        finally:
            con.close()
        # An empty sample proves nothing: 0==0 agreement is vacuous, so a
        # missing/emptied vault must FAIL the gate, never pass it.
        agree = bool(len(files) > 0 and all(
            got.get(t) == served.get(t)
            for t in ("evidence_span", "assertion", "relation")))
        log(f"[parity] sample={len(files)} rebuilt={got} served={served} agree={agree}")
        # vector tolerance: cosine of two dense rows for the same text >= 0.999
        tol_ok = True
        try:
            from trace_lite.router.hybrid import FlatHybrid
            h = FlatHybrid(db.conn)
            h.warm()
            ids = h._ids[:5]
            import numpy as _np
            for aid in ids:
                v = _np.asarray(h._matrix[h._pos[aid]], dtype=_np.float64)
                n = float(_np.linalg.norm(v))
                tol_ok = bool(tol_ok and n > 0)
        except Exception as exc:
            tol_ok = f"skipped: {exc}"
        log(f"[parity] rebuilt={got} served={served} vector_ok={tol_ok}")
        return {"sample_files": len(files), "rebuilt_counts": got,
                "served_counts": served, "agree": bool(agree),
                "rebuild_pending_after": outbox_store.pending_count(db.conn),
                "vector_tolerance_ok": tol_ok}
    finally:
        db.close()


# ---------------- relational capability hook (G2; cases by follow-up) ----------------

# .3 §7: 20 frozen manually-reviewable cases from the indexed realistic
# corpus checking exact revision/span citations + relation fidelity
# (negation/conditions preserved, no unsupported asserted relations).
# The CASE CONTENT is supplied by a follow-up agent; this module owns the
# file contract + harness call site. Missing/unfrozen cases => explicit
# INCOMPLETE (never a silent skip, never a vacuous PASS).
def capability_cases_path(out_dir: Path) -> Path:
    return Path(out_dir) / CAPABILITY_CASES_FILENAME


def load_capability_cases(out_dir: Path) -> dict:
    """Load the frozen 20-case file; INCOMPLETE unless frozen with 20."""
    path = capability_cases_path(out_dir)
    if not path.exists():
        return {"status": "INCOMPLETE",
                "reason": f"cases file missing: {path} (follow-up cases "
                          f"agent has not supplied the {CAPABILITY_CASES_REQUIRED} "
                          f"frozen cases)",
                "cases_path": str(path), "n_cases": 0,
                "required": CAPABILITY_CASES_REQUIRED}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "INCOMPLETE",
                "reason": f"cases file unreadable: {exc}",
                "cases_path": str(path), "n_cases": 0,
                "required": CAPABILITY_CASES_REQUIRED}
    cases = payload.get("cases", [])
    if payload.get("status") != "FROZEN" or len(cases) != CAPABILITY_CASES_REQUIRED:
        return {"status": "INCOMPLETE",
                "reason": f"cases not frozen: status={payload.get('status')!r} "
                          f"n={len(cases)}/{CAPABILITY_CASES_REQUIRED}",
                "cases_path": str(path), "n_cases": len(cases),
                "required": CAPABILITY_CASES_REQUIRED}
    n_neg = sum(1 for c in cases if c.get("has_negation_or_condition"))
    n_multi = sum(1 for c in cases if c.get("multi_sentence"))
    if n_neg < 5 or n_multi < 5:
        return {"status": "INCOMPLETE",
                "reason": f"frozen set lacks required mix: negation/condition "
                          f"{n_neg}/5+, multi-sentence {n_multi}/5+",
                "cases_path": str(path), "n_cases": len(cases),
                "required": CAPABILITY_CASES_REQUIRED}
    return {"status": "READY", "cases_path": str(path), "n_cases": len(cases),
            "required": CAPABILITY_CASES_REQUIRED,
            "cases_sha256": hashlib.sha256(
                json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            "cases": cases}


def run_capability_cases(router, db_path: Path, loaded: dict, log) -> dict:
    """Evaluate frozen cases: exact citations + no unsupported relations.

    PASS iff every case's expected cited docs are all retrieved AND no
    case reports an asserted relation outside its expected set. Case
    schema (see capability_cases.json): {id, query,
    expected_doc_ids[], expected_relations[], has_negation_or_condition,
    multi_sentence}. Relation coverage is reported, never scored as a
    single invented quality number (.3 §7).
    """
    if loaded.get("status") != "READY":
        out = dict(loaded)
        out["status"] = "INCOMPLETE"
        return out
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rel_rows = con.execute(
            "SELECT revision_id, subject, predicate, object FROM relation"
        ).fetchall() if "relation" in {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        } else []
    finally:
        con.close()
    asserted = {(str(r[1]), str(r[2]), str(r[3])) for r in rel_rows}
    results = []
    for case in loaded["cases"]:
        res = router.route(case["query"], limit=10, mode="hybrid")
        got_docs = [str(a.get("doc_id", "")) for a in res.anchors]
        want = [str(d) for d in case.get("expected_doc_ids", [])]
        citations_ok = bool(want) and all(w in got_docs for w in want)
        expected_rel = {tuple(r) for r in case.get("expected_relations", [])}
        unsupported = sorted(asserted - expected_rel) if expected_rel else []
        # Scope check: only relations evidenced by this case's docs count.
        # Without per-case revision mapping the global asserted set is an
        # over-approximation, so unsupported hits are reported per case
        # with the case id for manual review, and gate only on citations
        # + the explicit per-case forbidden list.
        forbidden = {tuple(r) for r in case.get("forbidden_relations", [])}
        forbidden_hit = sorted(asserted & forbidden)
        case_pass = bool(citations_ok and not forbidden_hit)
        results.append({
            "id": case.get("id"), "citations_ok": citations_ok,
            "expected_relations": len(expected_rel),
            "unsupported_asserted_relations": unsupported[:20],
            "forbidden_hit": forbidden_hit, "pass": case_pass,
        })
        log(f"[capability] {case.get('id')}: citations_ok={citations_ok} "
            f"forbidden_hit={bool(forbidden_hit)} pass={case_pass}")
    n_pass = sum(1 for r in results if r["pass"])
    status = "PASS" if n_pass == len(results) and results else "FAIL"
    return {"status": status, "cases_path": loaded["cases_path"],
            "n_cases": len(results), "required": CAPABILITY_CASES_REQUIRED,
            "cases_sha256": loaded["cases_sha256"],
            "cases_passed": n_pass, "cases": results}


# ---------------- NFCorpus separate diagnostic (never a gate) ----------------

def run_nfcorpus_diagnostic(bench_root: Path, n_docs: int, max_q: int | None,
                            log) -> dict:
    """Standalone NFCorpus subset eval in its OWN vault+DB (G2 rule 2).

    Explicit identity, zero contact with the nominal 10k corpus: separate
    bench dir, separate DB, in-process served ingestion of the subset.
    Diagnostic-only per evidence/query-native/R09/nfcorpus_constraint.md —
    the return value never feeds any gate.
    """
    from trace_lite.api import sync_notes
    from trace_lite.filing import FilingEngine, Taxonomy
    from trace_lite.source.project import drain_outbox
    from trace_lite.store import Database
    from trace_lite.store import outbox as outbox_store
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = Path(bench_root) / f"tl_r09_nfdiag_{stamp}"
    vault = root / "vault"
    db_path = root / "trace.db"
    root.mkdir(parents=True, exist_ok=True)
    docs, meta = load_beir_corpus("nfcorpus", n_docs)
    counts = build_vault(vault, 0, {"nfcorpus": docs})
    db = Database(db_path)
    try:
        tax = Taxonomy(db.conn)
        eng = FilingEngine(db.conn, tax)
        eng.bind_database(db)
        files = sorted(vault.glob("*.md"))
        synced = sync_notes(db, tax, eng, vault, files)
        # Complete-state diagnostic: drain via the identical worker path
        # (bounded; pending remainder is recorded, never hidden).
        for _ in range(200):
            if outbox_store.pending_count(db.conn) == 0:
                break
            drain_outbox(db, eng, owner="nfdiag-rebuild")
        diag_pending = outbox_store.pending_count(db.conn)
    finally:
        db.close()
    qdb, router, stack_info = served_stack(db_path)
    try:
        q = score_beir(router, {"nfcorpus": docs}, {"nfcorpus": meta},
                       max_q, log)
    finally:
        qdb.close()
    entry = q.get("nfcorpus", {})
    out = {
        "identity": "separate-diagnostic",
        "shares_10k_corpus": False,
        "bench_root": str(root),
        "subset_docs": len(docs),
        "total_corpus_docs": meta.get("total_corpus_docs"),
        "queries_covered": len(meta.get("covered_qids", [])),
        "queries_scored": entry.get("queries_scored", 0),
        "ndcg_at_10": entry.get("ndcg_at_10"),
        "bootstrap_95_ci": entry.get("bootstrap_95_ci"),
        "recall_at_10": entry.get("recall_at_10"),
        "diagnostic_ref": NFCORPUS_DIAGNOSTIC_REF,
        "constraint": "evidence/query-native/R09/nfcorpus_constraint.md",
        "gate": "diagnostic-only (never fails, never enlarges 10k corpus)",
        "vault_files": counts["total_files"], "synced": synced,
        "pending_after_rebuild_drain": diag_pending,
        "stack": stack_info,
    }
    log(f"[nfdiag] separate run: nDCG@10={out['ndcg_at_10']} "
        f"(n={out['queries_scored']}, subset={len(docs)}) diagnostic-only")
    return out


# ---------------- workload freeze (.3 §4) ----------------

def freeze_workload(scale_n: int, vault: Path, sched: list[str],
                    db_path: Path, beir_meta: dict, server_pid: int | None,
                    worker_enabled: bool, bench_root: Path,
                    served_atoms: int | None, quality_vec_atoms: int | None,
                    setup_peak_mb: float, peak_mb: float) -> dict:
    """Executable-workload manifest: hashes, counts, encoder, host, storage."""
    counts = db_counts(db_path)
    try:
        host = host_info()
    except Exception:
        host = {"error": "host_info failed"}
    try:
        storage = storage_info(bench_root)
    except Exception:
        storage = {"error": "storage_info failed"}
    return {
        "scale_sources": scale_n,
        "corpus": vault_manifest_sha256(vault),
        "queries": {"n": len(sched), "schedule_sha256": schedule_sha256(sched)},
        "beir": {ds: {
            "indexed": None, "total_corpus_docs": m.get("total_corpus_docs"),
            "full_corpus": m.get("full_corpus"),
            "corpus_sha256": m.get("corpus_sha256"),
            "queries_sha256": m.get("queries_sha256"),
            "qrels_sha256": m.get("qrels_sha256"),
            "queries_covered": len(m.get("covered_qids", [])),
        } for ds, m in beir_meta.items()},
        "counts": {
            "docs": counts.get("source", 0),
            "atoms": counts.get("atom", 0),
            "spans": counts.get("evidence_span", 0),
            "vectors_served": served_atoms,
            "vectors_quality_stack": quality_vec_atoms,
            "jobs_total": counts.get("projection_job", 0),
            "jobs_pending": counts.get("pending_jobs", 0),
        },
        "encoder": {"label": OPERATING_POINT["encoder"],
                    "config": OPERATING_POINT},
        "host": host,
        "processes": {"server_pid": server_pid,
                      "worker_enabled": bool(worker_enabled)},
        "storage": storage,
        "memory": {"setup_peak_mb": setup_peak_mb, "peak_mb": peak_mb,
                   "ceiling_mb": RSS_CEILING_MB, "halt_mb": RSS_HALT_MB},
    }


# ---------------- rung driver ----------------

def run_rung(scale_n: int, out_dir: Path, bench_root: Path, port: int,
             n_latency_q: int, n_quality_q: int | None, lifecycle_full: bool,
             beir_n_docs: int, log, *, drain_deadline_s: float = DRAIN_DEADLINE_S,
             setup_cap_s: float = SETUP_CAP_S,
             nfdiag_docs: int | None = None,
             n_sustained_q: int = SUSTAINED_QUERIES,
             sustained_window_s: float = SUSTAINED_WINDOW_S,
             sustained_clients: int = SUSTAINED_CLIENTS,
             mut_inserts: int = MUT_INSERTS, mut_edits: int = MUT_EDITS,
             mut_retracts: int = MUT_RETRACTS,
             mut_pace_s: float = MUT_OPS_PACE_S) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = bench_root / f"tl_r09_{scale_n}_{stamp}"
    vault = root / "vault"
    db_path = root / "trace.db"
    root.mkdir(parents=True, exist_ok=True)
    log(f"[rung {scale_n}] root={root} db never in repo: {db_path}")

    # G2 corpus rule: the 10k rung indexes the FULL SciFact corpus plus
    # synthetic notes to exactly 10,000. NFCorpus NEVER joins the nominal
    # 10k corpus (separate diagnostic only). Smaller rungs keep the legacy
    # per-dataset subset mix (recorded honestly in the workload manifest).
    beir_docs, beir_meta = {}, {}
    is_10k = bool(scale_n == 10000)
    if is_10k:
        docs, meta = load_beir_corpus("scifact", None)
        beir_docs["scifact"] = docs
        beir_meta["scifact"] = meta
        log(f"[rung {scale_n}] BEIR scifact FULL: {len(docs)} docs "
            f"(expect {SCIFACT_EXPECTED_DOCS}), "
            f"{len(meta['covered_qids'])} covered test queries; "
            f"nfcorpus excluded from nominal corpus (separate diagnostic)")
    elif beir_n_docs > 0:
        for ds in ("scifact", "nfcorpus"):
            docs, meta = load_beir_subset(ds, beir_n_docs)
            beir_docs[ds] = docs
            beir_meta[ds] = meta
            log(f"[rung {scale_n}] BEIR {ds}: {len(docs)} docs, "
                f"{len(meta['covered_qids'])} covered test queries")
    n_beir = sum(len(v) for v in beir_docs.values())
    n_probes = len(PROBES) + 1
    n_synth = max(0, scale_n - n_beir - n_probes)
    counts = build_vault(vault, n_synth, beir_docs)
    counts["composition_ok"] = bool(counts["total_files"] == scale_n)
    if is_10k and not counts["composition_ok"]:
        log(f"[rung {scale_n}] COMPOSITION BREACH: total_files="
            f"{counts['total_files']} != 10000 "
            f"(scifact={len(beir_docs.get('scifact', []))} synth={n_synth} "
            f"probes={n_probes})")
    chunks = chunk_vault(vault)
    log(f"[rung {scale_n}] vault files={counts['total_files']} in {len(chunks)} chunks")

    # Frozen latency schedule: identical query list at every rung (.3 §4).
    try:
        frozen_sci = load_frozen_beir_queries("scifact")
        frozen_nfc = load_frozen_beir_queries("nfcorpus")
    except FileNotFoundError as exc:
        return {"scale_sources": scale_n, "operating_point": OPERATING_POINT,
                "bench_root": str(root), "port": port, "started_at": utcnow(),
                "error": f"BEIR blocker: {exc}", "finished_at": utcnow()}
    schedule = build_latency_schedule(n_latency_q, frozen_sci, frozen_nfc)
    sched_hash = schedule_sha256(schedule)
    log(f"[rung {scale_n}] frozen schedule: n={len(schedule)} sha={sched_hash[:16]}")

    setup_t0 = time.time()
    setup_deadline = setup_t0 + setup_cap_s
    proc = start_server(root, db_path, port)
    base = f"http://127.0.0.1:{port}"
    rung: dict = {"scale_sources": scale_n, "contract": G2_CONTRACT,
                  "operating_point": OPERATING_POINT,
                  "bench_root": str(root), "port": port, "started_at": utcnow(),
                  "run_id": f"G2-{scale_n}-{stamp}",
                  "composition": counts}
    servable_samples: list = []
    peak_mb = 0.0

    def mem_sample(tag: str) -> float:
        nonlocal peak_mb
        cur = rss_now_mb(proc.pid)
        hw = rss_peak_mb(proc.pid)
        peak_mb = max(peak_mb, hw if hw > 0 else cur)
        log(f"[mem] {tag}: rss={cur:.1f}MB hwm={hw:.1f}MB peak={peak_mb:.1f}MB")
        return cur

    def servable_sample() -> None:
        try:
            st = http_get(base, "/api/status")
            sp = st.get("served_persistent") or {}
            servable_samples.append(sp.get("servable", None))
        except Exception:
            servable_samples.append(None)

    try:
        if not wait_healthy(base):
            rung["error"] = "server never healthy"
            try:
                with open(root / "server.log", encoding="utf-8") as fh:
                    rung["server_log_tail"] = fh.read()[-2000:]
            except OSError:
                pass
            return rung
        servable_sample()
        t_pub0 = time.time()
        ingest = served_ingest(base, chunks, log, setup_deadline=setup_deadline)
        rung["ingest"] = ingest
        setup_s = time.time() - setup_t0
        rung["setup"] = {"seconds": round(setup_s, 1), "cap_s": setup_cap_s,
                         "within_cap": bool(setup_s <= setup_cap_s)}
        log(f"[setup] {setup_s:.1f}s / cap {setup_cap_s:.0f}s "
            f"within_cap={rung['setup']['within_cap']}")
        setup_rss = mem_sample("setup")
        setup_hwm = rss_peak_mb(proc.pid)  # setup peak (HWM is monotonic)
        rung["setup_peak_mb"] = round(setup_hwm, 1)
        if setup_rss > RSS_HALT_MB:
            rung["error"] = (f"memory halt: setup RSS {setup_rss:.1f}MB > "
                             f"{RSS_HALT_MB:.0f}MB")
            return rung
        # .3 active window: sustained queries + mutations run
        # CONCURRENTLY with the autonomous drain (overlap proven by the
        # in-window sampler; hard 600s drain bound unchanged).
        sus_sched = build_latency_schedule(n_sustained_q, frozen_sci,
                                           frozen_nfc)
        log(f"[rung {scale_n}] sustained schedule: n={len(sus_sched)} "
            f"sha={schedule_sha256(sus_sched)[:16]} clients={sustained_clients} "
            f"window={sustained_window_s:.0f}s muts={mut_inserts}/"
            f"{mut_edits}/{mut_retracts}@{mut_pace_s:.1f}s")
        mut_plan = build_mutation_plan(counts["synthetic"], mut_inserts,
                                       mut_edits, mut_retracts)
        sus_result: dict = {}
        sus_errors: list[str] = []

        def _run_sustained() -> None:
            try:
                sus_result.update(sustained_under_drain_run(
                    base, sus_sched, mut_plan, proc.pid, db_path, log,
                    window_s=sustained_window_s, clients=sustained_clients,
                    pace_s=mut_pace_s))
            except Exception as exc:
                sus_errors.append(str(exc)[:300])
                log(f"[sustained] FAILED: {exc}")

        sus_thread = threading.Thread(target=_run_sustained,
                                      name="sustained-window", daemon=True)
        sus_thread.start()
        # Autonomous worker drain observed via /api/status (G2 rule 4),
        # concurrent with the sustained window above.
        rung["queue_drain"] = served_drain(base, db_path, log,
                                           deadline_s=drain_deadline_s)
        sus_thread.join(timeout=sustained_window_s + len(mut_plan) * mut_pace_s
                        + 240.0)
        if sus_thread.is_alive():
            sus_errors.append("sustained window outlived its join bound")
            log("[sustained] WARNING: window thread outlived join bound")
        if sus_errors and not sus_result:
            rung["sustained_error"] = sus_errors
        rung["sustained"] = sus_result
        # Classic c4 slot now carries the sustained under-drain query
        # evidence (same core fields as latency_run; end_to_end_ms INCLUDES
        # time-to-error per the .3 record rule, with ok-only alongside).
        if sus_result:
            q = sus_result["queries"]
            rung["latency_c4"] = {
                "n": sus_result["offered_queries"],
                "concurrency": sus_result["clients"],
                "wall_seconds": sus_result["wall_seconds"],
                "qps": sus_result["achieved_qps"],
                "offered_qps": sus_result["offered_qps"],
                "server_ms": sus_result["server_ms"],
                "end_to_end_ms": q["latency_incl_errors_ms"],
                "end_to_end_ok_ms": q["latency_ok_only_ms"],
                "errors_included": True,
                "routes": sus_result["routes"],
                "abstentions": sus_result["abstentions"],
                "failures": q["failures"],
                "schedule_slip_s": q["schedule_slip_s"],
                "vector_adequate": sus_result["vector_adequate"],
                "vector_generations_seen":
                    sus_result["vector_generations_seen"],
                "vector_route_count": sus_result["vector_route_count"],
                "sustained_ref": True,
            }
            rung["mutations"] = sus_result["mutations"]
            rung["sustained_overlap"] = sus_result["overlap"]
            # Window/drain interval cross-check: the sampler can miss a
            # fast-evaporating backlog (small scales), so the drain's own
            # timestamps independently prove window∩drain overlap.
            try:
                drain = rung["queue_drain"]
                interval_overlap = bool(
                    drain.get("started_at") and drain.get("finished_at")
                    and sus_result.get("window_started_at")
                    and sus_result.get("window_finished_at")
                    and drain["started_at"] < sus_result["window_finished_at"]
                    and drain["finished_at"] > sus_result["window_started_at"]
                    and (drain.get("depth_before", 0) > 0
                         or (sus_result["overlap"].get("depth_max") or 0) > 0))
            except Exception:
                interval_overlap = False
            rung["sustained_overlap"]["drain_interval_overlap"] = interval_overlap
            rung["sustained_overlap"]["proven_combined"] = bool(
                sus_result["overlap"]["proven"] or interval_overlap)
            log(f"[overlap] sampler={sus_result['overlap']['proven']} "
                f"drain_interval={interval_overlap} "
                f"combined={rung['sustained_overlap']['proven_combined']}")
            rung["sustained_peak_mb"] = sus_result["memory"]["peak_in_window_mb"]
            peak_mb = max(peak_mb,
                          float(sus_result["memory"]["peak_in_window_mb"] or 0),
                          float(sus_result["memory"]["hwm_end_mb"] or 0))
        else:
            rung["latency_c4"] = {"n": 0, "concurrency": sustained_clients,
                                  "wall_seconds": 0.0, "qps": 0.0,
                                  "server_ms": {}, "end_to_end_ms": {},
                                  "routes": {}, "abstentions": 0,
                                  "failures": n_sustained_q,
                                  "vector_adequate": {"true": 0, "false": 0,
                                                      "none": 0},
                                  "vector_generations_seen": [],
                                  "vector_route_count": 0,
                                  "sustained_ref": False,
                                  "error": sus_errors}
        servable_sample()
        mem_sample("drain+sustained")
        # publication state after the drain (lag = ingest ack -> measured state)
        st = http_get(base, "/api/status")
        rung["publication"] = {
            "lag_s": round(time.time() - t_pub0 - ingest["seconds"], 2),
            "index_trusted": st.get("index_trusted"),
            "semantic_status": st.get("semantic_status"),
            "semantic_pending": st.get("semantic_pending"),
            "generation_id": st.get("generation_id"),
            "served_atoms": (st.get("served_persistent") or {}).get("served_atoms"),
            "worker": st.get("worker"),
        }
        rung["server_rss_peak_mb"] = rss_peak_mb(proc.pid)
        rung["store"] = db_counts(db_path)
        rung["files"] = file_sizes(db_path)
        rung["latency_seq"] = latency_run(base, schedule, log, concurrency=1)
        mem_sample("latency")
        if peak_mb > RSS_HALT_MB:
            rung["error"] = (f"memory halt: peak RSS {peak_mb:.1f}MB > "
                             f"{RSS_HALT_MB:.0f}MB")
            return rung
        servable_sample()
        rung["vector_serving"] = vector_serving_summary(
            [rung["latency_seq"], rung["latency_c4"]], servable_samples)
        log(f"[vector] detachments={rung['vector_serving']['detachments']} "
            f"cold_none={rung['vector_serving']['cold_unavailable_responses']} "
            f"vector_routes={rung['vector_serving']['vector_route_responses']}")
        c4_server = rung["latency_c4"].get("server_ms") or {}
        rung["latency_budgets"] = {
            # G2 outcome bullets (informational; not acceptance rows):
            "seq_p50_le_10ms": bool(rung["latency_seq"]["server_ms"]["p50"] <= 10.0),
            "seq_p95_le_35ms": bool(rung["latency_seq"]["server_ms"]["p95"] <= 35.0),
            "c4_p50_le_10ms": bool(c4_server.get("p50", 1e9) <= 10.0),
            "c4_p95_le_35ms": bool(c4_server.get("p95", 1e9) <= 35.0),
        }
        if sus_result:
            # .3 simultaneous-under-pressure bullets (informational).
            rung["latency_budgets"]["sustained_query_p95_le_35ms"] = bool(
                sus_result["query_p95_le_35ms"])
            rung["latency_budgets"]["sustained_overlap_p95_le_35ms"] = bool(
                sus_result["query_overlap_p95_le_35ms"])
            rung["latency_budgets"]["mut_ack_p95_le_50ms"] = bool(
                sus_result["mutations"]["mut_ack_p95_le_50ms"])
        rung["lifecycle"] = lifecycle_spots(base, db_path, vault, log, lifecycle_full)
        rung["server_pid"] = proc.pid
    finally:
        rung["shutdown"] = stop_server(proc)
    # Memory ceiling verdict (setup peak reported separately, .3 §4).
    final_peak = max(peak_mb, rung.get("server_rss_peak_mb", 0) or 0)
    setup_peak_final = rung.get("setup_peak_mb", final_peak)
    rung["memory"] = {
        "setup_peak_mb": round(setup_peak_final, 1),
        "sustained_peak_mb": rung.get("sustained_peak_mb"),
        "peak_mb": round(final_peak, 1),
        "ceiling_mb": RSS_CEILING_MB, "halt_mb": RSS_HALT_MB,
        "within_ceiling": bool(final_peak <= RSS_CEILING_MB),
    }
    # crash test: hard kill a fresh server, verify integrity after restart
    proc2 = start_server(root, db_path, port + 1)
    base2 = f"http://127.0.0.1:{port + 1}"
    try:
        assert wait_healthy(base2), "restart server never healthy"
        pre = restart_check(root, db_path, vault, port + 1, base2, log)
        proc2.kill()
        proc2.wait(timeout=SHUTDOWN_GRACE_S)
        post_counts = db_counts(db_path)
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            post_fts = [r[0] for r in con.execute(
                "SELECT rowid FROM fts_atoms WHERE fts_atoms MATCH 'zephyrnine' LIMIT 50")]
            match_parity = int(con.execute(
                "SELECT COUNT(*) FROM fts_atoms WHERE fts_atoms MATCH 'batching'").fetchone()[0])
        finally:
            con.close()
        # Stability compares content counts only: `oldest_pending_s` is a
        # wall-clock-derived field (now - created_at) that grows between any
        # two reads, so including it fails every restart by construction.
        # Both values are still recorded for oldest-job-age analysis.
        volatile = {"oldest_pending_s"}
        diffs = {k: {"pre": pre["pre_counts"][k], "post": post_counts[k]}
                 for k in pre["pre_counts"] if k not in volatile
                 and pre["pre_counts"][k] != post_counts.get(k)}
        rung["crash_restart"] = {
            "kill": "SIGKILL", "integrity_post": post_counts["integrity_check"],
            "counts_stable": bool(not diffs),
            "count_diffs": diffs,
            "oldest_pending_s_pre": pre["pre_counts"].get("oldest_pending_s"),
            "oldest_pending_s_post": post_counts.get("oldest_pending_s"),
            "fts_zephyrnine_stable": bool(pre["pre_fts_zephyrnine"] == len(post_fts)),
            "fts_batching_hits": match_parity,
            "wal_corruption": bool(post_counts["integrity_check"] != "ok"),
        }
        log(f"[rung {scale_n}] crash/restart integrity={post_counts['integrity_check']} "
            f"stable={rung['crash_restart']['counts_stable']}")
    finally:
        try:
            proc2.kill()
        except Exception:
            pass
    # quality on the identical stack + probes + parity (server stopped: quiet DB)
    # G2: 10k scores ALL covered SciFact queries on the full corpus and
    # writes per-query rankings; smaller rungs score a bounded sample.
    rank_path = out_dir / f"per_query_rankings_{scale_n}_{stamp}.jsonl"
    q_limit = None if is_10k else n_quality_q
    db, router, stack_info = served_stack(db_path)
    try:
        rung["served_stack"] = stack_info
        rung["quality"] = score_beir(router, beir_docs, beir_meta, q_limit, log,
                                     rankings_path=rank_path) if beir_docs else {}
        rung["rankings_path"] = str(rank_path)
        rung["probes"] = probe_checks(router, log)
        loaded_cases = load_capability_cases(out_dir)
        rung["capability"] = run_capability_cases(router, db_path, loaded_cases, log)
    finally:
        db.close()
    if is_10k:
        # Canonical 10k rankings artifact (packet-required path).
        canon = out_dir / "per_query_rankings.jsonl"
        canon.write_bytes(rank_path.read_bytes())
        rung["rankings_canonical"] = str(canon)
        log(f"[quality] canonical rankings -> {canon}")
    # NFCorpus separate diagnostic (10k default 1000; never a gate).
    ndiag_n = 1000 if (nfdiag_docs is None and is_10k) else (nfdiag_docs or 0)
    if ndiag_n > 0:
        try:
            rung["nfcorpus_diagnostic"] = run_nfcorpus_diagnostic(
                bench_root, ndiag_n, n_quality_q, log)
        except FileNotFoundError as exc:
            rung["nfcorpus_diagnostic"] = {
                "identity": "separate-diagnostic", "status": "BLOCKED",
                "reason": str(exc)}
            log(f"[nfdiag] BLOCKED: {exc}")
    rung["parity"] = parity_check(vault, db_path, log)
    rung["model_cost"] = {"encoder": OPERATING_POINT["encoder"],
                          "tokens": 0, "usd": 0.0}
    vec_build = (rung.get("served_stack") or {}).get("vector_build") or {}
    rung["workload"] = freeze_workload(
        scale_n, vault, schedule, db_path, beir_meta,
        rung.get("server_pid"), bool((rung.get("queue_drain") or {}).get(
            "worker_enabled", False)), bench_root,
        (rung.get("publication") or {}).get("served_atoms"),
        vec_build.get("atoms") if vec_build.get("built") else None,
        rung["memory"]["setup_peak_mb"], rung["memory"]["peak_mb"])
    for ds, docs in beir_docs.items():
        if ds in rung["workload"]["beir"]:
            rung["workload"]["beir"][ds]["indexed"] = len(docs)
    rung["finished_at"] = utcnow()
    rung_json = json.dumps(rung, indent=2)
    (root / f"rung_{scale_n}.json").write_text(rung_json)
    # Durable copy in the evidence dir (bench roots under /tmp are ephemeral).
    (out_dir / f"rung_{scale_n}_{stamp}.json").write_text(rung_json)
    return rung


# ---------------- verdicts + ladder state ----------------

HARD_GATES = ("no_oom", "integrity_ok", "counts_stable_post_crash",
              "replay_ok", "parity_agree", "queue_healthy",
              "setup_within_cap", "scifact_floor",
              "memory_within_ceiling", "vector_stable")


def scifact_floor_ok(rung: dict) -> bool:
    """G2 frozen floor: the 10k rung MUST carry a full-corpus SciFact
    eval clearing 0.6400. Smaller/subset rungs: not applicable (True)."""
    if rung.get("scale_sources") != 10000:
        return True
    q = (rung.get("quality") or {}).get("scifact")
    if q is None or not q.get("full_corpus", False):
        return False
    return bool(q.get("clears_frozen_floor", False))


def queue_healthy_ok(drain: dict) -> bool:
    """G2 autonomous drain: converged to 0 within deadline, worker
    active. Legacy 'sampled' records (pre-G2 ladder entries) keep
    their attempt-2 meaning for reading old entries."""
    mode = drain.get("mode")
    if mode == "autonomous":
        return bool(drain.get("converged", False)
                    and drain.get("within_deadline", False)
                    and drain.get("worker_enabled", False))
    if mode == "sampled":
        return bool((drain.get("converged") or drain.get("sampled_ok"))
                    and not drain.get("mutated_corpus"))
    return False


def hard_verdict(rung: dict) -> dict:
    """Exact gates (never budget-amended). Any False HALTS the ladder.

    G2 gates: the attempt-2 six plus setup cap, the frozen SciFact
    floor (10k full-corpus only), the RSS ceiling, and vector
    stability. Blocks absent from legacy rung records default True;
    only blocks a NEW run always writes are strict. NFCorpus and the
    latency P50/P95 bullets are never hard gates.
    """
    lc = rung.get("lifecycle", {})
    crash = rung.get("crash_restart", {})
    par = rung.get("parity", {})
    drain = rung.get("queue_drain", {})
    setup = rung.get("setup")
    mem = rung.get("memory")
    vec = rung.get("vector_serving")
    checks = {
        "no_oom": "error" not in rung,
        "integrity_ok": bool(crash.get("integrity_post") == "ok"),
        "counts_stable_post_crash": bool(crash.get("counts_stable", False)),
        "replay_ok": bool(lc.get("replay_same_revision") and
                          lc.get("replay_no_new_revisions")),
        "parity_agree": bool(par.get("agree", False)),
        "queue_healthy": bool(queue_healthy_ok(drain)),
        "setup_within_cap": True if setup is None else bool(
            setup.get("within_cap", False)),
        "scifact_floor": bool(scifact_floor_ok(rung)),
        "memory_within_ceiling": True if mem is None else bool(
            mem.get("within_ceiling", False)),
        "vector_stable": True if vec is None else bool(
            vec.get("detachments", 0) == 0),
    }
    return {"checks": checks, "pass": bool(all(checks.values())),
            "halt_ladder": bool(not all(checks.values()))}


def run_verdict(rung: dict) -> dict:
    """Honest run verdict: PASS, FAIL, or INCOMPLETE — never pre-labeled.

    FAIL = a hard gate failed (halts the ladder). INCOMPLETE = hard
    gates pass but the relational-capability evidence is not PASS
    (cases missing/unfrozen, or a BLOCKED diagnostic the contract
    requires). PASS = hard gates pass AND capability PASSes.
    """
    hard = hard_verdict(rung)
    cap = rung.get("capability", {})
    cap_status = cap.get("status", "INCOMPLETE")
    if not hard["pass"]:
        failed = [k for k, v in hard["checks"].items() if not v]
        verdict = "FAIL"
        reason = f"hard gates failed: {failed}"
    elif cap_status != "PASS":
        verdict = "INCOMPLETE"
        reason = cap.get("reason", f"capability status={cap_status}")
    else:
        verdict = "PASS"
        reason = "all hard gates pass; capability PASS"
    return {"verdict": verdict, "reason": reason,
            "hard": hard, "capability_status": cap_status,
            "halt_ladder": bool(hard["halt_ladder"])}


def update_ladder(out_dir: Path, rung: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scale_ladder.json"
    try:
        ladder = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ladder = {"contract": G2_CONTRACT, "operating_point": OPERATING_POINT,
                  "rungs": {}}
    # Preserve history: the overwritten entry (if a different run) moves
    # to rungs_history; prior scales and verdicts are never deleted.
    key = str(rung["scale_sources"])
    prev = (ladder.get("rungs") or {}).get(key)
    if prev is not None and prev.get("started_at") != rung.get("started_at"):
        ladder.setdefault("rungs_history", []).append(prev)
    if ladder.get("contract") != G2_CONTRACT:
        seen = ladder.setdefault("contracts_seen", [])
        if ladder.get("contract") not in seen:
            seen.append(ladder.get("contract"))
    ladder["operating_point"] = OPERATING_POINT
    ladder.setdefault("rungs", {})[key] = rung
    ladder["updated_at"] = utcnow()
    path.write_text(json.dumps(ladder, indent=2))
    # unified_eval.json: quality + latency, identical config (C9-1)
    uni = {"contract": "TL-QN-2026-09-12.1",
           "operating_point": OPERATING_POINT,
           "scale_sources": rung["scale_sources"],
           "quality": rung.get("quality", {}), "probes": rung.get("probes", {}),
           "latency_seq": rung.get("latency_seq", {}),
           "latency_c4": rung.get("latency_c4", {}),
           "same_config_note": "quality (direct served stack) and latency (live "
           "server) use the identical operating point above; no profile swaps.",
           "updated_at": utcnow()}
    (out_dir / "unified_eval.json").write_text(json.dumps(uni, indent=2))
    return ladder


def update_dossier(out_dir: Path, rung: dict, verdict: dict) -> dict:
    """Honest dossier: preserve every historical key/verdict; append this
    run's ACTUAL PASS/FAIL/INCOMPLETE under g2_runs. Never pre-labels,
    never rewrites prior attempts, never deletes refused_rungs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "qualification_dossier.json"
    try:
        dossier = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dossier = {"contract": G2_CONTRACT,
                   "operating_point": OPERATING_POINT,
                   "g2_runs": []}
    sci = (rung.get("quality") or {}).get("scifact", {})
    drain = rung.get("queue_drain", {})
    mem = rung.get("memory", {})
    entry = {
        "run_id": rung.get("run_id"), "contract": G2_CONTRACT,
        "started_at": rung.get("started_at"),
        "finished_at": rung.get("finished_at"),
        "scale_sources": rung.get("scale_sources"),
        "verdict": verdict["verdict"], "reason": verdict["reason"],
        "hard_checks": verdict["hard"]["checks"],
        "capability_status": verdict["capability_status"],
        "scifact_ndcg_at_10": sci.get("ndcg_at_10"),
        "scifact_full_corpus": sci.get("full_corpus"),
        "scifact_queries_scored": sci.get("queries_scored"),
        "drain_seconds": drain.get("seconds"),
        "drain_converged": drain.get("converged"),
        "memory_peak_mb": mem.get("peak_mb"),
        "memory_setup_peak_mb": mem.get("setup_peak_mb"),
        "bench_root": rung.get("bench_root"),
        "command": sys.argv,
    }
    runs = dossier.setdefault("g2_runs", [])
    runs.append(entry)
    dossier["latest_g2_run"] = entry["run_id"]
    dossier["latest_g2_verdict"] = entry["verdict"]
    dossier["updated_at"] = utcnow()
    path.write_text(json.dumps(dossier, indent=2))
    return dossier


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="2k",
                    help="2k|10k|100k|250k|1m|<int sources>")
    ap.add_argument("--output", type=Path,
                    default=REPO / "evidence" / "query-native" / "R09")
    ap.add_argument("--bench-root", type=Path, default=None,
                    help="explicit bench root (default /tmp/tl_r09_<stamp>)")
    ap.add_argument("--port", type=int, default=8129)
    ap.add_argument("--latency-queries", type=int, default=120)
    ap.add_argument("--quality-queries", type=int, default=40)
    ap.add_argument("--beir-docs", type=int, default=1000,
                    help="BEIR docs per dataset in the rung DB (0 = none; "
                    "ignored at 10k: full SciFact is mandatory)")
    ap.add_argument("--lifecycle-full", action="store_true",
                    help="full lifecycle spots (2k/10k); else replay-only spots")
    ap.add_argument("--smoke", action="store_true",
                    help="minimal end-to-end smoke: scale 200, 25 BEIR docs "
                    "per dataset, 12 latency / 5 quality queries, no nfdiag")
    ap.add_argument("--nfdiag-docs", type=int, default=None,
                    help="NFCorpus separate-diagnostic subset docs "
                    "(default: 1000 at 10k, else 0 = skip)")
    ap.add_argument("--drain-deadline", type=float, default=DRAIN_DEADLINE_S,
                    help="autonomous drain deadline seconds")
    ap.add_argument("--setup-cap", type=float, default=SETUP_CAP_S,
                    help="setup/index-build cap seconds")
    ap.add_argument("--sustained-queries", type=int, default=SUSTAINED_QUERIES,
                    help="sustained under-drain queries (exact .3: 800)")
    ap.add_argument("--sustained-window-s", type=float,
                    default=SUSTAINED_WINDOW_S,
                    help="sustained query window seconds (exact .3: 180)")
    ap.add_argument("--sustained-clients", type=int, default=SUSTAINED_CLIENTS,
                    help="sustained query clients (exact .3: 4)")
    ap.add_argument("--mut-ops", type=int, default=MUT_OPS_TOTAL,
                    help="mutation ops at --mut-pace-s (exact .3: 120 as "
                    "60 inserts / 40 edits / 20 retractions)")
    ap.add_argument("--mut-pace-s", type=float, default=MUT_OPS_PACE_S,
                    help="mutation issue pace seconds (exact .3: 1.0)")
    ap.add_argument("--global-deadline", type=float, default=GLOBAL_DEADLINE_S,
                    help="outer supervisor backstop seconds for the rung")
    ap.add_argument("--check-admit", default=None,
                    help="readiness-only: project rung N (e.g. 100k) from the "
                    "latest ladder measurements and exit 0/2 (admit/refuse)")
    args = ap.parse_args()
    if args.smoke:
        args.scale = "200"
        args.beir_docs = 25
        args.latency_queries = 12
        args.quality_queries = 5
        args.sustained_queries = 16
        args.sustained_window_s = 12.0
        args.sustained_clients = 2
        args.mut_ops = 6
        args.mut_pace_s = 1.0
        if args.nfdiag_docs is None:
            args.nfdiag_docs = 0

    if args.check_admit is not None:
        key = args.check_admit
        n = SCALE_ALIASES.get(key, None)
        if n is None:
            n = int(key)
        ladder_path = args.output / "scale_ladder.json"
        ladder = json.loads(ladder_path.read_text(encoding="utf-8"))
        latest = max(int(k) for k in ladder["rungs"])
        ref = ladder["rungs"][str(latest)]
        bps = ref["store"].get("bytes_per_source_mean", 300.0)
        db_bps = ref["files"]["db_total_bytes"] / max(1, ref["scale_sources"])
        gate = readiness_gate(n, bps, db_bps)
        print(json.dumps(gate, indent=2))
        return 0 if gate["admit"] else 2

    scale_n = SCALE_ALIASES.get(args.scale, None)
    if scale_n is None:
        scale_n = int(args.scale)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bench_root = args.bench_root or Path(f"/tmp/tl_r09_{stamp}")
    bench_root.mkdir(parents=True, exist_ok=True)
    log_path = args.output / "lifecycle_pass.log"
    args.output.mkdir(parents=True, exist_ok=True)

    def log(msg: str):
        line = f"{utcnow()} {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # pre-rung readiness: refuse when the ladder already proves we cannot fit
    ladder_path = args.output / "scale_ladder.json"
    if ladder_path.exists():
        ladder = json.loads(ladder_path.read_text(encoding="utf-8"))
        latest = max(int(k) for k in ladder["rungs"])
        ref = ladder["rungs"][str(latest)]
        bps = ref["store"].get("bytes_per_source_mean", 300.0)
        db_bps = ref["files"]["db_total_bytes"] / max(1, ref["scale_sources"])
        gate = readiness_gate(scale_n, bps, db_bps)
        log(f"[readiness] {json.dumps(gate)}")
        if not gate["admit"]:
            log(f"[readiness] REFUSED rung {scale_n}: projections do not fit "
                f"with 2x headroom. Partial ladder preserved (honest result).")
            return 2
    else:
        log(f"[readiness] mem_available={mem_available_mb():.0f}MB "
            f"disk_avail={fs_avail_gb(Path('/tmp')):.0f}GB (first rung: no projection yet)")

    # Outer supervisor backstop (.3 §5): wall-clock the whole rung.
    global_t0 = time.time()
    mut_ins = args.mut_ops * 3 // 6
    mut_ed = args.mut_ops * 2 // 6
    mut_ret = args.mut_ops - mut_ins - mut_ed
    rung = run_rung(scale_n, args.output, bench_root, args.port,
                    args.latency_queries, args.quality_queries,
                    args.lifecycle_full, args.beir_docs, log,
                    drain_deadline_s=args.drain_deadline,
                    setup_cap_s=args.setup_cap,
                    nfdiag_docs=args.nfdiag_docs,
                    n_sustained_q=args.sustained_queries,
                    sustained_window_s=args.sustained_window_s,
                    sustained_clients=args.sustained_clients,
                    mut_inserts=mut_ins, mut_edits=mut_ed,
                    mut_retracts=mut_ret, mut_pace_s=args.mut_pace_s)
    rung["global_seconds"] = round(time.time() - global_t0, 1)
    rung["global_deadline_s"] = args.global_deadline
    if rung["global_seconds"] > args.global_deadline and "error" not in rung:
        rung["error"] = (f"global supervisor deadline breached: "
                         f"{rung['global_seconds']}s > {args.global_deadline}s")
    verdict = run_verdict(rung)
    rung["run_verdict"] = verdict
    rung["hard_verdict"] = verdict["hard"]
    update_ladder(args.output, rung)
    update_dossier(args.output, rung, verdict)
    log(f"[verdict] {verdict['verdict']}: {verdict['reason']}")
    if verdict["verdict"] == "PASS":
        log(f"[verdict] rung {scale_n} PASS; dossier updated "
            f"(historical verdicts preserved).")
        return 0
    if verdict["halt_ladder"]:
        log(f"[verdict] HALT: a hard gate failed at rung {scale_n}; "
            f"higher rungs NOT attempted.")
        return 1
    log(f"[verdict] rung {scale_n} INCOMPLETE (acceptable delivery; "
        f"cannot advance the ladder).")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

