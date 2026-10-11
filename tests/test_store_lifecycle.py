"""R01 store lifecycle: atomic replacement, idempotency, outbox, gates 1/2/3/5/7/8/10.

Behavior-level tests against real SQLite files. Crash faults use the private
``_pre_commit_fault`` closed-over hook (invoked inside the writer transaction
before commit); no globals are touched.
"""

import pytest

from trace_lite import source as S
from trace_lite.store import Database, outbox
from trace_lite.store import generations as gen


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "lifecycle.db")
    yield database
    database.close()


def _match_texts(database, query, namespace="default"):
    return sorted(h["text"] for h in S.search_current(database.conn, query, namespace))


def test_crash_mid_replacement_leaves_old_revision_intact(db):
    """C1-1 / gate 3: fault before commit keeps the old coherent revision."""
    v1 = S.publish(db, "doc-crash", "stable cabinet text about quokkas", idempotency_key="k-crash-1")

    def _boom():
        raise RuntimeError("injected crash before commit")

    before = {
        "revisions": db.conn.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0],
        "atoms": db.count_atoms(),
        "jobs": db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0],
        "events": db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
    }
    with pytest.raises(RuntimeError, match="injected crash"):
        S.publish(db, "doc-crash", "half written replacement text", _pre_commit_fault=_boom)

    assert S.get_current(db.conn, "doc-crash")["revision_id"] == v1.revision_id
    assert [r["revision_id"] for r in S.list_revisions(db.conn, "doc-crash")] == [v1.revision_id]
    assert S.get_revision(db.conn, v1.revision_id)["status"] == "current"
    after = {
        "revisions": db.conn.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0],
        "atoms": db.count_atoms(),
        "jobs": db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0],
        "events": db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
    }
    assert after == before
    assert _match_texts(db, "quokkas") == ["stable cabinet text about quokkas"]
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_crash_mid_replacement_matches_clean_rebuild(db, tmp_path):
    """C1-1: FTS MATCH parity between crash-recovered DB and a clean rebuild."""
    S.publish(db, "a", "alpha cabinet ledger notes")
    S.publish(db, "b", "beta garden soil remarks")

    def _boom():
        raise RuntimeError("injected crash before commit")

    with pytest.raises(RuntimeError, match="injected crash"):
        S.publish(db, "a", "alpha replacement that never lands", _pre_commit_fault=_boom)

    clean = Database(tmp_path / "clean.db")
    try:
        S.publish(clean, "a", "alpha cabinet ledger notes")
        S.publish(clean, "b", "beta garden soil remarks")
        for probe in ("alpha", "cabinet", "beta", "garden", "ledger", "soil"):
            assert _match_texts(db, probe) == _match_texts(clean, probe), probe
        assert S.verify_store(db.conn)["fts_integrity"] == "ok"
    finally:
        clean.close()


def test_crash_on_first_publish_leaves_no_partial_state(db):
    """C1-1 limit: never partial deletion — a failed first write leaves nothing."""

    def _boom():
        raise RuntimeError("injected crash before commit")

    with pytest.raises(RuntimeError, match="injected crash"):
        S.publish(db, "fresh", "text that must not partially land", _pre_commit_fault=_boom)
    assert S.get_current(db.conn, "fresh") is None
    assert db.conn.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0] == 0
    assert db.count_atoms() == 0
    assert db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0] == 0
    assert S.search_current(db.conn, "partially", "default") == []


def test_idempotent_replay_returns_identical_revision(db):
    """C1-2 / gate 2: same key+payload replays with zero new effects."""
    first = S.publish(db, "doc-idem", "repeatable payload text", idempotency_key="key-1")
    counts = {
        t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("source", "source_revision", "atom", "projection_job", "events", "idempotency_keys")
    }
    for _ in range(3):
        replay = S.publish(db, "doc-idem", "repeatable payload text", idempotency_key="key-1")
        assert replay.revision_id == first.revision_id
        assert replay.created is False
        assert replay.job_id == first.job_id
    for t, n in counts.items():
        assert db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == n, t


def test_idempotency_conflict_names_key(db):
    """C1-2 / gate 2: same key + different payload is an explicit conflict."""
    S.publish(db, "doc-idem", "original payload", idempotency_key="key-2")
    with pytest.raises(S.IdempotencyConflict) as exc:
        S.publish(db, "doc-idem", "CONFLICTING payload", idempotency_key="key-2")
    assert "key-2" in str(exc.value)
    assert S.get_current(db.conn, "doc-idem")["text"] == "original payload"
    assert len(S.list_revisions(db.conn, "doc-idem")) == 1


