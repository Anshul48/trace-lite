"""P06 acceptance: markdown parsing, debounced sync, REST API on :8420."""

import time

from fastapi.testclient import TestClient

from trace_lite.api import create_app
from trace_lite.obsidian import DebouncedWatcher, facet_hints, parse_note

NOTE = """---
tags: [gpu, attention]
type: Paper
aliases: [FlashAttn]
---

# FlashAttention Notes

Discussing #gpu kernels and [[FlashAttention|FlashAttn]] with reference to
[[Triton]] for tile programs.
"""


def test_parser_frontmatter_tags_wikilinks_byte_exact():
    """C01: frontmatter, tags, wikilinks parsed with exact byte offsets."""
    note = parse_note("notes/FlashAttention.md", NOTE)
    assert note.frontmatter["tags"] == ["gpu", "attention"]
    assert note.frontmatter["type"] == ["Paper"]
    assert "gpu" in note.tags
    targets = [t for t, _, _ in note.wikilinks]
    assert "FlashAttention" in targets and "Triton" in targets
    blob = NOTE.encode("utf-8")
    for _, _, span in note.wikilinks:
        assert blob[span.start_byte:span.end_byte] == span.text.encode("utf-8")
        assert span.text.startswith("[[")
    assert note.body_byte_offset == len(NOTE.encode("utf-8")) - len(note.body.encode("utf-8"))
    hints = facet_hints(note)
    assert "gpu" in hints["Topics"] and "FlashAttention" in hints["Entities"]
    assert hints["Types"] == ["Paper"] and hints["Sources"] == ["ObsidianVault"]
    assert hints["Projects"] == ["notes"]


def test_debounce_coalesces_rapid_writes(tmp_path):
    """C02 / MED-02: 10 rapid saves → exactly 1 re-indexing pass (500ms buffer)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "Note.md"
    target.write_text("# hello\n")
    fired: list = []
    watcher = DebouncedWatcher(vault, on_sync=fired.append, poll_seconds=0.05)
    for i in range(10):  # 10 saves inside 200ms, emulating keystroke bursts
        target.write_text(f"# hello {i}\n")
        watcher.scan_once()
        time.sleep(0.02)
    assert watcher.pending_count() == 1
    time.sleep(0.55)  # let the 500ms debounce window elapse
    watcher.scan_once()
    assert watcher.sync_count == 1 and len(fired) == 1
    assert fired[0] == [target]
    assert watcher.pending_count() == 0


def _seeded_client(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text(NOTE)
    (vault / "Other.md").write_text("# Gardening\n\nSoil and #compost notes.\n")
    client = TestClient(create_app(tmp_path / "api.db", vault))
    return client, vault


def test_plugin_wire_protocol_compat(tmp_path):
    """F2: exact plugin shapes — ingest push, query_text/top_k/mode, items envelope, status."""
    client, _ = _seeded_client(tmp_path)
    with client:
        pushed = client.post("/api/ingest", json={
            "text": "# Pushed\n\nNote about #quokka pushed over HTTP.\n",
            "document_name": "Pushed.md",
        }).json()
        assert pushed["ok"] is True and pushed["atoms"] == 1
        assert pushed["doc_id"] == "Pushed.md"
        # Plugin-shaped query (was HTTP 422 before dual mapping).
        data = client.post("/api/query", json={
            "query_text": "quokka pushed note", "top_k": 5, "mode": "hybrid",
        }).json()
        assert data["query_text"] == "quokka pushed note" and data["mode"] == "hybrid"
        assert data["total_results"] >= 1
        first = data["items"][0]
        assert first["atom"]["content"] and "quokka" in first["atom"]["content"]
        assert first["source_artifact"]["document_name"] == "Pushed.md"
        # Legacy envelope still served alongside.
        assert data["anchors"] and data["sufficiency_state"] == "answerable"
        status = client.get("/api/status").json()
        for key in ("total_atoms", "total_trees", "index_trusted",
                    "needs_organization", "pending_atoms"):
            assert key in status, f"status missing plugin key {key}"
        assert status["total_atoms"] == 1


def test_watcher_lifespan_syncs_new_notes(tmp_path):
    """F4: lifespan-started watcher picks up vault notes without manual /api/sync."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Seed.md").write_text("# Seed\n\nBase note.\n")
    client = TestClient(create_app(tmp_path / "watch.db", vault))
    with client:
        client.post("/api/sync", json={})
        (vault / "Live.md").write_text("# Live\n\nFresh note about #quokka watcher flow.\n")
        deadline = time.monotonic() + 5.0
        found = False
        while time.monotonic() < deadline:
            time.sleep(0.3)
            data = client.post("/api/query", json={"query": "quokka watcher"}).json()
            if any(a["doc_id"] == "Live.md" for a in data["anchors"]):
                found = True
                break
        assert found, "background watcher never synced the new note"
        # Critic loop: deletions propagate too — no ghost notes.
        (vault / "Live.md").unlink()
        deadline = time.monotonic() + 5.0
        gone = False
        while time.monotonic() < deadline:
            time.sleep(0.3)
            status = client.get("/api/status").json()
            if status["total_atoms"] == 1:
                gone = True
                break
        assert gone, "deleted note still indexed after watcher removal pass"
        data = client.post("/api/query", json={"query": "quokka watcher"}).json()
        assert all(a["doc_id"] != "Live.md" for a in data["anchors"])


