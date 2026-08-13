# Runtime-NFR protocol configuration robustness plan (v1)

Frozen before execution: 2026-08-10 (Asia/Shanghai)

## Purpose and claim boundary

This is a configuration-robustness analysis of the already frozen governance protocol. It is not parameter optimization, model selection, or evidence that any setting is organizationally optimal. No configuration will replace the frozen primary setting (`W=24 h`, `Cmin=0.50`, `Rt=10`, `q=0.95`, `k=3.0`).

## Source population and evidence reconstruction

- Population: the same 8,668 event-anchor × service × SLI cards used in the frozen primary analysis.
- Source telemetry: `dataset/outputs/topo_intent_v2/segment_tenants_all`.
- Frozen card reference: `checkpoints/runtime_nfr_v3_academic/governance_cards/academic_governance_cards.json`.
- Historical observations are reconstructed through the production rules: one-minute aggregation, finite observations only, zero-traffic exclusion for error ratios, and exclusion of overlapping impacted intervals.
- The 24-hour, `q=0.95`, `k=3.0`, `Cmin=0.50`, `Rt=10` reconstruction must reproduce all frozen card routes before counterfactual results are accepted.

## Prespecified configuration grid

- History window `W`: 12, 18, 24 hours.
- Minimum history coverage `Cmin`: 0.40, 0.50, 0.60, 0.75.
- Minimum valid samples: `ceil(Cmin × 60 × W)`; it is tied to coverage/window and is not an independent factor.
- Latency tail-ratio cutoff `Rt`: 8.0, 9.5, 10.0, 10.5, 12.0.
- Candidate quantile `q`: 0.90, 0.95, 0.99.
- Robust-scale multiplier `k`: 2.5, 3.0, 3.5.

All 540 combinations are evaluated descriptively. The frozen setting is the sole reference.

## Deterministic routing and precedence

1. Incomplete provenance is a construction failure and never a fifth route.
2. History coverage below `Cmin` or valid samples below the tied minimum routes to `insufficient_evidence`.
3. A zero error baseline or a threshold determined only by the measurement-scale floor routes to `threshold_unresolved`.
4. Otherwise, latency tail ratio `(Qq - median) / max(robust_scale, 1e-6)` at least `Rt` routes to `needs_context_review`.
5. All remaining cards route to `candidate_for_stakeholder_review`.

The candidate threshold is `max(Qq, median + k × robust_scale)`, except that an all-zero error history retains a zero boundary. Rule priority remains history → resolution → tail.

## Prespecified outputs

For every configuration:

1. four-state counts and proportions;
2. route migration rate relative to the frozen configuration;
3. Jaccard similarity of the stakeholder-review set relative to the frozen configuration;
4. proportion assigned a non-numeric disposition (all routes except stakeholder review).

Aggregate reporting will give the minimum, median, and maximum of the non-numeric disposition proportion across all configurations. The principal visualization is the `Cmin × Rt` migration landscape at `W=24 h`, `q=0.95`, `k=3.0`; secondary one-factor panels display window and candidate-generation robustness without selecting a preferred value.

## Dependence and inference policy

Cards share event anchors and services, so the analysis is descriptive and does not treat 8,668 cards as independent inferential observations. No null-hypothesis tests or multiplicity-adjusted parameter selection will be performed. Exact deterministic counts and ranges are the estimands.

## Acceptance checks

- source files are hashed in the output manifest;
- the frozen configuration reproduces 8,668/8,668 route assignments;
- every configuration contains exactly 8,668 routed cards;
- state counts sum to 8,668;
- migration is zero and Jaccard is one for the frozen configuration;
- results are written only to this versioned experiment directory and a new figure directory.