def test_obsolete_worker_publish_rejected(db):
    """Gate 5: a stale expected revision cannot overwrite current projections."""
    v1 = S.publish(db, "doc-race", "first edition")
    v2 = S.publish(db, "doc-race", "second edition", expected_revision=v1.revision_id)
    with pytest.raises(S.ObsoleteRevisionError):
        S.publish(db, "doc-race", "stale worker edition", expected_revision=v1.revision_id)
    assert S.get_current(db.conn, "doc-race")["revision_id"] == v2.revision_id
    assert len(S.list_revisions(db.conn, "doc-race")) == 2
    assert db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0] == 2
    with pytest.raises(S.ObsoleteRevisionError):
        S.retract(db, "doc-race", expected_revision=v1.revision_id)


def test_outbox_claim_complete_lease_rules(db):
    """C1-3: lease floor, ownership, idempotent completion, enqueue idempotency."""
    res = S.publish(db, "doc-job", "work item text")
    with pytest.raises(ValueError, match=">= 30"):
        outbox.claim(db.conn, "w-short", lease_seconds=5)
    first = outbox.claim(db.conn, "worker-a")
    assert first is not None and first.job_id == res.job_id
    assert first.attempt == 1
    row = outbox.get_job(db.conn, first.job_id)
    import time

    assert row["lease_expires_at"] > time.time() + 29  # lease floor >= 30s
    assert outbox.claim(db.conn, "worker-b") is None  # live lease blocks others
    with pytest.raises(outbox.LeaseError):
        outbox.complete(db.conn, first.job_id, "worker-b")
    assert outbox.complete(db.conn, first.job_id, "worker-a") is True
    assert outbox.complete(db.conn, first.job_id, "worker-a") is True  # idempotent
    assert outbox.complete(db.conn, first.job_id, "worker-b") is True  # done stays done
    again = outbox.enqueue(
        db.conn, job_type="projection.refresh", input_revision_id=res.revision_id,
        idempotency_key=f"refresh:{res.revision_id}",
    )
    assert again == res.job_id


def test_outbox_expiry_reclaim_and_restart_resume(db, tmp_path):
    """C1-3: expired leases are reclaimable; jobs survive daemon restart."""
    res = S.publish(db, "doc-resume", "durable work payload")
    dead = outbox.claim(db.conn, "dead-worker")
    assert dead is not None
    # Simulate the dead worker's lease expiring without waiting 30+ wall seconds.
    db.conn.execute(
        "UPDATE projection_job SET lease_expires_at = ? WHERE job_id = ?",
        (0.0, dead.job_id),
    )
    db.conn.commit()
    rescued = outbox.claim(db.conn, "new-worker")
    assert rescued is not None and rescued.job_id == dead.job_id
    assert rescued.attempt == dead.attempt + 1

    path = str(tmp_path / "lifecycle.db")
    job_id, rev_id = dead.job_id, res.revision_id
    db.close()
    resumed = Database(path)  # daemon restart: same file, fresh handle
    try:
        # The rescued job is claimed with a live lease, so a restart still
        # sees it as owned, while a fresh pending job remains claimable.
        assert outbox.claim(resumed.conn, "anyone-else") is None
        still = outbox.get_job(resumed.conn, job_id)
        assert still["state"] == "claimed"
        assert still["input_revision_id"] == rev_id
        fresh = S.publish(resumed, "doc-after-restart", "post restart ingest works")
        assert fresh.created is True
        assert outbox.claim(resumed.conn, "restarted-worker") is not None
    finally:
        resumed.close()


def test_worker_work_happens_outside_writer_transaction(db, tmp_path):
    """C1-3: while claimed work runs, the writer stays free on another connection."""
    res = S.publish(db, "doc-w", "pin this revision for background work")
    claim = outbox.claim(db.conn, "bg-worker")
    reader = Database(tmp_path / "lifecycle.db")  # separate reader connection
    try:
        rev = S.get_revision(reader.conn, claim.input_revision_id)
        assert rev is not None and rev["text"] == "pin this revision for background work"
        # Writer is not held by the claimed job: a concurrent publish commits fine.
        other = S.publish(reader, "doc-w2", "concurrent ingest during background work")
        assert other.created is True
    finally:
        reader.close()
    assert outbox.complete(db.conn, claim.job_id, "bg-worker") is True
    assert res.job_id == claim.job_id


