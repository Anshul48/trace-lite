"""R02 source spans + versioned segmentation views (C2-1..C2-4).

Behavior-level tests against real slicing and real SQLite files. Every span
assertion slices the retained raw payload bytes; fidelity is 100% or the test
fails. Fixtures are bounded; databases live in ``tmp_path``.
"""

import pytest

from trace_lite import source as S
from trace_lite.source import segmentation as seg
from trace_lite.source import views as V
from trace_lite.store import Database, migrate

UNICODE_PARA = "Héllo wörld with snowman ☃ and math ∑ ∀ ∃."
EMOJI_PARA = "Launch 🚀 moon 🌕 stars ✨ done — party 🎉."
CRLF_PARA = "line one\r\nline two\r\nline three\r\n"
REPEAT_PARA = "The identical sentence appears here verbatim."
TABLE_TEXT = "| claim | result |\n|---|---|\n| batching | +30% |\n| wal | readers ✓ |\n"
FENCE_TEXT = "```python\nprint('héllo 🚀')\n\n# blank line inside fence\nx = 1\n```\n"


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "spans.db")
    yield database
    database.close()


def _roundtrip_ok(raw, spans):
    blob = raw.encode("utf-8")
    assert seg.verify_spans(raw, spans)
    for span in spans:
        assert blob[span.start_byte : span.end_byte] == span.text.encode("utf-8")
        assert 0 <= span.start_byte < span.end_byte <= len(blob)


# -- C2-1 exact byte round-trip ---------------------------------------------

