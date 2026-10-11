# R02 — Independent review (B3)

Record type: independent verification
Reviewer role and date (UTC): R02 independent verifier (B3) / 2026-09-12
Packet and contract revision: R02-spans.md / TL-QN-2026-09-12.1
Candidate identity: git HEAD `0df044b51fb3b40ca3360ea7abd78ac36924b9f8`
  + R01 owned set (VERIFIED, per `evidence/query-native/R01/review.md`)
  + R02 owned set (as found in working tree)
Environment: WSL2, `.venv/bin/python` (3.11.15), repo v9fs; scratch DBs under /tmp (ext4)

## 1. Scope control (ownership check)

`git status --short` (tracked modifications): `COORDINATOR_LAUNCH_PROMPT.md`,
`docs/builds/query-native/{COORDINATOR,STATE}.md` (coordinator-owned prompt/state
tracking, incl. the R02 `IN_REVIEW` row — not builder edits),
`src/trace_lite/obsidian/parser.py`, `src/trace_lite/store/{__init__.py,schema.sql}`.
Untracked: `src/trace_lite/source/`, `src/trace_lite/store/{generations,migrate,outbox}.py`,
`tests/{test_migration,test_store_lifecycle,test_source_spans,test_parser_fidelity}.py`,
`evidence/query-native/`. No other tracked file is modified:

- `git diff HEAD --stat -- tests/ benchmarks/ evidence/query-native/R00
  evidence/query-native/R01` is empty; `benchmarks/` untouched.
- `git diff HEAD -- tests/test_store_lifecycle.py tests/test_migration.py` is empty
  (0 bytes; both are untracked R01-new files, older tests byte-unmodified).
- All `evidence/query-native/R00/*` and `R01/*` mtimes predate the R02 build window
  (R02 files 01:39–01:44; R01 newest 01:36, R00 newest 01:18) — R02 touched only
  `evidence/query-native/R02/`.
- R01/R02 attribution inside shared new files is structural, not mechanical (both
  uncommitted): `source/service.py` (mtime 01:23, R01 era) has zero references to
  `segmentation`/`views`; `source/__init__.py` = R01 exports + two additive imports;
  `migrate.py` = R01 `migrate_legacy` + additive `migrate_spans_002`; no module outside
  `source/__init__.py` imports `segmentation`/`views`. No finding.

## 2. Evidence table

| Criterion | Method (independent) | Observed result | Verdict |
|---|---|---|---|
| C2-1 Exact byte round-trip | `tests/test_source_spans.py` + `tests/test_parser_fidelity.py` re-run by verifier (36 passed, exit 0); own /tmp probe: emoji+CRLF+frontmatter doc, repeats, nested/unclosed fences, tamper case — every span sliced against raw bytes | 100% byte fidelity on tested matrix; tamper raises `ValueError: hash verification`; frontmatter/CRLF mapping verified against RAW bytes (`raw.encode()[off:] == body.encode()`), not normalized text | PASS |
| C2-2 Sub-document addressability | Re-ran `test_a1_b_a2_*`, `test_length_cap_*`; validated `subdoc_test.json` offsets self-consistent (6 spans, A1 ord 1 / A2 ord 5, max 58 chars ≤ 1200); own probe: 3000-char single-line para → 3 spans, max 1175, order preserved, byte-exact; 4 repeats → 4 distinct span IDs | Passes for single-space single-line input; **FAILS for soft-wrapped / double-space / tab over-cap text** (F1): `ValueError` → whole-revision `partial` view with **zero** spans | FAIL (F1) |
| C2-3 Structural integrity | Re-ran fence/table/unclosed tests; own probe: nested ` ```` ` fence, heading-like/table-like/bullet-like lines inside unclosed fence, 4928-char over-cap fence (stays atomic, byte-exact) | 0 syntax breaks; atomicity holds incl. over-cap fences | PASS |
| C2-4 Discourse envelope | Re-ran envelope/header tests; validated `discourse_context.json` (bullet ord 3 → heading ord 0 "Definitions", prev 2 / next 4); own probe: re-segmentation keeps old excerpts resolving | 1-hop parent + ordinal neighbors correct; prior views immutable, new span IDs disjoint | PASS |
| Migration-002 (additive) | Own scratch copy (`/tmp/r02_mig002.db`): bulk_ingest → `migrate_legacy` (fidelity 1.0) → `migrate_spans_002` → rerun; plus committed `test_migration_002_*` green | Tables added, version 1→2, rerun-safe, `.bak-002` written, R01 revision rows byte-identical, post-002 views work | PASS |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 109 passed (73 pre-existing + 36 new) in ~17 s; one pre-existing Starlette deprecation warning only | PASS |
| Non-goals | grep for ML/perplexity deps; table inventory of `schema.sql` + migrated DB | No ML/perplexity boundary code (one non-goal comment only); tables are R01 set + `evidence_span`/`segmentation_view` only (extra names are FTS5 shadow tables) — no extraction/graph/embedding tables | PASS |

## 3. Findings

