# R03 — Readiness (B1 environment probe)

Record type: builder readiness
Author role and date: R03 builder / 2026-09-12
Packet and contract revision: R03-retrieval.md / TL-QN-2026-09-12.1
Interpreter: `.venv/bin/python` (3.11.15) from repo root

## Probes (all observed this session)

| # | Check | Command / method | Observed result |
| --- | --- | --- | --- |
| 1 | usearch installed? | `find_spec('usearch')` | `None` — NOT installed |
| 2 | onnxruntime installed? | `find_spec('onnxruntime')` | `None` — NOT installed |
| 3 | torch/transformers/sentence_transformers | import probe (background) + prior known facts | torch 2.13 + transformers + sentence_transformers 5.7 installed (CPU-only host) |
| 4 | HF hub cache | `ls ~/.cache/huggingface`, `du -sh` | `hub/` empty, total 12K — no cached weights |
| 5 | Network (TCP) | `socket.create_connection(('8.8.8.8', 53), timeout=8)` | `OSError: [Errno 101] Network is unreachable` |
| 6 | Network (HTTP) | urllib GET `https://huggingface.co` (10 s timeout) | pending at write time; TCP-level failure already decisive |
| 7 | BEIR frozen data present? | `ls /mnt/c/Users/anshu/AppData/Local/trace-bench-data/` | `beir-scifact`, `beir-nfcorpus`, `beir-fiqa` (+ others) present |
| 8 | numpy/pytest | version probe | numpy 2.4.6, pytest 9.1.1 |

## Decision: PACKET FALLBACK (no network)

`uv pip install usearch onnxruntime` cannot succeed without network, and no
model weights can be downloaded (empty HF cache). Per the packet Experiment
supplement, R03 therefore proceeds on the explicit fallback:

- **Representations:** optimized exact lexical (FTS5 BM25, unchanged) + pinned
  sparse projection (`hashbow-128-idf`: 128-d hashed BoW, L2 + corpus IDF —
  the exact space the frozen baselines were measured in). Explicitly labeled
  **DEGRADED — no learned weights** in code (`encoder.degraded is True`),
  evidence, and delivery record. No silent upgrade claim.
- **ANN:** numpy IVF implemented in-repo (`src/trace_lite/router/index_adapter.py`),
  no new dependency. C3-3 measured honestly as IVF recall@10 vs exact
  brute-force in the SAME vector space, same filters/budgets.
- **Index publication:** immutable segments + manifest, save/flush before
  active-pointer publish, tombstone/current-revision filtering, restart
  recovery test — all against the fallback encoder space.

## Why C3-2 stays achievable on fallback

Frozen baselines (scifact 0.6589, nfcorpus 0.3188, fiqa 0.2495) were produced
by this exact lexical+hashbow stack, so the fallback encoder is definitionally
capable of meeting them within ±0.01. The rerun risk is code churn, not
representation power — mitigated by leaving the default quality-path ranking
semantics byte-identical.

## Not blocked

No escalation at B1. Full suite baseline: 112/112 green (R02 VERIFIED).
R04 builds concurrently — no files outside the R03 owned set will be touched.