CASES = {
    "unicode": UNICODE_PARA + "\n",
    "emoji": EMOJI_PARA + "\n",
    "crlf": CRLF_PARA,
    "mixed_crlf_unicode": ("Héllo 🚀\r\n" + UNICODE_PARA + "\r\n"),
    "table": TABLE_TEXT,
    "code_fence": FENCE_TEXT,
    "repeated": (REPEAT_PARA + "\n\n") * 3,
    "bullets": "- alpha héllo\n- beta 🚀\n- gamma ☃\n",
    "turns": "User: does batching help?\n\nAssistant: yes, by 30% under low contention.\n",
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_byte_roundtrip_case(name):
    """C2-1: every claimed span matches raw source bytes verbatim (100%)."""
    raw = CASES[name]
    spans = seg.segment_text(raw)
    assert spans, name
    _roundtrip_ok(raw, spans)


def test_byte_roundtrip_frontmatter_body_maps_to_raw():
    """C2-1: frontmatter-stripped body spans address raw payload bytes."""
    from trace_lite.obsidian.parser import parse_note

    raw = "---\ntitle: héllo 🚀\n---\n\n" + UNICODE_PARA + "\n\n" + EMOJI_PARA + "\n"
    note = parse_note("n.md", raw)
    spans = seg.segment_text(note.body, body_byte_base=note.body_byte_offset)
    assert spans
    _roundtrip_ok(raw, spans)


def test_db_excerpt_roundtrip_all_spans(db):
    """C2-1: persisted spans resolve byte-exactly via excerpt_span (gate 1)."""
    raw = (
        "# Héllo 🚀\r\n\r\n" + UNICODE_PARA + "\r\n\r\n" + TABLE_TEXT + "\r\n"
        + FENCE_TEXT + "\r\n" + (REPEAT_PARA + "\r\n\r\n") * 2
    )
    rev = S.publish(db, "doc-fidelity", raw)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    assert view["status"] == "complete" and view["span_count"] > 0
    spans = V.get_spans(db.conn, view["view_id"])
    assert len(spans) == view["span_count"]
    blob = raw.encode("utf-8")
    for span in spans:
        excerpt = V.excerpt_span(db.conn, span["span_id"])
        assert excerpt == span["text"].encode("utf-8")
        assert blob[span["start_byte"] : span["end_byte"]] == excerpt
        assert span["coordinate_space"] == "raw_utf8_bytes"


def test_excerpt_detects_corruption(db):
    """C2-1 negative: a tampered payload fails hash verification, not silent."""
    rev = S.publish(db, "doc-corrupt", "stable text body here\n")
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    span_id = view["span_ids"][0]
    db.conn.execute(
        "UPDATE source_revision SET text = ? WHERE revision_id = ?",
        ("tampered text changed!", rev.revision_id),
    )
    db.conn.commit()
    with pytest.raises(ValueError, match="hash verification"):
        V.excerpt_span(db.conn, span_id)


def test_repeated_text_gets_separate_span_ids(db):
    """C2-1/ARCH §2: identical occurrences keep separate span IDs (ordinal)."""
    raw = (REPEAT_PARA + "\n\n") * 3
    rev = S.publish(db, "doc-repeat", raw)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    spans = V.get_spans(db.conn, view["view_id"])
    assert len(spans) == 3
    assert len({s["span_id"] for s in spans}) == 3
    assert [s["ordinal"] for s in spans] == [0, 1, 2]
    assert all(s["text"].rstrip("\n") == REPEAT_PARA for s in spans)


# -- C2-2 sub-document addressability ----------------------------------------

A1B2_DOC = (
    "# A1 finding\n\nBatching improves throughput by 30% under low contention.\n\n"
    "# B method\n\nWe measured syscalls per item on NVMe hardware.\n\n"
    "# A2 finding\n\nUnder high contention the batching benefit disappears entirely.\n"
)


def test_a1_b_a2_independent_retrieval(db):
    """C2-2: A1 and A2 retrieve independently while sequence order is kept."""
    rev = S.publish(db, "doc-a1b2", A1B2_DOC)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    spans = V.get_spans(db.conn, view["view_id"])
    assert [s["ordinal"] for s in spans] == sorted(s["ordinal"] for s in spans)
    by_header = {}
    for s in spans:
        by_header.setdefault(s["header"] or s["text"].strip(), []).append(s)
    a1 = by_header["A1 finding"]
    a2 = by_header["A2 finding"]
    assert a1 and a2
    assert a1[0]["ordinal"] < a2[0]["ordinal"]
    assert "30%" in a1[0]["text"] and "disappears" in a2[0]["text"]
    for s in spans:
        assert len(s["text"]) <= seg.MAX_SPAN_CHARS


def test_length_cap_splits_long_paragraph():
    """C2-2 limit: over-cap paragraphs split; every piece stays byte-exact."""
    raw = " ".join(["Sentence number %d states a verifiable fact." % i for i in range(120)])
    raw += "\n"
    assert len(raw) > 3 * seg.MAX_SPAN_CHARS
    spans = seg.segment_text(raw)
    assert len(spans) >= 3
    for span in spans:
        assert len(span.text) <= seg.MAX_SPAN_CHARS
    _roundtrip_ok(raw, spans)


def _ws_variant_sentences(n=60):
    return [
        "Sentence number %d states a verifiable fact with supporting detail." % i
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "joiner,name",
    [("\n", "soft-wrapped"), ("  ", "double-space"), ("\t", "tab-separated")],
)
def test_length_cap_whitespace_variants_split_byte_exact(db, joiner, name):
    """C2-2/F1: over-cap paragraphs joined by any whitespace run split ≤1200
    chars, byte-exact, order preserved — no ValueError, no zero-span partial."""
    raw = joiner.join(_ws_variant_sentences()) + "\n"
    assert len(raw) > 2 * seg.MAX_SPAN_CHARS
    spans = seg.segment_text(raw)
    assert len(spans) >= 2, name
    for span in spans:
        assert len(span.text) <= seg.MAX_SPAN_CHARS, name
    assert [s.ordinal for s in spans] == list(range(len(spans))), name
    _roundtrip_ok(raw, spans)
    rev = S.publish(db, "doc-ws-" + name, raw)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    assert view["status"] == "complete", name
    assert view["span_count"] > 0, name
    stored = V.get_spans(db.conn, view["view_id"])
    assert len(stored) == view["span_count"], name
    blob = raw.encode("utf-8")
    for s in stored:
        assert len(s["text"]) <= seg.MAX_SPAN_CHARS, name
        assert blob[s["start_byte"] : s["end_byte"]] == s["text"].encode("utf-8"), name
    assert [s["ordinal"] for s in stored] == sorted(s["ordinal"] for s in stored), name


# -- C2-3 structural integrity -----------------------------------------------

def test_code_fence_atomic_with_tricky_inside():
    """C2-3: fences stay undivided despite blank lines and markdown inside."""
    raw = (
        "Intro para.\n\n```python\n# not a heading\n\n| not | a table |\n"
        "- not a bullet\n\nprint(1)\n```\n\nAfter para.\n"
    )
    spans = seg.segment_text(raw)
    codes = [s for s in spans if s.kind == "code"]
    assert len(codes) == 1
    assert "After para" not in codes[0].text
    assert [s.kind for s in spans] == ["paragraph", "code", "paragraph"]
    _roundtrip_ok(raw, spans)


def test_table_atomic():
    """C2-3: markdown tables remain undivided in one span."""
    spans = seg.segment_text("Intro.\n\n" + TABLE_TEXT + "\nTail.\n")
    tables = [s for s in spans if s.kind == "table"]
    assert len(tables) == 1
    assert "+30%" in tables[0].text and "readers ✓" in tables[0].text


def test_zero_syntax_breaks_stress():
    """C2-3: every fence open/close pair lands in the same span (zero breaks)."""
    raw = (
        "# Doc héllo 🚀\n\nText one.\n\n```sql\nSELECT a\n\nFROM t\n```\n\n"
        "| h1 | h2 |\n|---|---|\n| x | y |\n\n- item one\n- item two\n\n"
        "```\nplain fence\nwith # heading-like\n```\n\nFinal text.\n"
    )
    spans = seg.segment_text(raw)
    _roundtrip_ok(raw, spans)
    breaks = 0
    lines = raw.splitlines(keepends=True)
    offs, pos = [], 0
    for line in lines:
        offs.append((pos, pos + len(line.encode("utf-8"))))
        pos += len(line.encode("utf-8"))
    markers = [i for i, line in enumerate(lines) if line.strip().startswith("```")]
    assert len(markers) == 4
    for open_i, close_i in zip(markers[::2], markers[1::2]):
        holders = {
            s.ordinal
            for s in spans
            if s.start_byte <= offs[open_i][0] and offs[close_i][1] <= s.end_byte
        }
        if holders != {next(iter(holders))} or len(holders) != 1:
            breaks += 1
    assert breaks == 0
    assert sum(1 for s in spans if s.kind == "code") == 2
    assert sum(1 for s in spans if s.kind == "table") == 1


def test_unclosed_fence_atomic_to_eof():
    """C2-3: an unclosed fence becomes one atomic span (no orphan fragments)."""
    raw = "Intro.\n\n```python\nprint(1)\nmore code\n"
    spans = seg.segment_text(raw)
    assert [s.kind for s in spans] == ["paragraph", "code"]
    assert spans[1].text.endswith("more code\n")
    _roundtrip_ok(raw, spans)


# -- C2-4 discourse envelope ---------------------------------------------------

def test_discourse_envelope_parent_and_neighbors(db):
    """C2-4: child spans expose antecedent header + 1-hop ordinal neighbors."""
    raw = "# Definitions\n\nWAL mode allows concurrent readers.\n\n- reader rule one\n- reader rule two\n"
    rev = S.publish(db, "doc-discourse", raw)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    spans = V.get_spans(db.conn, view["view_id"])
    bullets = [s for s in spans if s["kind"] == "bullet"]
    assert len(bullets) == 2
    assert all(b["header"] == "Definitions" for b in bullets)
    env = V.envelope(db.conn, bullets[0]["span_id"])
    assert env["parent"] is not None
    assert env["parent"]["kind"] == "heading"
    assert "Definitions" in env["parent"]["text"]
    assert env["prev"] is not None and env["next"] is not None
    assert env["prev"]["ordinal"] == bullets[0]["ordinal"] - 1
    assert env["next"]["ordinal"] == bullets[0]["ordinal"] + 1


def test_header_context_flows_to_paragraphs():
    """C2-4: paragraphs under a heading carry its text as context."""
    spans = seg.segment_text("# Claim\n\nBatching wins.\n\n# Other\n\nLoses here.\n")
    paras = [s for s in spans if s.kind == "paragraph"]
    assert [p.header for p in paras] == ["Claim", "Other"]


# -- versioned views ------------------------------------------------------------

def test_resegmentation_new_view_prior_immutable(db):
    """Views: re-segmentation mints a new view; prior span IDs untouched."""
    rev = S.publish(db, "doc-views", "# T\n\nBody text here.\n\n- x\n")
    first = V.create_segmentation_view(db.conn, rev.revision_id, config="structural-v1")
    before_spans = V.get_spans(db.conn, first["view_id"])
    before_rows = db.conn.execute("SELECT * FROM evidence_span ORDER BY span_id").fetchall()
    second = V.create_segmentation_view(db.conn, rev.revision_id, config="structural-v2")
    assert second["view_id"] != first["view_id"]
    assert len(V.list_views(db.conn, rev.revision_id)) == 2
    after_rows = db.conn.execute("SELECT * FROM evidence_span ORDER BY span_id").fetchall()
    assert [tuple(r) for r in after_rows if r["view_id"] == first["view_id"]] == [
        tuple(r) for r in before_rows
    ]
    assert V.get_spans(db.conn, first["view_id"]) == before_spans
    assert set(second["span_ids"]).isdisjoint(set(first["span_ids"]))


def test_partial_status_on_segmentation_failure(db, monkeypatch):
    """Recovery: failure keeps the retained source with a partial empty view."""
    rev = S.publish(db, "doc-partial", "some body\n")

    def _boom(_text, **_kwargs):
        raise RuntimeError("injected segmenter fault")

    monkeypatch.setattr(V, "segment_text", _boom)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    assert view["status"] == "partial"
    assert view["span_ids"] == [] and view["span_count"] == 0
    assert S.get_revision(db.conn, rev.revision_id)["text"] == "some body\n"


def test_version_hashes_recorded(db):
    """R00-F2: parser/segmenter version hashes ride on spans and views."""
    from trace_lite.obsidian.parser import parser_version_for

    rev = S.publish(db, "doc-versions", "Body one.\n")
    view = V.create_segmentation_view(
        db.conn, rev.revision_id, parser_version=parser_version_for()
    )
    stored = db.conn.execute(
        "SELECT * FROM segmentation_view WHERE view_id = ?", (view["view_id"],)
    ).fetchone()
    assert stored["segmenter_version"] == seg.segmenter_version_for("structural-v1")
    assert stored["parser_version"] == parser_version_for()
    assert stored["config_hash"] and stored["span_count"] == 1
    span = V.get_spans(db.conn, view["view_id"])[0]
    assert span["segmenter_version"] == stored["segmenter_version"]
    assert span["parser_version"] == stored["parser_version"]


def test_unknown_revision_raises(db):
    with pytest.raises(KeyError):
        V.create_segmentation_view(db.conn, "no-such-revision")


def test_span_id_deterministic_per_view():
    """Same (revision, run, ordinal, bytes) always mints the same span ID."""
    a = V.span_id_for("rev1", "run1", 0, 0, 10)
    assert a == V.span_id_for("rev1", "run1", 0, 0, 10)
    assert a != V.span_id_for("rev1", "run2", 0, 0, 10)
    assert a != V.span_id_for("rev1", "run1", 1, 0, 10)


# -- migration 002 ---------------------------------------------------------------

def test_migration_002_additive_and_rerunnable(tmp_path):
    """Migration-002 adds span tables only; existing rows survive; rerun safe."""
    path = tmp_path / "spans002.db"
    db = Database(path)
    db.bulk_ingest([("doc-a", "alpha héllo 🚀"), ("doc-b", "beta text")])
    db.close()
    first = migrate.migrate_legacy(path)
    assert first["migration_version"] == 1
    report = migrate.migrate_spans_002(path)
    assert report["migration_version"] == 2
    assert report["tables"] == ["evidence_span", "segmentation_view"]
    assert report["backup"] is not None and report["backup"].endswith(".bak-002")
    again = migrate.migrate_spans_002(path)
    assert again["migration_version"] == 2
    db = Database(path)
    try:
        assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
        assert db.count_atoms() == 2
        assert S.get_current(db.conn, "doc-a")["text"] == "alpha héllo 🚀"
        # Span tables are usable on the migrated file.
        rev = S.publish(db, "doc-c", "Fresh body.\n")
        view = V.create_segmentation_view(db.conn, rev.revision_id)
        assert view["span_count"] == 1
    finally:
        db.close()
