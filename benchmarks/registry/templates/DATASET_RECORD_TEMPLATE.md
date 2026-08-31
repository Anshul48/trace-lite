# Dataset Record: <dataset-id>

> **State:** candidate | source_verified | rights_reviewed | staging_verified |
> transform_verified | evaluator_admitted | retired_or_superseded

## 1. Identity

| Field | Value |
| --- | --- |
| Stable dataset ID | |
| Official title | |
| Version/release date | |
| Publisher/maintainer | |
| Official source URL | |
| Primary documentation | |
| Intended evidence lane | regression | public | held_out | fault | scale |
| Intended query classes | |

## 2. Rights, privacy, and retention

| Question | Answer/evidence |
| --- | --- |
| License and link | |
| Attribution requirements | |
| Download/use restrictions | |
| Redistribution/caching policy | |
| PII/sensitive-content assessment | |
| Local retention/deletion policy | |
| Reviewer/owner | |

## 3. Source and staging

| Field | Value |
| --- | --- |
| Approved acquisition method | |
| Approved external staging-root policy | |
| Archive filename/size | |
| Archive SHA-256 | |
| Download timestamp | |
| Source availability checks | |
| Failure behavior if unavailable | Fail closed; no substitute |

## 4. Normalization and provenance

| Field | Value |
| --- | --- |
| Normalizer/parser version | |
| Transform configuration hash | |
| Normalized-content SHA-256 | |
| Source artifact count | |
| Source atom count after declared segmentation | |
| Segmentation/atomization rule | |
| Source-to-atom/evidence mapping | |
| Exclusions and reason codes | |

## 5. Query and judgment suitability

| Field | Value |
| --- | --- |
| Query pack/version/hash | |
| Judgment pack/version/hash | |
| Stable query/evidence IDs | |
| Supported metrics | |
| Baseline compatibility | |
| Known label limitations | |
| No-evidence/abstention handling | |

## 6. Admission review

### Required checks

- [ ] Official source and version verified.
- [ ] License and access reviewed.
- [ ] Archive and normalized hashes recorded.
- [ ] Transform/evidence mapping reproducible.
- [ ] Query/judgment semantics fit the requested metrics.
- [ ] Raw data is outside Git and access policy is documented.
- [ ] Missing source fails the relevant protocol.
- [ ] Claim boundary and limitations are explicit.

### Decision

| Field | Value |
| --- | --- |
| Admission outcome | |
| Decision/review ID | |
| Date | |
| Allowed protocol use | |
| Explicitly prohibited claims | |
| Follow-up risks | |
