"""Training, baselines, metrics, bootstrap CIs, and locked evaluation for runtime NFR."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import random
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    ndcg_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import torch

from ..data.runtime_nfr_dataset import (
    RuntimeNFRSample,
    aggregate_app_predictions,
    assert_runtime_nfr_temporal_contract,
)
from ..models.runtime_nfr_model import RuntimeNFRLoss, RuntimeNFRPredictor


BASELINE_MODELS = ("prior", "persistence", "logistic", "hgb")
NEURAL_MODELS = ("no_graph", "rgcn")


class RuntimeFeatureStandardizer:
    """Fit only on training events and honor explicit observation masks."""

    def __init__(self) -> None:
        self.mean: dict[str, torch.Tensor] = {}
        self.std: dict[str, torch.Tensor] = {}

    def fit(self, samples: list[RuntimeNFRSample]) -> None:
        for ntype in ("Vphy", "Vvm", "Vbiz"):
            values = torch.cat(
                [sample.features[ntype].reshape(-1, sample.features[ntype].shape[-1]) for sample in samples],
                dim=0,
            )
            masks = torch.cat(
                [sample.masks[ntype].reshape(-1, sample.masks[ntype].shape[-1]) for sample in samples],
                dim=0,
            )
            count = masks.sum(dim=0).clamp_min(1.0)
            mean = (values * masks).sum(dim=0) / count
            variance = (((values - mean) ** 2) * masks).sum(dim=0) / count
            self.mean[ntype] = mean
            self.std[ntype] = variance.sqrt().clamp_min(1e-6)

    def transform(self, sample: RuntimeNFRSample) -> RuntimeNFRSample:
        features = {}
        for ntype in ("Vphy", "Vvm", "Vbiz"):
            value = (sample.features[ntype] - self.mean[ntype]) / self.std[ntype]
            features[ntype] = value.clamp(-20.0, 20.0) * sample.masks[ntype]
        return replace(sample, features=features)

    def state_dict(self) -> dict[str, dict[str, list[float]]]:
        return {
            "mean": {key: value.tolist() for key, value in self.mean.items()},
            "std": {key: value.tolist() for key, value in self.std.items()},
        }

    def load_state_dict(self, state: dict[str, dict[str, list[float]]]) -> None:
        self.mean = {key: torch.tensor(value, dtype=torch.float32) for key, value in state["mean"].items()}
        self.std = {key: torch.tensor(value, dtype=torch.float32) for key, value in state["std"].items()}


def fit_severity_scale(samples: list[RuntimeNFRSample]) -> float:
    values = []
    for sample in samples:
        mask = sample.label_mask.bool() & sample.breach_labels.bool()
        values.extend(sample.severity_raw[mask].tolist())
    return max(float(np.quantile(values, 0.99)) if values else 1.0, 1e-6)


def _scaled_severity(sample: RuntimeNFRSample, scale: float) -> torch.Tensor:
    return (sample.severity_raw / scale).clamp(0.0, 1.0)


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    if left.size < 2 or np.std(left) == 0.0 or np.std(right) == 0.0:
        return 0.0
    left_rank = left.argsort().argsort().astype(float)
    right_rank = right.argsort().argsort().astype(float)
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def expected_calibration_error(labels: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    if labels.size == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    result = 0.0
    for index in range(bins):
        if index == bins - 1:
            mask = (probabilities >= edges[index]) & (probabilities <= edges[index + 1])
        else:
            mask = (probabilities >= edges[index]) & (probabilities < edges[index + 1])
        if mask.any():
            result += float(mask.mean()) * abs(float(probabilities[mask].mean()) - float(labels[mask].mean()))
    return result


def select_f1_threshold(labels: np.ndarray, probabilities: np.ndarray) -> float:
    if labels.size == 0 or np.unique(labels).size < 2:
        return 0.5
    candidates = np.unique(np.concatenate(([0.0, 0.5, 1.0], probabilities)))
    scored = [(f1_score(labels, probabilities >= threshold, zero_division=0), threshold) for threshold in candidates]
    best = max(score for score, _ in scored)
    tied = [float(threshold) for score, threshold in scored if score == best]
    return min(tied, key=lambda value: abs(value - 0.5))


def _event_feature_matrix(samples: list[RuntimeNFRSample]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int]]]:
    rows, breach, severity, mapping = [], [], [], []
    for event_index, sample in enumerate(samples):
        values = sample.features["Vbiz"].detach().cpu().numpy()
        masks = sample.masks["Vbiz"].detach().cpu().numpy()
        early_steps = max(1, int((sample.metadata.input_end - sample.metadata.event_minute).total_seconds() // 60))
        early = values[-early_steps:]
        early_mask = masks[-early_steps:]
        history = values[:-early_steps]
        history_mask = masks[:-early_steps]
        for app_index in range(values.shape[1]):
            if not bool(sample.label_mask[app_index]):
                continue
            app_early = early[:, app_index]
            app_early_mask = early_mask[:, app_index]
            app_history = history[:, app_index]
            app_history_mask = history_mask[:, app_index]

            def summary(array: np.ndarray, mask: np.ndarray, detailed: bool) -> list[float]:
                masked = np.where(mask > 0, array, np.nan)
                output: list[float] = []
                for channel in range(array.shape[-1]):
                    series = masked[:, channel]
                    finite = series[np.isfinite(series)]
                    if finite.size == 0:
                        output.extend([0.0] * (5 if detailed else 2))
                        continue
                    if detailed:
                        trend = float((finite[-1] - finite[0]) / max(1, finite.size - 1))
                        output.extend([float(finite.mean()), float(finite.std()), float(finite.max()), float(finite[-1]), trend])
                    else:
                        output.extend([float(finite.mean()), float(finite.std())])
                return output

            rows.append([*summary(app_early, app_early_mask, True), *summary(app_history, app_history_mask, False)])
            breach.append(float(sample.breach_labels[app_index]))
            severity.append(float(sample.severity_raw[app_index]))
            mapping.append((event_index, app_index))
    return np.asarray(rows, dtype=float), np.asarray(breach, dtype=int), np.asarray(severity, dtype=float), mapping


def _event_arrays_from_flat(
    samples: list[RuntimeNFRSample],
    mapping: list[tuple[int, int]],
    breach_probability: np.ndarray,
    severity_score: np.ndarray,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    probabilities = [np.full(len(sample.node_ids["Vbiz"]), np.nan, dtype=float) for sample in samples]
    severities = [np.full(len(sample.node_ids["Vbiz"]), np.nan, dtype=float) for sample in samples]
    for row, (event_index, app_index) in enumerate(mapping):
        probabilities[event_index][app_index] = float(breach_probability[row])
        severities[event_index][app_index] = float(severity_score[row])
    return probabilities, severities


def prediction_metrics(
    samples: list[RuntimeNFRSample],
    probabilities: list[np.ndarray],
    severity_scores: list[np.ndarray],
    *,
    severity_scale: float,
    threshold: float,
) -> dict[str, float]:
    labels_flat, prob_flat, severity_true_flat, severity_pred_flat = [], [], [], []
    recalls, ndcgs, hits = [], [], []
    for sample, probability, severity_prediction in zip(samples, probabilities, severity_scores):
        mask = sample.label_mask.detach().cpu().numpy().astype(bool) & np.isfinite(probability)
        if not mask.any():
            continue
        labels = sample.breach_labels.detach().cpu().numpy()[mask].astype(int)
        true_severity = np.clip(sample.severity_raw.detach().cpu().numpy()[mask] / severity_scale, 0.0, 1.0)
        event_probability = probability[mask]
        event_severity = severity_prediction[mask]
        labels_flat.extend(labels.tolist())
        prob_flat.extend(event_probability.tolist())
        positive = labels.astype(bool)
        if positive.any():
            order = np.argsort(-event_probability)
            top_k = order[: min(3, order.size)]
            recalls.append(float(positive[top_k].sum() / positive.sum()))
            hits.append(float(positive[order[0]]))
            relevance = np.maximum(true_severity, labels.astype(float))
            ndcgs.append(float(ndcg_score(relevance[None, :], event_probability[None, :], k=min(3, order.size))))
        positive_with_severity = positive & np.isfinite(event_severity)
        severity_true_flat.extend(true_severity[positive_with_severity].tolist())
        severity_pred_flat.extend(event_severity[positive_with_severity].tolist())
    labels_array = np.asarray(labels_flat, dtype=int)
    probability_array = np.asarray(prob_flat, dtype=float)
    predicted = probability_array >= threshold
    if labels_array.size == 0:
        return {key: 0.0 for key in (
            "pr_auc", "roc_auc", "brier", "ece", "f1", "precision", "recall",
            "recall_at_3", "ndcg_at_3", "hit_at_1", "severity_mae", "severity_spearman",
        )}
    result = {
        "pr_auc": (
            float(average_precision_score(labels_array, probability_array))
            if np.unique(labels_array).size == 2
            else float(labels_array.mean())
        ),
        "roc_auc": float(roc_auc_score(labels_array, probability_array)) if np.unique(labels_array).size == 2 else 0.5,
        "brier": float(brier_score_loss(labels_array, probability_array)),
        "ece": expected_calibration_error(labels_array, probability_array),
        "f1": float(f1_score(labels_array, predicted, zero_division=0)),
        "precision": float(precision_score(labels_array, predicted, zero_division=0)),
        "recall": float(recall_score(labels_array, predicted, zero_division=0)),
        "recall_at_3": float(np.mean(recalls)) if recalls else 0.0,
        "ndcg_at_3": float(np.mean(ndcgs)) if ndcgs else 0.0,
        "hit_at_1": float(np.mean(hits)) if hits else 0.0,
    }
    severity_true = np.asarray(severity_true_flat, dtype=float)
    severity_pred = np.asarray(severity_pred_flat, dtype=float)
    result["severity_mae"] = float(np.mean(np.abs(severity_true - severity_pred))) if severity_true.size else 0.0
    result["severity_spearman"] = _spearman(severity_true, severity_pred)
    return result


def prediction_rows(
    samples: list[RuntimeNFRSample],
    probabilities: list[np.ndarray],
    severity_scores: list[np.ndarray],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample, probability, severity in zip(samples, probabilities, severity_scores):
        valid_indices = [index for index, value in enumerate(probability) if np.isfinite(value)]
        order = sorted(valid_indices, key=lambda index: (-probability[index], index))
        rank = {app_index: position + 1 for position, app_index in enumerate(order)}
        for app_index, app_id in enumerate(sample.node_ids["Vbiz"]):
            rows.append(
                {
                    "event_id": sample.metadata.event_id,
                    "record_id": sample.metadata.record_id,
                    "segment_id": sample.metadata.segment_id,
                    "event_start": sample.metadata.event_start.isoformat(),
                    "topology_hash": sample.metadata.topology_hash,
                    "fault_family": sample.metadata.fault_family,
                    "target_type": sample.metadata.target_type,
                    "app_id": app_id,
                    "valid": bool(sample.label_mask[app_index]),
                    "breach_label": int(sample.breach_labels[app_index]),
                    "severity_raw": float(sample.severity_raw[app_index]),
                    "breach_probability": None if not np.isfinite(probability[app_index]) else float(probability[app_index]),
                    "severity_score": None if not np.isfinite(severity[app_index]) else float(severity[app_index]),
                    "rank": rank.get(app_index),
                    "early_request_volume": float(sample.early_request_volume[app_index]),
                    "invalid_reason": sample.invalid_reasons.get(app_id),
                }
            )
    return rows


def event_bootstrap_prior_delta(
    samples: list[RuntimeNFRSample],
    probabilities: list[np.ndarray],
    *,
    iterations: int = 2000,
    seed: int = 42,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    event_payload = []
    for sample, probability in zip(samples, probabilities):
        mask = sample.label_mask.detach().cpu().numpy().astype(bool) & np.isfinite(probability)
        event_payload.append((sample.breach_labels.detach().cpu().numpy()[mask], probability[mask]))
    observed_labels = np.concatenate([item[0] for item in event_payload if item[0].size])
    observed_prob = np.concatenate([item[1] for item in event_payload if item[1].size])
    if observed_labels.size == 0:
        return {"delta": 0.0, "lower": 0.0, "upper": 0.0}
    if np.unique(observed_labels).size < 2:
        return {"delta": 0.0, "lower": 0.0, "upper": 0.0}
    prevalence = float(observed_labels.mean())

    def delta(labels: np.ndarray, prediction: np.ndarray) -> float:
        return float(average_precision_score(labels, prediction) - average_precision_score(labels, np.full(labels.shape, prevalence)))

    values = []
    for _ in range(iterations):
        indices = rng.integers(0, len(event_payload), len(event_payload))
        labels = np.concatenate([event_payload[index][0] for index in indices if event_payload[index][0].size])
        prediction = np.concatenate([event_payload[index][1] for index in indices if event_payload[index][1].size])
        if labels.size and np.unique(labels).size == 2:
            values.append(delta(labels, prediction))
    return {
        "delta": delta(observed_labels, observed_prob),
        "lower": float(np.quantile(values, 0.025)) if values else 0.0,
        "upper": float(np.quantile(values, 0.975)) if values else 0.0,
    }


def event_bootstrap_recall_at_k_delta(
    samples: list[RuntimeNFRSample],
    probabilities: list[np.ndarray],
    *,
    k: int = 3,
    iterations: int = 2000,
    seed: int = 42,
) -> dict[str, float]:
    """Paired event bootstrap of Recall@k minus random-ranking expectation."""

    event_deltas: list[float] = []
    model_recalls: list[float] = []
    random_recalls: list[float] = []
    for sample, probability in zip(samples, probabilities):
        mask = sample.label_mask.detach().cpu().numpy().astype(bool) & np.isfinite(probability)
        labels = sample.breach_labels.detach().cpu().numpy()[mask].astype(bool)
        if labels.size == 0 or not labels.any():
            continue
        order = np.argsort(-probability[mask])[: min(k, labels.size)]
        model_recall = float(labels[order].sum() / labels.sum())
        random_recall = float(min(k, labels.size) / labels.size)
        model_recalls.append(model_recall)
        random_recalls.append(random_recall)
        event_deltas.append(model_recall - random_recall)
    if not event_deltas:
        return {
            "model_recall_at_k": 0.0,
            "random_expectation": 0.0,
            "delta": 0.0,
            "lower": 0.0,
            "upper": 0.0,
        }
    rng = np.random.default_rng(seed)
    values = np.asarray(event_deltas, dtype=float)
    bootstrap = [
        float(values[rng.integers(0, len(values), len(values))].mean())
        for _ in range(iterations)
    ]
    return {
        "model_recall_at_k": float(np.mean(model_recalls)),
        "random_expectation": float(np.mean(random_recalls)),
        "delta": float(values.mean()),
        "lower": float(np.quantile(bootstrap, 0.025)),
        "upper": float(np.quantile(bootstrap, 0.975)),
    }


class RuntimeNFRBaselineTrainer:
    def __init__(self, model_name: str) -> None:
        if model_name not in BASELINE_MODELS:
            raise ValueError(f"unsupported baseline: {model_name}")
        self.model_name = model_name
        self.classifier: Any = None
        self.regressor: Any = None
        self.prior = 0.5
        self.severity_scale = 1.0
        self.threshold = 0.5

    def fit(self, train: list[RuntimeNFRSample], val: list[RuntimeNFRSample]) -> None:
        self.severity_scale = fit_severity_scale(train)
        x_train, y_train, severity_train, _ = _event_feature_matrix(train)
        self.prior = float(y_train.mean()) if y_train.size else 0.5
        train_has_both_classes = y_train.size > 0 and np.unique(y_train).size == 2
        if self.model_name == "logistic" and train_has_both_classes:
            self.classifier = make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000, random_state=42))
            self.classifier.fit(x_train, y_train)
            self.regressor = make_pipeline(StandardScaler(), HistGradientBoostingRegressor(max_iter=150, random_state=42))
        elif self.model_name == "hgb" and train_has_both_classes:
            self.classifier = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, random_state=42)
            positive = max(1, int((y_train == 1).sum()))
            negative = max(1, int((y_train == 0).sum()))
            weights = np.where(y_train == 1, negative / positive, 1.0)
            self.classifier.fit(x_train, y_train, sample_weight=weights)
            self.regressor = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, random_state=42)
        if self.regressor is not None:
            positives = y_train.astype(bool)
            if positives.any():
                self.regressor.fit(x_train[positives], np.clip(severity_train[positives] / self.severity_scale, 0.0, 1.0))
            else:
                self.regressor = None
        val_probabilities, _ = self.predict(val)
        label_parts = [sample.breach_labels[sample.label_mask.bool()].numpy() for sample in val]
        probability_parts = [value[np.isfinite(value)] for value in val_probabilities]
        labels = np.concatenate(label_parts).astype(int) if label_parts else np.asarray([], dtype=int)
        probabilities = np.concatenate(probability_parts) if probability_parts else np.asarray([], dtype=float)
        self.threshold = select_f1_threshold(labels, probabilities)

    def predict(self, samples: list[RuntimeNFRSample]) -> tuple[list[np.ndarray], list[np.ndarray]]:
        x, _, _, mapping = _event_feature_matrix(samples)
        if self.model_name == "prior":
            probability = np.full(len(mapping), self.prior, dtype=float)
            severity = np.full(len(mapping), self.prior, dtype=float)
        elif self.model_name == "persistence":
            probability, severity = [], []
            for event_index, app_index in mapping:
                sample = samples[event_index]
                early_steps = max(1, int((sample.metadata.input_end - sample.metadata.event_minute).total_seconds() // 60))
                values = sample.features["Vbiz"][-early_steps:, app_index]
                observed = sample.masks["Vbiz"][-early_steps:, app_index]
                exceedance = ((values[:, 4] * observed[:, 4]) + (values[:, 5] * observed[:, 5])).clamp(0.0, 1.0)
                probability.append(float(exceedance.mean()))
                severity.append(float(exceedance.max()))
            probability = np.asarray(probability)
            severity = np.asarray(severity)
        elif self.classifier is not None:
            probability = self.classifier.predict_proba(x)[:, 1]
            severity = self.regressor.predict(x) if self.regressor is not None else probability.copy()
        else:
            probability = np.full(len(mapping), self.prior, dtype=float)
            severity = probability.copy()
        return _event_arrays_from_flat(samples, mapping, np.clip(probability, 0.0, 1.0), np.clip(severity, 0.0, 1.0))

    def save(self, path: str | Path, config: dict[str, Any]) -> None:
        joblib.dump(
            {
                "model_name": self.model_name,
                "classifier": self.classifier,
                "regressor": self.regressor,
                "prior": self.prior,
                "severity_scale": self.severity_scale,
                "threshold": self.threshold,
                "config": config,
            },
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> tuple["RuntimeNFRBaselineTrainer", dict[str, Any]]:
        payload = joblib.load(path)
        trainer = cls(payload["model_name"])
        for key in ("classifier", "regressor", "prior", "severity_scale", "threshold"):
            setattr(trainer, key, payload[key])
        return trainer, payload.get("config", {})


class RuntimeNFRNeuralTrainer:
    def __init__(
        self,
        model: RuntimeNFRPredictor,
        *,
        learning_rate: float = 1e-3,
        weight_decay: float = 1e-5,
        severity_weight: float = 0.5,
        rank_weight: float = 0.25,
        device: str = "cpu",
    ) -> None:
        self.model = model.to(device)
        self.device = torch.device(device)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=learning_rate, weight_decay=weight_decay)
        self.severity_weight = severity_weight
        self.rank_weight = rank_weight
        self.standardizer = RuntimeFeatureStandardizer()
        self.severity_scale = 1.0
        self.threshold = 0.5
        self.temperature = 1.0

    def _loss_fn(self, train: list[RuntimeNFRSample]) -> RuntimeNFRLoss:
        positive = sum(int((sample.breach_labels.bool() & sample.label_mask.bool()).sum()) for sample in train)
        valid = sum(int(sample.label_mask.sum()) for sample in train)
        negative = max(1, valid - positive)
        return RuntimeNFRLoss(
            positive_weight=negative / max(1, positive),
            severity_weight=self.severity_weight,
            rank_weight=self.rank_weight,
        ).to(self.device)

    def _predict(
        self,
        samples: list[RuntimeNFRSample],
        *,
        training: bool,
        loss_fn: RuntimeNFRLoss,
    ) -> tuple[float, list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
        self.model.train(training)
        total = 0.0
        probabilities, severities, logits = [], [], []
        for raw_sample in samples:
            assert_runtime_nfr_temporal_contract(raw_sample)
            sample = self.standardizer.transform(raw_sample)
            output = self.model(sample)
            breach = sample.breach_labels.to(self.device)
            severity = _scaled_severity(sample, self.severity_scale).to(self.device)
            valid = sample.label_mask.to(self.device)
            loss, _ = loss_fn(output, breach, severity, valid)
            if training:
                self.optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                self.optimizer.step()
            total += float(loss.detach().cpu())
            probability = torch.sigmoid(output.breach_logit / self.temperature)
            mask = sample.label_mask.detach().cpu().numpy().astype(bool)
            prob = output.breach_probability.detach().cpu().numpy().astype(float)
            calibrated = probability.detach().cpu().numpy().astype(float)
            sev = output.severity_score.detach().cpu().numpy().astype(float)
            logit = output.breach_logit.detach().cpu().numpy().astype(float)
            prob[~mask] = np.nan
            calibrated[~mask] = np.nan
            sev[~mask] = np.nan
            logit[~mask] = np.nan
            probabilities.append(calibrated if not training else prob)
            severities.append(sev)
            logits.append(logit)
        return total / max(1, len(samples)), probabilities, severities, logits

    def _fit_temperature(self, logits: np.ndarray, labels: np.ndarray) -> None:
        if logits.size == 0 or np.unique(labels).size < 2:
            self.temperature = 1.0
            return
        logit_tensor = torch.tensor(logits, dtype=torch.float32, device=self.device)
        label_tensor = torch.tensor(labels, dtype=torch.float32, device=self.device)
        log_temperature = torch.zeros((), requires_grad=True, device=self.device)
        optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=50)

        def closure() -> torch.Tensor:
            optimizer.zero_grad()
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                logit_tensor / log_temperature.exp().clamp(0.05, 20.0), label_tensor
            )
            loss.backward()
            return loss

        optimizer.step(closure)
        self.temperature = float(log_temperature.exp().detach().clamp(0.05, 20.0).cpu())

    def fit(
        self,
        train: list[RuntimeNFRSample],
        val: list[RuntimeNFRSample],
        *,
        epochs: int,
        output_dir: str | Path,
        config: dict[str, Any],
        seed: int,
        patience: int | None = None,
    ) -> dict[str, Any]:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        # Model construction normally happens before ``fit``. Reinitializing
        # after seeding makes the documented seed cover the initial weights as
        # well as data order, dropout, and optimization.
        for module in self.model.modules():
            reset = getattr(module, "reset_parameters", None)
            if callable(reset):
                reset()
        self.standardizer.fit(train)
        self.severity_scale = fit_severity_scale(train)
        loss_fn = self._loss_fn(train)
        best_ap = -1.0
        best_state: dict[str, torch.Tensor] | None = None
        best_epoch = -1
        epochs_without_improvement = 0
        history = []
        for epoch in range(epochs):
            train_loss, train_probability, train_severity, _ = self._predict(train, training=True, loss_fn=loss_fn)
            with torch.no_grad():
                val_loss, val_probability, val_severity, _ = self._predict(val, training=False, loss_fn=loss_fn)
            train_metrics = prediction_metrics(train, train_probability, train_severity, severity_scale=self.severity_scale, threshold=0.5)
            val_metrics = prediction_metrics(val, val_probability, val_severity, severity_scale=self.severity_scale, threshold=0.5)
            history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss, "train": train_metrics, "val": val_metrics})
            if val_metrics["pr_auc"] > best_ap:
                best_ap = val_metrics["pr_auc"]
                best_epoch = epoch
                epochs_without_improvement = 0
                best_state = {key: value.detach().cpu().clone() for key, value in self.model.state_dict().items()}
            else:
                epochs_without_improvement += 1
            if patience is not None and epoch >= 9 and epochs_without_improvement >= patience:
                break
        if best_state is None:
            raise RuntimeError("neural training produced no checkpoint")
        self.model.load_state_dict(best_state)
        with torch.no_grad():
            _, _, _, val_logits = self._predict(val, training=False, loss_fn=loss_fn)
        flat_logits = np.concatenate([value[np.isfinite(value)] for value in val_logits])
        val_labels = np.concatenate([sample.breach_labels[sample.label_mask.bool()].numpy() for sample in val]).astype(int)
        self._fit_temperature(flat_logits, val_labels)
        with torch.no_grad():
            _, val_probability, _, _ = self._predict(val, training=False, loss_fn=loss_fn)
        flat_probability = np.concatenate([value[np.isfinite(value)] for value in val_probability])
        self.threshold = select_f1_threshold(val_labels, flat_probability)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "model_state": self.model.state_dict(),
            "standardizer": self.standardizer.state_dict(),
            "severity_scale": self.severity_scale,
            "threshold": self.threshold,
            "temperature": self.temperature,
            "config": config,
        }
        torch.save(checkpoint, output / "best_model.pt")
        payload = {
            "config": config,
            "history": history,
            "best_val_pr_auc": best_ap,
            "best_epoch": best_epoch,
            "epochs_completed": len(history),
            "early_stopping_patience": patience,
            "threshold": self.threshold,
            "temperature": self.temperature,
        }
        (output / "training_history.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def predict(self, samples: list[RuntimeNFRSample]) -> tuple[list[np.ndarray], list[np.ndarray]]:
        loss_fn = RuntimeNFRLoss().to(self.device)
        with torch.no_grad():
            _, probability, severity, _ = self._predict(samples, training=False, loss_fn=loss_fn)
        return probability, severity

    def load_checkpoint(self, path: str | Path) -> dict[str, Any]:
        payload = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(payload["model_state"])
        self.standardizer.load_state_dict(payload["standardizer"])
        self.severity_scale = float(payload["severity_scale"])
        self.threshold = float(payload["threshold"])
        self.temperature = float(payload.get("temperature", 1.0))
        return payload


def evaluate_predictions(
    samples: list[RuntimeNFRSample],
    probabilities: list[np.ndarray],
    severity_scores: list[np.ndarray],
    *,
    severity_scale: float,
    threshold: float,
    train_topology_hashes: set[str] | None = None,
    bootstrap_iterations: int = 2000,
) -> dict[str, Any]:
    metrics = prediction_metrics(
        samples,
        probabilities,
        severity_scores,
        severity_scale=severity_scale,
        threshold=threshold,
    )
    subgroups: dict[str, dict[str, float]] = {}
    group_values: dict[str, list[int]] = {}
    for index, sample in enumerate(samples):
        keys = [
            f"fault_family={sample.metadata.fault_family}",
            f"target_type={sample.metadata.target_type}",
            f"segment={sample.metadata.segment_id}",
        ]
        if train_topology_hashes is not None:
            keys.append(f"topology_seen={sample.metadata.topology_hash in train_topology_hashes}")
        for key in keys:
            group_values.setdefault(key, []).append(index)
    for key, indices in sorted(group_values.items()):
        subgroups[key] = prediction_metrics(
            [samples[index] for index in indices],
            [probabilities[index] for index in indices],
            [severity_scores[index] for index in indices],
            severity_scale=severity_scale,
            threshold=threshold,
        )
    rows = prediction_rows(samples, probabilities, severity_scores)
    node_aggregates = []
    for sample, probability in zip(samples, probabilities):
        clean_probability = np.nan_to_num(probability, nan=0.0)
        node_aggregates.append(
            {
                "event_id": sample.metadata.event_id,
                "aggregates": aggregate_app_predictions(sample, clean_probability),
            }
        )
    return {
        "num_events": len(samples),
        "valid_app_event_pairs": int(sum(int(sample.label_mask.sum()) for sample in samples)),
        "threshold": threshold,
        "severity_scale_train_q99": severity_scale,
        "metrics": metrics,
        "bootstrap_pr_auc_delta_vs_prior": event_bootstrap_prior_delta(
            samples,
            probabilities,
            iterations=bootstrap_iterations,
        ),
        "bootstrap_recall_at_3_delta_vs_random": event_bootstrap_recall_at_k_delta(
            samples,
            probabilities,
            k=3,
            iterations=bootstrap_iterations,
        ),
        "subgroups": subgroups,
        "predictions": rows,
        "node_aggregates": node_aggregates,
    }
