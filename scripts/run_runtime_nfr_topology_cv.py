"""Five-fold unseen-topology evaluation for legal runtime-NFR baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from src.data.runtime_nfr_dataset import load_runtime_nfr_dataset, topology_runtime_folds
from src.runtime_nfr_cli import _json_write, runtime_nfr_data_hash
from src.training.runtime_nfr_trainer import RuntimeNFRBaselineTrainer, evaluate_predictions


def _development_split(samples: list) -> tuple[list, list]:
    ordered = sorted(samples, key=lambda sample: sample.metadata.event_start)
    boundary = max(1, int(len(ordered) * 0.8))
    return ordered[:boundary], ordered[boundary:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-root", default="dataset/outputs/topo_intent_v2/segment_tenants_all")
    parser.add_argument("--output-root", default="checkpoints/runtime_nfr_topology_cv")
    parser.add_argument("--models", nargs="+", default=["prior", "persistence", "logistic", "hgb"])
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    args = parser.parse_args()

    legal = {"prior", "persistence", "logistic", "hgb"}
    if not set(args.models) <= legal:
        raise ValueError(f"topology CV supports only {sorted(legal)}")
    dataset = load_runtime_nfr_dataset(args.segment_root)
    folds = topology_runtime_folds(dataset.samples, n_splits=args.folds)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    data_hash = runtime_nfr_data_hash(args.segment_root)
    rows: list[dict] = []
    manifests: list[dict] = []
    for fold_index, (development_indices, test_indices) in enumerate(folds):
        development = [dataset.samples[index] for index in development_indices]
        test = [dataset.samples[index] for index in test_indices]
        train, val = _development_split(development)
        train_topologies = {sample.metadata.topology_hash for sample in train}
        test_topologies = {sample.metadata.topology_hash for sample in test}
        if train_topologies & test_topologies:
            raise AssertionError("topology hash appears in both development and evaluation sets")
        manifest = {
            "fold": fold_index,
            "train_event_ids": [sample.metadata.event_id for sample in train],
            "val_event_ids": [sample.metadata.event_id for sample in val],
            "test_event_ids": [sample.metadata.event_id for sample in test],
            "train_topology_hashes": sorted(train_topologies),
            "test_topology_hashes": sorted(test_topologies),
        }
        manifests.append(manifest)
        for model_name in args.models:
            model_dir = output_root / model_name / f"fold_{fold_index}"
            model_dir.mkdir(parents=True, exist_ok=True)
            config = {
                "protocol": "runtime_nfr_v1",
                "evaluation": "unseen_topology_5fold",
                "fold": fold_index,
                "model": model_name,
                "segment_root": str(Path(args.segment_root).resolve()),
                "data_hash": data_hash,
                "train_topology_hashes": sorted(train_topologies),
                "forbidden_inputs": ["fault_type", "target_node_ids", "duration", "impact_score"],
            }
            trainer = RuntimeNFRBaselineTrainer(model_name)
            trainer.fit(train, val)
            trainer.save(model_dir / "model.joblib", config)
            probability, severity = trainer.predict(test)
            evaluation = evaluate_predictions(
                test,
                probability,
                severity,
                severity_scale=trainer.severity_scale,
                threshold=trainer.threshold,
                train_topology_hashes=train_topologies,
                bootstrap_iterations=args.bootstrap_iterations,
            )
            evaluation.update({"evaluation_split": "unseen_topology_fold", "fold": fold_index})
            _json_write(model_dir / "evaluation.json", evaluation)
            rows.append({"model": model_name, "fold": fold_index, **evaluation["metrics"]})
    _json_write(output_root / "fold_manifests.json", manifests)
    aggregate = []
    for model_name in args.models:
        model_rows = [row for row in rows if row["model"] == model_name]
        aggregate.append({
            "model": model_name,
            "folds": len(model_rows),
            **{
                key: {
                    "mean": float(np.mean([row[key] for row in model_rows])),
                    "std": float(np.std([row[key] for row in model_rows], ddof=1)),
                }
                for key in ("pr_auc", "recall_at_3", "ndcg_at_3", "brier", "ece")
            },
        })
    _json_write(output_root / "fold_metrics.json", rows)
    _json_write(output_root / "topology_cv_summary.json", aggregate)
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()
