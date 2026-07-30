"""Topology- and intent-conditioned risk propagation model."""

from __future__ import annotations

from dataclasses import dataclass

import dgl
import torch
import torch.nn as nn
import torch.nn.functional as F

from ..data.intervention_dataset import (
    FAULT_FAMILIES,
    TARGET_TYPES,
    IntentDescriptor,
    InterventionSample,
)


BASE_RELATIONS = ("r_link", "r_hosting", "r_traffic", "r_deployment", "r_calling")


class CausalConv1d(nn.Conv1d):
    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._causal_trim = (self.kernel_size[0] - 1) * self.dilation[0]

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        output = super().forward(value)
        return output[..., :-self._causal_trim] if self._causal_trim else output


class CausalTemporalEncoder(nn.Module):
    """Encodes only telemetry observed before an intervention starts."""

    def __init__(self, channels: int, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        input_dim = channels * 2  # value plus explicit availability mask
        self.conv1 = CausalConv1d(input_dim, hidden_dim, kernel_size=3, padding=2, dilation=1)
        self.conv2 = CausalConv1d(hidden_dim, hidden_dim, kernel_size=3, padding=4, dilation=2)
        self.norm = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, values: torch.Tensor, masks: torch.Tensor) -> torch.Tensor:
        # (T, N, C) -> (N, 2C, T)
        x = torch.cat([values, masks], dim=-1).permute(1, 2, 0)
        x = F.relu(self.conv1(x))
        x = self.dropout(F.relu(self.conv2(x)))
        return self.norm(x[..., -1])


class IntentEncoder(nn.Module):
    def __init__(self, hidden_dim: int) -> None:
        super().__init__()
        self.family_to_index = {name: index for index, name in enumerate(FAULT_FAMILIES)}
        self.target_to_index = {name: index for index, name in enumerate(TARGET_TYPES)}
        self.family_embedding = nn.Embedding(len(FAULT_FAMILIES), hidden_dim)
        self.target_embedding = nn.Embedding(len(TARGET_TYPES), hidden_dim)
        self.project = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.LayerNorm(hidden_dim),
        )

    def forward(self, intent: IntentDescriptor, device: torch.device) -> torch.Tensor:
        family_index = self.family_to_index.get(intent.fault_family, self.family_to_index["unknown"])
        target_index = self.target_to_index.get(intent.target_type, self.target_to_index["service"])
        family = self.family_embedding(torch.tensor(family_index, device=device))
        target = self.target_embedding(torch.tensor(target_index, device=device))
        return self.project(torch.cat([family, target], dim=-1))


class TopologyTransitionEncoder(nn.Module):
    """Turns signed edge additions/removals into per-node topology context."""

    def __init__(self, relations: tuple[str, ...] = BASE_RELATIONS) -> None:
        super().__init__()
        self.relations = relations
        self.output_dim = len(relations) * 4 + 2  # add/remove, in/out, previous/time flags

    def forward(
        self,
        delta_graph: dgl.DGLGraph,
        current_graph: dgl.DGLGraph,
        has_previous_graph: bool,
        seconds_since_change: float,
        device: torch.device,
    ) -> tuple[dict[str, torch.Tensor], torch.Tensor]:
        features = {
            ntype: torch.zeros(current_graph.num_nodes(ntype), self.output_dim, device=device)
            for ntype in current_graph.ntypes
        }
        for etype in delta_graph.canonical_etypes:
            relation_with_sign = etype[1]
            if relation_with_sign.endswith("_added"):
                relation, sign_offset = relation_with_sign[:-6], 0
            elif relation_with_sign.endswith("_removed"):
                relation, sign_offset = relation_with_sign[:-8], 2
            else:
                continue
            if relation not in self.relations:
                continue
            relation_index = self.relations.index(relation) * 4 + sign_offset
            src, dst = delta_graph.edges(etype=etype)
            if len(src) == 0:
                continue
            ones = torch.ones(len(src), device=device)
            features[etype[0]][:, relation_index].index_add_(0, src.to(device), ones)
            features[etype[2]][:, relation_index + 1].index_add_(0, dst.to(device), ones)
        time_feature = min(seconds_since_change / 86400.0, 30.0) / 30.0
        for value in features.values():
            value[:, -2] = float(has_previous_graph)
            value[:, -1] = time_feature
        pooled = torch.cat([value.mean(dim=0) for value in features.values()], dim=0).view(len(features), self.output_dim).mean(dim=0)
        return features, pooled


