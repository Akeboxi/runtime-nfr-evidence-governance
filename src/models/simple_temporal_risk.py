#!/usr/bin/env python3
"""简化版时序风险模型 - 类似MLP但保留时序结构

架构：
1. 时序特征 -> TemporalEncoder (1D CNN)
2. 静态特征 -> MLP
3. 融合后 -> 风险预测
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import dgl


class SimpleTemporalRiskGCN(nn.Module):
    """简化版时序风险模型 - 保留时序处理，简化图传播"""

    def __init__(
        self,
        static_dims: dict[str, int],
        temporal_dims: dict[str, int],
        hidden_dim: int = 256,
        num_layers: int = 2,
        dropout: float = 0.3,
        disable_graph_propagation: bool = True,
    ):
        super().__init__()
        self.static_dims = static_dims
        self.temporal_dims = temporal_dims
        self.hidden_dim = hidden_dim
        self.disable_graph_propagation = disable_graph_propagation

        # 时序编码器 - 1D CNN
        self.temporal_encoder = nn.ModuleDict()
        for ntype, dim in temporal_dims.items():
            self.temporal_encoder[ntype] = nn.Sequential(
                nn.Conv1d(dim, hidden_dim, kernel_size=3, padding=1),
                nn.BatchNorm1d(hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1),
                nn.BatchNorm1d(hidden_dim),
                nn.ReLU(),
                nn.AdaptiveAvgPool1d(1),  # 压缩到单个值
            )

        # 静态特征编码器
        self.static_encoder = nn.ModuleDict()
        for ntype, dim in static_dims.items():
            self.static_encoder[ntype] = nn.Sequential(
                nn.Linear(dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
            )

        # Vbiz 专用预测器 - 使用 h0 直接预测（禁用图传播时）
        self.risk_predictor = nn.Sequential(
            nn.Linear(hidden_dim + hidden_dim // 2, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 4),
            nn.ReLU(),
            nn.Linear(hidden_dim // 4, 1),
        )

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(
        self,
        g: dgl.DGLGraph,
        static_features: dict[str, torch.Tensor],
        temporal_features: dict[str, torch.Tensor],
    ):
        """
        Args:
            g: DGL图
            static_features: {ntype: (B, N_ntype, D_static)}
            temporal_features: {ntype: (B, T, N_ntype, D_temporal)}
        Returns:
            TemporalModelOutput
        """
        # 只处理 Vbiz 节点
        biz_temporal = temporal_features.get("Vbiz")  # (B, T, N_biz, D)
        biz_static = static_features.get("Vbiz")  # (B, N_biz, D_static)

        B, T, N_biz, D_temporal = biz_temporal.shape
        _, N_static, D_static = biz_static.shape

        # 时序编码: (B, T, N, D) -> (B, N, D)
        x_temporal = biz_temporal.permute(0, 2, 1, 3)  # (B, N, T, D)
        x_temporal = x_temporal.reshape(B * N_biz, D_temporal, T)  # (B*N, D, T)
        x_temporal = self.temporal_encoder["Vbiz"](x_temporal)  # (B*N, hidden, 1)
        x_temporal = x_temporal.squeeze(-1)  # (B*N, hidden)
        x_temporal = x_temporal.reshape(B, N_biz, -1)  # (B, N, hidden)

        # 静态编码: (B, N, D_static) -> (B, N, hidden//2)
        x_static = self.static_encoder["Vbiz"](biz_static)  # (B, N, hidden//2)

        # 融合: (B, N, hidden + hidden//2)
        x = torch.cat([x_temporal, x_static], dim=-1)

        # 预测: (B, N)
        risk_logit = self.risk_predictor(x).squeeze(-1)

        return SimpleRiskOutput(risk_logit=risk_logit)


class SimpleRiskOutput:
    """简化输出"""
    def __init__(self, risk_logit: torch.Tensor):
        self.risk_logit = risk_logit
        self.risk_prob = torch.sigmoid(risk_logit)


class TenantRiskPredictorSimple(nn.Module):
    """简化版租户风险预测器"""

    def __init__(
        self,
        static_dims: dict[str, int],
        temporal_dims: dict[str, int],
        hidden_dim: int = 256,
        num_layers: int = 2,
        dropout: float = 0.3,
        disable_graph_propagation: bool = True,
    ):
        super().__init__()
        self.model = SimpleTemporalRiskGCN(
            static_dims=static_dims,
            temporal_dims=temporal_dims,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            dropout=dropout,
            disable_graph_propagation=disable_graph_propagation,
        )

    def forward(self, g, static_features, temporal_features):
        return self.model(g, static_features, temporal_features)