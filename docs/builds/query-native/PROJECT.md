# Trace-Lite Query-Native Memory Program — Current contract

Revision: TL-QN-2026-09-12.1
Status: Settled for planning and authorized execution under Autonomous Overnight Coordinator
Scope: Trace-Lite (`trace-lite`) only. Project TRACE (`utilities/trace`) is a read-only architectural reference.

## Outcome and scope

- **Intended user outcome**: Ingest a source once, preserve exact byte-level evidence, extract a local relational assertion graph (stigmergy), selectively reconcile it with a sparse global graph, maintain overlapping cross-source subgraph holons, and answer queries with traceable evidence dossiers. Static files remain provenance envelopes; query-assembled dossiers become the primary access model.
- **First useful milestone**: Ingest two sources describing a mechanism with conflicting conditions. Ingestion produces source-linked local assertions, cross-source grouping without text fusion, and an evidence dossier retaining the conflict. Editing a condition makes affected interpretations stale, updates only the relevant neighborhood, and changes the next current-state dossier. A restart preserves the result.
- **Users and representative workflow**: Downstream cognitive agents (Cordis, DSH, Antigravity) and human queriers submit natural language objectives or queries. The dual-dispatch router unions global lexical/vector search with holon navigation to assemble bounded evidence dossiers with byte-exact citations and conflict surfaces.
- **Inputs and outputs**:
  - *Inputs*: Raw markdown notes, text sources, conversation episodes, code diffs, and query objectives.
  - *Outputs*: Structured evidence dossiers containing exact source spans, assertion frames, relationships, qualifications, omissions, and verification receipts.
- **Important exceptions/failure cases**:
  - *Misfiled/unfiled evidence*: Must remain discoverable via independent global lexical and vector indexes.
  - *Conflicting claims*: Must be explicitly preserved with source provenance, never collapsed into an artificial consensus.
  - *Model/runtime outage*: Ingestion falls back to deterministic structural extraction and lexical indexing; semantic state is marked degraded, never silently fabricated.
  - *Crash during replacement*: Transaction boundaries ensure a source replacement either fully commits or rolls back to the prior coherent revision.
- **Non-goals and deferred ambitions**:
  - Multi-node distributed clustering (single-node embedded ANN and SQLite first; 1M scale is handled in-process).
  - Autonomous action execution or external tool calls (TL is strictly a memory and evidence retrieval substrate).
  - Blanket prohibition on ML/embeddings (quantized local ONNX and learned embeddings are evaluated empirically against baselines).
  - Destructive cross-document text merging at ingestion time (holons link and group; source text remains intact).

## Requirements and targets

| ID | Requirement or proposed target | Status/authority | Source | How checked |
|---|---|---|---|---|
| QN-01 | Query-native evidence access with retained source identity | Accepted requirement | User request 2026-09-12 | R01/R02: Byte-exact round-trip and dossier span resolution |
| QN-02 | Local relational graph before selective global integration | Accepted requirement | User request 2026-09-12 | R04: Assertion extraction preserving polarity, conditions, and roles |
| QN-03 | Native overlapping cross-source holons | Accepted requirement | User request 2026-09-12 | R05: Multi-document grouping without text fusion; cycle rejection |
| QN-04 | Adaptive segmentation & predictive repair evaluated explicitly | Accepted requirement | User request 2026-09-12 | R08: Controlled ablation matrix; non-inferiority qualification |
| QN-05 | Atomic evidence updates & observable projection freshness | Accepted requirement | Architecture contract | R01/R06: Crash injection, idempotent replay, and generation tracking |
| QN-06 | Independent global lexical/vector retrieval | Accepted requirement | Architecture contract | R03: Unfiled/late-rowid counterexample retrieval |
| QN-07 | Bounded incremental local work & parallel workers | Accepted requirement | Architecture contract | R06: O(1) running accumulators; sub-linear maintenance cost |
| QN-08 | Single-node first, replaceable embedded ANN & model adapters | Accepted requirement | Architecture contract | R03/R09: Evaluated in-process with frozen resource budgets |
| QN-09 | No unmarked generated facts or automatic authority promotion | Accepted requirement | Evidence contract | R04/R07: Provenance lineage, status tags, and invariant screening |
| QN-10 | Matched candidate/configuration for quality and performance | Accepted requirement | Qualification contract | R00/R09: Paired reports on identical configuration without profile swaps |

*Note*: Sub-50ms latency, 500MB RSS, and 1,200 docs/s are historical comparison targets, not dogmatic architectural ceilings. Workload-specific envelopes are frozen in R00.

## Decisions and unresolved questions

