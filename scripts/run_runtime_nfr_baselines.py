"""Run all legal non-neural runtime-NFR baselines with one dataset load."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.data.runtime_nfr_dataset import chronological_runtime_split, load_runtime_nfr_dataset
from src.runtime_nfr_cli import _json_write, _split_manifest, runtime_nfr_data_hash
from src.training.runtime_nfr_trainer import (
    BASELINE_MODELS,
    RuntimeNFRBaselineTrainer,
    evaluate_predictions,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-root", default="dataset/outputs/topo_intent_v2/segment_tenants_all")
    parser.add_argument("--output-root", default="checkpoints/runtime_nfr_baselines")
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    args = parser.parse_args()

    dataset = load_runtime_nfr_dataset(args.segment_root)
    train, val, test = chronological_runtime_split(dataset.samples)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest = _split_manifest(train, val, test, split="chronological", topology_fold=0)
    _json_write(output_root / "split_manifest.json", manifest)
    data_hash = runtime_nfr_data_hash(args.segment_root)
    train_topologies = {sample.metadata.topology_hash for sample in train}
    summary = []
    for model_name in BASELINE_MODELS:
        output = output_root / model_name
        output.mkdir(parents=True, exist_ok=True)
        config = {
            "protocol": "runtime_nfr_v1",
            "model": model_name,
            "segment_root": str(Path(args.segment_root).resolve()),
            "data_hash": data_hash,
            "split": "chronological",
            "history_minutes": 1440,
            "pre_minutes": 60,
            "observation_minutes": 5,
            "horizon_minutes": 15,
            "boundary_quantile": 0.95,
            "persistence_minutes": 3,
            "min_history_minutes": 120,
            "min_early_minutes": 4,
            "min_future_minutes": 8,
            "split_manifest": str((output_root / "split_manifest.json").resolve()),
            "train_topology_hashes": sorted(train_topologies),
            "forbidden_inputs": ["fault_type", "target_node_ids", "duration", "impact_score"],
        }
        trainer = RuntimeNFRBaselineTrainer(model_name)
        trainer.fit(train, val)
        trainer.save(output / "model.joblib", config)
        probabilities, severity = trainer.predict(val)
        evaluation = evaluate_predictions(
            val,
            probabilities,
            severity,
            severity_scale=trainer.severity_scale,
            threshold=trainer.threshold,
            train_topology_hashes=train_topologies,
            bootstrap_iterations=args.bootstrap_iterations,
        )
        evaluation["evaluation_split"] = "validation"
        _json_write(output / "validation_evaluation.json", evaluation)
        summary.append(
            {
                "model": model_name,
                "threshold": trainer.threshold,
                **evaluation["metrics"],
                "pr_auc_delta_ci": evaluation["bootstrap_pr_auc_delta_vs_prior"],
            }
        )
    summary.sort(key=lambda row: (row["pr_auc"], row["recall_at_3"], row["ndcg_at_3"]), reverse=True)
    _json_write(output_root / "baseline_comparison.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

