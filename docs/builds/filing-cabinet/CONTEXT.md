# Project Trace-Lite: Context & Architectural Foundations

Revision: 2026-09-11.2
Subject: Smart Filing Cabinet & Decoupled Memory Substrate

---

## 1. Executive Summary

`trace-lite` is conceived as a lightweight, single-node, hardware-efficient smart filing cabinet that serves as the external memory substrate for local agentic workflows (specifically DeepSeek Harness / Cordis runtime) and personal knowledge bases (Obsidian vaults).

Unlike monolithic LLM agent memory architectures that couple reasoning directly to vector storage or force documents into rigid single-parent directory trees, Trace-Lite implements:
1. **Strict Memory-Reasoning Separation**: External memory is a deterministic passive substrate; reasoning belongs to the agent.
2. **Hearst Multi-Parent Faceted Classification**: Documents belong simultaneously to multiple orthogonal taxonomic facets without artificial tree hierarchies.
3. **Sub-50ms 3-Tier Dual-Dispatch Router**: Query routing dynamically cascades from lexical short-circuit to faceted beam search to global hybrid fallback.
4. **Formal Executive Plugin Suite**: Full conformance to DeepSeek Harness (`dsh`) formal schemas, including token-budgeted prompt compilation and 4-tier authority lattice enforcement.

---

## 2. Freedom Directive & Legacy Burial Protocol

The current repository contains an early exploratory prototype with LanceDB experiments, obsolete scripts, and orphaned RAPTOR modules. Under explicit user instructions:
- The builder is commanded to **exercise complete freedom** in redesigning the directory structure.
- All obsolete prototype code is cleanly archived to branch `archive/v1-legacy-scaffold` using `bury_and_reset.sh`.
- The companion Obsidian client in `obsidian-plugin/` is explicitly preserved.
- A clean, modern Python 3.11+ foundation with Pydantic 2, Typer, and SQLite is established.

---

## 3. The 4-Tier Authority Lattice

Formally defined in `research_specifications/schemas/interfaces.py`:
$$\text{USER (Rank 4)} > \text{ARCH\_SPEC (Rank 3)} > \text{AGENT\_DECISION (Rank 2)} > \text{TOOL\_OUTPUT (Rank 1)}$$
A lower-ranked authority can never revoke, modify, or relax an invariant established by a higher-ranked authority. The Modular Contract-Boundary TMS (`IMCBTMSEngine`) actively intercepts and vetoes any unauthorized modification proposals.