| ID | Decision/question | Accepted/proposed/open/superseded | Rationale and source | Impact/dependents |
|---|---|---|---|---|
| D-01 | Replace static document filing with query dossiers | Accepted | User directive: "documents are dead, replaced by queries" | Shapes R02, R05, R07 |
| D-02 | Local relational graph (stigmergy) before global merge | Accepted | User directive: preserve intra-source relations and extract max juice | Shapes R04, R05 |
| D-03 | Overlapping cross-source holons; rip out same-doc constraint | Accepted | Holons represent composite arguments across sources; text remains separate | Refactors `holon.py` in R05 |
| D-04 | Prediction-error atomization evaluated as controlled experiment | Accepted | Theoretical promise must be proven empirically against structural baseline | Bounded to R08; fallback baseline in R02 |
| D-05 | Remove Obsidian as architecture-dictating dependency | Accepted | Obsidian wire protocol preserved on port 8420, but core is decoupled | Decouples router & store in R01/R06 |
| D-06 | Decouple read/write paths; eliminate daemon global lock | Accepted | WAL mode + independent reader connections + immutable index generations | R01, R03, R06 |
| D-07 | Single-node embedded ANN (e.g. usearch/sqlite-vec) for 1M scale | Accepted | Avoids distributed network overhead; scales 1M in-process | R03, R09 |
| Q-01 | Model choice for relation extraction (heuristic vs local SLM) | Open / To evaluate | Balance CPU latency/RAM footprint against extraction recall | Investigated in R04 |
| Q-02 | Dynamic generation compaction frequency | Open / To tune | Balance storage headroom against query read stability | Tuned in R06 |

## Authorization and resources

- **Work already authorized**: Full blanket authority to execute packets R00 through R09 under the Autonomous Overnight Coordinator, including refactoring store, router, filing, cordis, and benchmarks modules, creating database migrations, running tests, and compiling evidence dossiers.
- **Decisions/actions reserved by the user**: Production git push requiring external credentials; any modifications to the read-only TRACE repository (`utilities/trace`).
- **Available environment, tools, and runtime**:
  - Linux / WSL2 environment on host.
  - Python venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python` (and `pytest`).
  - SQLite 3 with FTS5 support.
  - Persistent ext4 benchmark storage paths where required.
- **Material limitations needing confirmation**:
  - Push access to remote git requires manual authentication.
  - Memory budget ceiling: 500MB RSS target (soft guideline, frozen in R00).

## Evidence and assumptions

| Claim | Observed/source-inspected/historical/assumed | Evidence and date | Required verification |
|---|---|---|---|
| TL test suite 39/39 passing on legacy foundation | Observed | `pytest -v` run in 13.3s (2026-09-12) | R00 baseline confirmation |
| 1M benchmark passed with 6.1ms P95 via latency sampling | Observed / Inspected | `hybrid.py:16`, `bench-1m.json` | R00/R09 full-corpus evaluation |
| Monolithic atom ingestion dilutes centroids & limits holons | Observed / Inspected | `app.py:68-74`, `holon.py:60` | R02/R05 multi-span & cross-doc tests |
| Global lock serializes API queries during vault sync | Observed / Inspected | `app.py:132-150` | R06 concurrent write/read test |
| Embedded ANN can deliver sub-5ms search on 1M vectors in-process | Assumed / Proposed | Industry benchmarks (`usearch`/HNSW) | R03/R09 empirical benchmark |
| Prediction error segmentation improves over structural splitting | Hypothesis | Research blueprints & literature | R08 controlled ablation |

## Source map

| Source ID | Path/attachment/message | Relevant sections | Availability and qualifications |
|---|---|---|---|
| SRC-01 | `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace/docs/prompt-kit` | `FRAMEWORK.md`, `RUNBOOK.md`, `prompts/`, `templates/` | Primary process & contract governance |
| SRC-02 | `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/docs/builds/query-native` | `RESEARCH.md`, `ARCHITECTURE.md`, `EXECUTION.md`, `EVALUATION.md` | Core query-native architectural blueprints |
| SRC-03 | `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace` | `src/trace/v2/`, `RFC-001-COGNITIVE-CONSOLIDATION.md` | Read-only reference for atomization & scale lessons |
| SRC-04 | `/mnt/c/Users/anshu/OneDrive/Desktop/trace lite and trillion dreams` | `03_trace_lite_filing_cabinet_core_architecture.md` | Historical architecture notes & filing concepts |

## Change record

| Revision | Change and reason | Authority/evidence | Affected packets |
|---|---|---|---|
| TL-QN-2026-09-12.1 | Replaced legacy filing-cabinet plan with Query-Native Memory Program; structured strictly according to Prompt Kit templates | User directive 2026-09-12 | All packets R00–R09 |