class ConditionedRelationalLayer(nn.Module):
    """Relation-specific messages modulated by topology and intervention context."""

    def __init__(self, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        relation_names = [*BASE_RELATIONS, *(f"rev_{name}" for name in BASE_RELATIONS)]
        self.transforms = nn.ModuleDict({name: nn.Linear(hidden_dim, hidden_dim, bias=False) for name in relation_names})
        self.gates = nn.ModuleDict({name: nn.Linear(hidden_dim, hidden_dim * 2) for name in relation_names})
        self.self_transforms = nn.ModuleDict({ntype: nn.Linear(hidden_dim, hidden_dim, bias=False) for ntype in ("Vphy", "Vvm", "Vbiz")})
        self.norms = nn.ModuleDict({ntype: nn.LayerNorm(hidden_dim) for ntype in ("Vphy", "Vvm", "Vbiz")})
        self.dropout = nn.Dropout(dropout)

    def forward(self, graph: dgl.DGLGraph, features: dict[str, torch.Tensor], context: torch.Tensor) -> tuple[dict[str, torch.Tensor], dict[str, float]]:
        aggregate = {ntype: torch.zeros_like(value) for ntype, value in features.items()}
        counts = {ntype: torch.zeros(value.shape[0], 1, device=value.device) for ntype, value in features.items()}
        gate_summary: dict[str, float] = {}
        for etype in graph.canonical_etypes:
            relation = etype[1]
            if relation not in self.transforms:
                continue
            src, dst = graph.edges(etype=etype)
            if len(src) == 0:
                continue
            for name, source_type, target_type, source_index, target_index in (
                (relation, etype[0], etype[2], src, dst),
                (f"rev_{relation}", etype[2], etype[0], dst, src),
            ):
                gamma, beta = self.gates[name](context).chunk(2, dim=-1)
                message = self.transforms[name](features[source_type][source_index])
                message = message * (1.0 + gamma) + beta
                aggregate[target_type].index_add_(0, target_index, message)
                counts[target_type].index_add_(0, target_index, torch.ones(len(target_index), 1, device=message.device))
                gate_summary[name] = float(gamma.detach().abs().mean().cpu())
        output: dict[str, torch.Tensor] = {}
        for ntype, value in features.items():
            message = aggregate[ntype] / counts[ntype].clamp_min(1.0)
            output[ntype] = self.norms[ntype](self.self_transforms[ntype](value) + message)
            output[ntype] = self.dropout(F.relu(output[ntype]))
        return output, gate_summary


@dataclass
class TopoIntentOutput:
    risk_logit: torch.Tensor
    risk_prob: torch.Tensor
    node_embeddings: dict[str, torch.Tensor]
    topology_context: torch.Tensor
    relation_gate_strengths: dict[str, float]


class TopoIntentRiskPredictor(nn.Module):
    """ID-free predictor for pre-intervention business impact."""

    def __init__(self, channels: int = 7, hidden_dim: int = 128, num_layers: int = 3, dropout: float = 0.15) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.temporal_encoders = nn.ModuleDict({
            "Vphy": CausalTemporalEncoder(channels, hidden_dim, dropout),
            "Vvm": CausalTemporalEncoder(channels, hidden_dim, dropout),
        })
        self.intent_encoder = IntentEncoder(hidden_dim)
        self.delta_encoder = TopologyTransitionEncoder()
        self.delta_projectors = nn.ModuleDict({
            ntype: nn.Linear(self.delta_encoder.output_dim, hidden_dim)
            for ntype in ("Vphy", "Vvm", "Vbiz")
        })
        self.context_projector = nn.Sequential(
            nn.Linear(hidden_dim + self.delta_encoder.output_dim, hidden_dim),
            nn.ReLU(),
            nn.LayerNorm(hidden_dim),
        )
        self.intent_injectors = nn.ModuleDict({ntype: nn.Linear(hidden_dim, hidden_dim) for ntype in ("Vphy", "Vvm", "Vbiz")})
        self.layers = nn.ModuleList([ConditionedRelationalLayer(hidden_dim, dropout) for _ in range(num_layers)])
        self.risk_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, 1),
        )

    @staticmethod
    def _target_masks(sample: InterventionSample, device: torch.device) -> dict[str, torch.Tensor]:
        targets = set(sample.intent.target_node_ids) | set(sample.intent.source_node_ids) | set(sample.intent.destination_node_ids)
        aliases = {"redis-cart": "redis"}
        masks: dict[str, torch.Tensor] = {}
        for ntype, node_ids in sample.node_ids.items():
            masks[ntype] = torch.tensor(
                [float(node_id in targets or aliases.get(node_id) in targets) for node_id in node_ids],
                device=device,
            ).unsqueeze(-1)
        return masks

    def forward(self, sample: InterventionSample) -> TopoIntentOutput:
        device = next(self.parameters()).device
        graph = sample.current_graph.to(device)
        delta_graph = sample.delta_graph.to(device)
        intent = self.intent_encoder(sample.intent, device)
        delta_features, delta_global = self.delta_encoder(
            delta_graph,
            graph,
            sample.has_previous_graph,
            sample.metadata.seconds_since_topology_change,
            device,
        )
        context = self.context_projector(torch.cat([intent, delta_global], dim=-1))
        features = {
            "Vphy": self.temporal_encoders["Vphy"](sample.pre_features["Vphy"].to(device), sample.missing_masks["Vphy"].to(device)),
            "Vvm": self.temporal_encoders["Vvm"](sample.pre_features["Vvm"].to(device), sample.missing_masks["Vvm"].to(device)),
            "Vbiz": torch.zeros(graph.num_nodes("Vbiz"), self.hidden_dim, device=device),
        }
        masks = self._target_masks(sample, device)
        for ntype in features:
            features[ntype] = features[ntype] + self.delta_projectors[ntype](delta_features[ntype])
            features[ntype] = features[ntype] + masks[ntype] * self.intent_injectors[ntype](intent)
        gate_strengths: dict[str, float] = {}
        for layer in self.layers:
            features, gate_strengths = layer(graph, features, context)
        risk_logit = self.risk_head(features["Vbiz"]).squeeze(-1)
        return TopoIntentOutput(
            risk_logit=risk_logit,
            risk_prob=torch.sigmoid(risk_logit),
            node_embeddings=features,
            topology_context=context,
            relation_gate_strengths=gate_strengths,
        )
