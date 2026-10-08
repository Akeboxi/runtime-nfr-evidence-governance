"""Reconstruct telemetry and run the prespecified Runtime-NFR robustness grid."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from slo_evidence import Policy

from src.data.runtime_nfr_dataset import (
    _collect_event_intervals,
    _exclude_overlaps,
    _metric_dir,
    _read_segment_layout,
    fit_nfr_boundary,
    load_app_sli_frame,
)
from src.data.runtime_nfr_v3_academic import GOVERNANCE_STATUSES


WINDOW_HOURS = (12, 18, 24)
COVERAGE_MIN = (0.40, 0.50, 0.60, 0.75)
TAIL_RATIO = (8.0, 9.5, 10.0, 10.5, 12.0)
QUANTILES = (0.90, 0.95, 0.99)
ROBUST_K = (2.5, 3.0, 3.5)
SCALE_FLOOR = 1e-6
FROZEN = {"window_hours": 24, "coverage_min": 0.50, "tail_ratio": 10.0, "quantile": 0.95, "robust_k": 3.0}


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def reconstruct_statistics(
    cards: list[dict[str, Any]], segment_root: Path
) -> pd.DataFrame:
    layouts = _read_segment_layout(segment_root)
    intervals = _collect_event_intervals(layouts)
    tenant_by_segment = {
        str(meta.get("segment_id") or tenant.name.rsplit("_", 1)[-1]): tenant
        for tenant, meta, _, _ in layouts
    }
    frame_cache: dict[Path, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []

    for index, card in enumerate(cards, start=1):
        segment_id = str(card["observation_context"]["segment_id"])
        tenant = tenant_by_segment[segment_id]
        record_id = str(card["evidence_provenance"]["record_id"])
        metrics_dir = _metric_dir(tenant, tenant / "records" / record_id)
        app_path = metrics_dir / "app" / f"{card['subject_id']}.csv"
        if app_path not in frame_cache:
            loaded = (
                load_app_sli_frame(app_path)
                if app_path.exists()
                else pd.DataFrame(columns=["request", "latency", "error_ratio", "availability"])
            )
            # Overlap exclusion is invariant across cards and windows for a
            # service-level source frame. Apply it once per physical CSV rather
            # than rebuilding the same boolean mask for every card/window.
            frame_cache[app_path] = _exclude_overlaps(
                loaded, app_id=str(card["subject_id"]), intervals=intervals
            )
        frame = frame_cache[app_path]
        history_end = pd.Timestamp(card["history_end"]).floor("min")
        for window_hours in WINDOW_HOURS:
            history_start = history_end - pd.Timedelta(hours=window_hours)
            history = frame.loc[(frame.index >= history_start) & (frame.index < history_end)]
            values = history[str(card["sli"])]
            for quantile in QUANTILES:
                boundary = fit_nfr_boundary(
                    values,
                    app_id=str(card["subject_id"]),
                    sli=str(card["sli"]),
                    history_start=history_start.to_pydatetime(),
                    history_end=history_end.to_pydatetime(),
                    quantile=quantile,
                )
                rows.append(
                    {
                        "card_id": card["card_id"],
                        "event_id": card["evidence_provenance"]["event_id"],
                        "subject_id": card["subject_id"],
                        "sli": card["sli"],
                        "window_hours": window_hours,
                        "quantile": quantile,
                        "valid_samples": boundary.valid_samples,
                        "coverage": min(1.0, boundary.valid_samples / (window_hours * 60)),
                        "quantile_value": boundary.quantile_value,
                        "median": boundary.median,
                        "mad": boundary.mad,
                        "robust_scale": boundary.robust_scale,
                        "nonzero_count": boundary.nonzero_count,
                        "all_zero": boundary.all_zero,
                        "max_value": boundary.max_value,
                    }
                )
        if index % 500 == 0:
            print(f"reconstructed {index}/{len(cards)} cards", flush=True)
    return pd.DataFrame(rows)


def route_rows(frame: pd.DataFrame, coverage_min: float, tail_ratio: float, robust_k: float) -> np.ndarray:
    window_minutes = frame["window_hours"].to_numpy(dtype=int) * 60
    valid_samples = frame["valid_samples"].to_numpy(dtype=int)
    coverage = frame["coverage"].to_numpy(dtype=float)
    sli = frame["sli"].to_numpy(dtype=str)
    qvalue = frame["quantile_value"].to_numpy(dtype=float)
    median = frame["median"].to_numpy(dtype=float)
    mad = frame["mad"].to_numpy(dtype=float)
    scale = frame["robust_scale"].to_numpy(dtype=float)
    tied_minimum = np.asarray([Policy(int(n), coverage_min).minimum_samples for n in window_minutes])
    history = (coverage < coverage_min) | (valid_samples < tied_minimum)

    raw_threshold = np.maximum(qvalue, median + robust_k * scale)
    all_zero_error = (
        (sli == "error_ratio")
        & (valid_samples > 0)
        & (frame["nonzero_count"].to_numpy(dtype=int) == 0)
    )
    raw_threshold[all_zero_error] = 0.0
    floor_threshold = median + robust_k * SCALE_FLOOR
    floor_derived = (
        (sli == "error_ratio")
        & np.isclose(mad, 0.0)
        & (qvalue <= floor_threshold + 1e-15)
        & np.isclose(raw_threshold, np.maximum(qvalue, floor_threshold), rtol=0.0, atol=1e-15)
    )
    resolution = all_zero_error | floor_derived
    ratio = (qvalue - median) / np.maximum(scale, SCALE_FLOOR)
    tail = (sli == "latency") & (ratio >= tail_ratio)

    status = np.full(len(frame), "candidate_for_stakeholder_review", dtype=object)
    status[tail] = "needs_context_review"
    status[resolution] = "threshold_unresolved"
    status[history] = "insufficient_evidence"
    return status


def config_key(window_hours: int, coverage_min: float, tail_ratio: float, quantile: float, robust_k: float) -> str:
    return f"W{window_hours}_C{coverage_min:g}_Rt{tail_ratio:g}_q{quantile:g}_k{robust_k:g}"


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-root", default="dataset/outputs/topo_intent_v2/segment_tenants_all")
    parser.add_argument("--cards", default="checkpoints/runtime_nfr_v3_academic/governance_cards/academic_governance_cards.json")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    segment_root = Path(args.segment_root)
    cards_path = Path(args.cards)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    source_hash_before = file_sha256(cards_path)
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    if len(cards) != 8668:
        raise AssertionError(f"expected 8668 frozen cards, got {len(cards)}")

    stats = reconstruct_statistics(cards, segment_root)
    stats.to_csv(output / "reconstructed_statistics.csv.gz", index=False, compression="gzip")
    frozen_status = np.asarray([card["evidence_status"] for card in cards], dtype=object)
    frozen_ids = np.asarray([card["card_id"] for card in cards], dtype=object)
    frozen_candidate = set(frozen_ids[frozen_status == "candidate_for_stakeholder_review"])
    results: list[dict[str, Any]] = []

    for window_hours, coverage_min, tail_ratio, quantile, robust_k in itertools.product(
        WINDOW_HOURS, COVERAGE_MIN, TAIL_RATIO, QUANTILES, ROBUST_K
    ):
        subset = stats.loc[
            (stats["window_hours"] == window_hours)
            & np.isclose(stats["quantile"], quantile)
        ].copy()
        subset = subset.set_index("card_id").loc[frozen_ids].reset_index()
        observed = route_rows(subset, coverage_min, tail_ratio, robust_k)
        counts = Counter(observed)
        candidate_ids = set(frozen_ids[observed == "candidate_for_stakeholder_review"])
        union = frozen_candidate | candidate_ids
        intersection = frozen_candidate & candidate_ids
        migration = float(np.mean(observed != frozen_status))
        row = {
            "config_id": config_key(window_hours, coverage_min, tail_ratio, quantile, robust_k),
            "window_hours": window_hours,
            "coverage_min": coverage_min,
            "valid_samples_min": Policy(window_hours * 60, coverage_min).minimum_samples,
            "tail_ratio": tail_ratio,
            "quantile": quantile,
            "robust_k": robust_k,
            **{status: int(counts.get(status, 0)) for status in GOVERNANCE_STATUSES},
            **{f"proportion_{status}": counts.get(status, 0) / len(cards) for status in GOVERNANCE_STATUSES},
            "migration_count": int(np.sum(observed != frozen_status)),
            "migration_rate": migration,
            "stakeholder_review_jaccard": len(intersection) / len(union) if union else 1.0,
            "non_numeric_disposition_count": int(len(cards) - counts.get("candidate_for_stakeholder_review", 0)),
            "non_numeric_disposition_rate": 1.0 - counts.get("candidate_for_stakeholder_review", 0) / len(cards),
        }
        if sum(row[status] for status in GOVERNANCE_STATUSES) != len(cards):
            raise AssertionError(f"state counts do not sum for {row['config_id']}")
        results.append(row)

    write_csv(output / "robustness_grid.csv", results)
    result_frame = pd.DataFrame(results)
    frozen_mask = (
        (result_frame["window_hours"] == FROZEN["window_hours"])
        & np.isclose(result_frame["coverage_min"], FROZEN["coverage_min"])
        & np.isclose(result_frame["tail_ratio"], FROZEN["tail_ratio"])
        & np.isclose(result_frame["quantile"], FROZEN["quantile"])
        & np.isclose(result_frame["robust_k"], FROZEN["robust_k"])
    )
    frozen_row = result_frame.loc[frozen_mask].iloc[0].to_dict()
    if int(frozen_row["migration_count"]) != 0 or not math.isclose(float(frozen_row["stakeholder_review_jaccard"]), 1.0):
        raise AssertionError(f"frozen configuration did not reproduce frozen routes: {frozen_row}")

    one_factor = result_frame.loc[
        ((result_frame["coverage_min"] == 0.50) & (result_frame["tail_ratio"] == 10.0) & (result_frame["quantile"] == 0.95) & (result_frame["robust_k"] == 3.0))
        | ((result_frame["window_hours"] == 24) & (result_frame["coverage_min"] == 0.50) & (result_frame["tail_ratio"] == 10.0) & (result_frame["robust_k"] == 3.0))
        | ((result_frame["window_hours"] == 24) & (result_frame["coverage_min"] == 0.50) & (result_frame["tail_ratio"] == 10.0) & (result_frame["quantile"] == 0.95))
    ].drop_duplicates("config_id")
    one_factor.to_csv(output / "one_factor_robustness.csv", index=False, encoding="utf-8-sig")

    nonnumeric = result_frame["non_numeric_disposition_rate"]
    summary = {
        "protocol": "runtime-nfr-protocol-robustness/1-corrected-20260929",
        "population_cards": len(cards),
        "configurations": len(results),
        "frozen_configuration": frozen_row,
        "frozen_route_reproduction": {
            "matched": len(cards),
            "total": len(cards),
            "pass": True,
        },
        "migration_rate": {
            "minimum": float(result_frame["migration_rate"].min()),
            "median": float(result_frame["migration_rate"].median()),
            "maximum": float(result_frame["migration_rate"].max()),
        },
        "stakeholder_review_jaccard": {
            "minimum": float(result_frame["stakeholder_review_jaccard"].min()),
            "median": float(result_frame["stakeholder_review_jaccard"].median()),
            "maximum": float(result_frame["stakeholder_review_jaccard"].max()),
        },
        "non_numeric_disposition_rate": {
            "minimum": float(nonnumeric.min()),
            "median": float(nonnumeric.median()),
            "maximum": float(nonnumeric.max()),
        },
        "maximum_migration_configurations": result_frame.loc[
            result_frame["migration_rate"] == result_frame["migration_rate"].max(), "config_id"
        ].tolist(),
        "claim_limit": (
            "Descriptive configuration robustness only; no parameter is selected and card-level "
            "rows are not treated as independent inferential observations."
        ),
    }
    json_write(output / "robustness_summary.json", summary)
    report = f"""# Runtime-NFR configuration robustness results

