# R02 — Delivery (B2)

Record type: builder delivery
Author role and date: R02 builder (B1 readiness + B2 build) / 2026-09-12
Packet and contract revision: R02-spans.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01 owned set
(R01 VERIFIED base, untouched except additive migration-002) + R02 owned files below
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite + FTS5, repo v9fs,
scratch/evidence DBs under /tmp ext4

## Delivery section

- Behavior/artifact changed and why: source-faithful structural spans with UTF-8
  half-open byte ranges plus versioned segmentation views (C2-1..C2-4). New
  `source.segmentation` owns the structural splitter (paragraphs, bullets,
  headings, atomic code fences/tables, turns/log-events, 1200-char cap); new
  `source.views` persists immutable `evidence_span` rows + `segmentation_view`
  rows so re-segmentation mints a new view without rewriting prior evidence.
- Files changed:
  - `src/trace_lite/source/segmentation.py` (new: `segment_text`, `verify_spans`,
    `segmenter_version_for`, `MAX_SPAN_CHARS = 1200`)
  - `src/trace_lite/source/views.py` (new: `create_segmentation_view`,
    `list_views`, `get_spans`, `excerpt_span`, `envelope`, `span_id_for`)
  - `src/trace_lite/source/__init__.py` (additive exports only; `service.py`
    NOT rewritten)
  - `src/trace_lite/obsidian/parser.py` (adapter only: CRLF-tolerant frontmatter
    regex, `PARSER_NAME/VERSION`, `parser_version_for`, `frontmatter_span` /
    `body_char_offset` / `newline` mapping fields with defaults; all existing
    `parse_note` outputs identical for LF input)
  - `src/trace_lite/store/schema.sql` (additive: `evidence_span`,
    `segmentation_view` + indexes; deliberately NO version-2 row in static DDL
    so migration-001 callers keep `MAX(schema_version) == 1`)
  - `src/trace_lite/store/migrate.py` (additive: `migrate_spans_002` + `.bak-002`
    snapshot; rerun-safe; no extraction/graph/holon/embedding tables)
  - `tests/test_source_spans.py` (new, 27 tests), `tests/test_parser_fidelity.py`
    (new, 9 tests); all pre-existing tests unmodified
  - `evidence/query-native/R02/{readiness.md,delivery.md,byte_fidelity.log,
    subdoc_test.json,syntax_blocks.log,discourse_context.json}` (this packet)
- Material implementation decisions:
  - Span text is the verbatim raw slice; only whole blank boundary lines drop, so
    byte ranges always cover exactly the returned text (no normalization exists
    to misclaim identity). Frontmatter-stripped bodies segment with
    `body_byte_base = body_byte_offset`; the mapping rides on the view
    `signal_json` (`body_byte_base`, `newline`, `frontmatter_bytes`).
  - `span_id = sha256(revision_id:run_id:ordinal:start:end)[:32]`: deterministic
    per view, distinct per repeat occurrence (ordinal) and per re-segmentation
    (run id). View row is inserted before its spans (FK); failure path inserts a
    `status='partial'` view with zero spans and commits — never fabricated offsets.
  - Over-cap code fences/tables stay atomic (integrity wins over the 1200-char
    cap); over-cap paragraphs split on sentence boundaries (inter-chunk
    whitespace stays unclaimed — each claimed span is still byte-exact).
  - 1-hop parent envelope = nearest preceding heading span (`parent_ordinal`) +
    header text + ordinal neighbors via `envelope()`.
  - CRLF gap fixed: old `FRONTMATTER_RE` required LF so CRLF notes leaked
    frontmatter into the body; new regex tolerates `\r\n` with identical LF behavior.
- Known limitations and checks not run: structural baseline only (no
  ML/perplexity boundaries — R08 non-goal); setext headings, indented code
  blocks, and single-pipe-line tables are not special-cased; no benchmark-scale
  loads (bounded fixtures + 200-doc BEIR prefix sample); BEIR mean 2.64
  spans/source is a prefix-sample measurement, not a corpus claim.

## Evidence

