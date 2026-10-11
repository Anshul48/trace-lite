# S-packet binding addendum (2026-09-12, coordinator)

Source: third-party review of the Audit-1/Audit-2 adjudication ( assessment
history preserved; this file adds binding requirements, it does not relitigate
the ruling). One correction: the mid-run directory deletion cited as
"operator" error was this program's own R09 builder subagent, not an external
operator — ownership is ours, fixed by single-attempt guardrails.

## B1 — S4 (persistent vectors): embedding throughput is a capacity gate

Background workers fix query latency, not encode cost (BGE-small CPU measured
~7–16 docs/s; a 50k vault ≈ 1–2 h pinned CPU, unbounded lag if edits outpace
encoding). S4 exit criteria additionally require:

- Content-addressed vector cache (content × encoder/config identity): fixed
  model + unchanged content is never re-encoded after unrelated edits.
- Measured CPU encode throughput with a stated path above arrival rate
  (quantized ONNX INT8 or equivalent evaluated, not assumed).
- Backlog-lag metric with backpressure behavior; stalled semantic worker
  must not block lexical retrieval (already required, restated as gate).

## B2 — S5 (semantic value): define "cheap structure" before the trial

No false dichotomy (14-verb regex vs local SLM on every sentence). The S5
contenders must include the middle ground: structural markdown hierarchy
(sections, lists, callouts, tables, wikilinks) + entity co-occurrence
clusters with byte-anchored spans. Open-domain regex triples are the floor
to beat, not the structure arm. Utility metric: missing-counterevidence
rate and qualifier retention on questions requiring beyond-top-K evidence,
not relation counts.

## B3 — S0/S1: black-box native gate, no verification theater

S0 capability ledger entries must name the executable check, not the unit
test file. S1 additionally requires: black-box HTTP against a separate
long-running native server process under concurrent write load, kill -9
crash injection with external-log recovery audit, and deliberate-blocking
probes outside the router timer. TestClient-only evidence is inadmissible
for S1. (Steering §5 already requires real server + load client; this makes
the adversarial minimum explicit.)
