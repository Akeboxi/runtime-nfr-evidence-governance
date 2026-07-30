"""Command-line workflow for the runtime NFR paper protocol."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import argparse
import json
import subprocess
from typing import Any

import numpy as np
import pandas as pd

from .data.runtime_nfr_dataset import (
    RuntimeNFRDataset,
    RuntimeNFRSample,
    boundary_to_dict,
    chronological_runtime_split,
    load_runtime_nfr_dataset,
    topology_runtime_folds,
)
from .data.runtime_nfr_v2 import (
    ExperimentProtocol,
    RUNTIME_NFR_V2_PROTOCOL,
    _traffic_quartiles,
    build_boundary_cards,
    matched_non_event_control_audit,
    relation_diversity_audit,
    select_blinded_expert_cards,
    summarize_expert_ratings,
    target_mapping_audit,
    topology_evidence_for_sample,
    topology_residual_audit,
    write_boundary_card_package,
)
from .data.runtime_nfr_v3_academic import (
    audit_external_dataset_manifest,
    build_round2_expert_package,
    finalize_first_round_expert_review,
    summarize_round2_expert_review,
    write_academic_governance_cards,
)
from .data.runtime_nfr_external import adapt_rcaeval_re1_ob
from .data.runtime_nfr_paper_tables import (
    build_matched_control_manual_audit_sample,
    build_runtime_nfr_paper_tables,
)
from .data.runtime_nfr_paper_artifacts import build_runtime_nfr_paper_artifacts
from .data.runtime_nfr_integrity import freeze_or_audit_legacy_integrity
from .data.runtime_nfr_v3_audit import audit_runtime_nfr_v3_formal_results
from .data.runtime_nfr_manual_audit import (
    build_matched_control_audit_package,
    summarize_manual_audit_results,
)
from .models.runtime_nfr_model import RuntimeNFRPredictor
from .models.runtime_nfr_v2_model import TopologyGatedResidualPredictor
from .training.runtime_nfr_trainer import (
    BASELINE_MODELS,
    NEURAL_MODELS,
    RuntimeNFRBaselineTrainer,
    RuntimeNFRNeuralTrainer,
    evaluate_predictions,
)
from .training.runtime_nfr_v3_ablation import run_feature_ablation


DEFAULT_SEGMENT_ROOT = "dataset/outputs/topo_intent_v2/segment_tenants_all"
RUNTIME_NFR_V1_PROTOCOL = "runtime_nfr_v1"


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def runtime_nfr_data_hash(segment_root: str | Path) -> str:
    """Stable signature of event metadata, graph layout, and metric inventory."""

    root = Path(segment_root)
    digest = sha256()
    patterns = (
        "tenant_*/segment_meta.json",
        "tenant_*/graph/nodes/*.csv",
        "tenant_*/graph/edges/*.csv",
        "tenant_*/records/record_*/global_label.json",
        "tenant_*/_staging/metrics/app/*.csv",
        "tenant_*/_staging/metrics/host/*.csv",
        "tenant_*/_staging/metrics/vm/*.csv",
    )
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(root.glob(pattern))
    for path in sorted(set(paths)):
        stat = path.stat()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        # Small semantic/configuration files are content-hashed; large metric
        # CSVs use inventory metadata to keep preparation practical.
        if stat.st_size <= 1_000_000 or "global_label.json" in path.as_posix():
            digest.update(path.read_bytes())
        else:
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def _dataset_from_args(args: argparse.Namespace) -> RuntimeNFRDataset:
    return load_runtime_nfr_dataset(
        args.segment_root,
        history_minutes=args.history_minutes,
        pre_minutes=args.pre_minutes,
        observation_minutes=args.observation_minutes,
        horizon_minutes=args.horizon_minutes,
        quantile=args.boundary_quantile,
        persistence=args.persistence_minutes,
        min_history_minutes=args.min_history_minutes,
        min_early_minutes=args.min_early_minutes,
        min_future_minutes=args.min_future_minutes,
        max_events=args.max_events,
    )


def _split(
    dataset: RuntimeNFRDataset,
    split: str,
    topology_fold: int,
) -> tuple[list[RuntimeNFRSample], list[RuntimeNFRSample], list[RuntimeNFRSample]]:
    if split == "chronological":
        return chronological_runtime_split(dataset.samples)
    folds = topology_runtime_folds(dataset.samples)
    if topology_fold < 0 or topology_fold >= len(folds):
        raise ValueError(f"topology_fold must be in [0, {len(folds) - 1}]")
    train_indices, test_indices = folds[topology_fold]
    train_pool = sorted(
        (dataset.samples[index] for index in train_indices),
        key=lambda sample: sample.metadata.event_start,
    )
    val_size = max(1, int(len(train_pool) * 0.2))
    return train_pool[:-val_size], train_pool[-val_size:], [dataset.samples[index] for index in test_indices]


def _split_manifest(
    train: list[RuntimeNFRSample],
    val: list[RuntimeNFRSample],
    test: list[RuntimeNFRSample],
    *,
    split: str,
    topology_fold: int,
) -> dict[str, Any]:
    ids = {
        "train": [sample.metadata.event_id for sample in train],
        "val": [sample.metadata.event_id for sample in val],
        "test": [sample.metadata.event_id for sample in test],
    }
    for left, right in (("train", "val"), ("train", "test"), ("val", "test")):
        if set(ids[left]) & set(ids[right]):
            raise AssertionError(f"event leakage between {left} and {right}")
    return {
        "split": split,
        "topology_fold": topology_fold if split == "topology" else None,
        "train_event_ids": ids["train"],
        "val_event_ids": ids["val"],
        "test_event_ids": ids["test"],
        "counts": {key: len(value) for key, value in ids.items()},
        "time_boundaries": {
            key: max(sample.metadata.event_start for sample in samples).isoformat()
            for key, samples in (("train", train), ("val", val), ("test", test))
            if samples
        },
    }


def _flatten_valid(samples: list[RuntimeNFRSample], attribute: str) -> np.ndarray:
    values = []
    for sample in samples:
        tensor = getattr(sample, attribute)
        values.extend(tensor[sample.label_mask.bool()].detach().cpu().numpy().tolist())
    return np.asarray(values, dtype=float)


def _paired_control_audit(samples: list[RuntimeNFRSample], persistence: int, seed: int = 42) -> dict[str, float]:
    future, control = [], []
    for sample in samples:
        early_steps = max(1, int((sample.metadata.input_end - sample.metadata.event_minute).total_seconds() // 60))
        pre = sample.features["Vbiz"][-(early_steps + 15):-early_steps]
        pre_mask = sample.masks["Vbiz"][-(early_steps + 15):-early_steps]
        pre_bad = (((pre[:, :, 4] * pre_mask[:, :, 4]) + (pre[:, :, 5] * pre_mask[:, :, 5])) > 0).sum(dim=0) >= persistence
        valid = sample.label_mask.bool()
        future.extend(sample.breach_labels[valid].tolist())
        control.extend(pre_bad[valid].float().tolist())
    future_array = np.asarray(future, dtype=float)
    control_array = np.asarray(control, dtype=float)
    difference = future_array - control_array
    rng = np.random.default_rng(seed)
    boot = [float(difference[rng.integers(0, difference.size, difference.size)].mean()) for _ in range(2000)] if difference.size else [0.0]
    return {
        "future_breach_rate": float(future_array.mean()) if future_array.size else 0.0,
        "matched_pre_event_rate": float(control_array.mean()) if control_array.size else 0.0,
        "paired_rate_difference": float(difference.mean()) if difference.size else 0.0,
        "bootstrap_lower": float(np.quantile(boot, 0.025)),
        "bootstrap_upper": float(np.quantile(boot, 0.975)),
    }


def _sensitivity_audit(samples: list[RuntimeNFRSample]) -> dict[str, Any]:
    primary, correlations = [], {}
    for sample in samples:
        primary.extend(sample.severity_raw[sample.label_mask.bool()].tolist())
    primary_array = np.asarray(primary, dtype=float)
    for key in ("q90_p3", "q99_p3", "q95_p1", "q95_p5"):
        alternative = []
        for sample in samples:
            alternative.extend(sample.sensitivity_targets[key].severity_raw[sample.label_mask.bool()].tolist())
        alternative_array = np.asarray(alternative, dtype=float)
        if primary_array.size < 2 or np.std(primary_array) == 0 or np.std(alternative_array) == 0:
            value = 0.0
        else:
            left_rank = primary_array.argsort().argsort().astype(float)
            right_rank = alternative_array.argsort().argsort().astype(float)
            value = float(np.corrcoef(left_rank, right_rank)[0, 1])
        correlations[key] = value
    return {"severity_spearman_vs_primary": correlations, "minimum_adjacent_spearman": min(correlations.values())}


def _v2_protocol(
    dataset: RuntimeNFRDataset,
    *,
    data_hash: str,
    output_namespace: str,
) -> ExperimentProtocol:
    train, val, test = chronological_runtime_split(dataset.samples)
    development = [*train, *val]
    return ExperimentProtocol(
        protocol=RUNTIME_NFR_V2_PROTOCOL,
        parent_protocol=RUNTIME_NFR_V1_PROTOCOL,
        registered_at=datetime.now(),
        dataset_hash=data_hash,
        test_exposure_status=(
            "legacy v1 holdout outcomes have already been observed; v2 confirmation uses "
            "development-only rolling/topology OOF evidence"
        ),
        development_event_ids=tuple(sample.metadata.event_id for sample in development),
        legacy_holdout_event_ids=tuple(sample.metadata.event_id for sample in test),
        confirmatory_claims=(
            "candidate NFR specifications are measurable and traceable",
            "event windows breach candidate boundaries more often than matched non-event windows",
            "early App telemetry supports later App breach ranking under the frozen v1 protocol",
        ),
        exploratory_claims=(
            "topology has conditional residual value when coverage and observability gates pass",
        ),
        forbidden_model_inputs=("fault_type", "target_node_ids", "duration", "impact_score"),
        primary_metrics=("PR-AUC", "Recall@3", "NDCG@3"),
        graph_gate={
            "event_any_target_mapping_coverage_min": 0.80,
            "positive_rolling_folds_min": 4,
            "folds": 5,
            "pr_auc_delta_ci_lower_min": 0.0,
            "recall_at_3_delta_ci_lower_min": -0.02,
        },
        output_namespace=output_namespace,
        notes=(
            "The legacy holdout is secondary corroboration, not a fully blind v2 test.",
            "Expert review cannot change status to stakeholder_approved.",
        ),
    )


def export_nfr_boundary_cards_command(args: argparse.Namespace) -> None:
    dataset = _dataset_from_args(args)
    output = Path(args.output_dir)
    data_hash = runtime_nfr_data_hash(args.segment_root)
    protocol = _v2_protocol(dataset, data_hash=data_hash, output_namespace=str(output.resolve()))
    cards = build_boundary_cards(
        dataset.samples,
        dataset_hash=data_hash,
        persistence_minutes=args.persistence_minutes,
        history_minutes=args.history_minutes,
    )
    expert_cards, private_manifest = select_blinded_expert_cards(
        dataset.samples,
        cards,
        count=args.expert_card_count,
        seed=args.seed,
    )
    paths = write_boundary_card_package(
        output,
        cards=cards,
        expert_cards=expert_cards,
        private_manifest=private_manifest,
    )
    _json_write(output / "experiment_protocol.json", protocol.to_dict())
    summary = {
        "protocol": RUNTIME_NFR_V2_PROTOCOL,
        "cards": len(cards),
        "expert_cards": len(expert_cards),
        "status_counts": dict(Counter(card.status for card in cards)),
        "expert_packet_is_blinded": True,
        "future_labels_in_expert_packet": False,
        "paths": paths,
    }
    _json_write(output / "boundary_card_export_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def audit_runtime_nfr_validity_command(args: argparse.Namespace) -> None:
    dataset = _dataset_from_args(args)
    train, val, test = chronological_runtime_split(dataset.samples)
    development = [*train, *val]
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    protocol = _v2_protocol(
        dataset,
        data_hash=runtime_nfr_data_hash(args.segment_root),
        output_namespace=str(output.resolve()),
    )
    controls = (
        {"status": "skipped_by_explicit_flag"}
        if args.skip_matched_controls
        else matched_non_event_control_audit(
            dataset.samples,
            segment_root=args.segment_root,
            history_minutes=args.history_minutes,
            observation_minutes=args.observation_minutes,
            horizon_minutes=args.horizon_minutes,
            quantile=args.boundary_quantile,
            persistence=args.persistence_minutes,
            min_history_minutes=args.min_history_minutes,
            min_future_minutes=args.min_future_minutes,
            controls_per_pair=args.controls_per_pair,
            buffer_minutes=args.event_buffer_minutes,
            bootstrap_iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
    )
    audit = {
        "protocol": RUNTIME_NFR_V2_PROTOCOL,
        "generated_at": datetime.now().isoformat(),
        "test_exposure_status": protocol.test_exposure_status,
        "sensitivity": {
            "development": _sensitivity_audit(development),
            "legacy_holdout_post_lock": _sensitivity_audit(test),
        },
        "matched_non_event_controls": {
            key: value
            for key, value in controls.items()
            if key not in {"rows", "available_control_rows"}
        },
        "cluster_unit_main": "event_id",
        "cluster_sensitivities": ["event_date", "segment_id"],
        "multiple_comparison_policy": "prespecified subgroups; other analyses are exploratory with CIs",
    }
    if "event_cluster_bootstrap" in controls:
        audit["construct_validity_gate"] = {
            "matched_coverage_positive": controls["matched_app_event_pairs"] > 0,
            "event_cluster_ci_lower_above_zero": controls["event_cluster_bootstrap"]["lower"] > 0.0,
            "all_cluster_sensitivity_ci_lowers_above_zero": all(
                controls[key]["lower"] > 0.0
                for key in ("event_cluster_bootstrap", "date_cluster_sensitivity", "segment_cluster_sensitivity")
            ),
            "development_sensitivity_at_least_0_60": audit["sensitivity"]["development"]["minimum_adjacent_spearman"] >= 0.60,
        }
        audit["construct_validity_gate"]["pass"] = all(audit["construct_validity_gate"].values())
    _json_write(output / "experiment_protocol.json", protocol.to_dict())
    _json_write(output / "validity_audit.json", audit)
    if isinstance(controls, dict) and "rows" in controls:
        _json_write(output / "matched_control_rows.json", controls["rows"])
        _json_write(output / "available_control_rows.json", controls["available_control_rows"])
    print(json.dumps({key: value for key, value in audit.items() if key != "matched_non_event_controls"}, indent=2, ensure_ascii=False))


def audit_runtime_nfr_topology_command(args: argparse.Namespace) -> None:
    dataset = _dataset_from_args(args)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    mapping = target_mapping_audit(dataset.samples, segment_root=args.segment_root)
    residual = topology_residual_audit(dataset.samples, n_splits=args.folds)
    diversity = relation_diversity_audit(dataset.samples)
    evidence = [topology_evidence_for_sample(sample).to_dict() for sample in dataset.samples]
    gate = {
        "mapping_coverage_at_least_0_80": mapping["event_any_target_mapping_coverage"] >= 0.80,
        "positive_direction_in_at_least_4_folds": residual["direction_gate_pass"],
    }
    gate["pass"] = all(gate.values())
    report = {
        "protocol": RUNTIME_NFR_V2_PROTOCOL,
        "generated_at": datetime.now().isoformat(),
        "target_mapping": {key: value for key, value in mapping.items() if key != "rows"},
        "relation_diversity": diversity,
        "residual_informativeness": {key: value for key, value in residual.items() if key != "oof_rows"},
        "graph_development_gate": gate,
        "decision": "allow_topology_residual_training" if gate["pass"] else "stop_graph_architecture_development",
        "claim_limit": (
            "A failed gate supports only a statement about the current static/incomplete topology representation, "
            "not a universal claim that GNNs are ineffective."
        ),
    }
    _json_write(output / "topology_gate_report.json", report)
    _json_write(output / "target_mapping_rows.json", mapping["rows"])
    _json_write(output / "topology_evidence.json", evidence)
    _json_write(output / "topology_residual_oof_predictions.json", residual["oof_rows"])
    print(json.dumps(report, indent=2, ensure_ascii=False))


def summarize_nfr_expert_review_command(args: argparse.Namespace) -> None:
    frame = pd.read_csv(args.ratings)
    summary = summarize_expert_ratings(frame)
    output = Path(args.output)
    _json_write(output, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def finalize_nfr_expert_review_command(args: argparse.Namespace) -> None:
    result = finalize_first_round_expert_review(args.replies_dir, args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def build_nfr_academic_governance_cards_command(args: argparse.Namespace) -> None:
    result = write_academic_governance_cards(args.v2_cards, args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def build_nfr_round2_expert_package_command(args: argparse.Namespace) -> None:
    traffic_quartiles = None
    if not args.skip_traffic_stratification and args.traffic_quartiles is None:
        dataset = _dataset_from_args(args)
        by_event_service = _traffic_quartiles(dataset.samples)
        academic_cards = json.loads(Path(args.academic_cards).read_text(encoding="utf-8"))
        traffic_quartiles = {
            str(card["parent_card_id"]): by_event_service[
                (str(card["evidence_provenance"]["event_id"]), str(card["subject_id"]))
            ]
            for card in academic_cards
        }
    result = build_round2_expert_package(
        args.academic_cards,
        args.first_round_per_card,
        args.output_dir,
        seed=args.seed,
        traffic_quartiles_path=args.traffic_quartiles,
        traffic_quartiles=traffic_quartiles,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def summarize_nfr_round2_expert_review_command(args: argparse.Namespace) -> None:
    result = summarize_round2_expert_review(
        args.ratings,
        args.private_manifest,
        args.output_dir,
        first_round_ratings_path=args.first_round_ratings,
        declaration_paths=args.declarations,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def audit_external_nfr_dataset_command(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = audit_external_dataset_manifest(manifest)
    result["manifest_path"] = str(manifest_path.resolve())
    _json_write(Path(args.output), result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def adapt_rcaeval_nfr_dataset_command(args: argparse.Namespace) -> None:
    result = adapt_rcaeval_re1_ob(
        args.source_root,
        args.output_dir,
        frozen_archive_sha256=args.frozen_archive_sha256,
    )
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
            default=lambda value: value.item()
            if isinstance(value, np.generic)
            else str(value),
        )
    )


def run_nfr_v3_feature_ablation_command(args: argparse.Namespace) -> None:
    dataset = _dataset_from_args(args)
    result = run_feature_ablation(
        dataset.samples,
        args.output_dir,
        n_splits=args.folds,
        bootstrap_iterations=args.bootstrap_iterations,
        seed=args.seed,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def build_nfr_v3_paper_tables_command(args: argparse.Namespace) -> None:
    result = build_runtime_nfr_paper_tables(
        args.output_dir,
        round1_summary_path=args.round1_summary,
        governance_summary_path=args.governance_summary,
        round2_summary_path=args.round2_summary,
        external_manifest_audit_path=args.external_manifest_audit,
        external_governance_summary_path=args.external_governance_summary,
        feature_ablation_summary_path=args.feature_ablation_summary,
        topology_gate_path=args.topology_gate,
        validity_audit_path=args.validity_audit,
        manual_audit_summary_path=args.manual_audit_summary,
    )
    print(json.dumps(result["claims"], indent=2, ensure_ascii=False))


def build_nfr_v3_paper_artifacts_command(args: argparse.Namespace) -> None:
    result = build_runtime_nfr_paper_artifacts(
        args.output_dir,
        governance_summary_path=args.governance_summary,
        governance_cards_path=args.governance_cards,
        round1_summary_path=args.round1_summary,
        round2_summary_path=args.round2_summary,
        external_summary_path=args.external_summary,
        feature_ablation_summary_path=args.feature_ablation_summary,
        paired_bootstrap_path=args.paired_bootstrap,
    )
    print(
        json.dumps(
            {
                "manifest": result["manifest"],
                "figure_files": len(result["figures"]),
                "cases": list(result["cases"]),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def build_nfr_v3_manual_audit_sample_command(args: argparse.Namespace) -> None:
    result = build_matched_control_manual_audit_sample(
        args.matched_rows,
        args.output_dir,
        count=args.count,
        seed=args.seed,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def build_nfr_v3_manual_audit_package_command(
    args: argparse.Namespace,
) -> None:
    dataset = _dataset_from_args(args)
    result = build_matched_control_audit_package(
        dataset.samples,
        args.sample_manifest,
        args.output_dir,
        segment_root=args.segment_root,
        history_minutes=args.history_minutes,
        observation_minutes=args.observation_minutes,
        horizon_minutes=args.horizon_minutes,
        quantile=args.boundary_quantile,
        persistence=args.persistence_minutes,
        min_history_minutes=args.min_history_minutes,
        min_future_minutes=args.min_future_minutes,
        controls_per_pair=args.controls_per_pair,
        buffer_minutes=args.event_buffer_minutes,
    )
    print(
        json.dumps(
            {
                "pairs": result["pairs"],
                "controls": result["controls"],
                "reviewer_workbook": result["reviewer_workbook"],
                "private_workbook": result["private_workbook"],
                "blinding_checks": result["blinding_checks"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def summarize_nfr_v3_manual_audit_command(args: argparse.Namespace) -> None:
    result = summarize_manual_audit_results(
        args.input_dir,
        args.private_dir,
        args.output_dir,
        template_path=args.template,
        expected_reviewers=args.expected_reviewers,
        expected_pairs=args.expected_pairs,
        expected_controls=args.expected_controls,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "completeness": result["completeness"],
                "control_conclusions": result["control_conclusions"],
                "pair_conclusions": result["pair_conclusions"],
                "unanimity": result["unanimity"],
                "summary_path": result["summary_path"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def audit_nfr_v3_legacy_integrity_command(args: argparse.Namespace) -> None:
    result = freeze_or_audit_legacy_integrity(
        args.roots,
        args.output_dir,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["pass"]:
        raise RuntimeError("frozen v1/v2 artifacts changed; inspect integrity audit")


def audit_nfr_v3_formal_results_command(args: argparse.Namespace) -> None:
    dataset = _dataset_from_args(args)
    result = audit_runtime_nfr_v3_formal_results(
        dataset.samples,
        args.output_dir,
        round2_ratings_path=args.round2_ratings,
        round2_source_hashes_path=args.round2_source_hashes,
        v2_cards_path=args.v2_cards,
        academic_cards_path=args.academic_cards,
        external_split_path=args.external_split,
        external_manifest_audit_path=args.external_manifest_audit,
        oof_predictions_path=args.oof_predictions,
        feature_ablation_summary_path=args.feature_ablation_summary,
        legacy_integrity_audit_path=args.legacy_integrity_audit,
        frozen_result_paths=args.frozen_results,
        formal_baseline_path=args.formal_baseline,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["pass"]:
        raise RuntimeError("Runtime NFR v3 formal audit failed")


def prepare_runtime_nfr_command(args: argparse.Namespace) -> None:
    dataset = _dataset_from_args(args)
    train, val, test = _split(dataset, args.split, args.topology_fold)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    total_pairs = sum(len(sample.node_ids["Vbiz"]) for sample in dataset.samples)
    valid_pairs = sum(int(sample.label_mask.sum()) for sample in dataset.samples)
    invalid = [
        {"event_id": sample.metadata.event_id, "app_id": app_id, "reason": reason}
        for sample in dataset.samples
        for app_id, reason in sample.invalid_reasons.items()
    ]
    positives = {
        name: int(sum(int((sample.breach_labels.bool() & sample.label_mask.bool()).sum()) for sample in samples))
        for name, samples in (("train", train), ("val", val), ("test", test))
    }
    sensitivity = _sensitivity_audit(dataset.samples)
    control = _paired_control_audit(dataset.samples, args.persistence_minutes)
    audit = {
        "protocol": "runtime_nfr_v1",
        "generated_at": datetime.now().isoformat(),
        "segment_root": str(Path(args.segment_root).resolve()),
        "data_hash": runtime_nfr_data_hash(args.segment_root),
        "num_events": len(dataset),
        "segments": dict(Counter(sample.metadata.segment_id for sample in dataset.samples)),
        "topology_hashes": len({sample.metadata.topology_hash for sample in dataset.samples}),
        "total_app_event_pairs": total_pairs,
        "valid_app_event_pairs": valid_pairs,
        "valid_coverage": valid_pairs / max(1, total_pairs),
        "positive_app_event_pairs": int(_flatten_valid(dataset.samples, "breach_labels").sum()),
        "positive_counts_by_split": positives,
        "invalid_reason_counts": dict(Counter(item["reason"] for item in invalid)),
        "matched_control": control,
        "sensitivity": sensitivity,
        "label_gate": {
            "coverage_at_least_0_80": valid_pairs / max(1, total_pairs) >= 0.80,
            "at_least_50_positives_each_split": all(value >= 50 for value in positives.values()),
            "event_rate_exceeds_control_ci": control["bootstrap_lower"] > 0.0,
            "sensitivity_spearman_at_least_0_60": sensitivity["minimum_adjacent_spearman"] >= 0.60,
        },
        "time_protocol": {
            "history_minutes": args.history_minutes,
            "pre_minutes": args.pre_minutes,
            "observation_minutes": args.observation_minutes,
            "horizon_minutes": args.horizon_minutes,
            "boundary_quantile": args.boundary_quantile,
            "persistence_minutes": args.persistence_minutes,
        },
    }
    audit["label_gate"]["pass"] = all(audit["label_gate"].values())
    boundaries = [
        {
            "event_id": sample.metadata.event_id,
            "app_id": app_id,
            "sli": sli,
            **boundary_to_dict(boundary),
        }
        for sample in dataset.samples
        for app_id, app_boundaries in sample.boundaries.items()
        for sli, boundary in app_boundaries.items()
    ]
    _json_write(output / "dataset_audit.json", audit)
    _json_write(output / "boundary_manifest.json", boundaries)
    _json_write(output / "insufficient_samples.json", invalid)
    _json_write(output / "split_manifest.json", _split_manifest(train, val, test, split=args.split, topology_fold=args.topology_fold))
    print(json.dumps(audit, indent=2, ensure_ascii=False))


def _git_metadata() -> dict[str, Any]:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = "unknown", True
    return {"git_sha": sha, "git_dirty": dirty}


def _training_config(args: argparse.Namespace, manifest_path: Path) -> dict[str, Any]:
    return {
        "protocol": args.protocol,
        "model": args.model,
        "segment_root": str(Path(args.segment_root).resolve()),
        "data_hash": runtime_nfr_data_hash(args.segment_root),
        "split": args.split,
        "topology_fold": args.topology_fold if args.split == "topology" else None,
        "history_minutes": args.history_minutes,
        "pre_minutes": args.pre_minutes,
        "observation_minutes": args.observation_minutes,
        "horizon_minutes": args.horizon_minutes,
        "boundary_quantile": args.boundary_quantile,
        "persistence_minutes": args.persistence_minutes,
        "min_history_minutes": args.min_history_minutes,
        "min_early_minutes": args.min_early_minutes,
        "min_future_minutes": args.min_future_minutes,
        "seed": args.seed,
        "split_manifest": str(manifest_path.resolve()),
        "forbidden_inputs": ["fault_type", "target_node_ids", "duration", "impact_score"],
        "test_exposure_status": (
            "v1 locked test" if args.protocol == RUNTIME_NFR_V1_PROTOCOL else
            "legacy holdout previously observed under v1; v2 model selection is development-only"
        ),
        **_git_metadata(),
    }


def _resolve_training_output(args: argparse.Namespace) -> Path:
    if args.output_dir:
        output = Path(args.output_dir)
    elif args.protocol == RUNTIME_NFR_V2_PROTOCOL:
        output = Path("checkpoints/runtime_nfr_v2") / args.model
    else:
        output = Path("checkpoints/runtime_nfr")
    resolved = output.resolve()
    if args.protocol == RUNTIME_NFR_V2_PROTOCOL:
        forbidden = {
            Path("checkpoints/runtime_nfr_baselines").resolve(),
            Path("checkpoints/runtime_nfr_full_audit").resolve(),
        }
        if any(resolved == path or path in resolved.parents for path in forbidden):
            raise RuntimeError("runtime_nfr_v2 outputs cannot be written into frozen v1 result directories")
    for config_path in (output / "training_history.json", output / "config.json"):
        if not config_path.exists():
            continue
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        existing = payload.get("protocol") or payload.get("config", {}).get("protocol")
        if existing and existing != args.protocol:
            raise RuntimeError(f"output directory already belongs to protocol {existing}")
    return output


def _require_topology_gate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.topology_gate_report:
        raise RuntimeError("topology_residual requires --topology-gate-report")
    report = json.loads(Path(args.topology_gate_report).read_text(encoding="utf-8"))
    if not report.get("graph_development_gate", {}).get("pass", False):
        raise RuntimeError("topology development gate did not pass; residual graph training is prohibited")
    return report


def train_runtime_nfr_command(args: argparse.Namespace) -> None:
    gate_report = _require_topology_gate(args) if args.model == "topology_residual" else None
    dataset = _dataset_from_args(args)
    train, val, test = _split(dataset, args.split, args.topology_fold)
    output = _resolve_training_output(args)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "split_manifest.json"
    manifest = _split_manifest(train, val, test, split=args.split, topology_fold=args.topology_fold)
    _json_write(manifest_path, manifest)
    config = _training_config(args, manifest_path)
    config.update({
        "hidden_dim": args.hidden_dim,
        "num_layers": args.num_layers,
        "dropout": args.dropout,
        "severity_weight": args.severity_weight,
        "rank_weight": args.rank_weight,
        "train_events": len(train),
        "val_events": len(val),
        "test_events": len(test),
        "train_topology_hashes": sorted({sample.metadata.topology_hash for sample in train}),
    })
    if args.protocol == RUNTIME_NFR_V2_PROTOCOL:
        protocol = _v2_protocol(
            dataset,
            data_hash=config["data_hash"],
            output_namespace=str(output.resolve()),
        )
        _json_write(output / "experiment_protocol.json", protocol.to_dict())
    if args.model == "topology_residual":
        config["topology_gate_report"] = str(Path(args.topology_gate_report).resolve())
        assert gate_report is not None
        config["topology_gate_snapshot"] = gate_report["graph_development_gate"]
    if args.model in BASELINE_MODELS:
        trainer = RuntimeNFRBaselineTrainer(args.model)
        trainer.fit(train, val)
        checkpoint = output / "model.joblib"
        trainer.save(checkpoint, config)
        probabilities, severity = trainer.predict(val)
        severity_scale, threshold = trainer.severity_scale, trainer.threshold
    else:
        if args.model == "topology_residual":
            model = TopologyGatedResidualPredictor(
                hidden_dim=args.hidden_dim,
                num_layers=args.num_layers,
                dropout=args.dropout,
            )
        else:
            model = RuntimeNFRPredictor(
                hidden_dim=args.hidden_dim,
                num_layers=args.num_layers,
                dropout=args.dropout,
                message_passing=args.model == "rgcn",
            )
        trainer = RuntimeNFRNeuralTrainer(
            model,
            learning_rate=args.lr,
            severity_weight=args.severity_weight,
            rank_weight=args.rank_weight,
            device=args.device,
        )
        epochs = args.epochs if args.epochs is not None else (100 if args.protocol == RUNTIME_NFR_V2_PROTOCOL else 30)
        patience = args.patience if args.patience is not None else (10 if args.protocol == RUNTIME_NFR_V2_PROTOCOL else None)
        config["epochs"] = epochs
        config["early_stopping_patience"] = patience
        trainer.fit(
            train,
            val,
            epochs=epochs,
            output_dir=output,
            config=config,
            seed=args.seed,
            patience=patience,
        )
        checkpoint = output / "best_model.pt"
        probabilities, severity = trainer.predict(val)
        severity_scale, threshold = trainer.severity_scale, trainer.threshold
    evaluation = evaluate_predictions(
        val,
        probabilities,
        severity,
        severity_scale=severity_scale,
        threshold=threshold,
        train_topology_hashes=set(config["train_topology_hashes"]),
        bootstrap_iterations=args.bootstrap_iterations,
    )
    evaluation["evaluation_split"] = "validation"
    evaluation["checkpoint"] = str(checkpoint.resolve())
    _json_write(output / "validation_evaluation.json", evaluation)
    print(json.dumps({"checkpoint": str(checkpoint), "validation": evaluation["metrics"]}, indent=2))


def _args_from_config(args: argparse.Namespace, config: dict[str, Any]) -> argparse.Namespace:
    for key in (
        "segment_root", "history_minutes", "pre_minutes", "observation_minutes", "horizon_minutes",
        "boundary_quantile", "persistence_minutes", "min_history_minutes", "min_early_minutes",
        "min_future_minutes", "split", "topology_fold",
    ):
        if key in config and config[key] is not None:
            setattr(args, key, config[key])
    return args


def _select_manifest_samples(dataset: RuntimeNFRDataset, ids: list[str]) -> list[RuntimeNFRSample]:
    by_id = {sample.metadata.event_id: sample for sample in dataset.samples}
    missing = [event_id for event_id in ids if event_id not in by_id]
    if missing:
        raise RuntimeError(f"split manifest contains missing events: {missing[:5]}")
    return [by_id[event_id] for event_id in ids]


def evaluate_runtime_nfr_command(args: argparse.Namespace) -> None:
    checkpoint = Path(args.checkpoint)
    if checkpoint.suffix == ".joblib":
        trainer, config = RuntimeNFRBaselineTrainer.load(checkpoint)
        neural = False
    else:
        import torch

        payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
        config = payload.get("config", {})
        if config.get("model") == "topology_residual":
            model = TopologyGatedResidualPredictor(
                hidden_dim=int(config.get("hidden_dim", 128)),
                num_layers=int(config.get("num_layers", 2)),
                dropout=float(config.get("dropout", 0.15)),
            )
        else:
            model = RuntimeNFRPredictor(
                hidden_dim=int(config.get("hidden_dim", 128)),
                num_layers=int(config.get("num_layers", 3)),
                dropout=float(config.get("dropout", 0.15)),
                message_passing=config.get("model", "rgcn") == "rgcn",
            )
        trainer = RuntimeNFRNeuralTrainer(model, device=args.device)
        trainer.load_checkpoint(checkpoint)
        neural = True
    args = _args_from_config(args, config)
    dataset = _dataset_from_args(args)
    current_hash = runtime_nfr_data_hash(args.segment_root)
    if current_hash != config.get("data_hash"):
        raise RuntimeError("runtime NFR data hash differs from the training checkpoint")
    manifest_path = Path(config["split_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    split_name = "test" if args.locked_test else "val"
    samples = _select_manifest_samples(dataset, manifest[f"{split_name}_event_ids"])
    lock_path = checkpoint.parent / "locked_test_manifest.json"
    if args.locked_test:
        lock = {
            "data_hash": current_hash,
            "checkpoint": str(checkpoint.resolve()),
            "test_event_ids": manifest["test_event_ids"],
        }
        if lock_path.exists() and json.loads(lock_path.read_text(encoding="utf-8")) != lock:
            raise RuntimeError("locked test manifest differs from this evaluation request")
        if not lock_path.exists():
            _json_write(lock_path, lock)
    probabilities, severity = trainer.predict(samples)
    evaluation = evaluate_predictions(
        samples,
        probabilities,
        severity,
        severity_scale=trainer.severity_scale,
        threshold=trainer.threshold,
        train_topology_hashes=set(config.get("train_topology_hashes", [])),
        bootstrap_iterations=args.bootstrap_iterations,
    )
    evaluation.update({
        "evaluation_split": split_name,
        "locked_test": bool(args.locked_test),
        "model": config.get("model"),
        "checkpoint": str(checkpoint.resolve()),
        "data_hash": current_hash,
        "neural": neural,
    })
    recall_comparison = evaluation["bootstrap_recall_at_3_delta_vs_random"]
    evaluation["evidence_gate"] = {
        "pr_auc_delta_ci_lower_above_zero": evaluation["bootstrap_pr_auc_delta_vs_prior"]["lower"] > 0.0,
        "recall_at_3_delta_ci_lower_above_zero": recall_comparison["lower"] > 0.0,
        "expected_random_recall_at_3": recall_comparison["random_expectation"],
    }
    evaluation["evidence_gate"]["pass"] = all(
        value for key, value in evaluation["evidence_gate"].items() if key != "expected_random_recall_at_3"
    )
    output = Path(args.output or checkpoint.parent / f"{split_name}_evaluation.json")
    _json_write(output, evaluation)
    _json_write(output.with_name(f"{split_name}_predictions.json"), evaluation["predictions"])
    print(json.dumps({"output": str(output), "metrics": evaluation["metrics"], "gate": evaluation["evidence_gate"]}, indent=2))


def _add_common_dataset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--segment-root", default=DEFAULT_SEGMENT_ROOT)
    parser.add_argument("--history-minutes", type=int, default=1440)
    parser.add_argument("--pre-minutes", type=int, default=60)
    parser.add_argument("--observation-minutes", type=int, default=5)
    parser.add_argument("--horizon-minutes", type=int, default=15)
    parser.add_argument("--boundary-quantile", type=float, default=0.95)
    parser.add_argument("--persistence-minutes", type=int, default=3)
    parser.add_argument("--min-history-minutes", type=int, default=120)
    parser.add_argument("--min-early-minutes", type=int, default=4)
    parser.add_argument("--min-future-minutes", type=int, default=8)
    parser.add_argument("--max-events", type=int, default=None, help="Smoke-test limit; omit for formal runs")


def register_runtime_nfr_subparsers(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    prepare = subparsers.add_parser("prepare-runtime-nfr", help="Mine and audit runtime NFR boundaries")
    _add_common_dataset_args(prepare)
    prepare.add_argument("--split", choices=["chronological", "topology"], default="chronological")
    prepare.add_argument("--topology-fold", type=int, default=0)
    prepare.add_argument("--output-dir", default="checkpoints/runtime_nfr_audit")

    train = subparsers.add_parser("train-runtime-nfr", help="Train a leakage-safe runtime NFR model")
    _add_common_dataset_args(train)
    train.add_argument("--protocol", choices=[RUNTIME_NFR_V1_PROTOCOL, RUNTIME_NFR_V2_PROTOCOL], default=RUNTIME_NFR_V1_PROTOCOL)
    train.add_argument("--model", choices=[*BASELINE_MODELS, *NEURAL_MODELS, "topology_residual"], default="logistic")
    train.add_argument("--split", choices=["chronological", "topology"], default="chronological")
    train.add_argument("--topology-fold", type=int, default=0)
    train.add_argument("--hidden-dim", type=int, default=128)
    train.add_argument("--num-layers", type=int, default=3)
    train.add_argument("--dropout", type=float, default=0.15)
    train.add_argument("--severity-weight", type=float, default=0.5)
    train.add_argument("--rank-weight", type=float, default=0.25)
    train.add_argument("--epochs", type=int, default=None, help="Defaults to 30 for v1 and 100 for v2")
    train.add_argument("--patience", type=int, default=None, help="Defaults to disabled for v1 and 10 for v2")
    train.add_argument("--lr", type=float, default=1e-3)
    train.add_argument("--device", default="cpu")
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--bootstrap-iterations", type=int, default=2000)
    train.add_argument("--topology-gate-report", default=None)
    train.add_argument("--output-dir", default=None)

    evaluate = subparsers.add_parser("evaluate-runtime-nfr", help="Evaluate validation or locked test events")
    _add_common_dataset_args(evaluate)
    evaluate.add_argument("--checkpoint", required=True)
    evaluate.add_argument("--locked-test", action="store_true")
    evaluate.add_argument("--device", default="cpu")
    evaluate.add_argument("--bootstrap-iterations", type=int, default=2000)
    evaluate.add_argument("--output", default=None)
    # These are replaced by checkpoint values before loading the dataset.
    evaluate.set_defaults(split="chronological", topology_fold=0)

    cards = subparsers.add_parser(
        "export-nfr-boundary-cards",
        help="Export candidate NFR specifications and a blinded expert-review packet",
    )
    _add_common_dataset_args(cards)
    cards.add_argument("--expert-card-count", type=int, default=48)
    cards.add_argument("--seed", type=int, default=20260722)
    cards.add_argument("--output-dir", default="checkpoints/runtime_nfr_v2/boundary_cards")

    validity = subparsers.add_parser(
        "audit-runtime-nfr-validity",
        help="Run v2 construct-validity, matched-control, and split sensitivity audits",
    )
    _add_common_dataset_args(validity)
    validity.add_argument("--controls-per-pair", type=int, default=3)
    validity.add_argument("--event-buffer-minutes", type=int, default=30)
    validity.add_argument("--bootstrap-iterations", type=int, default=2000)
    validity.add_argument("--seed", type=int, default=20260722)
    validity.add_argument("--skip-matched-controls", action="store_true", help="Smoke tests only")
    validity.add_argument("--output-dir", default="checkpoints/runtime_nfr_v2/validity_audit")

    topology = subparsers.add_parser(
        "audit-runtime-nfr-topology",
        help="Audit topology provenance, target coverage, diversity, and residual informativeness",
    )
    _add_common_dataset_args(topology)
    topology.add_argument("--folds", type=int, default=5)
    topology.add_argument("--output-dir", default="checkpoints/runtime_nfr_v2/topology_gate")

    expert = subparsers.add_parser(
        "summarize-nfr-expert-review",
        help="Validate and summarize completed expert ratings",
    )
    expert.add_argument("--ratings", required=True)
    expert.add_argument("--output", default="checkpoints/runtime_nfr_v2/expert_review/summary.json")

    finalize = subparsers.add_parser(
        "finalize-nfr-expert-review",
        help="Freeze and normalize the completed first-round expert review",
    )
    finalize.add_argument(
        "--replies-dir",
        default="docs/experiments/runtime-nfr-v2/专家回复",
    )
    finalize.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v2/expert_review",
    )

    governance = subparsers.add_parser(
        "build-nfr-academic-governance-cards",
        help="Wrap frozen v2 thresholds with deterministic academic governance",
    )
    governance.add_argument(
        "--v2-cards",
        default="checkpoints/runtime_nfr_v2/boundary_cards/boundary_cards.json",
    )
    governance.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/governance_cards",
    )

    round2 = subparsers.add_parser(
        "build-nfr-round2-expert-package",
        help="Build blinded and independently randomized round-two expert packets",
    )
    _add_common_dataset_args(round2)
    round2.add_argument(
        "--academic-cards",
        default=(
            "checkpoints/runtime_nfr_v3_academic/governance_cards/"
            "academic_governance_cards.json"
        ),
    )
    round2.add_argument(
        "--first-round-per-card",
        default="checkpoints/runtime_nfr_v2/expert_review/per_card_medians.csv",
    )
    round2.add_argument("--traffic-quartiles", default=None)
    round2.add_argument(
        "--skip-traffic-stratification",
        action="store_true",
        help="Use only when the frozen raw dataset is unavailable; recorded in the manifest",
    )
    round2.add_argument("--seed", type=int, default=20260723)
    round2.add_argument(
        "--output-dir",
        default="docs/experiments/runtime-nfr-v3-academic/expert-review-round2",
    )

    round2_summary = subparsers.add_parser(
        "summarize-nfr-round2-expert-review",
        help="Validate and summarize the independent round-two expert review",
    )
    round2_summary.add_argument("--ratings", nargs=3, required=True)
    round2_summary.add_argument(
        "--declarations",
        nargs=3,
        default=None,
        help="Three reviewer declarations to freeze and audit with the ratings",
    )
    round2_summary.add_argument(
        "--private-manifest",
        default=(
            "docs/experiments/runtime-nfr-v3-academic/expert-review-round2/"
            "sampling_and_parent_mapping_private.json"
        ),
    )
    round2_summary.add_argument(
        "--first-round-ratings",
        default="checkpoints/runtime_nfr_v2/expert_review/ratings.csv",
    )
    round2_summary.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/expert_review_round2",
    )

    external = subparsers.add_parser(
        "audit-external-nfr-dataset",
        help="Audit provenance and test-only isolation of a frozen external dataset",
    )
    external.add_argument("--manifest", required=True)
    external.add_argument(
        "--output",
        default="checkpoints/runtime_nfr_v3_academic/external_dataset/audit.json",
    )

    rcaeval = subparsers.add_parser(
        "adapt-rcaeval-nfr-dataset",
        help="Adapt frozen RCAEval RE1-OB cases for validity-only governance replication",
    )
    rcaeval.add_argument("--source-root", required=True)
    rcaeval.add_argument(
        "--frozen-archive-sha256",
        default="4a709297e0a829f0f2ee8a7792a6d74da32d663c600565b7fffc860963b840c4",
    )
    rcaeval.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/external_dataset/rcaeval_re1_ob",
    )

    ablation = subparsers.add_parser(
        "run-nfr-v3-feature-ablation",
        help="Run the frozen development-only Runtime NFR v3 feature ablation",
    )
    _add_common_dataset_args(ablation)
    ablation.add_argument("--folds", type=int, default=5)
    ablation.add_argument("--bootstrap-iterations", type=int, default=2000)
    ablation.add_argument("--seed", type=int, default=20260723)
    ablation.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/feature_ablation",
    )

    paper_tables = subparsers.add_parser(
        "build-nfr-v3-paper-tables",
        help="Build manuscript tables exclusively from frozen JSON results",
    )
    paper_tables.add_argument(
        "--round1-summary",
        default="checkpoints/runtime_nfr_v2/expert_review/summary.json",
    )
    paper_tables.add_argument(
        "--governance-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/governance_cards/"
            "governance_summary.json"
        ),
    )
    paper_tables.add_argument(
        "--round2-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/expert_review_round2/"
            "summary.json"
        ),
    )
    paper_tables.add_argument(
        "--external-manifest-audit",
        default="checkpoints/runtime_nfr_v3_academic/external_dataset/audit.json",
    )
    paper_tables.add_argument(
        "--external-governance-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/external_dataset/"
            "rcaeval_re1_ob/governance_summary.json"
        ),
    )
    paper_tables.add_argument(
        "--feature-ablation-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/summary.json"
        ),
    )
    paper_tables.add_argument(
        "--topology-gate",
        default=(
            "checkpoints/runtime_nfr_v2/topology_gate/"
            "topology_gate_report.json"
        ),
    )
    paper_tables.add_argument(
        "--validity-audit",
        default=(
            "checkpoints/runtime_nfr_v2/validity_audit/"
            "validity_audit.json"
        ),
    )
    paper_tables.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/paper_tables",
    )
    paper_tables.add_argument(
        "--manual-audit-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/manual_audit_results/"
            "manual_audit_summary.json"
        ),
    )

    paper_artifacts = subparsers.add_parser(
        "build-nfr-v3-paper-artifacts",
        help="Build representative cases and six publication figures",
    )
    paper_artifacts.add_argument(
        "--governance-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/governance_cards/"
            "governance_summary.json"
        ),
    )
    paper_artifacts.add_argument(
        "--governance-cards",
        default=(
            "checkpoints/runtime_nfr_v3_academic/governance_cards/"
            "academic_governance_cards.json"
        ),
    )
    paper_artifacts.add_argument(
        "--round1-summary",
        default="checkpoints/runtime_nfr_v2/expert_review/summary.json",
    )
    paper_artifacts.add_argument(
        "--round2-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/expert_review_round2/"
            "summary.json"
        ),
    )
    paper_artifacts.add_argument(
        "--external-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/external_dataset/"
            "rcaeval_re1_ob/governance_summary.json"
        ),
    )
    paper_artifacts.add_argument(
        "--feature-ablation-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/summary.json"
        ),
    )
    paper_artifacts.add_argument(
        "--paired-bootstrap",
        default=(
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/"
            "paired_bootstrap.json"
        ),
    )
    paper_artifacts.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/paper_artifacts",
    )

    manual_audit = subparsers.add_parser(
        "build-nfr-v3-manual-audit-sample",
        help="Build a fixed 20-row checklist for matched-control manual auditing",
    )
    manual_audit.add_argument(
        "--matched-rows",
        default=(
            "checkpoints/runtime_nfr_v2/validity_audit/"
            "matched_control_rows.json"
        ),
    )
    manual_audit.add_argument("--count", type=int, default=20)
    manual_audit.add_argument("--seed", type=int, default=20260723)
    manual_audit.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/manual_audit",
    )

    manual_audit_package = subparsers.add_parser(
        "build-nfr-v3-manual-audit-package",
        help="Build evidence-complete private and outcome-blinded control audit workbooks",
    )
    _add_common_dataset_args(manual_audit_package)
    manual_audit_package.add_argument(
        "--sample-manifest",
        default=(
            "checkpoints/runtime_nfr_v3_academic/manual_audit/"
            "matched_control_manual_audit_20.csv"
        ),
    )
    manual_audit_package.add_argument("--controls-per-pair", type=int, default=3)
    manual_audit_package.add_argument(
        "--event-buffer-minutes", type=int, default=30
    )
    manual_audit_package.add_argument(
        "--output-dir",
        default=(
            "checkpoints/runtime_nfr_v3_academic/"
            "manual_audit_evidence_complete"
        ),
    )

    manual_audit_summary = subparsers.add_parser(
        "summarize-nfr-v3-manual-audit",
        help="Validate, freeze, unblind, and summarize signed manual audits",
    )
    manual_audit_summary.add_argument(
        "--input-dir",
        default=(
            "checkpoints/runtime_nfr_v3_academic/"
            "manual_audit_evidence_complete/reviewer_package/核查结果"
        ),
    )
    manual_audit_summary.add_argument(
        "--private-dir",
        default=(
            "checkpoints/runtime_nfr_v3_academic/"
            "manual_audit_evidence_complete/private"
        ),
    )
    manual_audit_summary.add_argument(
        "--template",
        default=(
            "checkpoints/runtime_nfr_v3_academic/"
            "manual_audit_evidence_complete/reviewer_package/"
            "MATCH_AUDIT_REVIEWER.xlsx"
        ),
    )
    manual_audit_summary.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/manual_audit_results",
    )
    manual_audit_summary.add_argument("--expected-reviewers", type=int, default=4)
    manual_audit_summary.add_argument("--expected-pairs", type=int, default=20)
    manual_audit_summary.add_argument("--expected-controls", type=int, default=60)

    legacy_integrity = subparsers.add_parser(
        "audit-nfr-v3-legacy-integrity",
        help="Freeze or verify the v1/v2 evidence tree without modifying it",
    )
    legacy_integrity.add_argument(
        "--roots",
        nargs="+",
        default=[
            "checkpoints/runtime_nfr_v1",
            "checkpoints/runtime_nfr_v2",
            "docs/experiments/runtime-nfr-v1",
            "docs/experiments/runtime-nfr-v2",
        ],
    )
    legacy_integrity.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/integrity",
    )

    formal_audit = subparsers.add_parser(
        "audit-nfr-v3-formal-results",
        help="Cross-check all frozen v3 evidence, leakage controls, and hashes",
    )
    _add_common_dataset_args(formal_audit)
    formal_audit.add_argument(
        "--round2-ratings",
        default=(
            "checkpoints/runtime_nfr_v3_academic/expert_review_round2/"
            "ratings_normalized.csv"
        ),
    )
    formal_audit.add_argument(
        "--round2-source-hashes",
        default=(
            "checkpoints/runtime_nfr_v3_academic/expert_review_round2/"
            "source_evidence_hashes.json"
        ),
    )
    formal_audit.add_argument(
        "--v2-cards",
        default="checkpoints/runtime_nfr_v2/boundary_cards/boundary_cards.json",
    )
    formal_audit.add_argument(
        "--academic-cards",
        default=(
            "checkpoints/runtime_nfr_v3_academic/governance_cards/"
            "academic_governance_cards.json"
        ),
    )
    formal_audit.add_argument(
        "--external-split",
        default=(
            "checkpoints/runtime_nfr_v3_academic/external_dataset/"
            "rcaeval_re1_ob/split_manifest.json"
        ),
    )
    formal_audit.add_argument(
        "--external-manifest-audit",
        default="checkpoints/runtime_nfr_v3_academic/external_dataset/audit.json",
    )
    formal_audit.add_argument(
        "--oof-predictions",
        default=(
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/"
            "oof_predictions.csv.gz"
        ),
    )
    formal_audit.add_argument(
        "--feature-ablation-summary",
        default=(
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/summary.json"
        ),
    )
    formal_audit.add_argument(
        "--legacy-integrity-audit",
        default=(
            "checkpoints/runtime_nfr_v3_academic/integrity/"
            "legacy_v1_v2_integrity_audit.json"
        ),
    )
    formal_audit.add_argument(
        "--frozen-results",
        nargs="+",
        default=[
            "checkpoints/runtime_nfr_v3_academic/governance_cards/governance_summary.json",
            "checkpoints/runtime_nfr_v3_academic/expert_review_round2/summary.json",
            "checkpoints/runtime_nfr_v3_academic/external_dataset/audit.json",
            "checkpoints/runtime_nfr_v3_academic/external_dataset/rcaeval_re1_ob/adapter_audit.json",
            "checkpoints/runtime_nfr_v3_academic/external_dataset/rcaeval_re1_ob/governance_summary.json",
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/summary.json",
            "checkpoints/runtime_nfr_v3_academic/feature_ablation/paired_bootstrap.json",
            "checkpoints/runtime_nfr_v3_academic/manual_audit_results/manual_audit_summary.json",
            "checkpoints/runtime_nfr_v3_academic/paper_tables/paper_tables.json",
            "checkpoints/runtime_nfr_v3_academic/paper_artifacts/paper_artifact_manifest.json",
        ],
    )
    formal_audit.add_argument(
        "--formal-baseline",
        default=(
            "checkpoints/runtime_nfr_v3_academic/formal_audit/"
            "formal_result_hashes_baseline_v2_manual_audit.json"
        ),
        help=(
            "Versioned frozen-result hash baseline. The v2 baseline adds the "
            "completed manual audit and generated paper artifacts while "
            "preserving the original baseline."
        ),
    )
    formal_audit.add_argument(
        "--output-dir",
        default="checkpoints/runtime_nfr_v3_academic/formal_audit",
    )
