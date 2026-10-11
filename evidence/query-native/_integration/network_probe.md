# Bounded network/backend probe (steering §4) — 2026-09-12, WSL2

Method: `.venv/bin/python -c` urllib GET with 5s timeout each (no downloads, no installs).
Observed:
- https://pypi.org/simple/ → HTTP 200
- https://huggingface.co/ → HTTP 200

Installed (verified by import): torch 2.13, transformers 5.14, sentence_transformers 5.7.
NOT installed: onnxruntime, usearch. HF hub cache: empty (12K).

Conclusion: the "no network" premise behind R03's fallback-only posture was an
unprobed assumption and is FALSE on this host. Learned retrieval is NOT proven
unavailable; it is UNDELIVERED (no weights fetched, no encoder wired into the
served path, no ANN in production query). R03's fallback remains a valid named
baseline; its review verdict stands for what was actually built and labeled.

Next (queued after INT-1, disjoint scope): bounded learned-retrieval spike —
pin + fetch a small model artifact (e.g. BGE-small-en-v1.5), install usearch or
onnxruntime into the project env, wire encoder + ANN into the served path
behind INT-2, and measure task quality through the served path vs the fallback.
If the spike fails, record exact cause (blocked) — do not relabel fallback.
