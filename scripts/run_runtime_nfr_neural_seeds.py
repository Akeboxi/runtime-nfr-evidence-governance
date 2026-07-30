"""Run the no-graph and R-GCN runtime-NFR models over fixed random seeds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from src.data.runtime_nfr_dataset import chronological_runtime_split, load_runtime_nfr_dataset
from src.models.runtime_nfr_model import RuntimeNFRPredictor
from src.runtime_nfr_cli import _json_write, _split_manifest, runtime_nfr_data_hash
from src.training.runtime_nfr_trainer import RuntimeNFRNeuralTrainer, evaluate_predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-root", default="dataset/outputs/topo_intent_v2/segment_tenants_all")
    parser.add_argument("--output-root", default="checkpoints/runtime_nfr_neural_seeds")
    parser.add_argument("--models", nargs="+", choices=["no_graph", "rgcn"], default=["no_graph", "rgcn"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 73, 101, 137, 211])
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--num-layers", type=int, default=3)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    args = parser.parse_args()

    dataset = load_runtime_nfr_dataset(args.segment_root)
    train, val, test = chronological_runtime_split(dataset.samples)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "split_manifest.json"
    _json_write(manifest_path, _split_manifest(train, val, test, split="chronological", topology_fold=0))
    data_hash = runtime_nfr_data_hash(args.segment_root)
    train_topologies = {sample.metadata.topology_hash for sample in train}
    rows: list[dict] = []
    for model_name in args.models:
        for seed in args.seeds:
            output = output_root / model_name / f"seed_{seed}"
            config = {
                "protocol": "runtime_nfr_v1",
                "model": model_name,
                "segment_root": str(Path(args.segment_root).resolve()),
                "data_hash": data_hash,
                "split": "chronological",
                "split_manifest": str(manifest_path.resolve()),
                "hidden_dim": args.hidden_dim,
                "num_layers": args.num_layers,
                "dropout": args.dropout,
                "seed": seed,
                "epochs": args.epochs,
                "train_topology_hashes": sorted(train_topologies),
                "forbidden_inputs": ["fault_type", "target_node_ids", "duration", "impact_score"],
            }
            model = RuntimeNFRPredictor(
                hidden_dim=args.hidden_dim,
                num_layers=args.num_layers,
                dropout=args.dropout,
                message_passing=model_name == "rgcn",
            )
            trainer = RuntimeNFRNeuralTrainer(model, device=args.device)
            trainer.fit(train, val, epochs=args.epochs, output_dir=output, config=config, seed=seed)
            probability, severity = trainer.predict(val)
            evaluation = evaluate_predictions(
                val,
                probability,
                severity,
                severity_scale=trainer.severity_scale,
                threshold=trainer.threshold,
                train_topology_hashes=train_topologies,
                bootstrap_iterations=args.bootstrap_iterations,
            )
            evaluation.update({"evaluation_split": "validation", "model": model_name, "seed": seed})
            _json_write(output / "validation_evaluation.json", evaluation)
            rows.append({"model": model_name, "seed": seed, **evaluation["metrics"]})
    aggregate = []
    for model_name in args.models:
        model_rows = [row for row in rows if row["model"] == model_name]
        aggregate.append({
            "model": model_name,
            "seeds": [row["seed"] for row in model_rows],
            **{
                key: {
                    "mean": float(np.mean([row[key] for row in model_rows])),
                    "std": float(np.std([row[key] for row in model_rows], ddof=1)),
                }
                for key in ("pr_auc", "recall_at_3", "ndcg_at_3", "brier", "ece")
            },
        })
    _json_write(output_root / "seed_metrics.json", rows)
    _json_write(output_root / "neural_seed_summary.json", aggregate)
    print(json.dumps(aggregate, indent=2))


if __name__ == "__main__":
    main()
