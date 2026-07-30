"""Event-bootstrap paired comparison for two runtime-NFR evaluation files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, ndcg_score


def _events(path: Path) -> tuple[dict[str, list[dict]], float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for row in payload["predictions"]:
        if row["valid"] and row["breach_probability"] is not None:
            result.setdefault(row["event_id"], []).append(row)
    return result, float(payload.get("severity_scale_train_q99", 1.0))


def _metrics(events: list[list[dict]], severity_scale: float) -> dict[str, float]:
    labels, probabilities = [], []
    recalls, ndcgs = [], []
    for rows in events:
        event_labels = np.asarray([row["breach_label"] for row in rows], dtype=int)
        event_probability = np.asarray([row["breach_probability"] for row in rows], dtype=float)
        labels.extend(event_labels.tolist())
        probabilities.extend(event_probability.tolist())
        if event_labels.sum() > 0:
            order = np.argsort(-event_probability)[: min(3, len(rows))]
            recalls.append(float(event_labels[order].sum() / event_labels.sum()))
            relevance = np.maximum(
                event_labels.astype(float),
                np.clip(
                    np.asarray([row["severity_raw"] for row in rows], dtype=float)
                    / max(severity_scale, 1e-8),
                    0.0,
                    1.0,
                ),
            )
            ndcgs.append(float(ndcg_score(relevance[None, :], event_probability[None, :], k=min(3, len(rows)))))
    labels_array = np.asarray(labels, dtype=int)
    probability_array = np.asarray(probabilities, dtype=float)
    return {
        "pr_auc": float(average_precision_score(labels_array, probability_array)) if np.unique(labels_array).size == 2 else float(labels_array.mean()),
        "recall_at_3": float(np.mean(recalls)) if recalls else 0.0,
        "ndcg_at_3": float(np.mean(ndcgs)) if ndcgs else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    candidate, candidate_scale = _events(Path(args.candidate))
    baseline, baseline_scale = _events(Path(args.baseline))
    event_ids = sorted(set(candidate) & set(baseline))
    if not event_ids:
        raise RuntimeError("evaluation files have no common valid events")
    candidate_events = [candidate[event_id] for event_id in event_ids]
    baseline_events = [baseline[event_id] for event_id in event_ids]
    candidate_metrics = _metrics(candidate_events, candidate_scale)
    baseline_metrics = _metrics(baseline_events, baseline_scale)
    rng = np.random.default_rng(args.seed)
    distributions = {key: [] for key in candidate_metrics}
    for _ in range(args.iterations):
        indices = rng.integers(0, len(event_ids), len(event_ids))
        candidate_boot = _metrics([candidate_events[index] for index in indices], candidate_scale)
        baseline_boot = _metrics([baseline_events[index] for index in indices], baseline_scale)
        for key in distributions:
            distributions[key].append(candidate_boot[key] - baseline_boot[key])
    result = {
        "candidate": str(Path(args.candidate).resolve()),
        "baseline": str(Path(args.baseline).resolve()),
        "common_events": len(event_ids),
        "metrics": {
            key: {
                "candidate": candidate_metrics[key],
                "baseline": baseline_metrics[key],
                "difference": candidate_metrics[key] - baseline_metrics[key],
                "lower": float(np.quantile(distributions[key], 0.025)),
                "upper": float(np.quantile(distributions[key], 0.975)),
            }
            for key in candidate_metrics
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
