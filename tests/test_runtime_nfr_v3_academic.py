from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from src.data.runtime_nfr_v3_academic import (
    GOVERNANCE_STATUSES,
    ROUND2_DIMENSIONS,
    ROUND2_REVIEWER_IDS,
    academic_governance_decision,
    audit_external_dataset_manifest,
    build_academic_governance_cards,
    select_round2_cards,
    summarize_round2_expert_review,
    write_round2_expert_package,
)


def _v2_card(
    card_id: str,
    *,
    sli: str = "latency",
    coverage: float = 1.0,
    valid_samples: int = 1000,
    q95: float = 10.0,
    median: float = 5.0,
    mad: float = 1.0,
    robust_scale: float = 1.4826,
    threshold: float = 10.0,
    target_type: str = "service",
) -> dict:
    return {
        "card_id": card_id,
        "schema": "runtime-nfr-boundary-card/v1",
        "status": "candidate",
        "subject_type": "application_service",
        "subject_id": f"service-{card_id}",
        "sli": sli,
        "sli_semantics": "test",
        "unit": "ms" if sli == "latency" else "ratio",
        "measurement_point": "APM",
        "zero_traffic_rule": "exclude zero traffic",
        "history_start": "2025-01-01T00:00:00",
        "history_end": "2025-01-02T00:00:00",
        "valid_samples": valid_samples,
        "quantile_level": 0.95,
        "quantile_value": q95,
        "median": median,
        "mad": mad,
        "robust_scale": robust_scale,
        "threshold": threshold,
        "persistence_minutes": 3,
        "observation_context": {
            "segment_id": "private-segment",
            "topology_hash": "private-topology",
            "history_minutes_requested": 1440,
        },
        "evidence_provenance": {
            "protocol": "runtime_nfr_v2",
            "dataset_hash": "private-hash",
            "event_id": f"event-{card_id}",
            "record_id": f"record-{card_id}",
            "fault_family": "cpu",
            "target_type": target_type,
            "query_version": "runtime-nfr-boundary-card/v1",
        },
        "quality": {"history_coverage": coverage},
    }


def _academic_for_status(card_id: str, status: str) -> dict:
    if status == "insufficient_evidence":
        source = _v2_card(card_id, coverage=0.4, valid_samples=600)
    elif status == "threshold_unresolved":
        source = _v2_card(
            card_id,
            sli="error_ratio",
            q95=0.0,
            median=0.0,
            mad=0.0,
            robust_scale=1e-6,
            threshold=0.0,
        )
    elif status == "needs_context_review":
        source = _v2_card(
            card_id,
            q95=16.0,
            median=5.0,
            mad=0.5,
            robust_scale=1.0,
            threshold=16.0,
        )
    else:
        source = _v2_card(card_id)
    academic = build_academic_governance_cards([source])[0]
    assert academic["evidence_status"] == status
    return academic


def test_governance_preserves_threshold_retains_all_flags_and_applies_priority() -> None:
    source = _v2_card(
        "multi",
        sli="error_ratio",
        coverage=0.1,
        valid_samples=100,
        q95=0.0,
        median=0.0,
        mad=0.0,
        robust_scale=1e-6,
        threshold=0.0,
    )
    decision = academic_governance_decision(source)
    assert decision.evidence_status == "insufficient_evidence"
    assert "history_coverage_below_0_5" in decision.governance_flags
    assert "valid_samples_below_720" in decision.governance_flags
    assert "zero_error_baseline" in decision.governance_flags
    wrapped = build_academic_governance_cards([source])[0]
    assert wrapped["raw_threshold"] == source["threshold"]
    assert "threshold" not in wrapped
    assert wrapped["applicability_context"]["business_slo_approved"] is False


def test_governance_boundary_rules_cover_resolution_floor_and_tail_ratio() -> None:
    unresolved = _v2_card(
        "resolution",
        sli="error_ratio",
        q95=2e-6,
        median=0.0,
        mad=0.0,
        robust_scale=1e-6,
        threshold=3e-6,
    )
    assert academic_governance_decision(unresolved).evidence_status == "threshold_unresolved"
    at_boundary = _v2_card(
        "tail",
        q95=15.0,
        median=5.0,
        mad=0.5,
        robust_scale=1.0,
        threshold=15.0,
    )
    assert academic_governance_decision(at_boundary).evidence_status == "needs_context_review"


def _round2_fixture() -> tuple[list[dict], pd.DataFrame]:
    first_rows = [
        {
            "card_id": f"first-{index:02d}",
            "threshold_plausibility": 2 if index < 24 else 4,
        }
        for index in range(48)
    ]
    academic = [
        _academic_for_status(
            row["card_id"],
            GOVERNANCE_STATUSES[index % len(GOVERNANCE_STATUSES)],
        )
        for index, row in enumerate(first_rows)
    ]
    for status in GOVERNANCE_STATUSES:
        academic.extend(
            _academic_for_status(f"held-{status}-{index}", status) for index in range(6)
        )
    return academic, pd.DataFrame(first_rows)


