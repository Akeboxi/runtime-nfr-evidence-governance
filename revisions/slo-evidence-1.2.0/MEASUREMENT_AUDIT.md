# Measurement assessment, September 30, 2026

The core software remains `slo-evidence/1.2.0`. The assessment adapter is
`measurement-audit/20260930`, and its derived-series definition is
`derived-minute-series/legacy-preserved/2`.

| Dimension | Assessment | Supported scope and unresolved question |
| --- | --- | --- |
| M1, definition and unit | Unknown | The local formula is known. Upstream rrt meaning/unit and overlap of error and timeout counts are not independently established. |
| M2, sampling and window | Unknown | Local minute flooring and [start,end) selection are known. Source sampling, collection and missingness remain unverified. |
| M3, aggregation | Verified for the local transformation only | Local source fields to minute values and candidate statistics are documented. This does not certify upstream instrumentation. |
| M4, precision and special values | Unknown | Source precision and upstream zero-versus-missing semantics are not established. |

The adapter preserves the historical transformation for comparability. It
averages rrt in each minute, computes row-wise (error+timeout)/request before
averaging ratios, and retains the legacy filtering and clipping. These formulas
are not a request-level latency percentile or a request-weighted error ratio.
Source null error/timeout fields are filled with zero by the legacy conversion.
Service-specific known fault intervals are excluded offline using frozen
labels. This preprocessing does not establish online validity.

The September 29 publication candidate marked M1 and M2 verified too broadly.
The current adapter corrects that assessment. Every empirical card in the
8,668-card pool is missing M1, M2 and M4. The historical main routing and current
A/B routing counts remain unchanged after that assessment correction, while
the recorded missing dimensions now accurately reflect the available material.
No synthetic closure record is used to release an empirical card.

The exact local definition is included in
`artifacts/learned_g/local_measurement_definition.txt`. The synthetic
measurement-assessment test ensures local transform knowledge is not treated
as certification of upstream semantics or sampling.
