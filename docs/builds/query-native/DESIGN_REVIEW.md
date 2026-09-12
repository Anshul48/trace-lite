# Design review and handoff

Contract: TL-QN-2026-09-12.1.
Review type: architectural self-review and documentation consistency check; not independent implementation verification.

## Design tensions and dispositions

| Concern | Disposition | Verification owner |
|---|---|---|
| “Documents are dead” could erase provenance | Query-native access; retained source/episode revisions and coordinate mappings | R01/R02 |
| A graph could amplify unsupported model relations | Evidence-linked assertion status, residual text, unresolved alternatives | R04 |
| Cross-source grouping could collapse contradictions | Separate grouping, identity, equivalence and derivation operations | R05 |
| Fixed centroids could misrepresent multimodal holons | Representatives, dispersion and reversible split/merge | R05/R08 |
| Incremental sums fail when representation changes globally | Encoder/IDF generation identity and explicit rebuilding | R03/R06 |
| SQLite and ANN publication could disagree | Durable jobs, immutable manifests, version filters and crash probes | R01/R03/R06 |
| Dependency closure can exceed a “local” budget | Immediate stale filtering, bounded repair queue, explicit incomplete state | R06 |
| ANN filters can reduce recall silently | Filter capability tests, bounded overretrieval/fallback, coverage reporting | R03/R09 |
| Graph retrieval can regress ordinary factual search | Independent global retrieval plus B1–B4 comparisons | R03/R09 |
| Prediction error could become another dogmatic splitter | Separate predictive and contextual experiments with promotion criteria | R08 |
| “Answerable” could remain a similarity threshold | Candidate relevance separated from scoped support checking | R07 |
| New provenance increases storage | One source payload plus measured materializations; no copied payload events | R01/R09 |
| Recursive synthesis could corroborate itself | Derived lineage, duplicate-source tracking and retention separate from validation | R05/R07 |
| Existing source offsets may be unverifiable | Legacy coordinate label; reingest to obtain original provenance | R02 |
| Old reset/overnight prompts could override the new plan | Supersession notices and TL-only coordinator entry point | Planning update |

## Open empirical decisions

The encoder, extractor, ANN backend, predictive method, numeric operating envelopes, learned-repair utility and maximum qualified scale remain empirical decisions assigned to packets. They are intentionally not asserted as solved.

USearch and BGE-small are initial comparison candidates, not mandatory adoption choices. The 1M workload is sources plus measured span/edge expansion, not one million synthetic atoms. There is no commitment to a universal sub-50ms target.

## Scope of this delivery

Written: cited research assessment, architecture contract, project/authority boundary, evaluation contract, dependency plan, packet set R00–R09, state and TL-only coordinator brief.

Preserved: historical P01–P07 reports, overnight evidence, source code, concurrent repairs, untracked lockfile and TRACE's running work. Planning headers were added to historical entry points; their existing body content was retained.

No product tests, model inference, 1M runs, migrations, service restarts, git commits or pushes were performed by this planning update. Documentation checks cover link resolution, packet coverage/dependencies, reference completeness and whitespace. They do not validate the proposed runtime architecture.

Documentation validation completed: 18 new planning documents, 10 packets, 16 research references; no broken local links or missing footnote definitions in the new program. Packet dependencies are acyclic and resolve to R00–R09. All 17 legacy/root entry points contain the new-plan notice. Scoped git diff whitespace checks passed. HEAD remained c8e6b701dbd86a412ffec228afde319e122d337e during validation; concurrent uncommitted product/evidence changes remain outside this review.
