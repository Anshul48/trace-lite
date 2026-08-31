# Benchmark Dataset Registry

> **Status:** Empty planning registry. No dataset is declared downloaded,
> licensed, normalized, or certification-ready merely because this directory
> exists.

## Purpose

This directory will hold small, reviewable records describing real benchmark
datasets used by Trace-Lite. It records how data is obtained and verified
without committing raw public caches, private corpora, results, or credentials.

## Intended record structure

```text
benchmarks/registry/
  <dataset-id>.md
  <dataset-id>.manifest.json
```

## Required fields for each dataset

1. Dataset identity, official source, version, and license.
2. Evaluation purpose and query/task classes covered.
3. Access method and approved local staging location.
4. Content/archive checksum and download verification time.
5. Transform, segmentation, and judgment mapping identities.
6. Privacy, redistribution, and retention constraints.
7. Explicit statement that unavailable data fails the release stage.
8. Known limitations and suitability for 100K or later scale work.

## Initial candidate categories

- public retrieval benchmarks,
- multi-hop evidence benchmarks,
- temporal/revision-aware memory benchmarks,
- hierarchy/long-context organization benchmarks,
- real source corpora suitable for 100K scale and recovery testing,
- held-out downstream fixtures referenced externally by checksum only.

## Ownership

The data steward creates records. The evaluation engineer consumes reviewed
records. The coordinator marks a record ready for a specific evaluation packet.

## Registry lifecycle

Each record moves through these states:

~~~text
candidate
  -> source_verified
  -> rights_reviewed
  -> staging_verified
  -> transform_verified
  -> evaluator_admitted
  -> retired_or_superseded
~~~

Only an evaluator-admitted record may appear in a certification manifest. A
candidate source may be researched or downloaded under a packet, but it must
not be treated as benchmark-ready until its record is complete.

## Record naming and layout

Use stable lowercase identifiers and keep one Markdown explanation plus one
machine-readable manifest:

~~~text
benchmarks/registry/
  <dataset-id>.md
  <dataset-id>.manifest.json
  templates/
    DATASET_RECORD_TEMPLATE.md
    DATASET_MANIFEST_TEMPLATE.json
~~~

The record can name a local staging-root environment variable or configuration
key. It must not require another developer to discover a private absolute path.

## Dataset record checklist

### Identity and rights

- official dataset/corpus title, version, publisher, and primary URL;
- license text/link, attribution requirements, redistribution constraints;
- access credentials/terms if applicable, privacy/PII concerns, retention;
- reason this source is appropriate for a named evaluation lane.

### Integrity and staging

- archive and normalized-content hashes;
- expected file tree, byte size, document/query/judgment counts;
- approved fetch command/tool and verification command;
- external cache location policy, timestamp, and failed-download behavior.

### Transform and evaluation

- parser/normalizer version and configuration hash;
- source artifact boundaries, segmentation, atomization, and ID mapping;
- query/judgment mapping and exclusion policy;
- compatible metrics, baseline assumptions, query classes, and limitations.

## Registry review questions

Before marking a record evaluator-admitted, answer:

1. Can a reviewer obtain the same source legally and verify its identity?
2. Do the source and judgment semantics match the proposed metric?
3. Does the transform preserve a recoverable source-evidence mapping?
4. Is this data public, held-out private, regression-only, or scale-only?
5. Would a missing source cause a clear failure rather than a substitute run?
6. What claim is this record allowed to support, and what is it not allowed to
   support?

## Initial registry population order

1. Audit currently referenced adapters and fixtures.
2. Add candidate records with no download claim.
3. Review rights and source stability.
4. Authorize staged acquisition one source at a time.
5. Verify normalized forms and transforms.
6. Admit the minimum useful portfolio into a fixed evaluation manifest.

Do not populate dozens of weak records before one complete, reproducible path
exists.
