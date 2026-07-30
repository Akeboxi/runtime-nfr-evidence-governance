# Runtime NFR Evidence-Aware Governance

Private review snapshot for the manuscript:

> Operationalizing Runtime Non-functional Requirements without Explicit SLOs:
> Validity Auditing and Evidence-aware Governance of Candidate Boundaries

## Repository status

- Visibility: private during manuscript review.
- Planned release: public after manuscript acceptance.
- Licence: to be assigned before public release.
- Snapshot date: 2026-07-30.
- Manuscript source:
  `docs/manuscript/runtime_nfr/RUNTIME_NFR_MANUSCRIPT.md`.
- Content-freeze manifest:
  `checkpoints/runtime_nfr_v3_academic/content_freeze_candidate_v5/CONTENT_FREEZE_MANIFEST.json`.

This repository has a new root history. It intentionally does not inherit the
development repository's earlier commits.

## Included

- Runtime NFR source code and command-line entry points;
- analysis, audit, figure-generation and freeze scripts;
- 41 Runtime NFR regression tests;
- the Markdown manuscript and claim-boundary/audit records;
- the versioned v5 content-freeze evidence listed by the freeze manifest.

## Excluded

- raw public datasets, which must be downloaded from their cited sources;
- unrelated project checkpoints and training outputs;
- local virtual environments, caches and credentials;
- Word/PDF submission derivatives and temporary logs;
- unpublished development-repository history.

## Validation

At snapshot creation:

```text
41 passed
submission audit: pass
Markdown numeric audit: pass
formal result audit: pass
legacy integrity audit: pass
```

The content-freeze manifest is the authoritative file-and-hash inventory for
the manuscript evidence package.