def test_byte_exact_excerpts_unicode_emoji_crlf(db):
    """Gate 1: every claimed byte excerpt resolves exactly in its revision."""
    payloads = [
        "plain ascii excerpt boundary test",
        "héllo wörld ☃ snowman and naïve café",
        "emoji line 🚀🌕✨ done",
        "crlf\r\nline two\r\nline three\r\n",
        "---\ntitle: frontmatter\ndraft: true\n---\nbody after frontmatter",
        "```python\nprint('code fence')\n```\n| a | b |\n|---|---|\n| 1 | 2 |\n",
        "repeated repeated repeated repeated text text",
        "日本語と한국어 mixed scripts mixed scripts",
    ]
    for i, payload in enumerate(payloads):
        res = S.publish(db, f"doc-ex-{i}", payload)
        raw = payload.encode("utf-8")
        rev = S.get_revision(db.conn, res.revision_id)
        assert rev["text"] == payload
        assert rev["byte_len"] == len(raw)
        assert S.excerpt(db.conn, res.revision_id, 0, len(raw)) == raw
        mid = len(raw) // 3
        assert S.excerpt(db.conn, res.revision_id, mid, len(raw)) == raw[mid:]
        assert S.excerpt(db.conn, res.revision_id, 0, 0) == b""
    with pytest.raises(KeyError):
        S.excerpt(db.conn, "no-such-revision", 0, 1)
    with pytest.raises(ValueError):
        S.excerpt(db.conn, res.revision_id, 5, 4)
    with pytest.raises(ValueError):
        S.excerpt(db.conn, res.revision_id, 0, 10**9)


def test_rebuild_equivalence_after_update_delete_sequences(db, tmp_path):
    """Gate 7: incremental state agrees with a full rebuild on final content."""
    S.publish(db, "ga", "alpha one")
    S.publish(db, "gb", "beta one")
    S.publish(db, "ga", "alpha two")
    S.publish(db, "gc", "gamma one")
    S.retract(db, "gb")
    S.publish(db, "ga", "alpha three final")
    live_texts = {
        "ga": S.get_current(db.conn, "ga")["text"],
        "gc": S.get_current(db.conn, "gc")["text"],
    }
    assert S.get_current(db.conn, "gb") is None
    assert live_texts == {"ga": "alpha three final", "gc": "gamma one"}

    clean = Database(tmp_path / "rebuild.db")
    try:
        S.publish(clean, "ga", "alpha three final")
        S.publish(clean, "gc", "gamma one")
        for probe in ("alpha", "beta", "gamma", "final", "three"):
            assert _match_texts(db, probe) == _match_texts(clean, probe), probe
        assert S.verify_store(db.conn)["ok"] is True
        assert S.verify_store(clean.conn)["ok"] is True
    finally:
        clean.close()


def test_namespace_scope_isolation(db):
    """Gate 8/10: scoped search never crosses namespace boundaries."""
    S.publish(db, "shared-word", "quokka habitat conservation", namespace="ns-a")
    S.publish(db, "other-doc", "quokka habitat conservation", namespace="ns-b")
    hits_a = S.search_current(db.conn, "quokka", "ns-a")
    hits_b = S.search_current(db.conn, "quokka", "ns-b")
    assert {h["namespace"] for h in hits_a} == {"ns-a"}
    assert {h["namespace"] for h in hits_b} == {"ns-b"}
    assert S.search_current(db.conn, "quokka", "ns-empty") == []
    with pytest.raises((ValueError, S.IdempotencyConflict)):
        S.publish(db, "shared-word", "changed text", namespace="ns-other")


def test_restart_verifies_hashes_fts_and_manifests(db, tmp_path):
    """Gate 10: after restart, hashes + FTS postings + manifests verify."""
    S.publish(db, "doc-r1", "restart durable text alpha")
    S.publish(db, "doc-r2", "restart durable text beta")
    gid = gen.create_generation(
        db.conn, kind="refresh", config_hash="cfg-1", watermark="w1", manifest_hash="m1"
    )
    gen.activate_generation(db.conn, gid)
    job = outbox.claim(db.conn, "w-final")
    outbox.complete(db.conn, job.job_id, "w-final", output_generation=gid)
    db.close()

    restarted = Database(tmp_path / "lifecycle.db")
    try:
        report = S.verify_store(restarted.conn)
        assert report["ok"] is True, report
        assert report["hash_bad"] == [] and report["hash_ok"] == report["revisions"]
        assert report["fts_integrity"] == "ok"
        assert report["dangling_pointers"] == 0
        assert report["match_probes_failed"] == [] and report["match_probes_ok"] > 0
        active = gen.get_active(restarted.conn, "refresh")
        assert active is not None and active["generation_id"] == gid
        assert [h["text"] for h in restarted.search_fts("durable")] != []
    finally:
        restarted.close()


def test_separate_reader_connection_sees_commits(db, tmp_path):
    """Reader/writer separation: a second connection reads committed revisions."""
    S.publish(db, "doc-ro", "reader visible text")
    reader = Database(tmp_path / "lifecycle.db")
    try:
        assert S.get_current(reader.conn, "doc-ro")["text"] == "reader visible text"
        assert [h["doc_id"] for h in reader.search_fts("visible")] == ["doc-ro"]
    finally:
        reader.close()