def test_watcher_removal_callback_unit(tmp_path):
    """Critic loop: vanished files fire on_remove exactly once and stop tracking."""
    from trace_lite.obsidian import DebouncedWatcher

    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "Gone.md"
    target.write_text("# gone\n")
    removed: list = []
    watcher = DebouncedWatcher(vault, on_sync=lambda p: None,
                               on_remove=removed.append, poll_seconds=0.05)
    watcher.scan_once()
    target.unlink()
    watcher.scan_once()
    assert removed == [[target]]
    assert watcher.remove_count == 1
    watcher.scan_once()  # no repeat firing
    assert watcher.remove_count == 1


def test_rest_api_sync_and_query(tmp_path):
    """C03: /api/sync ingests the vault; /api/query answers in < 50ms."""
    client, _ = _seeded_client(tmp_path)
    with client:
        assert client.get("/api/health").json() == {"ok": True}
        synced = client.post("/api/sync", json={}).json()
        assert synced["synced"] == 2 and synced["atoms"] == 2
        status = client.get("/api/status").json()
        assert status["ok"] and status["atoms"] == 2 and status["facets"] >= 5
        start = time.perf_counter()
        answer = client.post(
            "/api/query", json={"query": "FlashAttention gpu kernels"}
        ).json()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert answer["sufficiency_state"] == "answerable"
        assert any(a["doc_id"] == "Note.md" for a in answer["anchors"])
        assert elapsed_ms < 50.0, f"API query {elapsed_ms:.1f}ms exceeds 50ms"
        # Idempotent re-sync replaces atoms instead of duplicating them.
        again = client.post("/api/sync", json={}).json()
        assert again["atoms"] == 2


def test_sequential_ingest_no_lock_freeze(tmp_path):
    """Multiple sequential /api/ingest calls use incremental indexing and remain queryable."""
    client, _ = _seeded_client(tmp_path)
    with client:
        for i in range(20):
            res = client.post("/api/ingest", json={
                "document_name": f"Note_{i}.md",
                "text": f"# Note {i}\n\nContent for note number {i} with unique keyword zebra_{i}.\n",
            }).json()
            assert res["ok"] is True

        # Immediately query the last note without waiting for background debounce
        data = client.post("/api/query", json={"query": "zebra_19 note"}).json()
        assert data["total_results"] >= 1
        assert any(a["doc_id"] == "Note_19.md" for a in data["anchors"])

        # Update an existing note and verify new content is immediately queryable
        client.post("/api/ingest", json={
            "document_name": "Note_0.md",
            "text": "# Note 0 Updated\n\nReplaced body with unique keyword platypus_updated.\n",
        })
        updated = client.post("/api/query", json={"query": "platypus_updated"}).json()
        assert updated["total_results"] >= 1
        assert any(a["doc_id"] == "Note_0.md" for a in updated["anchors"])

        # Replaced content should no longer match Note_0.md
        old_query = client.post("/api/query", json={"query": "zebra_0 note"}).json()
        assert not any(a["doc_id"] == "Note_0.md" for a in old_query["anchors"])


def test_concurrent_ingest_and_query_stress(tmp_path):
    """Concurrent /api/ingest and /api/query requests under multi-threading execute cleanly without lock freeze."""
    import concurrent.futures

    client, _ = _seeded_client(tmp_path)
    with client:
        errors: list[Exception] = []

        def do_ingest(i: int):
            try:
                res = client.post("/api/ingest", json={
                    "document_name": f"Concurrent_{i}.md",
                    "text": f"# Concurrent {i}\n\nConcurrent document payload {i} with key concurrent_key_{i}.\n",
                })
                assert res.status_code == 200
                assert res.json()["ok"] is True
            except Exception as e:
                errors.append(e)

        def do_query(i: int):
            try:
                res = client.post("/api/query", json={"query": f"concurrent_key_{i}"})
                assert res.status_code == 200
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for i in range(15):
                futures.append(executor.submit(do_ingest, i))
                futures.append(executor.submit(do_query, i))
            concurrent.futures.wait(futures)

        assert not errors, f"Concurrent operations failed with errors: {errors}"
