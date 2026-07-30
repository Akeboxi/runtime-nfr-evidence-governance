from __future__ import annotations

import pandas as pd
import pytest

from src.data.runtime_nfr_external import (
    EXTERNAL_OBSERVATION_COLUMNS,
    audit_canonical_external_observations,
    validate_external_split_manifest,
)
from src.data.runtime_nfr_integrity import freeze_or_audit_legacy_integrity


def test_external_observation_audit_requires_namespaced_unique_rows() -> None:
    frame = pd.DataFrame(
        [
            {
                "dataset_id": "external",
                "event_id": "event-1",
                "service_id": "external:frontend",
                "timestamp_utc": "2025-01-01T00:00:00Z",
                "request_count": None,
                "latency_ms": 12.0,
                "error_count": None,
                "fault_start": "2025-01-01T00:01:00Z",
                "reference_threshold": None,
                "reference_status": "absent",
            }
        ],
        columns=EXTERNAL_OBSERVATION_COLUMNS,
    )
    result = audit_canonical_external_observations(frame, dataset_id="external")
    assert result["pass"] is True
    duplicated = pd.concat([frame, frame], ignore_index=True)
    assert (
        audit_canonical_external_observations(
            duplicated, dataset_id="external"
        )["pass"]
        is False
    )


def test_external_split_manifest_rejects_event_overlap() -> None:
    valid = {
        "train_event_ids": ["a", "b"],
        "validation_event_ids": ["c"],
        "test_event_ids": ["d"],
    }
    validate_external_split_manifest(valid)
    invalid = {**valid, "test_event_ids": ["a", "d"]}
    with pytest.raises(AssertionError, match="more than one split"):
        validate_external_split_manifest(invalid)


def test_legacy_integrity_snapshot_detects_change(tmp_path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    evidence = legacy / "evidence.txt"
    evidence.write_text("frozen", encoding="utf-8")
    output = tmp_path / "audit"
    first = freeze_or_audit_legacy_integrity(
        [legacy], output, workspace=tmp_path
    )
    assert first["pass"] is True
    evidence.write_text("changed", encoding="utf-8")
    second = freeze_or_audit_legacy_integrity(
        [legacy], output, workspace=tmp_path
    )
    assert second["pass"] is False
    assert second["changed"] == ["legacy/evidence.txt"]
