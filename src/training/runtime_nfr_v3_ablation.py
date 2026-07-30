"""Frozen, leakage-safe feature ablation for the Runtime NFR v3 paper."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, ndcg_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..data.intervention_dataset import CANONICAL_CHANNELS
from ..data.runtime_nfr_dataset import RUNTIME_APP_CHANNELS, RuntimeNFRSample
from ..data.runtime_nfr_v2 import development_rolling_folds, topology_feature_matrix
from .runtime_nfr_trainer import _event_feature_matrix, expected_calibration_error


ABLATION_PROTOCOL = "runtime-nfr-v3-feature-ablation/1"
MODEL_VARIANTS = (
    "category_prior",
    "persistence",
    "app_logistic",
    "app_hgb",
    "app_infra_logistic",
    "app_infra_topology_logistic",
)
FORBIDDEN_FEATURE_TOKENS = (
    "fault_type",
    "fault_family",
    "fault_target",
    "target_type",
    "target_node",
    "duration",
    "impact_score",
    "label",
    "future",
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


def app_feature_names() -> list[str]:
    names: list[str] = []
    for channel in RUNTIME_APP_CHANNELS:
        names.extend(f"app_early_{stat}_{channel}" for stat in ("mean", "std", "max", "last", "trend"))
    for channel in RUNTIME_APP_CHANNELS:
        names.extend(f"app_history_{stat}_{channel}" for stat in ("mean", "std"))
    return names


def _summarize_node_context(
    values: np.ndarray,
    masks: np.ndarray,
    node_indices: Sequence[int],
    early_steps: int,
) -> list[float]:
    if not node_indices:
        return [0.0] * (4 * len(CANONICAL_CHANNELS) + 3)
    selected_values = values[:, list(node_indices), :]
    selected_masks = masks[:, list(node_indices), :]
    history_values = selected_values[:-early_steps]
    history_masks = selected_masks[:-early_steps]
    early_values = selected_values[-early_steps:]
    early_masks = selected_masks[-early_steps:]
    output: list[float] = []
    for window_values, window_masks in (
        (early_values, early_masks),
        (history_values, history_masks),
    ):
        masked = np.where(window_masks > 0, window_values, np.nan)
        for channel in range(masked.shape[-1]):
            finite = masked[..., channel][np.isfinite(masked[..., channel])]
            output.extend(
                [float(finite.mean()), float(finite.std())]
                if finite.size
                else [0.0, 0.0]
            )
    output.extend(
        [
            float(early_masks.mean()) if early_masks.size else 0.0,
            float(history_masks.mean()) if history_masks.size else 0.0,
            float(len(node_indices)),
        ]
    )
    return output


def infrastructure_feature_names() -> list[str]:
    names: list[str] = []
    for node_type in ("pod", "host"):
        for window in ("early", "history"):
            for channel in CANONICAL_CHANNELS:
                names.extend(
                    (
                        f"{node_type}_{window}_mean_{channel}",
                        f"{node_type}_{window}_std_{channel}",
                    )
                )
        names.extend(
            (
                f"{node_type}_early_observation",
                f"{node_type}_history_observation",
                f"{node_type}_node_count",
            )
        )
    return names


def infrastructure_feature_matrix(
    samples: Sequence[RuntimeNFRSample],
) -> tuple[np.ndarray, list[tuple[int, int]], list[str]]:
    """Aggregate only pre-cutoff pod/host telemetry deployed beneath each app."""

    rows: list[list[float]] = []
    mapping: list[tuple[int, int]] = []
    for event_index, sample in enumerate(samples):
        graph = sample.current_graph
        deployed: dict[int, set[int]] = defaultdict(set)
        vm_hosts: dict[int, set[int]] = defaultdict(set)
        deployment_type = ("Vvm", "r_deployment", "Vbiz")
        hosting_type = ("Vphy", "r_hosting", "Vvm")
        if deployment_type in graph.canonical_etypes:
            vm_indices, app_indices = graph.edges(etype=deployment_type)
            for vm_index, app_index in zip(vm_indices.tolist(), app_indices.tolist()):
                deployed[int(app_index)].add(int(vm_index))
        if hosting_type in graph.canonical_etypes:
            host_indices, vm_indices = graph.edges(etype=hosting_type)
            for host_index, vm_index in zip(host_indices.tolist(), vm_indices.tolist()):
                vm_hosts[int(vm_index)].add(int(host_index))
        early_steps = max(
            1,
            int(
                (
                    sample.metadata.input_end - sample.metadata.event_minute
                ).total_seconds()
                // 60
            ),
        )
        pod_values = sample.features["Vvm"].detach().cpu().numpy()
        pod_masks = sample.masks["Vvm"].detach().cpu().numpy()
        host_values = sample.features["Vphy"].detach().cpu().numpy()
        host_masks = sample.masks["Vphy"].detach().cpu().numpy()
        for app_index in range(len(sample.node_ids["Vbiz"])):
            if not bool(sample.label_mask[app_index]):
                continue
            pods = sorted(deployed.get(app_index, set()))
            hosts = sorted({host for pod in pods for host in vm_hosts.get(pod, set())})
            rows.append(
                [
                    *_summarize_node_context(
                        pod_values, pod_masks, pods, early_steps
                    ),
                    *_summarize_node_context(
                        host_values, host_masks, hosts, early_steps
                    ),
                ]
            )
            mapping.append((event_index, app_index))
    return np.asarray(rows, dtype=float), mapping, infrastructure_feature_names()


def _balanced_weights(labels: np.ndarray) -> np.ndarray:
    counts = np.bincount(labels.astype(int), minlength=2).astype(float)
    weights = np.ones(labels.size, dtype=float)
    for label in (0, 1):
        if counts[label] > 0:
            weights[labels == label] = labels.size / (2.0 * counts[label])
    return weights


def _fit_classifier(
    model: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_eval: np.ndarray,
    *,
    seed: int,
) -> np.ndarray:
    if np.unique(y_train).size < 2:
        return np.full(x_eval.shape[0], float(y_train.mean()), dtype=float)
    if model == "logistic":
        estimator = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                class_weight="balanced",
                max_iter=3000,
                random_state=seed,
            ),
        )
        estimator.fit(x_train, y_train)
    elif model == "hgb":
        estimator = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=200,
            max_leaf_nodes=15,
            l2_regularization=1.0,
            random_state=seed,
        )
        estimator.fit(x_train, y_train, sample_weight=_balanced_weights(y_train))
    else:
        raise ValueError(f"unknown classifier: {model}")
    return estimator.predict_proba(x_eval)[:, 1]


def _fit_severity(
    x_train: np.ndarray,
    y_train: np.ndarray,
    severity_train: np.ndarray,
    x_eval: np.ndarray,
    *,
    seed: int,
) -> np.ndarray:
    positive = y_train.astype(bool) & np.isfinite(severity_train)
    if positive.sum() < 3:
        fallback = float(np.nanmean(severity_train[positive])) if positive.any() else 0.0
        return np.full(x_eval.shape[0], fallback, dtype=float)
    regressor = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=150,
        max_leaf_nodes=15,
        l2_regularization=1.0,
        random_state=seed,
    )
    regressor.fit(x_train[positive], severity_train[positive])
    return np.maximum(0.0, regressor.predict(x_eval))


def _persistence_probability(x_app: np.ndarray) -> np.ndarray:
    positions = [
        RUNTIME_APP_CHANNELS.index("latency_exceedance") * 5,
        RUNTIME_APP_CHANNELS.index("error_exceedance") * 5,
    ]
    return np.clip(np.max(x_app[:, positions], axis=1), 0.0, 1.0)


def _variant_features(
    samples: Sequence[RuntimeNFRSample],
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, list[tuple[int, int]], dict[str, list[str]]]:
    x_app, labels, severity, mapping = _event_feature_matrix(list(samples))
    x_infra, infra_mapping, infra_names = infrastructure_feature_matrix(samples)
    x_topology, topology_mapping, topology_names = topology_feature_matrix(samples)
    if mapping != infra_mapping or mapping != topology_mapping:
        raise AssertionError("feature families use different event/App row order")
    app_names = app_feature_names()
    names = {
        "app": app_names,
        "app_infra": [*app_names, *infra_names],
        "app_infra_topology": [*app_names, *infra_names, *topology_names],
    }
    for family_names in names.values():
        lowered = [name.lower() for name in family_names]
        forbidden = sorted(
            token
            for token in FORBIDDEN_FEATURE_TOKENS
            if any(token in name for name in lowered)
        )
        if forbidden:
            raise AssertionError(f"forbidden model inputs detected: {forbidden}")
    matrices = {
        "app": x_app,
        "app_infra": np.column_stack((x_app, x_infra)),
        "app_infra_topology": np.column_stack((x_app, x_infra, x_topology)),
    }
    return matrices, labels, severity, mapping, names


def metrics_from_oof(frame: pd.DataFrame) -> dict[str, float]:
    labels = frame["breach_label"].to_numpy(dtype=int)
    probabilities = frame["breach_probability"].to_numpy(dtype=float)
    result = {
        "pr_auc": (
            float(average_precision_score(labels, probabilities))
            if np.unique(labels).size == 2
            else float(labels.mean())
        ),
        "brier": float(brier_score_loss(labels, probabilities)),
        "ece": float(expected_calibration_error(labels, probabilities)),
    }
    recalls: list[float] = []
    ndcgs: list[float] = []
    hits: list[float] = []
    for _, event in frame.groupby("event_id", sort=False):
        event_labels = event["breach_label"].to_numpy(dtype=int)
        event_probabilities = event["breach_probability"].to_numpy(dtype=float)
        positive = event_labels.astype(bool)
        if not positive.any():
            continue
        order = np.argsort(-event_probabilities, kind="stable")
        top = order[: min(3, len(order))]
        recalls.append(float(positive[top].sum() / positive.sum()))
        hits.append(float(positive[order[0]]))
        relevance = np.maximum(
            event["severity_raw"].to_numpy(dtype=float),
            event_labels.astype(float),
        )
        ndcgs.append(
            float(
                ndcg_score(
                    relevance[None, :],
                    event_probabilities[None, :],
                    k=min(3, len(order)),
                )
            )
        )
    result.update(
        {
            "recall_at_3": float(np.mean(recalls)) if recalls else 0.0,
            "ndcg_at_3": float(np.mean(ndcgs)) if ndcgs else 0.0,
            "hit_at_1": float(np.mean(hits)) if hits else 0.0,
        }
    )
    positive = labels.astype(bool)
    severity_true = frame.loc[positive, "severity_raw"].to_numpy(dtype=float)
    severity_pred = frame.loc[positive, "severity_prediction"].to_numpy(dtype=float)
    result["severity_mae"] = (
        float(np.mean(np.abs(severity_true - severity_pred)))
        if severity_true.size
        else 0.0
    )
    correlation = (
        spearmanr(severity_true, severity_pred).statistic
        if severity_true.size >= 2
        and np.std(severity_true) > 0
        and np.std(severity_pred) > 0
        else np.nan
    )
    result["severity_spearman"] = (
        float(correlation) if np.isfinite(correlation) else 0.0
    )
    return result


def _bootstrap_payloads(
    frame: pd.DataFrame,
    *,
    unit_column: str,
) -> dict[str, dict[str, np.ndarray]]:
    """Precompute unit-level arrays so 2,000 paired draws remain practical."""

    payloads: dict[str, dict[str, np.ndarray]] = {}
    for unit, unit_frame in frame.groupby(unit_column, sort=False):
        recalls: list[float] = []
        ndcgs: list[float] = []
        for _, event in unit_frame.groupby("event_id", sort=False):
            labels = event["breach_label"].to_numpy(dtype=int)
            probabilities = event["breach_probability"].to_numpy(dtype=float)
            positive = labels.astype(bool)
            if not positive.any():
                continue
            order = np.argsort(-probabilities, kind="stable")
            top = order[: min(3, len(order))]
            recalls.append(float(positive[top].sum() / positive.sum()))
            relevance = np.maximum(
                event["severity_raw"].to_numpy(dtype=float),
                labels.astype(float),
            )
            ndcgs.append(
                float(
                    ndcg_score(
                        relevance[None, :],
                        probabilities[None, :],
                        k=min(3, len(order)),
                    )
                )
            )
        payloads[str(unit)] = {
            "labels": unit_frame["breach_label"].to_numpy(dtype=int),
            "probabilities": unit_frame["breach_probability"].to_numpy(dtype=float),
            "recall_at_3": np.asarray(recalls, dtype=float),
            "ndcg_at_3": np.asarray(ndcgs, dtype=float),
        }
    return payloads


def _metrics_from_bootstrap_draw(
    payloads: dict[str, dict[str, np.ndarray]],
    sampled_units: Iterable[str],
) -> dict[str, float]:
    selected = [payloads[str(unit)] for unit in sampled_units]
    labels = np.concatenate([payload["labels"] for payload in selected])
    probabilities = np.concatenate(
        [payload["probabilities"] for payload in selected]
    )
    recalls = np.concatenate(
        [payload["recall_at_3"] for payload in selected]
    )
    ndcgs = np.concatenate([payload["ndcg_at_3"] for payload in selected])
    return {
        "pr_auc": (
            float(average_precision_score(labels, probabilities))
            if np.unique(labels).size == 2
            else float(labels.mean())
        ),
        "recall_at_3": float(recalls.mean()) if recalls.size else 0.0,
        "ndcg_at_3": float(ndcgs.mean()) if ndcgs.size else 0.0,
    }


def paired_bootstrap_delta(
    oof: pd.DataFrame,
    *,
    challenger: str,
    reference: str,
    iterations: int,
    seed: int,
    unit_column: str = "event_id",
) -> dict[str, Any]:
    left = oof[oof["model"] == challenger].copy()
    right = oof[oof["model"] == reference].copy()
    keys = ["event_id", "app_id"]
    if set(map(tuple, left[keys].to_numpy())) != set(map(tuple, right[keys].to_numpy())):
        raise AssertionError("paired models do not use the same event/App rows")
    observed_left = metrics_from_oof(left)
    observed_right = metrics_from_oof(right)
    metric_names = ("pr_auc", "recall_at_3", "ndcg_at_3")
    units = sorted(set(left[unit_column].astype(str)))
    left_payloads = _bootstrap_payloads(left, unit_column=unit_column)
    right_payloads = _bootstrap_payloads(right, unit_column=unit_column)
    rng = np.random.default_rng(seed)
    draws = {name: [] for name in metric_names}
    for _ in range(iterations):
        sampled = rng.choice(units, size=len(units), replace=True)
        left_metrics = _metrics_from_bootstrap_draw(left_payloads, sampled)
        right_metrics = _metrics_from_bootstrap_draw(right_payloads, sampled)
        for name in metric_names:
            draws[name].append(left_metrics[name] - right_metrics[name])
    metrics = {}
    for name in metric_names:
        values = np.asarray(draws[name], dtype=float)
        metrics[name] = {
            "observed_delta": float(observed_left[name] - observed_right[name]),
            "ci95_low": float(np.quantile(values, 0.025)),
            "ci95_high": float(np.quantile(values, 0.975)),
            "ci95_lower_above_zero": bool(np.quantile(values, 0.025) > 0),
            "increment_gate_pass": (
                bool(np.quantile(values, 0.025) > 0)
                if name == "pr_auc"
                else None
            ),
        }
    return {
        "challenger": challenger,
        "reference": reference,
        "unit": unit_column,
        "iterations": iterations,
        "seed": seed,
        "metrics": metrics,
    }


def run_feature_ablation(
    samples: Sequence[RuntimeNFRSample],
    output_dir: str | Path,
    *,
    n_splits: int = 5,
    bootstrap_iterations: int = 2000,
    seed: int = 20260723,
) -> dict[str, Any]:
    """Run fixed model variants on rolling development-only OOF folds."""

    folds = development_rolling_folds(samples, n_splits=n_splits)
    rows: list[dict[str, Any]] = []
    fold_manifest: list[dict[str, Any]] = []
    feature_names: dict[str, list[str]] = {}
    for fold_index, (train_indices, eval_indices) in enumerate(folds):
        train = [samples[index] for index in train_indices]
        evaluation = [samples[index] for index in eval_indices]
        x_train, y_train, severity_train, train_mapping, train_names = _variant_features(train)
        x_eval, y_eval, severity_eval, eval_mapping, eval_names = _variant_features(evaluation)
        if train_names != eval_names:
            raise AssertionError("train/evaluation feature schemas differ")
        feature_names = train_names
        train_prior = float(y_train.mean())
        train_severity_prior = (
            float(severity_train[y_train.astype(bool)].mean())
            if y_train.astype(bool).any()
            else 0.0
        )
        predictions = {
            "category_prior": np.full(y_eval.size, train_prior, dtype=float),
            "persistence": _persistence_probability(x_eval["app"]),
            "app_logistic": _fit_classifier(
                "logistic", x_train["app"], y_train, x_eval["app"], seed=seed + fold_index
            ),
            "app_hgb": _fit_classifier(
                "hgb", x_train["app"], y_train, x_eval["app"], seed=seed + fold_index
            ),
            "app_infra_logistic": _fit_classifier(
                "logistic",
                x_train["app_infra"],
                y_train,
                x_eval["app_infra"],
                seed=seed + fold_index,
            ),
            "app_infra_topology_logistic": _fit_classifier(
                "logistic",
                x_train["app_infra_topology"],
                y_train,
                x_eval["app_infra_topology"],
                seed=seed + fold_index,
            ),
        }
        severity_predictions = {
            "category_prior": np.full(y_eval.size, train_severity_prior, dtype=float),
            "persistence": np.full(y_eval.size, train_severity_prior, dtype=float),
        }
        for variant, family in (
            ("app_logistic", "app"),
            ("app_hgb", "app"),
            ("app_infra_logistic", "app_infra"),
            ("app_infra_topology_logistic", "app_infra_topology"),
        ):
            severity_predictions[variant] = _fit_severity(
                x_train[family],
                y_train,
                severity_train,
                x_eval[family],
                seed=seed + fold_index,
            )
        for row_index, (event_local_index, app_index) in enumerate(eval_mapping):
            sample = evaluation[event_local_index]
            base = {
                "fold": fold_index,
                "event_id": sample.metadata.event_id,
                "event_date": sample.metadata.event_start.date().isoformat(),
                "segment_id": sample.metadata.segment_id,
                "app_id": sample.node_ids["Vbiz"][app_index],
                "breach_label": int(y_eval[row_index]),
                "severity_raw": float(severity_eval[row_index]),
            }
            for variant in MODEL_VARIANTS:
                rows.append(
                    {
                        **base,
                        "model": variant,
                        "breach_probability": float(predictions[variant][row_index]),
                        "severity_prediction": float(
                            severity_predictions[variant][row_index]
                        ),
                    }
                )
        fold_manifest.append(
            {
                "fold": fold_index,
                "train_event_ids": [sample.metadata.event_id for sample in train],
                "evaluation_event_ids": [
                    sample.metadata.event_id for sample in evaluation
                ],
                "train_rows": len(train_mapping),
                "evaluation_rows": len(eval_mapping),
                "train_positive_rate": train_prior,
            }
        )
    oof = pd.DataFrame(rows)
    expected_models = set(MODEL_VARIANTS)
    if set(oof["model"]) != expected_models:
        raise AssertionError("not all frozen variants produced OOF predictions")
    reference_pairs = set(
        map(
            tuple,
            oof[oof["model"] == MODEL_VARIANTS[0]][["event_id", "app_id"]].to_numpy(),
        )
    )
    for model in MODEL_VARIANTS:
        model_rows = oof[oof["model"] == model]
        if model_rows.duplicated(["event_id", "app_id"]).any():
            raise AssertionError(f"OOF event/App row repeated for {model}")
        if set(map(tuple, model_rows[["event_id", "app_id"]].to_numpy())) != reference_pairs:
            raise AssertionError("feature ablation variants use different OOF rows")

    summary_by_model = {
        model: metrics_from_oof(oof[oof["model"] == model])
        for model in MODEL_VARIANTS
    }
    comparisons = (
        ("app_logistic", "category_prior"),
        ("app_logistic", "persistence"),
        ("app_infra_logistic", "app_logistic"),
        ("app_infra_topology_logistic", "app_infra_logistic"),
    )
    paired = [
        paired_bootstrap_delta(
            oof,
            challenger=challenger,
            reference=reference,
            iterations=bootstrap_iterations,
            seed=seed + index,
        )
        for index, (challenger, reference) in enumerate(comparisons)
    ]
    sensitivity = []
    for unit_index, unit in enumerate(("event_date", "segment_id")):
        for comparison_index, (challenger, reference) in enumerate(comparisons[2:]):
            sensitivity.append(
                paired_bootstrap_delta(
                    oof,
                    challenger=challenger,
                    reference=reference,
                    iterations=bootstrap_iterations,
                    seed=seed + 100 + unit_index * 10 + comparison_index,
                    unit_column=unit,
                )
            )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    oof_path = output / "oof_predictions.csv.gz"
    oof.to_csv(oof_path, index=False, compression="gzip")
    _write_json(output / "fold_manifest.json", fold_manifest)
    _write_json(output / "feature_schema.json", feature_names)
    bootstrap_payload = {
        "protocol": ABLATION_PROTOCOL,
        "paired_event_bootstrap": paired,
        "cluster_sensitivity": sensitivity,
    }
    _write_json(output / "paired_bootstrap.json", bootstrap_payload)
    result = {
        "protocol": ABLATION_PROTOCOL,
        "models": list(MODEL_VARIANTS),
        "folds": len(folds),
        "oof_events": int(oof["event_id"].nunique()),
        "oof_event_app_pairs_per_model": len(reference_pairs),
        "summary_by_model": summary_by_model,
        "primary_increment_gate": {
            comparison["challenger"]: comparison["metrics"]["pr_auc"][
                "increment_gate_pass"
            ]
            for comparison in paired
        },
        "locked_test_used": False,
        "forbidden_inputs_used": [],
        "same_event_set_and_order_for_all_variants": True,
        "oof_path": str(oof_path.resolve()),
        "paired_bootstrap_path": str(
            (output / "paired_bootstrap.json").resolve()
        ),
    }
    _write_json(output / "summary.json", result)
    return result
