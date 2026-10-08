# Current revised implementation

The audited revision is in
[`revisions/slo-evidence-1.2.0/`](revisions/slo-evidence-1.2.0/): software
`slo-evidence/1.2.0` with measurement assessment `measurement-audit/20260930`.
It includes corrected evidence assessment, scoped closure and rechecking,
17 implementation tests, and the frozen learning-source interface experiment.
Start with that directory's README for current-code reproduction.

The original code and study artifacts below are retained for historical
reproduction. The original submission snapshot is fixed at
[`1786cc50679095be66081da904d46bf2f3b66e89`](https://github.com/Akeboxi/runtime-nfr-evidence-governance/tree/1786cc50679095be66081da904d46bf2f3b66e89).

---

# Runtime Quality-Requirement Candidate Evidence Governance

Reproducibility snapshot for the manuscript:

> 面向 SLO 工程的运行时质量需求候选证据治理方法  
> Evidence Governance for Runtime Quality-Requirement Candidates in SLO Engineering

## Scope

This repository contains the code, de-identified analysis artifacts, and figure
sources needed to inspect the manuscript's evidence-governance results. The
method treats evidence checking as a quality gate between numerical candidate
generation and organizational SLO target selection. It is not an SLO candidate
generator.

The submission snapshot covers:

- 8,668 pseudonymized candidate cards and the four-state routing summary;
- the threshold-only representation comparison and rule-removal analysis;
- the visible-output expert evaluation;
- the first blinded-routing evaluation (32 cards, 96 judgments);
- the independent v1.1 B/D-boundary evaluation (24 cards, 72 judgments);
- 14 bidirectional interface conformance cases;
- 540 prespecified configuration variants; and
- publication figure sources.

Raw third-party datasets, evaluator identities, signed declarations, contact
details, and raw free-text responses are not included.

## Repository map

```text
artifacts/
  core/                    8,668-card package and visible-output evaluation
  blinded-routing/         de-identified first blinded evaluation
  bd-boundary-v1.1/        de-identified independent boundary evaluation
  bidirectional/           14 interface conformance cases
  robustness/              540-configuration sensitivity results
  figures/                 SVG/PNG figure sources used by the manuscript
  provenance/              official-400 to derived-394 case mapping
src/                       implementation used by the analysis pipeline
scripts/                   analysis and audit entry points
tests/                     regression tests
```

## Quick verification

Python 3.11 is recommended. Dependencies are locked in `uv.lock`.

```bash
uv sync --frozen
uv run python artifacts/core/reproduce_summary.py --check
uv run python artifacts/blinded-routing/scripts/analyze_responses.py \
  --responses artifacts/blinded-routing/data/blinded_responses_deidentified.csv \
  --answer-key artifacts/blinded-routing/data/protocol_answer_key.csv \
  --output-dir artifacts/blinded-routing/rebuilt-analysis
uv run python artifacts/bd-boundary-v1.1/scripts/analyze_responses.py \
  artifacts/bd-boundary-v1.1/data/blinded_responses_deidentified.csv \
  --key artifacts/bd-boundary-v1.1/data/protocol_answer_key.csv \
  --output artifacts/bd-boundary-v1.1/rebuilt-analysis
uv run pytest
```

The checked-in summaries are the manuscript-facing outputs. The commands above
rebuild them from the de-identified inputs.

## AIOps Challenge 2025 provenance

The cited official snapshot contains 400 cases in `input.json` and
`groundtruth.jsonl`, with telemetry distributed across 18 daily archives. The
analysis pipeline transformed those inputs into 27 topology-aligned segment
directories. Twenty-four segments contain 394 derived event anchors; each
derived `global_label.json` retains the source `groundtruth.jsonl` UUID.

Six source cases do not receive derived anchors because their sub-minute event
start times fall into one-minute gaps introduced at topology-segment
boundaries. `artifacts/provenance/aiops2025_case_mapping.csv` records all 400
source cases, their derived-anchor status, and the applicable reason. These six
cases were absent from the derived input before the manuscript's 8,668 analysis
units were formed; they were not removed by a later paper-level exclusion.

## Licenses

- Project code: MIT, see `LICENSE`.
- Author-generated de-identified tables, configurations, and documentation:
  CC BY 4.0, see `LICENSES/DERIVED-DATA.md`.
- Third-party source data are not redistributed here and remain under their
  upstream licenses. AIOps Challenge 2025 is used under CC BY-NC 4.0; RCAEval
  RE1-OB is used under CC BY 4.0.

## Citation and archival status

This branch is a submission snapshot. A versioned release and archival DOI can
be added after acceptance without changing the commit cited by the manuscript.

## Manuscript appendix details

The appendix supplement is in [docs/manuscript_supplement/appendix_details/](docs/manuscript_supplement/appendix_details/). It preserves the relocated timeline, complete questionnaire mapping, formative scores, blinded metrics, 14 historical interface cases, and the RCAEval refusal-path figure. The manuscript retains all seven tables added for the review revision; their current numbers are A3--A9.

Verify the supplement with the Python standard library:

```text
python docs/manuscript_supplement/appendix_details/verify_appendix_materials.py
```