- **F1 (verdict-driving, C2-2): over-cap splitter crashes on any non-single-space
  whitespace, zeroing the whole revision's view.** `_split_long_text`
  (`src/trace_lite/source/segmentation.py:92-151`) splits on `(?<=[.!?])\s+|\n+`,
  rejoins chunks with a single `" "`, then re-locates each chunk with
  `text.find(chunk, cursor_text)` — which fails whenever the original separator was
  not exactly one space, raising `ValueError("segmentation chunk lost its source
  position")`. Reproduced end-to-end (exit 0, retained source, `partial`/0 spans):
  3910-char soft-wrapped paragraph (`"\n".join(...)`), 2361-char double-space
  paragraph, tab-separated sentences — all ordinary in-scope input for "paragraph
  splitting with length caps". Because `create_segmentation_view` segments the whole
  body in one call, one such block discards ALL spans of the revision, not just its
  own. The committed `test_length_cap_splits_long_paragraph` uses single-space
  single-line text only, so the suite misses it. This also contradicts the delivery
  claim "inter-chunk whitespace stays unclaimed": the code crashes instead of
  skipping separators. Repair direction (B4): track offsets through the split
  (slice verbatim chunks, advance past separators in both char and byte cursors)
  instead of find-after-rejoin; add committed regression tests for soft-wrapped,
  double-space, and tab over-cap inputs through the persisted-view path.
