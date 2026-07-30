"""Training and evaluation loop for leakage-safe intervention samples."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
from typing import Any

import numpy as np
from sklearn.metrics import ndcg_score, roc_auc_score
import torch

from ..data.intervention_dataset import InterventionSample, assert_no_future_telemetry
from ..models.intervention_loss import InterventionRiskLoss
from ..models.topo_intent_risk import TopoIntentRiskPredictor


class InterventionFeatureStandardizer:
    """Fits feature statistics on the training event set only."""

    def __init__(self) -> None:
        self.mean: dict[str, torch.Tensor] = {}
        self.std: dict[str, torch.Tensor] = {}

    def fit(self, samples: list[InterventionSample]) -> None:
        for ntype in ("Vphy", "Vvm"):
            values, masks = [], []
            for sample in samples:
                values.append(sample.pre_features[ntype].reshape(-1, sample.pre_features[ntype].shape[-1]))
                masks.append(sample.missing_masks[ntype].reshape(-1, sample.missing_masks[ntype].shape[-1]))
            value = torch.cat(values, dim=0)
            mask = torch.cat(masks, dim=0)
            count = mask.sum(dim=0).clamp_min(1.0)
            mean = (value * mask).sum(dim=0) / count
            variance = (((value - mean) ** 2) * mask).sum(dim=0) / count
            self.mean[ntype] = mean
            self.std[ntype] = variance.sqrt().clamp_min(1e-6)

    def transform(self, sample: InterventionSample) -> InterventionSample:
        features = dict(sample.pre_features)
        for ntype in ("Vphy", "Vvm"):
            features[ntype] = (features[ntype] - self.mean[ntype]) / self.std[ntype]
            features[ntype] = features[ntype].clamp(-10.0, 10.0)
        return replace(sample, pre_features=features)

    def state_dict(self) -> dict[str, dict[str, list[float]]]:
        return {
            "mean": {key: value.tolist() for key, value in self.mean.items()},
            "std": {key: value.tolist() for key, value in self.std.items()},
        }

    def load_state_dict(self, state: dict[str, dict[str, list[float]]]) -> None:
        self.mean = {key: torch.tensor(value, dtype=torch.float32) for key, value in state["mean"].items()}
        self.std = {key: torch.tensor(value, dtype=torch.float32) for key, value in state["std"].items()}


class SplitConformalCalibrator:
    def __init__(self, coverage: float = 0.9) -> None:
        self.coverage = coverage
        self.quantile: float | None = None

    def fit(self, predictions: np.ndarray, labels: np.ndarray) -> None:
        residuals = np.abs(predictions - labels).reshape(-1)
        if residuals.size == 0:
            raise ValueError("cannot calibrate an empty validation set")
        self.quantile = float(np.quantile(residuals, self.coverage, method="higher"))

    def interval(self, predictions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.quantile is None:
            raise RuntimeError("calibrator is not fitted")
        return np.clip(predictions - self.quantile, 0.0, 1.0), np.clip(predictions + self.quantile, 0.0, 1.0)


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    if left.size < 2 or np.std(left) == 0.0 or np.std(right) == 0.0:
        return 0.0
    left_rank = left.argsort().argsort().astype(float)
    right_rank = right.argsort().argsort().astype(float)
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def _record_metrics(prediction: np.ndarray, labels: np.ndarray) -> dict[str, float]:
    top_k = min(3, prediction.size)
    pred_order = np.argsort(-prediction)
    label_order = np.argsort(-labels)
    relevant = set(np.flatnonzero(labels >= max(0.3, float(labels.max()) * 0.5)).tolist())
    hit1 = float(pred_order[0] in relevant) if relevant else 0.0
    recall = float(len(set(pred_order[:top_k]) & relevant) / len(relevant)) if relevant else 0.0
    return {
        "mae": float(np.mean(np.abs(prediction - labels))),
        "spearman": _spearman(prediction, labels),
        "ndcg_at_3": float(ndcg_score(labels[None, :], prediction[None, :], k=top_k)),
        "hit_at_1": hit1,
        "recall_at_3": recall,
        "top1_matches_label_top1": float(pred_order[0] == label_order[0]),
    }


class InterventionTrainer:
    def __init__(
        self,
        model: TopoIntentRiskPredictor,
        *,
        learning_rate: float = 1e-3,
        weight_decay: float = 1e-5,
        rank_weight: float = 0.5,
        device: str = "cpu",
    ) -> None:
        self.model = model.to(device)
        self.device = torch.device(device)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=learning_rate, weight_decay=weight_decay)
        self.loss_fn = InterventionRiskLoss(rank_weight=rank_weight)
        self.standardizer = InterventionFeatureStandardizer()
        self.calibrator = SplitConformalCalibrator()

    def _predict(self, samples: list[InterventionSample], *, training: bool) -> tuple[float, list[np.ndarray], list[np.ndarray], list[dict[str, float]]]:
        self.model.train(training)
        total_loss = 0.0
        predictions: list[np.ndarray] = []
        labels_list: list[np.ndarray] = []
        losses: list[dict[str, float]] = []
        for raw_sample in samples:
            assert_no_future_telemetry(raw_sample)
            sample = self.standardizer.transform(raw_sample)
            output = self.model(sample)
            labels = sample.labels.to(self.device)
            loss, loss_parts = self.loss_fn(output.risk_logit, labels)
            if training:
                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()
            total_loss += float(loss.detach().cpu())
            predictions.append(output.risk_prob.detach().cpu().numpy())
            labels_list.append(labels.detach().cpu().numpy())
            losses.append(loss_parts)
        return total_loss / max(1, len(samples)), predictions, labels_list, losses

    @staticmethod
    def _aggregate(predictions: list[np.ndarray], labels: list[np.ndarray]) -> dict[str, float]:
        if not predictions:
            return {key: 0.0 for key in ("mae", "spearman", "ndcg_at_3", "hit_at_1", "recall_at_3", "top1_matches_label_top1", "auc")}
        rows = [_record_metrics(prediction, label) for prediction, label in zip(predictions, labels)]
        result = {key: float(np.mean([row[key] for row in rows])) for key in rows[0]}
        flat_labels = np.concatenate(labels)
        flat_predictions = np.concatenate(predictions)
        binary = (flat_labels >= 0.3).astype(int)
        result["auc"] = float(roc_auc_score(binary, flat_predictions)) if len(np.unique(binary)) == 2 else 0.5
        return result

    def fit(
        self,
        train_samples: list[InterventionSample],
        val_samples: list[InterventionSample],
        *,
        epochs: int,
        output_dir: str | Path,
        seed: int = 42,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not train_samples or not val_samples:
            raise ValueError("train and validation samples must both be non-empty")
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        self.standardizer.fit(train_samples)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, Any]] = []
        best_state: dict[str, Any] | None = None
        best_mae = float("inf")
        for epoch in range(epochs):
            train_loss, train_predictions, train_labels, _ = self._predict(train_samples, training=True)
            with torch.no_grad():
                val_loss, val_predictions, val_labels, _ = self._predict(val_samples, training=False)
            train_metrics = self._aggregate(train_predictions, train_labels)
            val_metrics = self._aggregate(val_predictions, val_labels)
            row = {"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss, "train": train_metrics, "val": val_metrics}
            history.append(row)
            if val_metrics["mae"] < best_mae:
                best_mae = val_metrics["mae"]
                best_state = {key: value.detach().cpu().clone() for key, value in self.model.state_dict().items()}
        if best_state is None:
            raise RuntimeError("training did not produce a checkpoint")
        self.model.load_state_dict(best_state)
        with torch.no_grad():
            _, val_predictions, val_labels, _ = self._predict(val_samples, training=False)
        self.calibrator.fit(np.concatenate(val_predictions), np.concatenate(val_labels))
        payload = {
            "model_state": self.model.state_dict(),
            "standardizer": self.standardizer.state_dict(),
            "conformal_quantile": self.calibrator.quantile,
            "config": config or {},
            "best_val_mae": best_mae,
        }
        torch.save(payload, output_path / "best_model.pt")
        result = {"config": config or {}, "history": history, "best_val_mae": best_mae, "conformal_quantile": self.calibrator.quantile}
        (output_path / "training_history.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result

    def evaluate(self, samples: list[InterventionSample]) -> dict[str, Any]:
        with torch.no_grad():
            loss, predictions, labels, _ = self._predict(samples, training=False)
        flat_predictions = np.concatenate(predictions)
        flat_labels = np.concatenate(labels)
        lower, upper = self.calibrator.interval(flat_predictions)
        return {
            "loss": loss,
            "metrics": self._aggregate(predictions, labels),
            "interval_coverage": float(np.mean((flat_labels >= lower) & (flat_labels <= upper))),
            "interval_width": float(np.mean(upper - lower)),
            "num_records": len(samples),
        }

    def load_checkpoint(self, path: str | Path) -> dict[str, Any]:
        payload = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(payload["model_state"])
        self.standardizer.load_state_dict(payload["standardizer"])
        self.calibrator.quantile = float(payload["conformal_quantile"])
        return payload
