# Native Windows ladder (2026-09-12) — operator-run via run_win.bat

Source: `evidence/query-native/scale_ladder_20260912_164558/report_{2k,10k,100k,250k,1m}.json`
(operator-pasted console output corroborates; report files read from this checkout).

## What it is

LEGACY component/latency-profile evidence on native Windows (.venv_win, NTFS),
NOT integrated-system qualification. Harness: benchmarks/run_trace_lite_benchmark.py
(bulk atom ingest + router queries, no R02–R05 projections, no dossier path).
Same operating point across rungs (quality profile, uncapped dense); quality
(nDCG) NOT measured here — latency/ingest gates only.

## Results (all rungs pass=true on latency/ingest/cordis/sqlite gates)

| rung | ingest docs/s | query p50/p95 ms | verdicts ans/insuff |
|---|---|---|---|
| 2k | 14028 | 5.1/7.7 | 75/25 |
| 10k | 14250 | 3.3/8.3 | 375/125 |
| 100k | 9391 | 3.4/8.7 | 375/125 |
| 250k | 6599 | 3.3/9.3 | 375/125 |
| 1M | 4954 | 3.2/9.5 | 750/250 |

BEIR scifact (native smoke): nDCG@10 0.6589, CI [0.6136,0.7029] — exact match
to R00 frozen baseline. Gate: 60.0 GB free on C: (minimum 20 GB).

## Vacuous gate found and repaired

`resources.peak_rss_mb` read 0.0 on all five rungs (Windows ctypes fallback
failure) while `rss_lte_500mb` reported true. Those five RSS claims are
UNMEASURED and are NOT accepted as evidence. Fix (same day, this checkout):
telemetry failure now yields NaN→null with `telemetry_ok:false`, and the gate
requires `telemetry_ok and peak<=500` — a blind instrument fails closed.
Root cause of the ctypes failure still open; needs a native 3-line probe
(GetProcessMemoryInfo return + GetLastError). Until real RSS numbers land,
no memory claim attaches to this ladder.

## Standing

Native legacy-profile ladder: PASS except memory (unmeasured). Integrated
qualification remains R09's gated ladder on the new system paths.

## Rerun v2 (same day, `scale_ladder_20260912_171318`, hardened telemetry)

`telemetry_method: ctypes-psapi` on all rungs — missing-signatures root cause
confirmed and fixed. Real RSS curve (MB): 2k 46.3, 10k 59.8, 100k 188.1,
250k 385.9, 1M 1383.6. Latency/ingest gates pass on all rungs; smoke
re-passes identically (BEIR 0.6589). The 1M rung FAILS `rss_lte_500mb`
(1383.6 > 500) and the ladder HALTED with exit 1 — halt-on-failure proven
natively, no advancement past the failed rung.

The 500 MB line was an uncalibrated engineering guess, not a requirement:
its first contact with real data is this failure. Per steering §5 the budget
decision (raise with rationale / bound peak via chunked ingest / qualify to
250k) belongs to R09's contract amendments, which must preserve this
observation. Until then: native legacy-profile qualified to 250k; 1M
executed, memory-gate failed, cause documented here.
