# Architecture & Technical Design: Trace-Lite Smart Filing Cabinet

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../query-native/PROJECT.md), [execution plan](../query-native/EXECUTION.md), and [state](../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Revision: 2026-09-11.2
Scope: Clean Slate Production Architecture

---

## 1. Clean Slate Directory Structure

Exercising the user's explicit freedom directive, Trace-Lite establishes a pristine, modern Python architecture:

```text
trace-lite/
├── pyproject.toml              # Modern Hatchling build, Python 3.11+, Pydantic 2, Typer, Rich
├── README.md                   # System philosophy and quickstart
├── bury_and_reset.sh           # Legacy preservation and reset script
├── obsidian-plugin/            # Preserved companion Obsidian TypeScript client
├── docs/
│   └── builds/
│       └── filing-cabinet/     # Canonical Prompt Kit project records and packets
└── src/
    └── trace_lite/
        ├── __init__.py
        ├── cli.py              # Typer CLI entrypoint (`tl status`, `tl serve`, `tl ingest`)
        ├── py.typed
        ├── store/              # Single-node SQLite storage engine
        │   ├── __init__.py
        │   ├── database.py     # Connection manager (WAL mode, synchronous=NORMAL)
        │   ├── schema.sql      # Canonical atom, events, facets, memberships, fts_atoms
        │   └── governor.py     # Active WAL Checkpoint Governor (5,000 docs)
        ├── filing/             # Hearst multi-parent classification
        │   ├── __init__.py
        │   ├── engine.py       # Forest of taxonomy trees & multi-membership indexing
        │   ├── taxonomy.py     # Topics, Entities, Types, Projects, Sources
        │   └── holon.py        # Document-local holon grouping (A-B-A chunks)
        ├── router/             # Sub-50ms 3-tier dual-dispatch router
        │   ├── __init__.py
        │   ├── cascade.py      # Orchestrator with calibrated abstention
        │   ├── lexical.py      # Tier 1 lexical short-circuit (<= 5ms) via FTS5
        │   ├── beam.py         # Tier 2 faceted beam search (<= 35ms) via warmed centroids
        │   ├── hybrid.py       # Tier 3 flat hybrid fallback (<= 25ms)
        │   └── fusion.py       # Reciprocal Rank Fusion (RRF)
        ├── cordis/             # DeepSeek Harness (dsh) executive plugin suite
        │   ├── __init__.py
        │   ├── plugin.py       # Implements ITraceLiteMemoryPlugin & ILedgerService
        │   ├── compiler.py     # Implements IContextCompilerMiddleware (<= 3500 tokens)
        │   ├── tms.py          # Implements IMCBTMSEngine (4-tier authority lattice)
        │   └── gate.py         # Implements IStepAcceptanceGate (pre-commit gate & RPE)
        ├── obsidian/           # Obsidian vault synchronization
        │   ├── __init__.py
        │   ├── parser.py       # Frontmatter, #tags, [[wikilinks]] parser
        │   └── watcher.py      # Debounced (500ms) filesystem watcher
        └── api/                # Loopback REST server on :8420
            ├── __init__.py
            └── app.py          # FastAPI / Starlette daemon consumed by obsidian-plugin
```

---

## 2. Core Subsystems & Technical Specifications

### 2.1 Storage Engine (`src/trace_lite/store/`)
- **Single SQLite Database**: Default path `~/.trace-lite/storage.db` or configured via CLI.
- **Canonical Schema**:
  - `atom`: `(id INTEGER PRIMARY KEY, doc_id TEXT, content_hash BLOB, start_byte INT, end_byte INT, text TEXT NOT NULL)`
  - `events`: `(event_id TEXT PRIMARY KEY, stream_id TEXT, stream_seq INT, event_type TEXT, occurred_at TEXT, payload_json TEXT)`
  - `facets`: `(facet_id TEXT PRIMARY KEY, dimension TEXT, name TEXT, parent_facet_id TEXT, path TEXT, centroid_blob BLOB)`
  - `memberships`: `(atom_id INT, facet_id TEXT, confidence REAL, PRIMARY KEY(atom_id, facet_id))`
  - `fts_atoms`: Virtual FTS5 table with external content pointing to `atom.id`.
- **Active WAL Governor**: Executes `PRAGMA wal_checkpoint(PASSIVE)` every 5,000 committed documents strictly outside write transactions.

### 2.2 Hearst Multi-Parent Classification (`src/trace_lite/filing/`)
- Replaces rigid single-parent folder trees with a DAG forest across 5 default dimensions:
  1. `Topics`: Subject domain (e.g. `Topics:Systems/Database/WAL`).
  2. `Entities`: Named entities, code modules, APIs.
  3. `Types`: Document kinds (`Note`, `Specification`, `Log`, `Paper`).
  4. `Projects`: Associative project codes (`Trace`, `Cordis`, `Obsidian`).
  5. `Sources`: Ingestion origin (`ObsidianVault`, `GitRepo`, `CLI`).
- Atoms and documents belong to multiple facets simultaneously with confidence scores.
- Document-local holons group contiguous paragraph chunks (A-B-A pattern) preserving narrative continuity.

### 2.3 Sub-50ms 3-Tier Dual-Dispatch Router (`src/trace_lite/router/`)
Retrieval cascades through three sequential tiers:
1. **Tier 1: Lexical Short-Circuit ($\le 5\text{ ms}$)**:
   Triggered on syntax-dense queries (quotes, UPPER_SNAKE, file paths, code symbols). Evaluated via compiled SQLite FTS5. If $\ge 3$ confident hits found, returns immediately, bypassing vector computation.
2. **Tier 2: Top-Down Faceted Beam Search ($\le 35\text{ ms}$)**:
   Navigates Hearst facet trees using in-memory warmed centroid vectors, pruning candidate pool to tight semantic neighborhoods (capped at 400 candidates via round-robin allocation). Ghost facets (zero members) are filtered prior to beam window slicing to avoid starving non-empty facets. In large facets (>400 members), balanced bimodal sampling (head+tail) prevents rowid starvation of newly ingested notes.
3. **Tier 3: Global Flat Hybrid Fallback ($\le 25\text{ ms}$)**:
   Combines BM25 and dense embeddings with Reciprocal Rank Fusion:
   $$RRF(d) = \frac{1}{60 + \text{rank}_{\text{dense}}(d)} + \frac{1}{60 + \text{rank}_{\text{sparse}}(d)}$$
- **Calibrated Abstention & Corroboration**: If maximum score $< \theta_{\text{floor}}$ (0.35), returns explicit `insufficient_evidence` signal. Vector-only hits require corroboration from query term/Porter-stem overlap specifically in the returned anchor document (preventing database-wide term leakage).

### 2.4 DeepSeek Harness (`dsh` / Cordis) Plugin Suite (`src/trace_lite/cordis/`)
Directly implements formal protocols from `research_specifications/schemas/interfaces.py`:
- `TraceLiteMemoryPlugin` (`ITraceLiteMemoryPlugin`): Async persistence and faceted query retrieval.
- `TraceLiteContextCompiler` (`IContextCompilerMiddleware`): Compiles micro-step prompt context within strict token budgets ($\le 3,500$ tokens), prioritizing higher authority invariants.
- `ModularContractBoundaryTMS` (`IMCBTMSEngine`): Enforces the 4-tier authority lattice (`USER > ARCH_SPEC > AGENT_DECISION > TOOL_OUTPUT`).
- `StepAcceptanceGate` (`IStepAcceptanceGate`): Pre-commit verification with Reward Prediction Error (RPE) feedback ($\Delta = +1.0$ on accept, $\Delta = -1.0$ on reject).

### 2.5 Obsidian Vault Sync & REST API (`src/trace_lite/obsidian/`, `src/trace_lite/api/`)
- Watcher monitors Obsidian vault with a 500ms debounce buffer to prevent write conflicts during typing.
- Parses YAML frontmatter, `#tags`, and `[[wikilinks]]`.
- Maps tags and folders into Hearst multi-parent facets.
- Exposes loopback REST daemon on port 8420 (`POST /api/query`, `POST /api/sync`, `GET /api/status`, `GET /api/health`, `POST /api/ingest`) directly consumed by companion `obsidian-plugin/`.
- Ingestion endpoint (`POST /api/ingest`) uses sub-millisecond incremental dense vector indexing and debounced background re-warming (0.5s timer), preventing $O(N^2)$ lock contention during sequential syncs.
