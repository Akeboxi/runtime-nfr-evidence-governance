from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import dgl
import pandas as pd
import torch

from src.data.intervention_dataset import build_topology_delta, hosting_edges, topology_hash
from src.data.runtime_nfr_dataset import (
    NFRBoundary,
    RuntimeNFRMetadata,
    RuntimeNFRSample,
    RuntimeNFRTargets,
)
from src.data.runtime_nfr_v2 import (
    _RawInterval,
    _overlaps_any,
    build_boundary_cards,
    development_topology_oof_folds,
    development_rolling_folds,
    krippendorff_alpha_ordinal,
    select_blinded_expert_cards,
    summarize_expert_ratings,
    topology_feature_matrix,
)
from src.models.runtime_nfr_v2_model import TopologyGatedResidualPredictor
from src.training.runtime_nfr_v3_ablation import (
    MODEL_VARIANTS,
    infrastructure_feature_matrix,
    run_feature_ablation,
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
    return graph, {
        "Vphy": ["host-0"],
        "Vvm": ["app-a-0", "app-b-0"],
        "Vbiz": ["app-a", "app-b"],
    }


def _boundary(app_id: str, sli: str, offset: int = 0) -> NFRBoundary:
    start = datetime(2025, 1, 1) + timedelta(minutes=offset)
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
        valid_samples=1000,
    )


def _sample(index: int) -> RuntimeNFRSample:
    graph, node_ids = _graph()
    delta = build_topology_delta(None, None, graph, node_ids)
    event_minute = datetime(2025, 1, 2, 12, 0) + timedelta(hours=index)
    metadata = RuntimeNFRMetadata(
        event_id=f"event-{index:02d}",
        record_id=f"record-{index:02d}",
        segment_id=f"seg-{index % 3}",
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
        seconds_since_topology_change=float(index * 60),
        hosting_edges=hosting_edges(graph, node_ids),
        fault_family="cpu" if index % 2 else "network",
        target_type="service" if index % 2 else "network",
    )
    generator = torch.Generator().manual_seed(100 + index)
    features = {
        "Vphy": torch.randn(65, 1, 7, generator=generator),
        "Vvm": torch.randn(65, 2, 7, generator=generator),
        "Vbiz": torch.randn(65, 2, 7, generator=generator),
    }
    masks = {key: torch.ones_like(value) for key, value in features.items()}
    boundaries = {
        app_id: {
            "latency": _boundary(app_id, "latency", index),
            "error_ratio": _boundary(app_id, "error_ratio", index),
        }
        for app_id in node_ids["Vbiz"]
    }
    targets = RuntimeNFRTargets(
        breach=torch.tensor([float(index % 2), float((index + 1) % 2)]),
        severity_raw=torch.tensor([float(index % 2), float((index + 1) % 2)]),
    )
    return RuntimeNFRSample(
        current_graph=graph,
        previous_graph=None,
        delta_graph=delta.graph,
        features=features,
        masks=masks,
        breach_labels=targets.breach,
        severity_raw=targets.severity_raw,
        label_mask=torch.tensor([1.0, 1.0]),
        early_request_volume=torch.tensor([100.0 + index, 20.0 + index]),
        node_ids=node_ids,
        boundaries=boundaries,
        sensitivity_targets={"q95_p3": targets},
        invalid_reasons={},
        metadata=metadata,
        has_previous_graph=False,
    )


def test_v3_infrastructure_features_align_with_app_rows_and_ablation(tmp_path) -> None:
    samples = [_sample(index) for index in range(15)]
    matrix, mapping, names = infrastructure_feature_matrix(samples)
    assert matrix.shape == (30, len(names))
    assert mapping[:2] == [(0, 0), (0, 1)]
    result = run_feature_ablation(
        samples,
        tmp_path / "ablation",
        n_splits=3,
        bootstrap_iterations=5,
        seed=20260723,
    )
    assert set(result["models"]) == set(MODEL_VARIANTS)
    assert result["locked_test_used"] is False
    assert result["same_event_set_and_order_for_all_variants"] is True
    predictions = pd.read_csv(result["oof_path"])
    pairs = predictions.groupby("model")[["event_id", "app_id"]].size()
    assert pairs.nunique() == 1


