from pathlib import Path

import json

from scripts.analyze_runtime_nfr_phase2 import (
    assign_status,
    expected_calibration_error,
    status_counts,
    transition_rows,
    triggers,
)


def test_governance_priority_and_tail_counterfactual() -> None:
    card = {
        "governance_flags": [
            "history_coverage_below_0_5",
            "zero_error_baseline",
        ],
        "applicability_context": {"latency_tail_ratio": 10.2},
    }
    values = triggers(card)
    assert values == {"history": True, "resolution": True, "tail": True}
    assert assign_status(values) == "insufficient_evidence"
    assert assign_status(values, ("resolution", "history", "tail")) == "threshold_unresolved"
    assert triggers(card, tail_cutoff=10.5)["tail"] is False


def test_status_counts_and_transition_matrix_are_lossless() -> None:
    baseline = [
        "insufficient_evidence",
        "threshold_unresolved",
        "candidate_for_stakeholder_review",
    ]
    counterfactual = [
        "threshold_unresolved",
        "threshold_unresolved",
        "candidate_for_stakeholder_review",
    ]
    counts = status_counts(baseline)
    assert sum(counts.values()) == 3
    rows = transition_rows(baseline, counterfactual, "synthetic")
    assert sum(row["count"] for row in rows) == 3


def test_equal_width_ece() -> None:
    import numpy as np

    labels = np.asarray([0, 1], dtype=int)
    probabilities = np.asarray([0.0, 1.0], dtype=float)
    assert expected_calibration_error(labels, probabilities) == 0.0


def test_generated_phase2_manifest_when_present() -> None:
    manifest_path = Path(
        "checkpoints/runtime_nfr_v3_academic/phase2_analysis_v1/ANALYSIS_MANIFEST.json"
    )
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_files_unchanged"] is True
    assert manifest["analyses"] == [f"A{index:02d}" for index in range(1, 9)]
    assert all(
        source["sha256_before"] == source["sha256_after"] for source in manifest["source_files"]
    )
