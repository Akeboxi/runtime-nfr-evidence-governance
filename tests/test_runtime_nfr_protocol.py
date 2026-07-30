from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import dgl
import numpy as np
import pandas as pd
import torch

from src.data.intervention_dataset import build_topology_delta, hosting_edges, topology_hash
from src.data.runtime_nfr_dataset import (
    NFRBoundary,
    RuntimeNFRMetadata,
    RuntimeNFRSample,
    RuntimeNFRTargets,
    aggregate_app_predictions,
    assert_runtime_nfr_temporal_contract,
    chronological_runtime_split,
    fit_nfr_boundary,
)
from src.models.runtime_nfr_model import RuntimeNFRLoss, RuntimeNFRPredictor
from src.training.runtime_nfr_trainer import (
    event_bootstrap_recall_at_k_delta,
    prediction_metrics,
    select_f1_threshold,
)


def _graph():
    graph = dgl.heterograph(
        {
            ("Vphy", "r_link", "Vphy"): ([0], [0]),
            ("Vphy", "r_hosting", "Vvm"): ([0, 0], [0, 1]),
            ("Vvm", "r_traffic", "Vvm"): ([0], [1]),
            ("Vvm", "r_deployment", "Vbiz"): ([0, 1], [0, 1]),
            ("Vbiz", "r_calling", "Vbiz"): ([0], [1]),
        },
        num_nodes_dict={"Vphy": 1, "Vvm": 2, "Vbiz": 2},
    )
    node_ids = {"Vphy": ["host-0"], "Vvm": ["app-a-0", "app-b-0"], "Vbiz": ["app-a", "app-b"]}
    return graph, node_ids


def _boundary(app_id: str, sli: str) -> NFRBoundary:
    start = datetime(2025, 1, 1)
    return NFRBoundary(
        app_id=app_id,
        sli=sli,
        history_start=start,
        history_end=start + timedelta(days=1),
        quantile_level=0.95,
        quantile_value=1.0,
        median=0.5,
        mad=0.1,
        robust_scale=0.14826,
        threshold=1.0,
        valid_samples=1440,
    )


def _sample(event_id: str = "event-0", offset_minutes: int = 0) -> RuntimeNFRSample:
    graph, node_ids = _graph()
    delta = build_topology_delta(None, None, graph, node_ids)
    event_minute = datetime(2025, 1, 2, 12, 0) + timedelta(minutes=offset_minutes)
    metadata = RuntimeNFRMetadata(
        event_id=event_id,
        record_id=event_id,
        segment_id="seg-0",
        event_start=event_minute + timedelta(seconds=2),
        event_minute=event_minute,
        event_end=event_minute + timedelta(minutes=20),
        boundary_start=event_minute - timedelta(days=1),
        boundary_end=event_minute,
        input_start=event_minute - timedelta(minutes=60),
        input_end=event_minute + timedelta(minutes=5),
        label_start=event_minute + timedelta(minutes=5),
        label_end=event_minute + timedelta(minutes=20),
        topology_hash=topology_hash(graph, node_ids),
        previous_topology_hash=None,
        seconds_since_topology_change=0.0,
        hosting_edges=hosting_edges(graph, node_ids),
        fault_family="cpu",
        target_type="service",
    )
    generator = torch.Generator().manual_seed(7 + offset_minutes)
    features = {
        "Vphy": torch.randn(65, 1, 7, generator=generator),
        "Vvm": torch.randn(65, 2, 7, generator=generator),
        "Vbiz": torch.randn(65, 2, 7, generator=generator),
    }
    masks = {key: torch.ones_like(value) for key, value in features.items()}
    boundaries = {
        app_id: {"latency": _boundary(app_id, "latency"), "error_ratio": _boundary(app_id, "error_ratio")}
        for app_id in node_ids["Vbiz"]
    }
    sensitivity = {
        "q95_p3": RuntimeNFRTargets(torch.tensor([1.0, 0.0]), torch.tensor([3.0, 0.0]))
    }
    return RuntimeNFRSample(
        current_graph=graph,
        previous_graph=None,
        delta_graph=delta.graph,
        features=features,
        masks=masks,
        breach_labels=torch.tensor([1.0, 0.0]),
        severity_raw=torch.tensor([3.0, 0.0]),
        label_mask=torch.tensor([1.0, 1.0]),
        early_request_volume=torch.tensor([100.0, 20.0]),
        node_ids=node_ids,
        boundaries=boundaries,
        sensitivity_targets=sensitivity,
        invalid_reasons={},
        metadata=metadata,
        has_previous_graph=False,
    )


