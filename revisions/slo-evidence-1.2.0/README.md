# Evidence checking and responsibility routing for SLO candidates

Software `slo-evidence/1.2.0`, with measurement assessment
`measurement-audit/20260930`. This supplement accompanies the revised manuscript
*Evidence Checking and Responsibility Routing for Runtime Quality-Requirement
Candidates in SLO Engineering*.

The original submission snapshot remains available at commit
[`1786cc50679095be66081da904d46bf2f3b66e89`](https://github.com/Akeboxi/runtime-nfr-evidence-governance/tree/1786cc50679095be66081da904d46bf2f3b66e89).
The repository root retains those historical code and human-study artifacts;
this directory contains the audited current implementation and supplementary
numeric and synthetic artifacts. See [REVISION_RESULTS.md](REVISION_RESULTS.md)
for version-specific results and [MEASUREMENT_AUDIT.md](MEASUREMENT_AUDIT.md)
for the corrected measurement assessment.

## Reproduction

Run from this directory in an isolated Python environment. The release checks
use Python 3.10.18 and the pinned scientific packages in `requirements.txt`.
The core package and portable checks do not require torch or DGL.

```text
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/reproduce_corrected_grid.py --rebuild artifacts/rebuild --output rebuilt/grid
python scripts/run_learning_candidate_revision.py --rebuild artifacts/rebuild --output rebuilt/learned_g
python scripts/write_lifecycle_examples.py rebuilt/lifecycle
python verify_manifest.py
```

The learning experiment retains all 72 frozen selections, including 17 failed
fits. Compare rebuilt learning outputs with `artifacts/learned_g/`; elapsed
runtime can vary. The grid check starts from archived sufficient statistics.
Full raw-telemetry reconstruction needs upstream data and the original
graph-aware loader. The corresponding changes and sources are provided in
`legacy_changes.patch` and `reconstruction_sources/`.

The implementation checks provenance fields, evidence references, hashes,
timestamps and scope before routing. A calls for more observation history, B
for measurement clarification, C for context review, and D for target-owner
review. D does not approve an SLO. A valid scoped closure can resolve its task;
it cannot waive insufficient history. Hash checks do not establish the truth
of evidence, and a reviewer field does not authenticate a person.

Lifecycle fixtures are explicitly synthetic program-verification cases. They
are kept separate from empirical candidates. The tests verify behavior under
the defined rules; the learning experiment verifies another candidate source
against the same H, without a model-superiority or human-efficiency claim.

`ANALYSIS_PLAN.md` is the unchanged protocol frozen on September 29. Its M4
discussion is historical; the September 30 assessment also leaves M1 and M2
unknown. M3 is verified only for the local transformation.

Code is MIT. Telemetry-derived inputs retain applicable upstream CC BY-NC 4.0
terms, as described in [DATA_LICENSE.md](DATA_LICENSE.md). This supplement
contains no evaluator identities, raw survey free text, or private audit files.
