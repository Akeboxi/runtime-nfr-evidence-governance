"""Topology-confidence-gated residual model for the runtime-NFR v2 protocol."""

from __future__ import annotations

import torch
import torch.nn as nn

from ..data.runtime_nfr_dataset import RuntimeNFRSample
from .runtime_nfr_model import RuntimeNFROutput, RuntimeNFRPredictor


class TopologyGatedResidualPredictor(nn.Module):
    """Add a topology residual to a complete App-local prediction.

    The local path remains sufficient on its own.  Cross-layer messages can
    only make an additive correction, and that correction is attenuated when
    infrastructure telemetry is missing or the local App is already well
    observed.  Fault type, target and duration are absent from the interface.
    """

    def __init__(
        self,
        channels: int = 7,
        hidden_dim: int = 128,
        num_layers: int = 2,
        dropout: float = 0.15,
    ) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.local = RuntimeNFRPredictor(
            channels=channels,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            dropout=dropout,
            message_passing=False,
        )
        self.graph = RuntimeNFRPredictor(
            channels=channels,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            dropout=dropout,
            message_passing=True,
        )
        head_dim = max(8, hidden_dim // 2)
        self.breach_residual = nn.Sequential(
            nn.Linear(hidden_dim, head_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(head_dim, 1),
        )
        self.severity_residual = nn.Sequential(
            nn.Linear(hidden_dim, head_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(head_dim, 1),
        )
        self.confidence_gate = nn.Sequential(
            nn.Linear(4, head_dim),
            nn.ReLU(),
            nn.Linear(head_dim, 1),
            nn.Sigmoid(),
        )

    @staticmethod
    def _gate_features(sample: RuntimeNFRSample, device: torch.device) -> torch.Tensor:
        early_steps = max(
            1,
            int((sample.metadata.input_end - sample.metadata.event_minute).total_seconds() // 60),
        )
        app_observation = sample.masks["Vbiz"][-early_steps:].to(device).mean(dim=(0, 2))
        host_observation = sample.masks["Vphy"][-early_steps:].to(device).mean().expand_as(app_observation)
        pod_observation = sample.masks["Vvm"][-early_steps:].to(device).mean().expand_as(app_observation)
        topology_available = torch.full_like(
            app_observation,
            float(
                sum(
                    sample.current_graph.num_edges(etype=etype)
                    for etype in sample.current_graph.canonical_etypes
                )
                > 0
                and sample.has_previous_graph
            ),
        )
        return torch.stack(
            [1.0 - app_observation, host_observation, pod_observation, topology_available], dim=1
        )

    def forward(self, sample: RuntimeNFRSample) -> RuntimeNFROutput:
        local = self.local(sample)
        graph = self.graph(sample)
        device = local.breach_logit.device
        gate = self.confidence_gate(self._gate_features(sample, device)).squeeze(-1)
        graph_apps = graph.node_embeddings["Vbiz"]
        breach_residual = self.breach_residual(graph_apps).squeeze(-1)
        severity_residual = self.severity_residual(graph_apps).squeeze(-1)
        breach_logit = local.breach_logit + gate * breach_residual
        severity_logit = local.severity_logit + gate * severity_residual
        strengths = dict(graph.relation_gate_strengths)
        strengths["topology_confidence_gate_mean"] = float(gate.detach().mean().cpu())
        return RuntimeNFROutput(
            breach_logit=breach_logit,
            breach_probability=torch.sigmoid(breach_logit),
            severity_logit=severity_logit,
            severity_score=torch.sigmoid(severity_logit),
            node_embeddings=graph.node_embeddings,
            relation_gate_strengths=strengths,
        )
