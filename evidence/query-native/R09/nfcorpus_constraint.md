# R09 stamp: NFCorpus shortfall as fallback-representation constraint (2026-09-12)

Packet verdict UNCHANGED: FAIL (qualification not achieved). Rung status
UNCHANGED: 2k + 10k capacity-characterized CONDITIONAL-diagnostics; 100k+
unrun/refused. This note closes one open question so no further cycles are
spent on it.

Finding: on the 10k integrated run, NFCorpus nDCG@10 ≈ 0.18 vs R00 frozen
0.3188 (CI excludes baseline); SciFact ≈ 0.63 vs 0.6589 (CI covers baseline).

Disposition: DOCUMENTED CONSTRAINT, not a regression to chase. INT-3 already
proved the mechanism on the served path: NFCorpus's clinical vocabulary
collides under the unlearned 128-d hashbow fallback (learned BGE gained
+0.1077 paired there, the largest delta of any dataset), while SciFact's
vocabulary survives it. The integrated pipeline did not introduce this; it
inherited the fallback representation that R08/INT-3 parked.

Consequences:
- No further NFCorpus subset recomputation under the fallback config.
  Expected-behavior record, not an anomaly.
- The shortfall converts into a promotion argument for background-served
  learned vectors (S4): the dataset most needing them is identified, with
  paired deltas already measured (INT-3/spike-nfcorpus.json).
- S5/S6 quality tracking uses SciFact-parity plus NFCorpus-as-constraint:
  fallback must not regress SciFact; NFCorpus recovery is credited only to
  a served learned configuration, never to threshold or subset selection.
