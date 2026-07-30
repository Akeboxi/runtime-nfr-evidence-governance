"""Regression and within-event ranking objective for TopoIntent-Risk."""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class InterventionRiskLoss(nn.Module):
    def __init__(self, rank_weight: float = 0.5, min_label_gap: float = 0.1) -> None:
        super().__init__()
        self.rank_weight = rank_weight
        self.min_label_gap = min_label_gap
        self.regression = nn.SmoothL1Loss()

    def forward(self, logits: torch.Tensor, labels: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
        labels = labels.float()
        probabilities = torch.sigmoid(logits)
        regression = self.regression(probabilities, labels)
        differences = labels.unsqueeze(1) - labels.unsqueeze(0)
        valid = differences.abs() >= self.min_label_gap
        upper = torch.triu(torch.ones_like(valid, dtype=torch.bool), diagonal=1)
        valid = valid & upper
        if valid.any():
            logit_difference = logits.unsqueeze(1) - logits.unsqueeze(0)
            targets = differences.sign()
            ranking = F.softplus(-(logit_difference[valid] * targets[valid])).mean()
        else:
            ranking = torch.zeros((), device=logits.device)
        total = regression + self.rank_weight * ranking
        return total, {
            "regression": float(regression.detach().cpu()),
            "ranking": float(ranking.detach().cpu()),
            "total": float(total.detach().cpu()),
        }