def test_boundary_cards_are_candidate_specs_and_blinding_removes_outcomes() -> None:
    samples = [_sample(index) for index in range(12)]
    cards = build_boundary_cards(
        samples,
        dataset_hash="abc",
        persistence_minutes=3,
        history_minutes=1440,
    )
    selected, private = select_blinded_expert_cards(samples, cards, count=16, seed=7)
    assert len(cards) == 48
    assert len(selected) == len(private) == 16
    assert all(card.status == "candidate" for card in cards)
    blinded = selected[0].to_dict(blinded=True)
    assert "event_id" not in blinded["evidence_provenance"]
    assert "fault_family" not in blinded["evidence_provenance"]
    assert "breach" not in str(blinded).lower()
    assert private[0]["breach"] in (0, 1)


def test_boundary_card_sampling_resolves_redis_apm_alias() -> None:
    sample = _sample(0)
    node_ids = dict(sample.node_ids)
    node_ids["Vbiz"] = ["redis", "app-b"]
    boundaries = dict(sample.boundaries)
    boundaries["redis-cart"] = boundaries.pop("app-a")
    alias_sample = replace(sample, node_ids=node_ids, boundaries=boundaries)
    cards = build_boundary_cards(
        [alias_sample], dataset_hash="abc", persistence_minutes=3, history_minutes=1440
    )
    selected, _ = select_blinded_expert_cards([alias_sample], cards, count=4, seed=7)
    assert any(card.subject_id == "redis-cart" for card in selected)


def test_ordinal_agreement_and_supportive_expert_gate() -> None:
    assert krippendorff_alpha_ordinal([[5, 5, 5], [4, 4, 4]]) == 1.0
    rows = []
    for reviewer in ("r1", "r2", "r3"):
        for card in ("c1", "c2"):
            row = {"reviewer_id": reviewer, "card_id": card, "comments": ""}
            row.update({dimension: 4 for dimension in (
                "clarity", "measurability", "traceability",
                "threshold_plausibility", "runtime_actionability",
            )})
            rows.append(row)
    summary = summarize_expert_ratings(pd.DataFrame(rows))
    assert summary["reviewers"] == 3
    assert summary["supportive_gate_pass"] is True
    assert summary["dimensions"]["clarity"]["krippendorff_alpha_ordinal"] == 1.0


def test_rolling_folds_are_causal_and_validation_rows_are_unique() -> None:
    samples = [_sample(index) for index in range(30)]
    folds = development_rolling_folds(samples, n_splits=5)
    validation = []
    for train, val in folds:
        assert max(samples[index].metadata.event_start for index in train) < min(
            samples[index].metadata.event_start for index in val
        )
        validation.extend(val)
    assert len(validation) == len(set(validation))


def test_topology_oof_assigns_every_development_event_once() -> None:
    samples = []
    for index in range(30):
        sample = _sample(index)
        sample = replace(
            sample,
            metadata=replace(sample.metadata, topology_hash=f"topology-{index % 6}"),
        )
        samples.append(sample)
    folds = development_topology_oof_folds(samples, n_splits=5)
    validation = [index for _, fold_validation in folds for index in fold_validation]
    assert len(validation) == len(set(validation)) == int(len(samples) * 0.8)


def test_topology_features_match_valid_app_order_and_residual_model_contract() -> None:
    sample = _sample(1)
    matrix, mapping, names = topology_feature_matrix([sample])
    assert matrix.shape == (2, len(names))
    assert mapping == [(0, 0), (0, 1)]
    model = TopologyGatedResidualPredictor(hidden_dim=16, num_layers=1, dropout=0.0)
    output = model(sample)
    assert output.breach_probability.shape == sample.breach_labels.shape
    assert torch.isfinite(output.breach_probability).all()
    assert 0.0 <= output.relation_gate_strengths["topology_confidence_gate_mean"] <= 1.0


def test_control_candidates_exclude_buffered_event_intervals() -> None:
    start = datetime(2025, 1, 2, 12, 0)
    intervals = [_RawInterval(start=start - timedelta(minutes=30), end=start + timedelta(minutes=50))]
    assert _overlaps_any(start - timedelta(minutes=5), start + timedelta(minutes=5), intervals)
    assert not _overlaps_any(start + timedelta(hours=2), start + timedelta(hours=3), intervals)