- **F2 (minor, docs): `readiness.md` §4 says "No version-row insert in the static
  DDL" — inaccurate.** `schema.sql` ends with `INSERT OR IGNORE INTO schema_version
  ... VALUES (1, datetime('now'))`. Harmless: value is 1 (not 2), consistent with the
  migration-001 `MAX == 1` tests, and the full suite is green. Correct the sentence,
  no code action needed. (Whether that line is R01's or R02's is not mechanically
  separable — both sets are uncommitted — and it is behaviorally neutral either way.)
- **F3 (traceability note, no verdict impact): builder evidence logs
  (`byte_fidelity.log` "repeated spans=4", `subdoc_test.json` B/A2 wording) use
  slightly different fixtures than the committed tests** (e.g. test A1B2 B-para says
  "We measured…", JSON says "Measured…"; test repeats ×3, log says 4 spans) — they
  were generated by the unretained `/tmp/gen_r02_evidence.py`. The committed tests
  independently cover the same matrix and were re-run here, so this is a retention
  gap only: keep generator scripts under `evidence/` in future packets.
- **Cleared (not findings):** an initial `/tmp` probe FAIL on "frontmatter stripped"
  was a probe-expectation bug (body table `|---|---|` contains `"---"`; body
  verbatim retains the post-frontmatter blank line) — corrected check passes, and the
  byte-mapping assertions passed throughout. `STATE.md`/`COORDINATOR*.md` diffs are
  coordinator-owned tracking, not R02 edits.

## 4. Exact execution scope

From repo root with `.venv/bin/python` (exit codes recorded):

1. `python -m pytest tests/test_source_spans.py tests/test_parser_fidelity.py -q`
   → 36 passed, exit 0.
2. `python -m pytest tests/ -q` → 109 passed, exit 0.
3. `git diff HEAD --stat -- tests/ benchmarks/ evidence/query-native/R00
   evidence/query-native/R01` → empty; `git diff HEAD --
   tests/test_store_lifecycle.py tests/test_migration.py | wc -c` → 0.
4. Own probe `/tmp/r02_probe.py` (26 checks; no repo files written): 25 pass; 1
   genuine code failure (p4b, F1); 1 initial probe-expectation bug, corrected and
   re-confirmed passing.
5. Follow-up repro (exit 0): three whitespace variants → `partial`/0-span views with
   source retained; tamper → hash error; re-segmentation → old rows immutable.
6. Own scratch migration check on `/tmp/r02_mig002.db` (exit 0): 001 fidelity 1.0,
   002 adds 2 tables, version 2, rerun-safe, `.bak-002` written, R01 rows intact.

## 5. Verdict and next action

**Verdict: FAIL** — C2-1, C2-3, C2-4, migration-002, non-goals, and the full suite
all pass, but C2-2's length-cap mechanism fails on common in-scope input (F1).

**Next action:** route to B4 repair with F1 (root cause + repro above; builder's
repo files are otherwise untouched and must stay so). Required for re-verify: (a)
offset-tracking chunk split preserving verbatim bytes, (b) committed regression
tests for soft-wrapped / double-space / tab over-cap paragraphs via
`create_segmentation_view` (asserting `complete` status, all pieces ≤ 1200 chars,
byte-exact), (c) full suite green. B3 re-verification then needs only the F1 repro
plus the standard suite/scope checks. F2/F3 are non-blocking.

## 6. Re-verification after B4 repair attempt 1 (R02 re-verifier, 2026-09-12 UTC)

Record type: B4 re-verification of F1 only (earlier PASS findings on
C2-1/C2-3/C2-4/migration stand — re-run below contradicts none of them).
Repair delivery note: `evidence/query-native/R02/delivery.md` §"B4 repair
(attempt 1)". Candidate: same base `0df044b` + R01 set + R02 set with B4
touch-ups (mtimes: `segmentation.py` 01:52:17, `test_source_spans.py`
01:52:32, `delivery.md` 01:53:09; `views.py`/`migrate.py`/
`test_parser_fidelity.py` pre-B4, untouched).

Method (all independent, own artifacts only):
1. Own probe `/tmp/r02_reverify_probe.py` (no repo files written): each of
   soft-wrapped (`\n`), double-space, tab over-cap paragraphs through BOTH
   `segment_text` and the persisted `S.publish` →
   `V.create_segmentation_view` path, asserting no exception, `complete`
   status, non-zero spans, every span ≤ 1200 chars, byte-exact slices
   against `raw.encode()`, monotonic offsets, dense ordinals. Hostile
   extras: mixed whitespace runs, NBSP (`\u00a0`) separators, CRLF wrap,
   emoji over-cap, and a 5000-char single token with NO whitespace.
2. New regression tests + full suite re-run by verifier:
   `.venv/bin/python -m pytest tests/ -q`. Tautology inspection of the new
   test (see below).
3. Scope: `git status --short` + `git diff HEAD --stat` + file mtimes.
4. Byte fidelity: `tests/test_source_spans.py` + `tests/test_parser_fidelity.py`
   re-run by verifier (included in full suite).

Observed results:
- F1 originals FIXED: soft-wrapped → 4 spans (max 1155), double-space → 4
  spans (max 1171), tab-separated → 4 spans (max 1155); all `complete`
  views, byte-exact, order preserved. Zero `ValueError`, zero `partial` /
  zero-span views.
- Hostile extras, no crash: mixed runs → 60 spans (each source line became
  its own paragraph block because the mixed separator embeds
  whitespace-only lines, which are paragraph boundaries — correct
  segmentation, byte-exact, `complete`); NBSP → 4 spans (max 1155);
  CRLF wrap → 4 spans (max 1171); emoji over-cap → 3 spans (max 1189),
  byte-exact across multi-byte chars. 5000-char no-whitespace token → 5
  spans, all ≤ 1200, byte-exact (hard-split on char boundaries mid-token,
  never mid-byte; documented behavior, no crash).
- One probe-expectation bug of my own (emoji fixture 2090 chars < 2×cap
  gate) corrected by enlarging the fixture; all code assertions passed
  throughout.
- New tests NOT tautological: `test_length_cap_whitespace_variants_split_byte_exact`
  builds fixtures independently (`joiner.join(...)` over fixed sentences),
  asserts against the independent constant `MAX_SPAN_CHARS`, verifies
  byte-exactness by slicing `raw.encode()` with stored offsets (raw is
  fixture truth, not splitter output), and asserts `complete` status plus
  non-zero `span_count` through the persisted-view path. The
  `seg.verify_spans` call is supplemented by a direct blob-slice assertion,
  so the module never grades its own homework alone.
- Suite: targeted files 39 passed; FULL suite **112 passed**
  (109 pre-existing + 3 new params), zero failures, ~17 s. Only warning is
  the pre-existing Starlette deprecation. Byte fidelity matrix
  (round-trip/excerpt/corruption tests) all green → fidelity still 1.0 on
  the tested matrix.
- Scope CLEAN: tracked modifications identical to the B3 baseline
  (`COORDINATOR_LAUNCH_PROMPT.md`, `docs/.../COORDINATOR.md`,
  `docs/.../STATE.md`, `parser.py`, `store/__init__.py`, `schema.sql` —
  coordinator-owned + R01/R02-shared files, no new tracked edits);
  `git diff HEAD --stat -- src/ tests/` shows only those three shared
  files; all `tests/` entries remain untracked (`M tests/...`: none), so no
  pre-existing test was modified. B4-window mtimes confirm the repair
  touched only `source/segmentation.py`, `tests/test_source_spans.py`, and
  `evidence/query-native/R02/delivery.md` (appended B4 section).
  (`__pycache__` regeneration only — not a finding.)

F1 disposition: FIXED and verified end-to-end. The rejoin-and-`find` path is
gone; `_split_long_text` now tokenizes via `_SENTENCE_SPLIT_RE.finditer`
char offsets, emits verbatim `text[cs:ce]` slices, leaves between-chunk
separators unclaimed, and walks char→byte cursors monotonically — matching
the required repair direction (a)–(c) in full.

**Verdict: PASS** — C2-2 length-cap mechanism now holds for single-space,
soft-wrapped, double-space, tab, mixed, NBSP, CRLF, and emoji over-cap
input; full suite green (112); scope clean; byte fidelity 1.0.

Remaining risk (non-blocking): between-chunk separators stay unclaimed by
design (spans are byte-exact but do not tile the payload contiguously) — a
future consumer assuming full coverage needs a tiling pass. Hard-split of a
lone over-cap token cuts mid-word (chars, never bytes). F2/F3 from §3
still open as non-blocking notes. Modified in this pass: this file only
(appended §6).
