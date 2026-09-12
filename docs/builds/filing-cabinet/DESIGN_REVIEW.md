# Design Review & Adversarial Critique: Trace-Lite Architecture

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../query-native/PROJECT.md), [execution plan](../query-native/EXECUTION.md), and [state](../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Reviewer: Independent Architectural Auditor
Date: 2026-09-11
Verdict: **READY_FOR_PACKAGING**
Scope Reviewed: Clean slate migration plan, SQLite storage invariants, Hearst faceted classification DAG, 3-tier router latency bounds, Cordis plugin interface compliance, Obsidian sync loopback API.

---

## Findings Matrix

| Finding ID | Severity | Affected Section | Evidence | Consequence | Minimal Correction | Execution Blocker? |
|---|---|---|---|---|---|---|
| CRIT-01 | CRITICAL | Accidental Deletion of Companion Obsidian Plugin | `bury_and_reset.sh:95` | `bury_and_reset.sh` originally listed `obsidian-plugin` in `OBSOLETE_ITEMS`. Running the script would delete the actual TypeScript plugin that integrates with Trace-Lite. | Remove `obsidian-plugin` from `OBSOLETE_ITEMS`. Preserve `obsidian-plugin/` as the companion client in `trace-lite`. | YES (resolved in `bury_and_reset.sh`) |
| CRIT-02 | HIGH | Uncommitted Planning Files Contaminating Archive Branch | `trace-lite/` root | Placing untracked project files in the root of `trace-lite` causes `git add -A` in `bury_and_reset.sh` to commit new planning files into the legacy archive branch `archive/v1-legacy-scaffold`. | Direct all planning records to `docs/builds/filing-cabinet/`. Keep root clean except for a pointer and launch prompt. | YES (incorporated into project layout) |
| CRIT-03 | HIGH | Pydantic 2 Schema Drift in Cordis Plugin | `src/trace_lite/cordis/` | Mismatched interface types between Cordis runtime and Trace-Lite plugin will cause validation errors during micro-step prompt compilation. | Import and validate directly against formal schemas in `research_specifications/schemas/interfaces.py`. Run explicit contract compliance tests. | YES (incorporated into P05) |
| CRIT-04 | HIGH | SQLite Locking in WAL Checkpoint Governor | `src/trace_lite/store/governor.py` | Invoking `PRAGMA wal_checkpoint(PASSIVE)` inside open write transaction locks SQLite database. | Call checkpoint governor strictly outside transaction boundaries (post-commit). | YES (incorporated into P02) |
| MED-01 | MEDIUM | Cold Cache Latency Spike in Tier 2 Beam | `src/trace_lite/router/cascade.py` | First-query evaluation of facet centroids may exceed 50ms if centroids must be loaded from SQLite on demand. | Warm facet centroids in memory during database open/initialization. Keep centroid vector cache hot in RAM (<10MB footprint). | YES (incorporated into P04) |
| MED-02 | MEDIUM | Obsidian File Watcher Event Storms | `src/trace_lite/obsidian/watcher.py` | Rapid typing in Obsidian generates dozens of file modification events per second, causing thrashing in SQLite writes. | Implement a 500ms debounce buffer on file modification events before triggering document re-atomization and facet re-indexing. | YES (incorporated into P06) |
| LOW-01 | LOW | REST API Port Conflict on 8420 | `src/trace_lite/api/` | Port 8420 might be occupied by another local service. | Allow configuring port via CLI (`tl serve --port <PORT>`) and environment variable `TRACE_LITE_PORT`, defaulting to 8420. | NO (standard configurable port) |

---

## Architectural Dispositions
1. **CRIT-01 Disposition**: Accepted. `bury_and_reset.sh` updated to preserve `obsidian-plugin/`.
2. **CRIT-02 Disposition**: Accepted. Canonical planning files reside exclusively in `docs/builds/filing-cabinet/`.
3. **CRIT-03 Disposition**: Accepted. `src/trace_lite/cordis/` strictly adheres to `interfaces.py` protocols and Pydantic 2 schemas.
4. **CRIT-04 Disposition**: Accepted. Checkpoint governor runs post-commit outside active connection transaction blocks.
5. **MED-01 Disposition**: Accepted. Centroid cache warming added to database startup routine, guaranteeing steady-state P95 latency $< 50\text{ ms}$.
6. **MED-02 Disposition**: Accepted. 500ms debounce queue added to Obsidian sync watcher.

Conclusion: Certified READY FOR PACKAGING.
