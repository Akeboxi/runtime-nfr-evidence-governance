"""Two-head, intent-free runtime NFR predictor."""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..data.runtime_nfr_dataset import RuntimeNFRSample
from .topo_intent_risk import (
    CausalTemporalEncoder,
    ConditionedRelationalLayer,
    TopologyTransitionEncoder,
)


@dataclass
class RuntimeNFROutput:
    breach_logit: torch.Tensor
    breach_probability: torch.Tensor
    severity_logit: torch.Tensor
    severity_score: torch.Tensor
    node_embeddings: dict[str, torch.Tensor]
    relation_gate_strengths: dict[str, float]


class RuntimeNFRPredictor(nn.Module):
    """Predict later App-level NFR breaches from early cross-layer telemetry.

    Fault type, fault target, duration, and legacy impact labels are absent from
    the interface by design.  ``message_passing=False`` supplies the no-graph
    neural baseline with exactly the same App encoder and heads.
    """

    def __init__(
        self,
        channels: int = 7,
        hidden_dim: int = 128,
        num_layers: int = 3,
        dropout: float = 0.15,
        *,
        message_passing: bool = True,
    ) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.message_passing = message_passing
        self.temporal_encoders = nn.ModuleDict(
            {
                ntype: CausalTemporalEncoder(channels, hidden_dim, dropout)
                for ntype in ("Vphy", "Vvm", "Vbiz")
            }
        )
        self.delta_encoder = TopologyTransitionEncoder()
        self.delta_projectors = nn.ModuleDict(
            {
                ntype: nn.Linear(self.delta_encoder.output_dim, hidden_dim)
                for ntype in ("Vphy", "Vvm", "Vbiz")
            }
        )
        self.context_projector = nn.Sequential(
            nn.Linear(self.delta_encoder.output_dim, hidden_dim),
            nn.ReLU(),
            nn.LayerNorm(hidden_dim),
        )
        self.layers = nn.ModuleList(
            [ConditionedRelationalLayer(hidden_dim, dropout) for _ in range(num_layers)]
        )
        head_dim = max(8, hidden_dim // 2)
        self.breach_head = nn.Sequential(
            nn.Linear(hidden_dim, head_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(head_dim, 1),
        )
        self.severity_head = nn.Sequential(
            nn.Linear(hidden_dim, head_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(head_dim, 1),
        )

    def forward(self, sample: RuntimeNFRSample) -> RuntimeNFROutput:
        device = next(self.parameters()).device
        graph = sample.current_graph.to(device)
        features = {
            ntype: self.temporal_encoders[ntype](
                sample.features[ntype].to(device),
                sample.masks[ntype].to(device),
            )
            for ntype in ("Vphy", "Vvm", "Vbiz")
        }
        gate_strengths: dict[str, float] = {}
        if self.message_passing:
            delta_graph = sample.delta_graph.to(device)
            delta_features, delta_global = self.delta_encoder(
                delta_graph,
                graph,
                sample.has_previous_graph,
                sample.metadata.seconds_since_topology_change,
                device,
            )
            context = self.context_projector(delta_global)
            for ntype in features:
                features[ntype] = features[ntype] + self.delta_projectors[ntype](
                    delta_features[ntype]
                )
            for layer in self.layers:
                features, gate_strengths = layer(graph, features, context)
        else:
            # Keep non-App branches out of the no-graph prediction path.
            features["Vphy"] = features["Vphy"].detach() * 0.0
            features["Vvm"] = features["Vvm"].detach() * 0.0
        breach_logit = self.breach_head(features["Vbiz"]).squeeze(-1)
        severity_logit = self.severity_head(features["Vbiz"]).squeeze(-1)
        return RuntimeNFROutput(
            breach_logit=breach_logit,
            breach_probability=torch.sigmoid(breach_logit),
            severity_logit=severity_logit,
            severity_score=torch.sigmoid(severity_logit),
            node_embeddings=features,
            relation_gate_strengths=gate_strengths,
        )


class RuntimeNFRLoss(nn.Module):
    """Balanced breach classification plus positive severity and ranking."""

    def __init__(
        self,
        *,
        positive_weight: float = 1.0,
        severity_weight: float = 0.5,
        rank_weight: float = 0.25,
        min_rank_gap: float = 0.05,
    ) -> None:
        super().__init__()
        self.register_buffer("positive_weight", torch.tensor(float(positive_weight)))
        self.severity_weight = severity_weight
        self.rank_weight = rank_weight
        self.min_rank_gap = min_rank_gap

    def forward(
        self,
        output: RuntimeNFROutput,
        breach: torch.Tensor,
        severity: torch.Tensor,
        valid_mask: torch.Tensor,
    ) -> tuple[torch.Tensor, dict[str, float]]:
        valid = valid_mask.bool()
        if not valid.any():
            zero = output.breach_logit.sum() * 0.0
            return zero, {"classification": 0.0, "severity": 0.0, "ranking": 0.0, "total": 0.0}
        classification = F.binary_cross_entropy_with_logits(
            output.breach_logit[valid],
            breach[valid],
            pos_weight=self.positive_weight.to(output.breach_logit.device),
        )
        positives = valid & breach.bool()
        if positives.any():
            severity_loss = F.smooth_l1_loss(output.severity_score[positives], severity[positives])
        else:
            severity_loss = output.severity_logit.sum() * 0.0
        difference = severity.unsqueeze(1) - severity.unsqueeze(0)
        pair_valid = (
            valid.unsqueeze(1)
            & valid.unsqueeze(0)
            & (difference.abs() >= self.min_rank_gap)
        )
        pair_valid &= torch.triu(torch.ones_like(pair_valid, dtype=torch.bool), diagonal=1)
        if pair_valid.any():
            logit_difference = output.severity_logit.unsqueeze(1) - output.severity_logit.unsqueeze(0)
            direction = torch.sign(difference)
            ranking = F.softplus(-(logit_difference[pair_valid] * direction[pair_valid])).mean()
        else:
            ranking = output.severity_logit.sum() * 0.0
        total = classification + self.severity_weight * severity_loss + self.rank_weight * ranking
        return total, {
            "classification": float(classification.detach().cpu()),
            "severity": float(severity_loss.detach().cpu()),
            "ranking": float(ranking.detach().cpu()),
            "total": float(total.detach().cpu()),
        }

