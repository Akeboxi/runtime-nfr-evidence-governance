from scripts.analyze_runtime_nfr_phase3 import (
    counterfactual_transitions,
    evidence_date,
    threshold_only_strata,
)


def _card(
    status: str,
    *,
    service: str = "svc",
    sli: str = "latency",
    coverage_bucket: str = "ge_0_75",
    flags: list[str] | None = None,
    tail_ratio: float | None = None,
) -> dict:
    return {
        "subject_id": service,
        "sli": sli,
        "evidence_status": status,
        "governance_flags": flags or [],
        "observation_context": {"segment_id": "seg_0001"},
        "evidence_provenance": {"evidence_cutoff": "2025-06-20T12:00:00"},
        "applicability_context": {
            "history_coverage_bucket": coverage_bucket,
            "latency_tail_ratio": tail_ratio,
        },
    }


def test_evidence_date_uses_frozen_cutoff() -> None:
    assert evidence_date(_card("candidate_for_stakeholder_review")) == "2025-06-20"


def test_threshold_only_strata_are_lossless() -> None:
    cards = [
        _card("candidate_for_stakeholder_review"),
        _card(
            "insufficient_evidence",
            coverage_bucket="lt_0_5",
            flags=["history_coverage_below_0_5"],
        ),
    ]
    payload, rows = threshold_only_strata(cards)
    assert payload["hidden_evidence_or_action"] == {"count": 1, "rate": 0.5}
    for dimension in payload["dimensions"].values():
        assert sum(row["total"] for row in dimension["rows"]) == 2
    assert rows


def test_counterfactual_transition_matrices_are_lossless() -> None:
    cards = [
        _card("candidate_for_stakeholder_review"),
        _card(
            "insufficient_evidence",
            coverage_bucket="lt_0_5",
            flags=["history_coverage_below_0_5"],
        ),
        _card(
            "needs_context_review",
            tail_ratio=10.1,
        ),
    ]
    payload, rows = counterfactual_transitions(cards)
    assert payload["scenarios"]["drop_history"]["changed_cards"] == 1
    assert payload["scenarios"]["tail_cutoff_10"]["changed_cards"] == 0
    for scenario in payload["scenarios"]:
        assert sum(row["count"] for row in rows if row["scenario"] == scenario) == 3
