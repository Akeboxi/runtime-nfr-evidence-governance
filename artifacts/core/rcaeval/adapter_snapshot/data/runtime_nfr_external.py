"""External runtime-NFR adapters with explicit construct and leakage limits."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import json
from typing import Any

import numpy as np
import pandas as pd

from .runtime_nfr_v3_academic import (
    ACADEMIC_PROTOCOL,
    GOVERNANCE_STATUSES,
    academic_governance_decision,
)


EXTERNAL_OBSERVATION_COLUMNS = (
    "dataset_id",
    "event_id",
    "service_id",
    "timestamp_utc",
    "request_count",
    "latency_ms",
    "error_count",
    "fault_start",
    "reference_threshold",
    "reference_status",
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=lambda value: value.item()
            if isinstance(value, np.generic)
            else str(value),
        ),
        encoding="utf-8",
    )


def _external_card_id(event_id: str, service_id: str) -> str:
    value = f"rcaeval-re1-ob|{event_id}|{service_id}|latency"
    return f"external-{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def validate_external_split_manifest(manifest: dict[str, Any]) -> None:
    splits = {
        name: set(str(value) for value in manifest[f"{name}_event_ids"])
        for name in ("train", "validation", "test")
    }
    if any(splits[left] & splits[right] for left, right in (
        ("train", "validation"),
        ("train", "test"),
        ("validation", "test"),
    )):
        raise AssertionError("external event appears in more than one split")
    combined = set().union(*splits.values())
    if len(combined) != sum(len(values) for values in splits.values()):
        raise AssertionError("external split manifest contains duplicate events")


def audit_canonical_external_observations(
    frame: pd.DataFrame,
    *,
    dataset_id: str,
) -> dict[str, Any]:
    missing = sorted(set(EXTERNAL_OBSERVATION_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"external observations missing columns: {missing}")
    if frame.empty:
        raise ValueError("external observations are empty")
    timestamps = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="coerce")
    faults = pd.to_datetime(frame["fault_start"], utc=True, errors="coerce")
    service_ids = frame["service_id"].astype(str)
    latency = pd.to_numeric(frame["latency_ms"], errors="coerce")
    checks = {
        "dataset_id_constant": set(frame["dataset_id"].astype(str)) == {dataset_id},
        "service_ids_namespaced": bool(
            service_ids.str.startswith(f"{dataset_id}:").all()
        ),
        "timestamps_parse_as_utc": bool(timestamps.notna().all()),
        "fault_starts_parse_as_utc": bool(faults.notna().all()),
        "latency_nonnegative_when_present": bool((latency.dropna() >= 0).all()),
        "unique_event_service_timestamp": bool(
            not frame.duplicated(
                ["event_id", "service_id", "timestamp_utc"]
            ).any()
        ),
    }
    return {
        "rows": int(len(frame)),
        "events": int(frame["event_id"].nunique()),
        "services": int(frame["service_id"].nunique()),
        "time_range_start": timestamps.min().isoformat(),
        "time_range_end": timestamps.max().isoformat(),
        "checks": checks,
        "pass": bool(all(checks.values())),
    }


def _rcaeval_case_metadata(case_file: Path, source_root: Path) -> dict[str, Any]:
    relative = case_file.parent.relative_to(source_root)
    fault_group = relative.parts[0]
    repetition = relative.parts[1]
    event_id = f"rcaeval-re1-ob:{fault_group}:{repetition}"
    fault_start = int((case_file.parent / "inject_time.txt").read_text().strip())
    return {
        "event_id": event_id,
        "fault_group": fault_group,
        "repetition": repetition,
        "fault_start": fault_start,
    }


def _rcaeval_latency_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(
        column
        for column in frame.columns
        if column.endswith("_latency") or column.endswith("_latency-90")
    )


def adapt_rcaeval_re1_ob(
    source_root: str | Path,
    output_dir: str | Path,
    *,
    frozen_archive_sha256: str,
) -> dict[str, Any]:
    """Normalize RCAEval RE1-OB for validity-only governance replication.

    RCAEval does not independently document the latency unit in the released
    table. Values are converted from the source-native latency magnitude under
    an explicit seconds-to-milliseconds assumption and are never compared
    numerically across datasets.
    """

    source = Path(source_root)
    case_files = sorted(source.rglob("data.csv"))
    if len(case_files) < 100:
        raise ValueError("RCAEval RE1-OB must contain at least 100 cases")
    output = Path(output_dir)
    rows: list[dict[str, Any]] = []
    event_metadata: list[dict[str, Any]] = []
    source_schemas: Counter[tuple[str, ...]] = Counter()
    for case_file in case_files:
        metadata = _rcaeval_case_metadata(case_file, source)
        raw = pd.read_csv(case_file)
        source_schemas[tuple(raw.columns)] += 1
        time_column = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
        timestamps = pd.to_datetime(time_column, unit="s", utc=True, errors="coerce")
        latency_columns = _rcaeval_latency_columns(raw)
        if not latency_columns:
            raise ValueError(f"no latency columns in {case_file}")
        event_metadata.append(
            {
                **metadata,
                "start": int(time_column.min()),
                "end": int(time_column.max()),
                "pre_seconds": int(metadata["fault_start"] - time_column.min()),
                "post_seconds": int(time_column.max() - metadata["fault_start"]),
                "latency_columns": len(latency_columns),
            }
        )
        minute = timestamps.dt.floor("min")
        fault_iso = pd.to_datetime(
            metadata["fault_start"], unit="s", utc=True
        ).isoformat()
        for latency_column in latency_columns:
            service = latency_column.removesuffix("-90").removesuffix("_latency")
            values = pd.to_numeric(raw[latency_column], errors="coerce")
            minute_values = (
                pd.DataFrame({"minute": minute, "latency": values})
                .dropna()
                .groupby("minute", sort=True)["latency"]
                .median()
            )
            for timestamp, value in minute_values.items():
                rows.append(
                    {
                        "dataset_id": "rcaeval-re1-ob",
                        "event_id": metadata["event_id"],
                        "service_id": f"rcaeval-re1-ob:{service}",
                        "timestamp_utc": timestamp.isoformat(),
                        "request_count": np.nan,
                        "latency_ms": float(value) * 1000.0,
                        "error_count": np.nan,
                        "fault_start": fault_iso,
                        "reference_threshold": np.nan,
                        "reference_status": "absent",
                    }
                )
    observations = pd.DataFrame(rows, columns=EXTERNAL_OBSERVATION_COLUMNS)
    observation_audit = audit_canonical_external_observations(
        observations, dataset_id="rcaeval-re1-ob"
    )
    if not observation_audit["pass"]:
        raise AssertionError("canonical external observation audit failed")
    observations_path = output / "canonical_observations.csv.gz"
    output.mkdir(parents=True, exist_ok=True)
    observations.to_csv(observations_path, index=False, compression="gzip")

    ordered_events = [
        row["event_id"]
        for row in sorted(event_metadata, key=lambda item: (item["fault_start"], item["event_id"]))
    ]
    train_end = int(len(ordered_events) * 0.6)
    validation_end = int(len(ordered_events) * 0.8)
    split_manifest = {
        "protocol": ACADEMIC_PROTOCOL,
        "dataset_id": "rcaeval-re1-ob",
        "split_strategy": "chronological_60_20_20_by_fault_start",
        "train_event_ids": ordered_events[:train_end],
        "validation_event_ids": ordered_events[train_end:validation_end],
        "test_event_ids": ordered_events[validation_end:],
    }
    validate_external_split_manifest(split_manifest)
    _write_json(output / "split_manifest.json", split_manifest)

    cards: list[dict[str, Any]] = []
    for (event_id, service_id), group in observations.groupby(
        ["event_id", "service_id"], sort=True
    ):
        fault_start = pd.to_datetime(group["fault_start"].iloc[0], utc=True)
        timestamps = pd.to_datetime(group["timestamp_utc"], utc=True)
        history = pd.to_numeric(
            group.loc[timestamps < fault_start, "latency_ms"], errors="coerce"
        ).dropna()
        if history.empty:
            continue
        q95 = float(history.quantile(0.95))
        median = float(history.median())
        mad = float(np.median(np.abs(history.to_numpy() - median)))
        robust_scale = max(1.4826 * mad, 1e-6)
        threshold = max(q95, median + 3.0 * robust_scale)
        source_card = {
            "card_id": _external_card_id(str(event_id), str(service_id)),
            "subject_id": str(service_id),
            "sli": "latency",
            "valid_samples": int(len(history)),
            "quantile_value": q95,
            "median": median,
            "mad": mad,
            "robust_scale": robust_scale,
            "threshold": threshold,
            "quality": {"history_coverage": min(1.0, len(history) / 1440.0)},
        }
        decision = academic_governance_decision(source_card)
        cards.append(
            {
                **source_card,
                "dataset_id": "rcaeval-re1-ob",
                "event_id": str(event_id),
                "raw_threshold": threshold,
                "evidence_status": decision.evidence_status,
                "governance_flags": list(decision.governance_flags),
                "recommended_next_action": decision.recommended_next_action,
                "reference_status": "absent",
                "business_slo_approved": False,
                "measurement_resolution": "minute_aggregate_of_one_second_source_samples",
                "latency_unit_conversion": {
                    "source_unit": "source_native_assumed_seconds",
                    "target_unit": "milliseconds",
                    "multiplier": 1000.0,
                    "independently_documented": False,
                },
            }
        )
    _write_json(output / "governance_cards.json", cards)
    status_counts = Counter(card["evidence_status"] for card in cards)
    governance_summary = {
        "protocol": ACADEMIC_PROTOCOL,
        "dataset_id": "rcaeval-re1-ob",
        "intended_role": "external_test_only",
        "validation_role": "validity_only",
        "cards": len(cards),
        "status_counts": {
            status: int(status_counts.get(status, 0))
            for status in GOVERNANCE_STATUSES
        },
        "raw_threshold_formula_changed": False,
        "history_window_shortened": False,
        "reference_slo_available": False,
        "threshold_correctness_claim_allowed": False,
    }
    _write_json(output / "governance_summary.json", governance_summary)

    metadata_frame = pd.DataFrame(event_metadata)
    adapter_audit = {
        "protocol": ACADEMIC_PROTOCOL,
        "dataset_id": "rcaeval-re1-ob",
        "source_cases": len(case_files),
        "source_schema_variants": len(source_schemas),
        "frozen_archive_sha256": frozen_archive_sha256.lower(),
        "source_sampling_resolution_seconds": 1,
        "pre_event_seconds": {
            "minimum": int(metadata_frame["pre_seconds"].min()),
            "median": float(metadata_frame["pre_seconds"].median()),
            "maximum": int(metadata_frame["pre_seconds"].max()),
        },
        "post_event_seconds": {
            "minimum": int(metadata_frame["post_seconds"].min()),
            "median": float(metadata_frame["post_seconds"].median()),
            "maximum": int(metadata_frame["post_seconds"].max()),
        },
        "canonical_observations": observation_audit,
        "unit_conversion_status": "assumed_seconds_to_milliseconds_not_independently_documented",
        "request_count_available": False,
        "error_count_available": False,
        "prediction_protocol_compatible": False,
        "prediction_incompatibility_reasons": [
            "no case provides the frozen 24-hour history window",
            "many cases provide only six minutes before and after injection",
            "request counts are unavailable",
        ],
        "decision": "validity_only",
        "claim_limit": (
            "The external result may demonstrate adapter and refusal-governance portability; "
            "it cannot validate threshold correctness or the internal prediction protocol."
        ),
    }
    _write_json(output / "adapter_audit.json", adapter_audit)
    return {
        "observations": str(observations_path.resolve()),
        "split_manifest": str((output / "split_manifest.json").resolve()),
        "governance_cards": str((output / "governance_cards.json").resolve()),
        "governance_summary": str((output / "governance_summary.json").resolve()),
        "adapter_audit": str((output / "adapter_audit.json").resolve()),
        **adapter_audit,
    }