- The frozen configuration reproduced all {len(cards):,}/{len(cards):,} routes.
- {len(results)} prespecified configurations were evaluated.
- Route migration from the frozen configuration ranged from {summary['migration_rate']['minimum']:.1%} to {summary['migration_rate']['maximum']:.1%} (median {summary['migration_rate']['median']:.1%}).
- Stakeholder-review-set Jaccard similarity ranged from {summary['stakeholder_review_jaccard']['minimum']:.3f} to {summary['stakeholder_review_jaccard']['maximum']:.3f} (median {summary['stakeholder_review_jaccard']['median']:.3f}).
- Non-numeric disposition proportion ranged from {summary['non_numeric_disposition_rate']['minimum']:.1%} to {summary['non_numeric_disposition_rate']['maximum']:.1%} (median {summary['non_numeric_disposition_rate']['median']:.1%}).

These ranges describe sensitivity of the versioned routing policy to plausible configuration choices. They do not identify an optimal setting or establish cross-organizational validity.
"""
    (output / "RESULTS.md").write_text(report, encoding="utf-8")
    manifest = {
        "protocol": summary["protocol"],
        "analysis_plan": str((output / "ANALYSIS_PLAN.md").resolve()),
        "cards_path": str(cards_path.resolve()),
        "cards_sha256_before": source_hash_before,
        "cards_sha256_after": file_sha256(cards_path),
        "source_cards_unchanged": source_hash_before == file_sha256(cards_path),
        "segment_root": str(segment_root.resolve()),
        "script_path": str(Path(__file__).resolve()),
        "script_sha256": file_sha256(Path(__file__)),
        "outputs": [
            "reconstructed_statistics.csv.gz",
            "robustness_grid.csv",
            "one_factor_robustness.csv",
            "robustness_summary.json",
            "RESULTS.md",
        ],
    }
    json_write(output / "MANIFEST.json", manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
