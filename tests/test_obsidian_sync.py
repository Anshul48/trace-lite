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