def test_boundary_uses_robust_rule_and_zero_error_special_case() -> None:
    start = datetime(2025, 1, 1)
    latency = fit_nfr_boundary(
        pd.Series([10.0, 10.0, 11.0, 9.0, 10.0, 100.0]),
        app_id="app-a",
        sli="latency",
        history_start=start,
        history_end=start + timedelta(hours=1),
    )
    error = fit_nfr_boundary(
        pd.Series([0.0] * 20),
        app_id="app-a",
        sli="error_ratio",
        history_start=start,
        history_end=start + timedelta(hours=1),
    )
    assert latency.threshold >= latency.quantile_value
    assert latency.threshold >= latency.median + 3 * latency.robust_scale
    assert error.threshold == 0.0


def test_temporal_contract_and_split_are_event_level() -> None:
    samples = [_sample(f"event-{index}", index) for index in range(10)]
    for sample in samples:
        assert_runtime_nfr_temporal_contract(sample)
    train, val, test = chronological_runtime_split(samples)
    assert [len(train), len(val), len(test)] == [6, 2, 2]
    assert set(item.metadata.event_id for item in train).isdisjoint(item.metadata.event_id for item in test)
    broken = replace(samples[0], metadata=replace(samples[0].metadata, label_start=samples[0].metadata.input_end - timedelta(minutes=1)))
    try:
        assert_runtime_nfr_temporal_contract(broken)
    except AssertionError as error:
        assert "overlap" in str(error)
    else:
        raise AssertionError("overlapping input/label windows were accepted")


def test_two_head_model_is_intent_free_and_backward_safe() -> None:
    sample = _sample()
    model = RuntimeNFRPredictor(hidden_dim=16, num_layers=1, dropout=0.0)
    output = model(sample)
    assert output.breach_probability.shape == sample.breach_labels.shape
    assert output.severity_score.shape == sample.severity_raw.shape
    loss, parts = RuntimeNFRLoss(positive_weight=1.0)(
        output,
        sample.breach_labels,
        torch.tensor([1.0, 0.0]),
        sample.label_mask,
    )
    assert torch.isfinite(loss)
    assert parts["classification"] >= 0.0
    loss.backward()
    assert any(parameter.grad is not None for parameter in model.parameters())


def test_app_predictions_aggregate_to_pod_and_host_without_new_labels() -> None:
    sample = _sample()
    aggregates = aggregate_app_predictions(sample, np.asarray([0.8, 0.2]))
    assert aggregates["pod"]["app-a-0"]["max"] == 0.8
    assert aggregates["host"]["host-0"]["max"] == 0.8
    assert 0.2 <= aggregates["host"]["host-0"]["request_weighted_mean"] <= 0.8


def test_metrics_use_valid_app_pairs_and_validation_threshold() -> None:
    sample = _sample()
    probabilities = [np.asarray([0.9, 0.1])]
    severities = [np.asarray([0.8, 0.2])]
    threshold = select_f1_threshold(np.asarray([1, 0]), probabilities[0])
    metrics = prediction_metrics(
        [sample], probabilities, severities, severity_scale=3.0, threshold=threshold
    )
    assert metrics["pr_auc"] == 1.0
    assert metrics["recall_at_3"] == 1.0
    assert metrics["f1"] == 1.0
    recall_delta = event_bootstrap_recall_at_k_delta([sample], probabilities, k=1, iterations=20)
    assert recall_delta["model_recall_at_k"] == 1.0
    assert recall_delta["random_expectation"] == 0.5
