# Bidirectional protocol contract test plan (v1)

Frozen before execution: 2026-08-10 (Asia/Shanghai)

## Purpose and claim boundary

This deterministic suite tests both acceptance and refusal behavior at a changed input interface. It is a bidirectional protocol contract test, not an external-validity study and not an accuracy benchmark. The existing RCAEval experiment remains an external negative-control/refusal-path audit.

## Interface

The adapter produces the same canonical card fields consumed by the production `academic_governance_decision` implementation. Each valid test case carries complete, synthetic-but-valid provenance. One case intentionally omits provenance and must hard-fail before routing.

## Prespecified boundary cases

1. history duration 23 h 59 min versus 24 h;
2. coverage 0.49 versus 0.51;
3. valid samples 719 versus 721;
4. zero versus non-zero error baseline;
5. latency tail ratio 9.9 versus 10.1;
6. overlapping history, resolution, and tail triggers to verify precedence;
7. missing provenance to verify construction failure rather than silent dropping or a fifth route;
8. a fully eligible non-tail card that must reach stakeholder review.

Where a duration-only interface field is supplied, the adapter derives coverage against the frozen 24-hour requested window. Expected outcomes are frozen in `cases.json` before execution.

## Primary outputs and acceptance criteria

- adapter success/failure by case;
- observed versus expected governance route;
- four-state distribution among successfully routed cases;
- number of cases passing the evidence gate and reaching stakeholder review;
- anomaly list with exact reasons;
- 100% agreement for all routed cases and the expected provenance hard failure.

No statistical inference is planned because this is an executable contract suite.

