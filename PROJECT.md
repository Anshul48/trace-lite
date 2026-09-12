# Project: Trace-Lite — Smart Filing Cabinet & Passive Memory Substrate

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](docs/builds/query-native/PROJECT.md), [execution plan](docs/builds/query-native/EXECUTION.md), and [state](docs/builds/query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Revision: 2026-09-11.2
Status: Authorized for Clean Foundation Reset, Architecture, and Implementation
Working Directory: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite`
Governing Kit: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace/docs/prompt-kit`

---

## Architectural Mandate & Freedom Directive

> **CRITICAL USER DIRECTIVE**: The builder and coordinator for Trace-Lite are granted **COMPLETE FREEDOM** to decide the directory structure, package layout, and technology stack, and are explicitly instructed to **completely ignore, archive, or delete the current legacy prototype structure**.
> The repository provides `bury_and_reset.sh` to cleanly archive legacy prototype commits into `archive/v1-legacy-scaffold` (preserving `obsidian-plugin/` as companion client) and initialize a pristine production foundation.

---

## Outcome and scope

- **Intended user outcome**: Deliver `trace-lite`, a deterministic, single-node, hardware-efficient smart filing cabinet and decoupled passive external memory substrate. Trace-Lite strictly separates memory persistence from reasoning, implements Hearst multi-parent faceted classification, provides a sub-50ms 3-tier dual-dispatch retrieval router, exports a production plugin suite for DeepSeek Harness (Cordis / `dsh`), and synchronizes seamlessly with Obsidian vaults.
- **First useful milestone**: Execute legacy burial protocol (`bury_and_reset.sh`), initialize clean `pyproject.toml` (Python 3.11+, Pydantic 2, SQLite, Typer), and implement core single-node append-only SQLite storage (`atom`, `events`, `fts_atoms`) with Active WAL Governor.
- **Second milestone**: Implement Hearst multi-parent faceted classification engine (forest of independent taxonomy trees, multi-membership indexing, document-local holon grouping, zero single-parent hierarchy bottleneck).
- **Third milestone**: Implement sub-50ms 3-tier dual-dispatch router (Tier 1 Lexical <=5ms, Tier 2 Faceted Beam <=35ms, Tier 3 Global Flat Hybrid <=25ms, P95 <=50ms, calibrated abstention).
- **Fourth milestone**: Implement DeepSeek Harness (`dsh` / Cordis) executive plugin suite (`TraceLiteMemoryPlugin`, `TraceLiteLedgerService`, `TraceLiteContextCompiler`, Modular Contract-Boundary TMS with 4-tier authority lattice).
- **Fifth milestone**: Implement Obsidian vault sync engine (bidirectional markdown watcher with 500ms debounce, frontmatter & tag extraction, wikilink graph mapping, local REST API on `:8420`).
- **Final milestone**: End-to-end integration and verification suite (`pytest tests/`) validating multi-parent filing, sub-50ms queries, Cordis step gate interception, and Obsidian note retrieval.

---

## Requirements and targets

| ID | Requirement or proposed target | Status/authority | Source | How checked |
|---|---|---|---|---|
| REQ-01 | Exercise Complete Freedom: Bury/purge legacy scaffold and initialize clean modern foundation | ACCEPTED / User | User directive, `bury_and_reset.sh` | Clean repository status on `main`; fresh `pyproject.toml`, clean `src/trace_lite/` |
| REQ-02 | Single-Node SQLite Storage Engine | ACCEPTED / User | Architecture §2.1 | Append-only `events`, byte-exact `atom`, external-content `fts_atoms`, WAL mode |
| REQ-03 | Hearst Multi-Parent Faceted Classification | ACCEPTED / User | Architecture §2.2 | Items belong to orthogonal dimensions simultaneously (Topics, Entities, Types, Projects, Sources) |
| REQ-04 | Sub-50ms 3-Tier Dual-Dispatch Router | ACCEPTED / User | Architecture §2.3 | Tier 1 (<=5ms), Tier 2 (<=35ms), Tier 3 (<=25ms); P95 <= 50ms; calibrated abstention |
| REQ-05 | DeepSeek Harness (Cordis / DSH) Executive Plugin Suite | ACCEPTED / User | `research_specifications/schemas/interfaces.py` | Implements formal protocols: `ITraceLiteMemoryPlugin`, `IContextCompilerMiddleware`, `IStepAcceptanceGate`, `IMCBTMSEngine` |
| REQ-06 | 4-Tier Authority Lattice Enforcement in TMS | ACCEPTED / User | `interfaces.py:AuthorityLevel` | `USER (4) > ARCH_SPEC (3) > AGENT_DECISION (2) > TOOL_OUTPUT (1)` strictly enforced |
| REQ-07 | Obsidian Vault Synchronization & REST API (:8420) | ACCEPTED / User | `obsidian-plugin/main.ts` | 500ms debounced watcher; frontmatter/wikilink parser; `/api/query` and `/api/sync` on port 8420 |
| TGT-01 | Router P95 Latency <= 50ms | TARGET | Performance Budget | Benchmark over 1,000 queries |
| TGT-02 | Memory RSS <= 500MB | TARGET | Architecture §2.1 | Process resident set monitoring |
| TGT-03 | Sustained Ingestion >= 1,200 docs/sec | TARGET | Storage Engine | Ingestion benchmark over 10,000 documents |

---

## Source map

- Desktop Research Materials: `/mnt/c/Users/anshu/OneDrive/Desktop/trace lite and trillion dreams/`
  - `03_trace_lite_filing_cabinet_core_architecture.md`: Architectural blueprints for smart filing cabinet.
  - `08_executive_constraint_enforcement_and_dynamic_context_compiler.md`: Micro-step context compiler specs.
  - `research_specifications/schemas/interfaces.py`: Formal Pydantic 2 contracts and protocols.
- Companion Client: `obsidian-plugin/` (TypeScript client communicating via port 8420).
