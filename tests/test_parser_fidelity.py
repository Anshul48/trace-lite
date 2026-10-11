"""R02 parser fidelity: byte-offset accounting + frontmatter mapping (C2-1).

Adapter-level tests for ``trace_lite.obsidian.parser``. Existing ``parse_note``
behavior for current callers is preserved (LF expectations re-asserted here);
the adapter adds CRLF tolerance plus explicit mapping fields. All span
assertions slice the raw payload bytes.
"""

from trace_lite.obsidian.parser import parse_note, parser_version_for
from trace_lite.source import segmentation as seg

NOTE_LF = """---
tags: [gpu, attention]
type: Paper
aliases: [FlashAttn]
---

# FlashAttention Notes

Discussing #gpu kernels and [[FlashAttention|FlashAttn]] with reference to
[[Triton]] for tile programs.
"""


def _assert_span_slices(raw, span):
    blob = raw.encode("utf-8")
    assert blob[span.start_byte : span.end_byte] == span.text.encode("utf-8")


def test_existing_lf_behavior_preserved():
    """Adapter edits keep current-caller behavior for LF notes (C01 parity)."""
    note = parse_note("notes/FlashAttention.md", NOTE_LF)
    assert note.frontmatter["tags"] == ["gpu", "attention"]
    assert note.frontmatter["type"] == ["Paper"]
    assert "gpu" in note.tags
    targets = [t for t, _, _ in note.wikilinks]
    assert "FlashAttention" in targets and "Triton" in targets
    blob = NOTE_LF.encode("utf-8")
    for _, _, span in note.wikilinks:
        _assert_span_slices(NOTE_LF, span)
        assert span.text.startswith("[[")
    assert note.body_byte_offset == len(NOTE_LF.encode("utf-8")) - len(note.body.encode("utf-8"))
    assert note.newline == "lf"


def test_frontmatter_mapping_fields():
    """Byte mapping: frontmatter span + body offsets compose to raw identity."""
    note = parse_note("n.md", NOTE_LF)
    blob = NOTE_LF.encode("utf-8")
    assert note.frontmatter_span is not None
    _assert_span_slices(NOTE_LF, note.frontmatter_span)
    assert note.frontmatter_span.start_byte == 0
    assert note.frontmatter_span.end_byte == note.body_byte_offset
    assert blob[note.body_byte_offset :] == note.body.encode("utf-8")
    assert NOTE_LF[note.body_char_offset :] == note.body
    assert note.parser_version


def test_crlf_frontmatter_stripped_with_exact_offset():
    """CRLF notes strip frontmatter; the offset counts CR bytes (C2-1)."""
    raw = (
        "---\r\ntags: [gpu, attention]\r\ntype: Paper\r\n---\r\n\r\n"
        "# FlashAttention Notes\r\n\r\nDiscussing #gpu and [[Triton]].\r\n"
    )
    note = parse_note("notes/Crlf.md", raw)
    assert note.newline == "crlf"
    assert note.frontmatter["tags"] == ["gpu", "attention"]
    assert note.frontmatter["type"] == ["Paper"]
    assert "# FlashAttention Notes" in note.body
    assert not note.body.lstrip("\r\n").startswith("---") and "---\r\n" not in note.body
    assert raw.encode("utf-8")[note.body_byte_offset :] == note.body.encode("utf-8")
    assert note.frontmatter_span is not None
    _assert_span_slices(raw, note.frontmatter_span)
    for _, _, span in note.wikilinks:
        _assert_span_slices(raw, span)


def test_crlf_no_frontmatter_offsets():
    """CRLF body without frontmatter: wikilink/section spans stay byte-exact."""
    raw = "# Héllo 🚀\r\n\r\nSee [[Target Note|alias]] and #tag here.\r\n"
    note = parse_note("n.md", raw)
    assert note.body_byte_offset == 0
    assert note.frontmatter_span is None
    assert note.body == raw
    blob = raw.encode("utf-8")
    assert len("Héllo 🚀".encode("utf-8")) != len("Héllo 🚀")
    for _, _, span in note.wikilinks:
        _assert_span_slices(raw, span)
    for span in note.sections:
        _assert_span_slices(raw, span)
    # Pre-existing span_of behavior: the match runs to `$` (before `\n`), so the
    # span keeps the `\r` — byte-exact, resolved verbatim.
    assert blob[note.sections[0].start_byte : note.sections[0].end_byte].decode() == "# Héllo 🚀\r"


def test_unicode_emoji_offsets_after_multibyte():
    """Byte (not char) offsets past multi-byte content (C2-1)."""
    raw = "🚀🌕✨ prefix with emoji\n\nSee [[Link After Emoji]] here.\n"
    note = parse_note("n.md", raw)
    assert len(note.wikilinks) == 1
    _, _, span = note.wikilinks[0]
    _assert_span_slices(raw, span)
    char_index = raw.index("[[")
    assert span.start_byte == len(raw[:char_index].encode("utf-8"))
    assert span.start_byte != char_index  # multi-byte prefix: bytes != chars
    assert raw.encode("utf-8")[span.start_byte : span.end_byte].decode() == span.text


def test_frontmatter_unicode_char_vs_byte_offset():
    """Char/byte boundary differ past multi-byte frontmatter (mapping check)."""
    raw = "---\ntitle: héllo 🚀 world\n---\n\nBody here.\n"
    note = parse_note("n.md", raw)
    assert note.frontmatter["title"] == ["héllo 🚀 world"]
    assert note.body_char_offset != note.body_byte_offset  # multi-byte fm block
    assert raw.encode("utf-8")[note.body_byte_offset :] == note.body.encode("utf-8")
    assert raw[note.body_char_offset :] == note.body


def test_no_frontmatter_identity():
    """No frontmatter: body is the raw payload, offset zero, span None."""
    raw = "# Plain\n\nJust body ☃.\n"
    note = parse_note("n.md", raw)
    assert note.frontmatter == {}
    assert note.frontmatter_span is None
    assert note.body == raw and note.body_byte_offset == 0 and note.body_char_offset == 0


def test_segment_body_maps_to_raw_bytes():
    """Parser + segmenter compose: body spans slice the raw note bytes."""
    raw = NOTE_LF.replace("#gpu kernels", "#gpu kernels héllo 🚀")
    note = parse_note("n.md", raw)
    spans = seg.segment_text(note.body, body_byte_base=note.body_byte_offset)
    assert spans
    assert seg.verify_spans(raw, spans)
    for span in spans:
        _assert_span_slices(raw, span)


def test_parser_version_hash_stable():
    v1 = parser_version_for()
    assert v1 == parser_version_for()
    assert v1.startswith("obsidian-markdown-v1:")