| Criterion | Command or evaluation method | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C2-1 Exact byte round-trip | `.venv/bin/python /tmp/gen_r02_evidence.py` (exit 0) + `test_byte_roundtrip_*`, `test_db_excerpt_*`, `test_parser_*` (17 tests) | 16/16 spans byte-exact across unicode/CRLF/emoji/frontmatter/tables/fences/repeats; tamper raises hash error | `evidence/query-native/R02/byte_fidelity.log` | fidelity 1.0 on bounded matrix |
| C2-2 Sub-document addressability | same script (exit 0) + `test_a1_b_a2_*`, `test_length_cap_*` (3 tests) | A1 (ord 1) and A2 (ord 5) independent, order kept, max 58 chars; 200-doc scifact sample mean 2.64 spans/source; long para splits 3x ≤1200 | `evidence/query-native/R02/subdoc_test.json` | prefix sample; partial R00-F2(b–c) close |
| C2-3 Structural integrity | same script (exit 0) + `test_code_fence_*`, `test_table_*`, `test_zero_syntax_*`, `test_unclosed_*` (4 tests) | 2 fence pairs co-located, unclosed tail atomic, 0 breaks | `evidence/query-native/R02/syntax_blocks.log` | structural classes only |
| C2-4 Discourse envelope | same script (exit 0) + `test_discourse_*`, `test_header_*` (2 tests) | bullet ord 3 → parent heading ord 0 "Definitions", prev 2 / next 4 | `evidence/query-native/R02/discourse_context.json` | 1-hop only |
| Views immutable + partial | `test_resegmentation_*`, `test_partial_*`, `test_version_*`, `test_span_id_*`, `test_unknown_*` (5 tests) | new view, old rows byte-identical, new IDs disjoint; fault → partial/0 spans, source retained | new test files | injected fault, not power loss |
| Migration-002 additive | `test_migration_002_*` (1 test) | tables added, atoms/revisions intact, version 2, rerun safe, `.bak-002` written | new test file | bounded fixture |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 109 passed (73 pre-existing unmodified + 36 new) in ~17 s | session log | pre-existing warning only |
| Gates 1 (byte excerpts) | `test_db_excerpt_roundtrip_all_spans` + `test_excerpt_detects_corruption` | all spans resolve + hash-verified; corruption raises | new test files | bounded fixtures |

## Independent findings

(none — builder role; verifier uses review.md)

## B4 repair (attempt 1, 2026-09-12) — F1 over-cap whitespace crash

- Cause: `_split_long_text` (`src/trace_lite/source/segmentation.py`) split on
  sentence-boundary whitespace, re-joined parts with a single `" "`, then
  re-located each chunk via `text.find`. Any original separator other than one
  space (soft-wrap `\n`, double space, tab) made `find` fail, raising
  `ValueError`, which `create_segmentation_view` converts to a whole-revision
  `partial` view with zero spans. No span-ID or view-contract change was needed
  (`views.py` untouched — its retained-source/partial rule behaved as designed;
  the defect was purely the splitter's rejoin-and-find).
- Fix: offset-tracking split — tokenize into non-separator parts via
  `_SENTENCE_SPLIT_RE.finditer` (char offsets, same regex: covers spaces, tabs,
  newlines, unicode whitespace), group parts into ≤1200-char chunks that are
  verbatim slices `text[cs:ce]` (interior separators kept exactly as written;
  between-chunk separators stay unclaimed, so the "inter-chunk whitespace stays
  unclaimed" claim in §Delivery now actually holds), hard-split lone over-cap
  parts on char boundaries, and walk char→byte cursors monotonically (no `find`,
  no normalization, no `ValueError` path for this input class).
- New evidence: `/tmp/r02b4_repro.py` — pre-fix all three variants raised
  `ValueError`; post-fix all PASS (4 spans each, `complete` views). New
  committed test `test_length_cap_whitespace_variants_split_byte_exact`
  (soft-wrapped / double-space / tab-separated params) in
  `tests/test_source_spans.py` asserts `complete` status, non-zero spans, every
  piece ≤1200 chars, byte-exact slices, dense ordered ordinals, via both
  `segment_text` and the persisted-view path. Extra /tmp edge probe: NBSP,
  CRLF wrap, mixed whitespace, 3000-char lone sentence, emoji over-cap — all
  byte-exact, ≤1200, ordered.
- Full suite: `.venv/bin/python -m pytest tests/ -q` → 112 passed
  (109 pre-existing + 3 new params), zero failures; byte fidelity 1.0 on the
  existing matrix (all round-trip/excerpt tests green). Only warning is the
  pre-existing Starlette deprecation.
- Remaining risk: separators *between* chunks are unclaimed by design (spans do
  not tile the payload contiguously) — byte-exact but not covering; a future
  consumer assuming full coverage would need a tiling pass. Hard-split of a
  lone over-cap sentence can cut mid-word (chars, never bytes). F2/F3 from
  review.md untouched (non-blocking, out of B4 scope).