def test_round2_selection_is_exact_reproducible_disjoint_and_balanced() -> None:
    academic, first = _round2_fixture()
    selected_a, manifest_a = select_round2_cards(academic, first, seed=20260723)
    selected_b, manifest_b = select_round2_cards(academic, first, seed=20260723)
    assert [card["round2_card_id"] for card in selected_a] == [
        card["round2_card_id"] for card in selected_b
    ]
    assert manifest_a == manifest_b
    assert sum(row["cohort"] == "remediation" for row in manifest_a) == 24
    generalization = [row for row in manifest_a if row["cohort"] == "generalization"]
    assert len(generalization) == 24
    assert pd.Series([row["governance_status"] for row in generalization]).value_counts().to_dict() == {
        status: 6 for status in GOVERNANCE_STATUSES
    }
    assert not {
        row["parent_card_id"] for row in generalization
    } & set(first["card_id"])


def test_round2_packages_have_same_set_different_order_and_no_private_fields(
    tmp_path: Path,
) -> None:
    academic, first = _round2_fixture()
    selected, private = select_round2_cards(academic, first)
    manifest = write_round2_expert_package(
        tmp_path,
        selected_cards=selected,
        private_manifest=private,
    )
    orders = []
    for reviewer_id in ROUND2_REVIEWER_IDS:
        payload = json.loads(
            Path(manifest["packages"][reviewer_id]["cards"]).read_text(encoding="utf-8")
        )
        orders.append([card["round2_card_id"] for card in payload["cards"]])
        text = json.dumps(payload)
        for private_name in (
            "parent_card_id",
            "cohort",
            "fault_family",
            "target_type",
            "event_id",
            "record_id",
            "traffic_quartile",
        ):
            assert private_name not in text
        ratings = pd.read_csv(manifest["packages"][reviewer_id]["ratings"])
        assert len(ratings) == 48
        assert set(ratings["reviewer_id"]) == {reviewer_id}
    assert all(set(order) == set(orders[0]) for order in orders)
    assert len({tuple(order) for order in orders}) == 3


def test_round2_summary_reports_primary_cohorts_and_statuses(tmp_path: Path) -> None:
    academic, first = _round2_fixture()
    selected, private = select_round2_cards(academic, first)
    package_dir = tmp_path / "package"
    manifest = write_round2_expert_package(
        package_dir,
        selected_cards=selected,
        private_manifest=private,
    )
    rating_paths = []
    for reviewer_id in ROUND2_REVIEWER_IDS:
        path = Path(manifest["packages"][reviewer_id]["ratings"])
        frame = pd.read_csv(path)
        for dimension in ROUND2_DIMENSIONS:
            frame[dimension] = 4
        frame["comments"] = ""
        frame.to_csv(path, index=False)
        rating_paths.append(path)
    summary = summarize_round2_expert_review(
        rating_paths,
        package_dir / "sampling_and_parent_mapping_private.json",
        tmp_path / "summary",
    )
    assert summary["primary_endpoint"]["pass"] is True
    assert summary["primary_endpoint"]["observed_rate"] == 1.0
    assert set(summary["by_cohort"]) == {"remediation", "generalization"}
    assert set(summary["by_governance_status"]) == set(GOVERNANCE_STATUSES)


def test_external_manifest_audit_requires_test_only_isolation() -> None:
    manifest = {
        "dataset_name": "FIRM",
        "dataset_version": "frozen-v1",
        "source_url": "https://example.invalid",
        "license": "research",
        "frozen_sha256": "a" * 64,
        "frozen_at": "2026-07-25",
        "time_range_start": "2025-01-01",
        "time_range_end": "2025-12-31",
        "intended_role": "external_test_only",
        "used_for_rule_threshold_feature_or_model_selection": False,
        "independent_run_count": 125,
        "service_level_sli_available": True,
        "estimated_adapter_effort_days": 1,
        "history_window_minutes_available": 1440,
        "early_input_minutes_available": 5,
        "future_window_minutes_available": 15,
        "reference_slo_completeness": "complete",
    }
    assert audit_external_dataset_manifest(manifest)["pass"] is True
    assert (
        audit_external_dataset_manifest(manifest)["decision"]
        == "validity_and_prediction"
    )
    manifest["history_window_minutes_available"] = 35
    result = audit_external_dataset_manifest(manifest)
    assert result["pass"] is True
    assert result["decision"] == "validity_only"
    manifest["used_for_rule_threshold_feature_or_model_selection"] = True
    assert audit_external_dataset_manifest(manifest)["pass"] is False
    assert audit_external_dataset_manifest(manifest)["decision"] == "reject"
