#!/usr/bin/env python3
"""S1 black-box native correctness harness (contract TL-QN-2026-09-12.1 + B3).

Black-box only: drives a SEPARATE server process over real HTTP (stdlib
urllib; no TestClient anywhere in this file) and asserts on HTTP responses
plus on-disk DB reads (fresh read-only sqlite3 connections). Covers:

  1. ingest->query->edit->query->delete->query->restart(new proc)->query
     with per-step persisted-effects checks;
  2. stale-worker rejection at HTTP level (obsolete-payload replay refused
     observable effect + rapid-supersede skip);
  3. crash injection (SIGKILL/taskkill mid-write-batch) -> restart ->
     no half-revision served + outbox drains + crash-window log;
  4. audit targets: retract-then-crash; wrong-CLAIMED-revision GATE (never
     silently corrected); interior byte offsets + trimmed spans;
     excluded-endpoint relations; atomic-group partition (or explicit gap);
  5. sustained-write load with reader latency sampling (p50/p95/p99 recorded,
     no absolute pass/fail).

Platform: unchanged on WSL and native Windows; the ONLY platform branch is
the kill primitive (SIGKILL vs taskkill). Interpreter: .venv/bin/python on
WSL, .venv_win/Scripts/python.exe on native Windows.

Usage (WSL):
  .venv/bin/python benchmarks/blackbox_native_check.py --port 8765
  .venv/bin/python benchmarks/blackbox_native_check.py --port 8765 --keep-workdir /tmp/s1work
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
S1_DIR = REPO_ROOT / "evidence" / "query-native" / "_integration" / "S1"

# --------------------------------------------------------------------------
# fixtures (same shaped corpus as INT-1/INT-2, freshrun ids for crash safety)
# --------------------------------------------------------------------------

PREMISE_V1 = "# Premise\n\nBatching improves throughput by 30% under low contention.\n"
PREMISE_V2 = "# Premise\n\nBatching degrades throughput under high contention.\n"
SUPPORT = ("# Support\n\nUnder high contention, the batching benefit disappears; "
           "throughput matches the unbatched path.\n")
CONTEXT = "# Context\n\nWAL mode allows one writer with concurrent readers.\n"
LONG_TAIL = "background filler without query signal. " * 60
LONG_DOC = ("# Long\n\nBatching throughput contention anchor sentence here.\n"
            + LONG_TAIL + "\nBatching throughput contention closing sentence here.\n")

SERVER_BOOT = (
    "import os\n"
    "from trace_lite.api import create_app\n"
    "import uvicorn\n"
    "db = os.environ['TL_S1_DB']\n"
    "vault = os.environ.get('TL_S1_VAULT') or None\n"
    "app = create_app(db, vault)\n"
    "uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('TL_S1_PORT', '8765')),"
    " log_level='warning')\n"
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# HTTP client (stdlib urllib only)
# --------------------------------------------------------------------------

class HttpError(RuntimeError):
    pass


# Direct connection: never route 127.0.0.1 through proxy env vars.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(base: str, method: str, path: str, body=None, timeout: float = 30.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8", "replace")[:2000]
        except Exception:
            payload = ""
        return exc.code, {"_http_error": payload}


# --------------------------------------------------------------------------
# on-disk DB reads (fresh read-only connections; never the server's handle)
# --------------------------------------------------------------------------

def db_read(db_path: Path, fn, timeout: float = 15.0):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        return fn(conn)
    finally:
        conn.close()


def table_count(conn, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return -1


def current_revisions(conn) -> dict:
    try:
        return {r["source_id"]: r["revision_id"] for r in conn.execute(
            "SELECT source_id, revision_id FROM source_revision WHERE status = 'current'").fetchall()}
    except Exception:
        return {}


def revision_payload(conn, revision_id: str) -> bytes:
    row = conn.execute(
        "SELECT text FROM source_revision WHERE revision_id = ?", (revision_id,)).fetchone()
    if row is None or row[0] is None:
        return b""
    text = row[0]
    return text.encode("utf-8") if isinstance(text, str) else bytes(text)


def job_states(conn) -> list:
    try:
        return [dict(r) for r in conn.execute(
            "SELECT job_id, job_type, state, lease_owner, input_revision_id FROM projection_job").fetchall()]
    except Exception:
        return []


# --------------------------------------------------------------------------
# server process management (platform branch ONLY for the kill primitive)
# --------------------------------------------------------------------------

def kill_crash(proc: subprocess.Popen) -> dict:
    """Crash injection: SIGKILL on POSIX, taskkill /F on Windows. Returns the crash record."""
    record: dict = {"pid": proc.pid, "t_kill": utcnow(), "platform": platform.system()}
    if os.name == "nt":
        r = subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                           capture_output=True, text=True, timeout=30)
        record["tool"] = "taskkill /F"
        record["rc"] = r.returncode
        record["tool_tail"] = (r.stdout + r.stderr)[-500:]
    else:
        os.kill(proc.pid, signal.SIGKILL)
        record["tool"] = "SIGKILL"
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        record["reaped"] = False
    else:
        record["reaped"] = True
        record["exit_code"] = proc.returncode
    return record


class ServerProc:
    def __init__(self, db_path: Path, vault: Path | None, port: int, log_path: Path):
        self.db_path = db_path
        self.vault = vault
        self.port = port
        self.log_path = log_path
        self.base = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self._logfh = None

    def start(self, wait_s: float = 90.0) -> None:
        env = dict(os.environ)
        env["TL_S1_DB"] = str(self.db_path)
        env["TL_S1_VAULT"] = str(self.vault) if self.vault else ""
        env["TL_S1_PORT"] = str(self.port)
        src_path = str(SRC_DIR)
        env["PYTHONPATH"] = src_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self._logfh = open(self.log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", SERVER_BOOT],
            cwd=str(REPO_ROOT), env=env,
            stdout=self._logfh, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + wait_s
        last = None
        while time.monotonic() < deadline:
            try:
                status, _ = http_json(self.base, "GET", "/api/health", timeout=3.0)
                if status == 200:
                    return
            except Exception as exc:
                last = repr(exc)
            if self.proc.poll() is not None:
                raise HttpError(f"server proc exited rc={self.proc.returncode}: {last}")
            time.sleep(0.5)
        raise HttpError(f"server did not become healthy within {wait_s}s: {last}")

    def stop_graceful(self, timeout: float = 30.0) -> None:
        if self.proc is None:
            return
        proc, self.proc = self.proc, None
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_crash(proc)
        if self._logfh:
            self._logfh.close()
            self._logfh = None

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


# --------------------------------------------------------------------------
# step recorder (incremental JSONL flush: crash-safe evidence)
# --------------------------------------------------------------------------

class Recorder:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.steps: list[dict] = []
        self._fh = open(log_path, "w", encoding="utf-8")

    def record(self, step: str, checks: dict, extra: dict | None = None) -> bool:
        failed = sorted(k for k, v in checks.items() if not v)
        entry = {"step": step, "ok": not failed, "failed": failed,
                 "checks": checks, "t": utcnow()}
        if extra is not None:
            entry["extra"] = extra
        self.steps.append(entry)
        self._fh.write(json.dumps(entry, sort_keys=True) + "\n")
        self._fh.flush()
        assert not failed, f"harness step {step!r} failed: {failed}"
        return True

    def note(self, step: str, info: dict) -> None:
        entry = {"step": step, "ok": True, "note": True, "t": utcnow(), **info}
        self.steps.append(entry)
        self._fh.write(json.dumps(entry, sort_keys=True) + "\n")
        self._fh.flush()

    @property
    def ok(self) -> bool:
        return all(s.get("ok", True) for s in self.steps if not s.get("note"))

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def wait_for(fn, deadline_s: float, interval: float = 0.5):
    deadline = time.monotonic() + deadline_s
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


def query_anchors(base: str, text: str, timeout: float = 30.0) -> dict:
    status, payload = http_json(base, "POST", "/api/query", {"query": text}, timeout=timeout)
    assert status == 200, f"/api/query -> {status}: {payload}"
    return payload


def get_status(base: str, timeout: float = 30.0) -> dict:
    status, payload = http_json(base, "GET", "/api/status", timeout=timeout)
    assert status == 200, f"/api/status -> {status}: {payload}"
    return payload


def post_dossier(base: str, body: dict, timeout: float = 60.0) -> dict:
    status, payload = http_json(base, "POST", "/api/dossier", body, timeout=timeout)
    assert status == 200, f"/api/dossier -> {status}: {payload}"
    return payload


# --------------------------------------------------------------------------
# step 1: full lifecycle over HTTP (vault + /api/sync, mirrors INT-1 served path)
# --------------------------------------------------------------------------

def step_lifecycle(base: str, db_path: Path, vault: Path, rec: Recorder) -> None:
    (vault / "premise.md").write_text(PREMISE_V1, encoding="utf-8")
    (vault / "support.md").write_text(SUPPORT, encoding="utf-8")
    (vault / "context.md").write_text(CONTEXT, encoding="utf-8")

    status, synced = http_json(base, "POST", "/api/sync", {})
    assert status == 200, f"/api/sync -> {status}: {synced}"
    c_ing = db_read(db_path, lambda c: {
        "source_revision": table_count(c, "source_revision"),
        "current": len(current_revisions(c)),
        "spans": table_count(c, "evidence_span"),
        "views": table_count(c, "segmentation_view"),
        "runs": table_count(c, "extraction_run"),
        "relations": table_count(c, "relation"),
        "jobs": job_states(c),
    })
    st = get_status(base)
    rec.record("lifecycle.ingest", {
        "sync_ok": synced.get("synced") == 3,
        "atoms==3": synced.get("atoms") == 3,
        "revisions==3": c_ing["source_revision"] == 3 and c_ing["current"] == 3,
        "spans>0": c_ing["spans"] > 0,
        "views==3": c_ing["views"] == 3,
        "relations>0": c_ing["relations"] > 0,
        "jobs_drained": all(j["state"] == "done" for j in c_ing["jobs"]) and len(c_ing["jobs"]) == 3,
        "coverage_complete": synced.get("coverage_pending") == 0,
        "index_trusted": st.get("index_trusted") is True,
    })

    answer = query_anchors(base, "batching throughput")
    dossier = post_dossier(base, {"query": "batching throughput", "limit": 5})
    cited = {s.get("doc_id", "") for s in dossier.get("selections", [])}
    rec.record("lifecycle.query", {
        "answerable": answer.get("sufficiency_state") == "answerable",
        "premise_anchor": any(a.get("doc_id") == "premise.md" for a in answer.get("anchors", [])),
        "dossier_selections": len(dossier.get("selections", [])) >= 1,
        "dossier_cites_premise": "premise.md" in cited,
        "dossier_cites_only_served": bool(cited) and cited <= {"premise.md", "support.md", "context.md"},
    })

    # edit the premise through the served sync path (file change + POST /api/sync)
    (vault / "premise.md").write_text(PREMISE_V2, encoding="utf-8")
    status, resync = http_json(base, "POST", "/api/sync", {})
    assert status == 200, f"resync -> {status}: {resync}"
    edited = query_anchors(base, "batching throughput")
    premise_snippets = " ".join(
        a.get("snippet", "") for a in edited.get("anchors", [])
        if a.get("doc_id") == "premise.md")
    c_edit = db_read(db_path, lambda c: {
        "source_revision": table_count(c, "source_revision"),
        "hist": [tuple(r) for r in c.execute(
            "SELECT status FROM source_revision WHERE source_id = 'premise.md'"
            " ORDER BY observed_at").fetchall()],
    })
    st2 = get_status(base)
    rec.record("lifecycle.edit", {
        "history_retained": (len(c_edit["hist"]) == 2
                             and c_edit["hist"][0][0] == "superseded"
                             and c_edit["hist"][1][0] == "current"),
        "revisions_grew": c_edit["source_revision"] == 4,
        "query_serves_new_text": "degrades" in premise_snippets,
        "old_text_gone": "improves throughput by 30%" not in premise_snippets,
        "coverage_complete": resync.get("coverage_pending") == 0,
        "index_trusted": st2.get("index_trusted") is True,
    })

    # replay changes nothing
    c_before = db_read(db_path, lambda c: (
        table_count(c, "segmentation_view"), table_count(c, "source_revision")))
    status, replay = http_json(base, "POST", "/api/sync", {})
    assert status == 200, f"replay -> {status}: {replay}"
    c_after = db_read(db_path, lambda c: (
        table_count(c, "segmentation_view"), table_count(c, "source_revision")))
    rec.record("lifecycle.replay", {
        "no_new_revisions": c_after[1] == c_before[1],
        "no_duplicated_projections": c_after[0] == c_before[0],
        "still_complete": replay.get("coverage_pending") == 0,
    })

    # delete via the served watcher remove path (unlink + poll served query)
    (vault / "support.md").unlink()
    gone = wait_for(lambda: all(
        a.get("doc_id") != "support.md"
        for a in query_anchors(base, "unbatched path benefit disappears").get("anchors", [])),
        deadline_s=25.0)
    c_del = db_read(db_path, lambda c: {
        "support_atoms": int(c.execute(
            "SELECT COUNT(*) FROM atom WHERE doc_id = 'support.md'").fetchone()[0]),
        "support_revs": int(c.execute(
            "SELECT COUNT(*) FROM source_revision WHERE source_id = 'support.md'").fetchone()[0]),
        "atoms": table_count(c, "atom"),
    })
    st3 = get_status(base)
    rec.record("lifecycle.delete", {
        "watcher_removed_note": bool(gone),
        "lexical_rows_gone": c_del["support_atoms"] == 0,
        "revision_history_retained": c_del["support_revs"] >= 1,
        "coverage_complete": st3.get("coverage_pending") == 0,
        "atoms==2": c_del["atoms"] == 2,
    })


def step_restart_query(srv: ServerProc, rec: Recorder) -> None:
    """Restart is performed by the CALLER (new ServerProc, same DB); verify here."""
    restarted = query_anchors(srv.base, "batching contention")
    dossier2 = post_dossier(srv.base, {"query": "batching contention", "limit": 5})
    c4 = db_read(srv.db_path, lambda c: {
        "source_revision": table_count(c, "source_revision"),
        "spans": table_count(c, "evidence_span"),
        "relations": table_count(c, "relation"),
    })
    st4 = get_status(srv.base)
    rec.record("lifecycle.restart", {
        "query_served": restarted.get("total_results", 0) >= 1,
        "premise_survived": any(a.get("doc_id") == "premise.md"
                                for a in restarted.get("anchors", [])),
        "support_stays_gone": all(a.get("doc_id") != "support.md"
                                  for a in restarted.get("anchors", [])),
        "revisions_survived": c4["source_revision"] >= 4,
        "spans_survived": c4["spans"] > 0,
        "relations_survived": c4["relations"] > 0,
        "dossier_cites": len(dossier2.get("selections", [])) >= 1,
        "coverage_complete": st4.get("coverage_pending") == 0,
    })


# --------------------------------------------------------------------------
# step 2: stale-worker rejection at HTTP level
# --------------------------------------------------------------------------

def step_stale_worker(base: str, db_path: Path, rec: Recorder) -> None:
    """Stale-worker rejection, black-box (HTTP responses + on-disk reads only).

    (a) no-resurrection: re-publishing an OBSOLETE payload over HTTP replays
        to the old revision WITHOUT resurrecting it as current; serve keeps
        the new text. A stale worker's write is refused observable effect.
    (b) superseded-skip: a rapid v1->v2 supersede serves v2 text only; the
        stale v1 output never overwrites current projections.
    """
    # (a) obsolete-payload replay: premise.md is at V2 (lifecycle edit step).
    # Re-ingesting the V1 bytes must replay to the superseded revision and
    # leave the current pointer plus served text untouched.
    v1_rev = db_read(db_path, lambda c: c.execute(
        "SELECT revision_id FROM source_revision WHERE source_id='premise.md'"
        " AND status='superseded' ORDER BY observed_at LIMIT 1").fetchone())
    v1_rev = str(v1_rev[0]) if v1_rev else ""
    assert v1_rev, "lifecycle edit must leave a superseded premise revision"
    v2_rev = db_read(db_path, lambda c: c.execute(
        "SELECT revision_id FROM source_revision WHERE source_id='premise.md'"
        " AND status='current'").fetchone())
    v2_rev = str(v2_rev[0]) if v2_rev else ""
    n_prem_before = db_read(db_path, lambda c: int(c.execute(
        "SELECT COUNT(*) FROM source_revision WHERE source_id='premise.md'").fetchone()[0]))
    status, stale_replay = http_json(base, "POST", "/api/ingest", {
        "text": PREMISE_V1, "document_name": "premise.md"})
    assert status == 200, f"stale replay -> {status}: {stale_replay}"
    post_probe = db_read(db_path, lambda c: {
        "n": int(c.execute(
            "SELECT COUNT(*) FROM source_revision WHERE source_id='premise.md'").fetchone()[0]),
        "cur": c.execute(
            "SELECT revision_id FROM source_revision WHERE source_id='premise.md'"
            " AND status='current'").fetchone(),
    })
    stale_hits = query_anchors(base, "batching throughput")
    stale_snips = " ".join(
        a.get("snippet", "") for a in stale_hits.get("anchors", [])
        if a.get("doc_id") == "premise.md")
    rec.record("stale_worker.no_resurrection", {
        # the obsolete payload mints no new revision (idempotent replay)...
        "no_new_revision": post_probe["n"] == n_prem_before,
        # ...reports the still-current revision (never the obsolete one)...
        "reports_current_not_obsolete": str(stale_replay.get("revision_id")) == v2_rev
        and v1_rev != str(stale_replay.get("revision_id")),
        "current_pointer_unchanged": post_probe["cur"] is not None
        and str(post_probe["cur"][0]) == v2_rev,
        "serve_keeps_new_text": ("degrades" in stale_snips
                                 and "improves throughput by 30%" not in stale_snips),
    })

    # (b) rapid supersede: v1 immediately replaced by v2
    status, v1 = http_json(base, "POST", "/api/ingest", {
        "text": "# Flap\n\nBatching improves throughput by 30% under low contention.\n",
        "document_name": "flap.md"})
    assert status == 200, f"flap v1 -> {status}"
    status, v2 = http_json(base, "POST", "/api/ingest", {
        "text": "# Flap\n\nBatching degrades throughput under high contention.\n",
        "document_name": "flap.md"})
    assert status == 200, f"flap v2 -> {status}"
    assert v1.get("revision_id") != v2.get("revision_id"), "supersede must mint a new revision"
    flap_hits = query_anchors(base, "batching throughput contention flap")
    flap_snippets = " ".join(
        a.get("snippet", "") for a in flap_hits.get("anchors", [])
        if a.get("doc_id") == "flap.md")
    flap_db = db_read(db_path, lambda c: {
        "hist": [tuple(r) for r in c.execute(
            "SELECT status FROM source_revision WHERE source_id='flap.md'"
            " ORDER BY observed_at").fetchall()],
        "current_rev": c.execute(
            "SELECT revision_id FROM source_revision WHERE source_id='flap.md'"
            " AND status='current'").fetchone(),
    })
    st = get_status(base)
    rec.record("stale_worker.superseded_skip", {
        "new_revision_current": (len(flap_db["hist"]) == 2
                                 and flap_db["hist"][0][0] == "superseded"
                                 and flap_db["hist"][1][0] == "current"),
        "serves_v2_only": ("degrades" in flap_snippets
                            and "improves throughput by 30%" not in flap_snippets),
        "current_is_v2": flap_db["current_rev"] is not None
                         and str(flap_db["current_rev"][0]) == str(v2.get("revision_id")),
        "coverage_complete": st.get("coverage_pending") == 0,
    })


# --------------------------------------------------------------------------
# step 3: crash injection mid-write-batch + restart recovery
# --------------------------------------------------------------------------

def sustained_writers(base: str, prefix: str, n_threads: int, per_thread: int,
                      stop: threading.Event, errors: list) -> list:
    """Background HTTP writers. Returns per-request (ok, latency_s) samples."""
    samples: list = []
    lock = threading.Lock()

    def _worker(tid: int):
        for i in range(per_thread):
            if stop.is_set():
                break
            doc = f"{prefix}-t{tid}-{i}.md"
            text = (f"# {prefix} {tid}/{i}\n\nBatching throughput contention note {tid}-{i} "
                    f"with WAL readers and unbatched fallback path.\n")
            t0 = time.monotonic()
            try:
                sc, _ = http_json(base, "POST", "/api/ingest",
                                  {"text": text, "document_name": doc}, timeout=60.0)
                ok = (sc == 200)
            except Exception as exc:
                ok = False
                errors.append(repr(exc)[:200])
            dt = time.monotonic() - t0
            with lock:
                samples.append((ok, dt))

    threads = [threading.Thread(target=_worker, args=(t,), daemon=True) for t in range(n_threads)]
    for t in threads:
        t.start()
    return threads, samples


def check_no_half_revision(db_path: Path) -> dict:
    """Every CURRENT revision must be fully covered or honestly pending.

    A half-revision would be: current revision with partial projections that
    the status endpoint does NOT report as pending. Returns the finding.
    """
    def _probe(conn):
        cur = {r["source_id"]: r["revision_id"] for r in conn.execute(
            "SELECT source_id, revision_id FROM source_revision WHERE status='current'").fetchall()}
        uncovered = []
        try:
            uncovered = [dict(r) for r in conn.execute(
                "SELECT revision_id FROM pending_coverage").fetchall()]
        except Exception:
            pass
        pending_revs: set = set()
        try:
            for r in conn.execute(
                    "SELECT revision_id FROM source_revision WHERE status='current'"
                    " AND revision_id NOT IN (SELECT revision_id FROM segmentation_view)"):
                pending_revs.add(r[0])
        except Exception:
            pass
        half = []
        for sid, rev in cur.items():
            has_view = conn.execute(
                "SELECT 1 FROM segmentation_view WHERE revision_id=?", (rev,)).fetchone()
            if has_view is None:
                half.append({"source_id": sid, "revision_id": rev, "missing": "segmentation_view"})
        open_jobs = int(conn.execute(
            "SELECT COUNT(*) FROM projection_job WHERE state IN ('pending','claimed')").fetchone()[0])
        return {"current_count": len(cur), "half": half, "open_jobs": open_jobs,
                "pending_revs": sorted(pending_revs)}
    return db_read(db_path, _probe)


def check_dossier_byte_exact(base: str, db_path: Path, query: str, limit: int = 8) -> dict:
    """Every served dossier selection must cite a current revision with byte-exact excerpts."""
    dossier = post_dossier(base, {"query": query, "limit": limit})
    selections = dossier.get("selections", [])

    def _probe(conn):
        cur = current_revisions(conn)
        cur_set = set(cur.values())
        findings = []
        for s in selections:
            rev = s.get("revision_id", "")
            raw = revision_payload(conn, rev)
            exc = (s.get("excerpt_text", "") or "").encode("utf-8")
            sb, eb = int(s.get("start_byte", -1)), int(s.get("end_byte", -2))
            window = raw[sb:eb] if 0 <= sb <= eb <= len(raw) else b"<OOB>"
            findings.append({
                "candidate_id": s.get("candidate_id"),
                "rev_current": rev in cur_set,
                # containment (INT-2 battery semantics): the served excerpt
                # must occur inside the cited window, never merely echo it.
                "window_exact": len(exc) > 0 and exc in window,
                "in_bounds": 0 <= sb <= eb <= len(raw),
                "valid": bool(s.get("citation_valid")),
            })
        return {"n": len(selections), "findings": findings}
    detail = db_read(db_path, _probe)
    ok_list = [f["rev_current"] and f["window_exact"] and f["in_bounds"] and f["valid"]
               for f in detail["findings"]]
    return {"dossier": dossier, "detail": detail,
            "ok": bool(ok_list) and all(ok_list) if ok_list else False,
            "n": detail["n"]}


def step_crash(srv: ServerProc, rec: Recorder, crash_log: Path) -> ServerProc:
    """Kill -9/taskkill the server mid-write-batch; restart; verify recovery."""
    base, db_path = srv.base, srv.db_path
    pre_counts = db_read(db_path, lambda c: {
        "source_revision": table_count(c, "source_revision"),
        "jobs_done": int(c.execute(
            "SELECT COUNT(*) FROM projection_job WHERE state='done'").fetchone()[0]),
    })
    stop = threading.Event()
    errors: list = []
    threads, samples = sustained_writers(base, "crashload", 4, 30, stop, errors)
    time.sleep(2.0)  # let the batch build mid-write pressure
    assert srv.is_alive(), "server died before crash injection"
    crash_record = kill_crash(srv.proc)
    assert srv.proc is not None
    try:
        srv.proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    stop.set()
    for t in threads:
        t.join(timeout=30)
    crash_record.update({"t_batch_start": utcnow(), "pre_counts": pre_counts,
                         "inflight_samples": len(samples),
                         "inflight_ok": sum(1 for ok, _ in samples if ok)})
    with open(crash_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "crash_injected", **crash_record}, sort_keys=True) + "\n")

    # restart: NEW server process, same DB file
    srv2 = ServerProc(db_path, srv.vault, srv.port, srv.log_path)
    srv2.start()
    t_restart = utcnow()
    with open(crash_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "restarted", "t_restart": t_restart,
                             "pid": srv2.proc.pid if srv2.proc else None}, sort_keys=True) + "\n")

    # bounded wait: outbox drains to complete
    def _drained():
        try:
            st = get_status(srv2.base, timeout=10.0)
            if st.get("coverage_pending") != 0:
                return None
            jobs = db_read(db_path, job_states)
            if any(j["state"] in ("pending", "claimed") for j in jobs):
                return None
            return st
        except Exception:
            return None
    st = wait_for(_drained, deadline_s=180.0, interval=2.0)
    half = check_no_half_revision(db_path)
    jobs = db_read(db_path, job_states)
    crashed_mid = [j for j in jobs if j["state"] != "done"]
    byte_check = check_dossier_byte_exact(srv2.base, db_path, "batching throughput contention")
    with open(crash_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "recovery_verified", "t": utcnow(),
                             "status": st, "half": half,
                             "nondone_jobs": crashed_mid,
                             "byte_check_n": byte_check["n"]}, sort_keys=True) + "\n")
    rec.record("crash.recovery", {
        "server_restarted": True,
        "outbox_drained_bounded": st is not None,
        "no_open_jobs": len(crashed_mid) == 0,
        "no_half_revision": len(half["half"]) == 0,
        "post_crash_query_cites_current_byte_exact": bool(byte_check["ok"]),
        "crash_window_logged": crash_log.exists(),
    }, extra={"crash_pid": crash_record.get("pid"), "inflight": crash_record.get("inflight_samples")})
    return srv2


def step_retract_crash(srv: ServerProc, vault: Path, rec: Recorder, crash_log: Path) -> ServerProc:
    """Retract-then-crash: unlink WITHOUT syncing, kill immediately, restart.

    Post-restart state must be coherent: the doc is either cleanly served
    (pre-retract revision, fully covered) or cleanly gone after sync, with
    history retained either way and no half-revision.
    """
    base, db_path = srv.base, srv.db_path
    target = vault / "context.md"
    assert target.exists(), "retract target missing"
    rev_before = db_read(db_path, lambda c: c.execute(
        "SELECT revision_id FROM source_revision WHERE source_id='context.md'"
        " AND status='current'").fetchone())
    rev_before = str(rev_before[0]) if rev_before else ""
    target.unlink()  # retract at the filesystem; do NOT sync: crash inside the window
    crash_record = kill_crash(srv.proc)
    assert srv.proc is not None
    try:
        srv.proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    with open(crash_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "retract_crash_injected", **crash_record,
                             "retracted": "context.md",
                             "rev_before": rev_before}, sort_keys=True) + "\n")
    srv2 = ServerProc(db_path, srv.vault, srv.port, srv.log_path)
    srv2.start()
    # after restart, sync: the missing file must converge to cleanly gone
    status, resync = http_json(srv2.base, "POST", "/api/sync", {})
    assert status == 200, f"post-crash sync -> {status}"
    gone = wait_for(lambda: all(
        a.get("doc_id") != "context.md"
        for a in query_anchors(srv2.base, "WAL writer concurrent readers").get("anchors", [])),
        deadline_s=15.0)
    path_taken = "watcher_converged_post_restart"
    if not gone:
        # Honest window outcome: the dead server missed the unlink event and
        # neither restart-scan nor /api/sync retracts it. The served state
        # must then byte-match the pre-crash revision exactly (coherent
        # stale, never half), and the live-watcher path must converge.
        path_taken = "coherent_stale_then_live_retract"
        stale_hits = query_anchors(srv2.base, "WAL writer concurrent readers")
        stale_snips = " ".join(
            a.get("snippet", "") for a in stale_hits.get("anchors", [])
            if a.get("doc_id") == "context.md")
        coherent = db_read(db_path, lambda c: (
            revision_payload(c, rev_before).decode("utf-8", "replace")
            if rev_before else ""))
        assert rev_before and ("WAL mode allows one writer" in stale_snips
                               or "concurrent readers" in stale_snips), \
            f"served context.md matches neither revision {rev_before[:8]} nor gone"
        assert "WAL mode allows one writer" in coherent, "pre-crash payload unreadable"
        target.write_text(CONTEXT, encoding="utf-8")
        # The watcher is poll-based (0.2s cadence): it must OBSERVE the file
        # before it can report its removal. Unlinking faster than one scan
        # leaves the create unobserved and the delete invisible, so settle.
        time.sleep(3.0)
        http_json(srv2.base, "POST", "/api/sync", {})
        target.unlink()
        gone = wait_for(lambda: all(
            a.get("doc_id") != "context.md"
            for a in query_anchors(srv2.base, "WAL writer concurrent readers").get("anchors", [])),
            deadline_s=25.0)
    post = db_read(db_path, lambda c: {
        "atoms": int(c.execute(
            "SELECT COUNT(*) FROM atom WHERE doc_id='context.md'").fetchone()[0]),
        "hist": int(c.execute(
            "SELECT COUNT(*) FROM source_revision WHERE source_id='context.md'").fetchone()[0]),
    })
    half = check_no_half_revision(db_path)
    rec.record("crash.retract_window", {
        "converged_gone": bool(gone),
        "lexical_rows_gone": post["atoms"] == 0,
        "history_retained": post["hist"] >= 1,
        "no_half_revision": len(half["half"]) == 0,
        "crash_window_logged": crash_log.exists(),
    }, extra={"path": path_taken, "rev_before": rev_before[:12]})
    return srv2


# --------------------------------------------------------------------------
# step 4: dossier audit targets (all black-box: HTTP + on-disk DB)
# --------------------------------------------------------------------------

def step_audit_injection_gate(base: str, db_path: Path, rec: Recorder) -> None:
    """GATE: caller-injected revision claims must never surface silently.

    The served /api/dossier endpoint builds candidates server-side; extra
    body fields carrying wrong revision ids must be ignored — no selection
    may cite them, and the raw response must not contain them.
    """
    fake_rev = "deadbeef-" * 4
    status, payload = http_json(base, "POST", "/api/dossier", {
        "query": "batching throughput contention",
        "limit": 5,
        "candidates": [{"candidate_id": "atom-1", "atom_id": 1,
                        "revision_id": fake_rev, "span_id": "s-fake",
                        "text": "injected text", "doc_id": "evil.md"}],
        "revision_id": fake_rev,
        "claimed_revision_id": fake_rev,
    }, timeout=60.0)
    assert status == 200, f"injection dossier -> {status}"
    blob = json.dumps(payload, sort_keys=True)
    cited_revs = {s.get("revision_id", "") for s in payload.get("selections", [])}
    cur_set = db_read(db_path, lambda c: set(current_revisions(c).values()))
    rec.record("audit.injection_gate", {
        "http_200": True,
        "fake_rev_never_cited": fake_rev not in cited_revs,
        "fake_rev_absent_from_response": fake_rev not in blob,
        "all_citations_current": bool(cited_revs) and cited_revs <= cur_set,
        "has_selections": len(payload.get("selections", [])) >= 1,
    })


UNI_DOC = (
    "# Caf\u00e9 notes \u2014 na\u00efve fa\u00e7ade\n\n"
    "R\u00e9sum\u00e9: the pi\u00f1ata emoji \U0001f389 survives byte slicing.\r\n"
    "Second line with repeats repeats repeats.\r\n"
    "Third line: IRC\u00a7 + na\u00efve caf\u00e9.\n"
)
REPEAT_DOC = "# Echo\n\n" + ("Batching throughput anchor repeats.\n\n" * 4)


def step_audit_byte_offsets(base: str, db_path: Path, rec: Recorder) -> None:
    """Interior byte offsets + trimmed-span citations over HTTP.

    Mirrors the INT-2 battery conditions (uni + long + echo docs, default
    and tight-budget dossier queries): excerpt bytes must round-trip
    byte-exact against the raw revision payload, trimmed excerpts must be
    anchored (not a bare prefix), and at least one excerpt must sit at a
    nonzero interior offset. Citation windows are independently re-read
    from the on-disk DB, never trusted from the response.
    """
    for name, text in (("longdoc.md", LONG_DOC), ("unidoc.md", UNI_DOC),
                       ("echodoc.md", REPEAT_DOC)):
        status, ing = http_json(base, "POST", "/api/ingest",
                                {"text": text, "document_name": name})
        assert status == 200, f"{name} ingest -> {status}: {ing}"
    # NOTE: crash-load docs from step 3 share generic "throughput
    # contention" vocabulary, so audit queries use longdoc-only terms
    # ("closing sentence", "filler signal") to isolate the target doc.
    sels = []
    for body in ({"query": "closing sentence filler signal", "limit": 6},
                 {"query": "batching closing sentence filler signal",
                  "limit": 3, "salient_evidence_budget": 60},
                 {"query": "pi\u00f1ata fa\u00e7ade r\u00e9sum\u00e9", "limit": 3}):
        dossier = post_dossier(base, body)
        sels.extend(dossier.get("selections", []))
    assert sels, "byte-offset audit needs served selections"

    def _probe(conn):
        out = []
        for s in sels:
            raw = revision_payload(conn, s.get("revision_id", ""))
            blob = (s.get("excerpt_text", "") or "").encode("utf-8")
            sb, eb = int(s.get("start_byte", -1)), int(s.get("end_byte", -2))
            window = raw[sb:eb] if 0 <= sb <= eb <= len(raw) else b"<OOB>"
            try:
                window.decode("utf-8")
                utf8 = True
            except UnicodeDecodeError:
                utf8 = False
            at = raw.find(blob) if blob else -1
            out.append({
                "doc": s.get("doc_id"),
                "is_excerpt": bool(s.get("is_excerpt")),
                "contained_in_window": len(blob) > 0 and blob in window,
                "roundtrip_exact": at >= 0 and raw[at:at + len(blob)] == blob,
                "offset": at,
                "interior": at > 0,
                "anchored_not_prefix": not raw.decode("utf-8", "replace").startswith(
                    s.get("excerpt_text", "") or "\x00"),
                "in_bounds": 0 <= sb <= eb <= len(raw),
                "utf8": utf8,
                "method": s.get("citation_method"),
                "valid": bool(s.get("citation_valid")),
                "degraded": bool(s.get("citation_degraded")),
            })
        return out
    probe = db_read(db_path, _probe)
    excerpts = [p for p in probe if p["is_excerpt"]]
    rec.record("audit.byte_offsets", {
        "selections_served": len(probe) >= 1,
        "all_valid_undegraded": all(p["valid"] and not p["degraded"] for p in probe),
        "methods_proven": all(p["method"] in ("r02_excerpt", "source_slice") for p in probe),
        "all_contained_byte_exact": all(p["contained_in_window"] and p["roundtrip_exact"]
                                        and p["utf8"] and p["in_bounds"] for p in probe),
        "trimmed_excerpt_present": len(excerpts) >= 1,
        "trimmed_anchored_not_prefix": all(p["anchored_not_prefix"] for p in excerpts),
        "interior_nonzero_offset": any(p["interior"] for p in probe),
    }, extra={"probe": probe})


def step_audit_excluded_relations(base: str, db_path: Path, rec: Recorder) -> None:
    """Excluded-endpoint relations must not leak into the served dossier."""
    status, _ = http_json(base, "POST", "/api/ingest", {
        "text": "# RelayA\n\nBatching improves throughput by 30% under low contention relay alpha.\n",
        "document_name": "relay-a.md"})
    assert status == 200
    status, _ = http_json(base, "POST", "/api/ingest", {
        "text": "# RelayB\n\nUnder high contention the batching relay beta disappears entirely.\n",
        "document_name": "relay-b.md"})
    assert status == 200
    before = post_dossier(base, {"query": "batching relay alpha beta", "limit": 8})
    # Black-box retraction over the served watcher path (write + sync +
    # unlink + poll for disappearance). ALL actions are HTTP/file operations;
    # ALL assertions are HTTP responses + on-disk reads.
    vault = db_path.parent / "vault"
    (vault / "relay-b.md").write_text(
        "# RelayB\n\nUnder high contention the batching relay beta disappears entirely.\n",
        encoding="utf-8")
    status, _ = http_json(base, "POST", "/api/sync", {})
    assert status == 200, "relay-b vault sync failed"
    # Poll-based watcher must OBSERVE the file before it can report its
    # removal (same settle as the retract-window step).
    time.sleep(3.0)
    (vault / "relay-b.md").unlink()
    gone = wait_for(lambda: all(
        a.get("doc_id") != "relay-b.md"
        for a in query_anchors(base, "batching relay beta").get("anchors", [])),
        deadline_s=25.0)
    assert gone, "served watcher path did not retract relay-b.md"
    st = get_status(base)
    after = post_dossier(base, {"query": "batching relay alpha beta", "limit": 8})
    blob = json.dumps({"r": after.get("relations"), "d": after.get("disagreements"),
                       "s": after.get("selections")}, sort_keys=True)
    rel_revs = set()
    for edge in (after.get("relations") or []):
        if isinstance(edge, dict) and edge.get("revision_id"):
            rel_revs.add(str(edge["revision_id"]))
    b_cur = db_read(db_path, lambda c: (
        [r[0] for r in c.execute(
            "SELECT revision_id FROM source_revision WHERE source_id='relay-b.md'").fetchall()],
        set(current_revisions(c).values())))
    b_revs, cur_set = b_cur
    rec.record("audit.excluded_relations", {
        "retraction_setup": True,
        "relay_b_not_selected": all(s.get("doc_id") != "relay-b.md"
                                    for s in after.get("selections", [])),
        "no_edge_cites_relay_b": not (rel_revs & set(b_revs)),
        "all_edges_in_visible_set": (not rel_revs) or bool(rel_revs <= cur_set),
        "relay_b_absent_from_edges": "relay-b.md" not in blob,
        "coverage_complete": st.get("coverage_pending") == 0,
    }, extra={"n_relations_before": len(before.get("relations") or []),
              "n_relations_after": len(after.get("relations") or [])})


def step_audit_atomic_group(base: str, db_path: Path, rec: Recorder) -> dict:
    """Atomic group with invalid member: HTTP endpoint builds candidates from
    served atoms without group bindings, so the compiler's partition path is
    unreachable over HTTP. Deliver the honest black-box substitute (explicit
    omission accounting: nothing silently dropped)."""
    dossier = post_dossier(base, {"query": "batching throughput contention", "limit": 8})
    selections = dossier.get("selections", [])
    omissions = dossier.get("omissions", [])
    empty_honest = (len(selections) > 0) or (
        len(omissions) > 0 or dossier.get("support_state") == "insufficient"
        or dossier.get("status") != "ok")
    gap = {"gap": "atomic_group_partitioned unreachable over HTTP: /api/dossier "
                  "constructs candidates from served atoms (no group_id/atomic_group "
                  "bindings accepted from callers); partition behavior remains "
                  "component-covered by the INT-2 battery, not black-box proven here."}
    rec.note("audit.atomic_group", gap)
    # No-silent-drop observable: a starved budget must yield NAMED omissions
    # (every shortfall labeled), never silent truncation.
    # NOTE (product finding, src/ untouched): mandatory_invariant_tokens
    # defaults to 600, so max_total_tokens < ~600 raises an unhandled
    # TokenBudget ValidationError -> HTTP 500 (observed, logged in the
    # server stdout of the run that probed it). The harness stays within
    # the valid envelope and asserts honest accounting there.
    starved = post_dossier(base, {"query": "batching throughput contention", "limit": 8,
                                  "max_total_tokens": 1000,
                                  "mandatory_invariant_tokens": 0,
                                  "salient_evidence_budget": 50})
    starved_om = starved.get("omissions", [])
    rec.record("audit.omission_accounting", {
        "empty_results_are_explicit": bool(empty_honest),
        "omissions_wellformed": all(isinstance(o, dict) and o.get("kind") for o in omissions),
        "support_state_present": bool(dossier.get("support_state")),
        "starved_shortfall_named": bool(starved_om) and all(
            isinstance(o, dict) and o.get("kind") and o.get("reason", "") != ""
            for o in starved_om),
    }, extra={"starved_omission_kinds": sorted({str(o.get("kind")) for o in starved_om})})
    return gap


# --------------------------------------------------------------------------
# step 5: sustained-write load with reader latency sampling (record only)
# --------------------------------------------------------------------------

def pct(sorted_samples: list, q: float) -> float:
    if not sorted_samples:
        return float("nan")
    idx = min(len(sorted_samples) - 1, max(0, int(q * len(sorted_samples))))
    return sorted_samples[idx]


def step_load(base: str, rec: Recorder) -> None:
    stop = threading.Event()
    errors: list = []
    threads, wsamples = sustained_writers(base, "loadrun", 4, 20, stop, errors)
    reader_lat: list = []
    t_end = time.monotonic() + 45.0
    n_reads = 0
    while time.monotonic() < t_end and any(t.is_alive() for t in threads):
        t0 = time.monotonic()
        try:
            query_anchors(base, "batching throughput contention", timeout=60.0)
            reader_lat.append(time.monotonic() - t0)
            n_reads += 1
        except Exception as exc:
            errors.append(repr(exc)[:200])
        time.sleep(0.2)
    stop.set()
    for t in threads:
        t.join(timeout=60)
    wsamples_sorted = sorted(dt for _, dt in wsamples)
    wok = sum(1 for ok, _ in wsamples if ok)
    rlat = sorted(reader_lat)
    summary = {
        "writes_attempted": len(wsamples),
        "writes_ok": wok,
        "write_lat_s": {"p50": pct(wsamples_sorted, 0.50), "p95": pct(wsamples_sorted, 0.95),
                        "p99": pct(wsamples_sorted, 0.99)},
        "reads": n_reads,
        "read_lat_s": {"p50": pct(rlat, 0.50), "p95": pct(rlat, 0.95), "p99": pct(rlat, 0.99)},
        "errors_tail": errors[-5:],
    }
    rec.record("load.sustained", {
        "writes_majority_ok": wok >= max(1, int(0.9 * max(1, len(wsamples)))),
        "reads_sampled": n_reads >= 5,
        "latency_recorded": True,  # record only; no absolute pass/fail per contract
    }, extra=summary)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="S1 black-box native correctness harness")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workdir", default="",
                    help="work dir for DB/vault/logs (default: fresh mkdtemp)")
    ap.add_argument("--keep-workdir", default="",
                    help="copy workdir tree to this path after the run")
    ap.add_argument("--skip-load", action="store_true",
                    help="skip the sustained-load step (faster smoke run)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    run_id = ("s1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "-" + uuid.uuid4().hex[:8])
    tmp = tempfile.TemporaryDirectory(prefix="s1-blackbox-")
    work = Path(args.workdir) if args.workdir else Path(tmp.name)
    work.mkdir(parents=True, exist_ok=True)
    vault = work / "vault"
    vault.mkdir(exist_ok=True)
    db_path = work / "s1.db"
    server_log = work / "server_stdout.log"
    crash_log = work / "crash_window.log"
    run_log = work / "blackbox_run.log"

    S1_DIR.mkdir(parents=True, exist_ok=True)
    rec = Recorder(run_log)
    rec.note("run.start", {"run_id": run_id, "platform": platform.system(),
                           "python": sys.version.split()[0],
                           "port": args.port, "workdir": str(work)})
    srv = ServerProc(db_path, vault, args.port, server_log)
    gaps: list = []
    completed = True
    try:
        srv.start()
        rec.note("server.started", {"pid": srv.proc.pid if srv.proc else None,
                                    "base": srv.base})
        step_lifecycle(srv.base, db_path, vault, rec)

        # graceful restart: NEW server process, same DB file
        srv.stop_graceful()
        srv = ServerProc(db_path, vault, args.port, server_log)
        srv.start()
        rec.note("server.restarted_graceful", {"pid": srv.proc.pid if srv.proc else None})
        step_restart_query(srv, rec)

        step_stale_worker(srv.base, db_path, rec)
        srv = step_crash(srv, rec, crash_log)
        srv = step_retract_crash(srv, vault, rec, crash_log)
        step_audit_injection_gate(srv.base, db_path, rec)
        step_audit_byte_offsets(srv.base, db_path, rec)
        step_audit_excluded_relations(srv.base, db_path, rec)
        gaps.append(step_audit_atomic_group(srv.base, db_path, rec))
        if not args.skip_load:
            step_load(srv.base, rec)
    except AssertionError as exc:
        completed = False
        rec.note("run.aborted", {"error": str(exc)[:500]})
    except Exception as exc:  # noqa: BLE001 - evidence must survive any failure
        completed = False
        rec.note("run.aborted", {"error": f"{type(exc).__name__}: {str(exc)[:500]}"})
    finally:
        try:
            srv.stop_graceful()
        except Exception:
            pass
        rec.note("run.end", {"ok": rec.ok, "gaps": gaps})
        rec.close()

    ok = bool(completed) and rec.ok
    summary = {"run_id": run_id, "ok": ok,
               "steps": rec.steps, "gaps": gaps, "platform": platform.system()}
    (work / "blackbox_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # deliver evidence into the repo (S1 owned runs log dir)
    rundir = S1_DIR / "runs" / run_id
    rundir.mkdir(parents=True, exist_ok=True)
    for name in ("blackbox_run.log", "blackbox_summary.json",
                 "crash_window.log", "server_stdout.log"):
        src = work / name
        if src.exists():
            (rundir / name).write_bytes(src.read_bytes())
    if args.keep_workdir:
        import shutil
        dest = Path(args.keep_workdir)
        dest.mkdir(parents=True, exist_ok=True)
        for child in work.iterdir():
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
    print(f"[{run_id}] {'PASS' if ok else 'FAIL'} "
          f"({sum(1 for s in rec.steps if s.get('ok') and not s.get('note'))} steps ok, "
          f"{len(gaps)} gap(s)) log={run_log}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
