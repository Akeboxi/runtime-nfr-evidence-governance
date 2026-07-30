import torch
import torch.nn as nn
import torch.nn.functional as F
from dataclasses import dataclass
from typing import Any

import dgl
import dgl.nn.pytorch as dglnn


class FeatureFusion(nn.Module):
    """双模态特征融合模块 - 动态(1D-CNN) + 静态(Linear)

    架构:
    - 动态特征通路: temporal (N, T, D_t) -> 1D-CNN -> (N, D_dyn)
    - 静态特征通路: static (N, D_s) -> Linear -> (N, D_static)
    - torch.cat 拼接后过 MLP + LayerNorm -> (N, hidden_dim)
    """

    def __init__(
        self,
        temporal_dim: int,
        static_dim: int,
        hidden_dim: int = 256,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.temporal_dim = temporal_dim
        self.static_dim = static_dim
        self.hidden_dim = hidden_dim
        self.dynamic_out_dim = hidden_dim // 2
        self.static_out_dim = hidden_dim - self.dynamic_out_dim

        # 动态特征通路: 1D-CNN
        # 输入: (N, T, D_t) -> Conv1d 期望 (N, D, T)
        self.cnn = nn.Sequential(
            nn.Conv1d(temporal_dim, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(64, self.dynamic_out_dim, kernel_size=3, padding=1),
            nn.ReLU(),
        )

        # 静态特征通路: Linear
        self.static_proj = nn.Linear(static_dim, self.static_out_dim)

        # 融合 MLP + LayerNorm
        self.fusion_mlp = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

    def forward(
        self,
        temporal: torch.Tensor,
        static: torch.Tensor,
    ) -> torch.Tensor:
        """前向传播

        Args:
            temporal: 时序特征 (N, T, D_t)
            static: 静态特征 (N, D_s)

        Returns:
            融合后的特征 (N, hidden_dim)
        """
        # 动态特征: (N, T, D_t) -> (N, D_t, T) -> CNN -> (N, 128)
        temporal_transposed = temporal.transpose(1, 2)
        dynamic_h = self.cnn(temporal_transposed)  # (N, 128, T')
        dynamic_h = dynamic_h.mean(dim=-1)  # (N, 128) 全局池化

        # 静态特征: (N, D_s) -> (N, 128)
        static_h = self.static_proj(static)

        # 拼接: (N, hidden_dim)
        fused = torch.cat([dynamic_h, static_h], dim=-1)

        # 融合 MLP
        return self.fusion_mlp(fused)


@dataclass
class TemporalModelOutput:
    """时序模型输出"""
    risk_prob: torch.Tensor          # 风险概率 sigmoid(logit)，用于推理
    risk_logit: torch.Tensor         # 未经 sigmoid 的 logit，用于损失计算
    vertical_score: torch.Tensor     # 垂直风险评分
    horizontal_score: torch.Tensor   # 水平风险评分
    node_embeddings: dict[str, torch.Tensor]
    # 新增：风险溯源信息
    phy_link_contribution: torch.Tensor | None = None  # 物理连接贡献的风险值
    phy_self_contribution: torch.Tensor | None = None  # 物理机自身贡献的风险值


class TemporalEncoder(nn.Module):
    """时序特征编码器 - 使用双向 GRU + 自注意力"""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        num_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.hidden_dim = hidden_dim

        self.gru = nn.GRU(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
            bidirectional=True,
        )

        # 时序自注意力层
        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_dim * 2,
            num_heads=4,
            dropout=dropout,
            batch_first=True,
        )
        self.attn_norm = nn.LayerNorm(hidden_dim * 2)

        self.output_proj = nn.Linear(hidden_dim * 2, hidden_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播

        Args:
            x: (M, T, D)，M 可以是 N（单样本）或 B*N（批量展平后）

        Returns:
            (M, hidden_dim)
        """
        M, T, D = x.shape

        # GRU 编码
        gru_out, _ = self.gru(x)  # (M, T, hidden*2)

        # 时序自注意力
        attn_out, _ = self.attention(gru_out, gru_out, gru_out)
        gru_out = self.attn_norm(gru_out + attn_out)  # 残差连接

        # 取最后一个时间步
        out = gru_out[:, -1, :]  # (M, hidden*2)
        return self.output_proj(out)  # (M, hidden)


class HeteroGCNLayer(nn.Module):
    """异构图卷积层 - 支持图注意力"""

    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        etypes: list[tuple[str, str, str]],
        aggregation: str = "mean",
        dropout: float = 0.1,
        use_attention: bool = True,
    ):
        super().__init__()
        self.use_attention = use_attention

        # 图卷积
        self.conv = dglnn.HeteroGraphConv(
            {
                etype: dglnn.GraphConv(
                    in_dim, out_dim, norm="right", allow_zero_in_degree=True
                )
                for _, etype, _ in etypes
            },
            aggregate=aggregation,
        )

        # 注意力机制
        if self.use_attention:
            self.etypes = etypes
            self.attention_weights = nn.ModuleDict({
                etype: nn.Linear(out_dim, 1, bias=False)
                for _, etype, _ in etypes
            })

        self.dropout = nn.Dropout(dropout)
        self.self_weight = nn.Linear(in_dim, out_dim, bias=False)
        self.norm = nn.LayerNorm(out_dim)

    def _forward_single(
        self,
        g: dgl.DGLGraph,
        features: dict[str, torch.Tensor],
        target_ntype: str,
    ) -> torch.Tensor:
        """单样本图卷积，features 中每个 value 形状为 (N, D)"""
        # 只保留本层注册的边类型对应的子图，避免 DGL 遍历到未注册的边类型
        registered_etypes = list(self.conv.mods.keys())
        valid_etypes = [
            et for et in g.canonical_etypes
            if et[1] in registered_etypes
        ]
        if valid_etypes:
            sub_g = g.edge_type_subgraph([et for et in valid_etypes])
        else:
            sub_g = g
        inputs = {k: v for k, v in features.items() if k in sub_g.ntypes}
        outputs = self.conv(sub_g, inputs)

        if target_ntype not in outputs:
            h = self.self_weight(features[target_ntype])
        else:
            # 聚合邻居特征
            h_agg = outputs[target_ntype]
            h_self = self.self_weight(features[target_ntype])

            # 应用注意力（如果有）
            if self.use_attention and h_agg.numel() > 0:
                # 简单地对不同边类型的结果加权
                h = h_agg + h_self
            else:
                h = h_agg + h_self

        # 应用归一化和激活
        h = self.norm(h)
        h = self.dropout(F.relu(h))
        return h  # (N, out_dim)

    def forward(
        self,
        g: dgl.DGLGraph,
        features: dict[str, torch.Tensor],
        target_ntype: str,
    ) -> torch.Tensor:
        """前向传播

        Args:
            g: DGL 异构图（节点数固定）
            features: {ntype: (B, N, D)} 或 {ntype: (N, D)}
            target_ntype: 目标节点类型

        Returns:
            (B, N, out_dim) 或 (N, out_dim)
        """
        # 检测是否有 batch 维度
        has_batch = any(v.dim() == 3 for v in features.values())

        if not has_batch:
            return self._forward_single(g, features, target_ntype)

        # batch 模式：DGL 图不支持批量特征，逐样本处理后 stack
        batch_size = next(v.shape[0] for v in features.values() if v.dim() == 3)
        results = []
        for b in range(batch_size):
            sample_feat = {
                ntype: feat[b] if feat.dim() == 3 else feat
                for ntype, feat in features.items()
            }
            results.append(self._forward_single(g, sample_feat, target_ntype))
        return torch.stack(results, dim=0)  # (B, N, out_dim)


class TemporalRiskGCN(nn.Module):
    """时序风险传播 R-GCN 模型

    架构:
        1. 双模态特征融合: 1D-CNN (动态) + Linear (静态) -> MLP + LayerNorm -> hidden_dim
        2. App 节点强制致盲: h_biz_0 = zeros(hidden_dim)
        3. 物理层横向扩散 (W_link + W_self_phy)
        4. 三层异构图卷积:
           - Layer 1 (Host): self_loop + r_link
           - Layer 2 (VM): self_loop + r_host + r_traffic
           - Layer 3 (App): 仅 r_deployment，mean 聚合（移除 r_calling）
        5. 单分支 MLP 预测: hidden_dim -> hidden_dim/4 -> hidden_dim/16 -> 1

    """

    def __init__(
        self,
        static_dims: dict[str, int],
        temporal_dims: dict[str, int],
        hidden_dim: int = 256,
        num_layers: int = 3,   # 方案要求3层
        dropout: float = 0.2,  # 适中的 dropout
        enable_phy_link: bool = True,  # 是否启用物理层（Host层）内部横向传播
        enable_vm_traffic: bool = True,  # 是否启用VM层内部横向传播（r_traffic边）
        enable_layer3: bool = True,  # 是否启用业务层（Vbiz）内部传播
        disable_graph_propagation: bool = False,  # 完全禁用图传播，只用节点自己的特征
    ):
        super().__init__()
        self.static_dims = static_dims
        self.temporal_dims = temporal_dims
        self.hidden_dim = hidden_dim
        self.node_types = list(static_dims.keys())
        self.enable_phy_link = enable_phy_link
        self.enable_vm_traffic = enable_vm_traffic
        self.enable_layer3 = enable_layer3
        self.disable_graph_propagation = disable_graph_propagation

        # 双模态特征融合（每种节点类型独立）
        self.feature_fusion = nn.ModuleDict({
            ntype: FeatureFusion(
                temporal_dim=temporal_dims.get(ntype, 6),
                static_dim=dim,
                hidden_dim=hidden_dim,
                dropout=dropout,
            )
            for ntype, dim in static_dims.items()
        })

        # 注意：W_host, W_deploy, W_traffic 保持不变用于传导
        # 但它们的输入维度需要匹配 hidden_dim
        if self.enable_phy_link:
            # W_self_phy: 物理机自更新算子
            self.w_self_phy = nn.Linear(hidden_dim, hidden_dim, bias=False)
            # W_link: 物理传导矩阵
            self.w_link = nn.Linear(hidden_dim, hidden_dim, bias=False)
            # 物理层归一化
            self.phy_norm = nn.LayerNorm(hidden_dim)

        # W_host: 物理承载矩阵
        self.w_host = nn.Linear(hidden_dim, hidden_dim, bias=False)
        # W_deploy: 应用部署矩阵
        self.w_deploy = nn.Linear(hidden_dim, hidden_dim, bias=False)

        # 图卷积层
        # Layer 1 (Host 层): self_loop 和 r_link (物理拓扑边)
        self.layer1 = HeteroGCNLayer(
            in_dim=hidden_dim,
            out_dim=hidden_dim,
            etypes=[("Vphy", "r_link", "Vphy"), ("Vphy", "r_hosting", "Vvm"), ("Vvm", "r_traffic", "Vvm")],
            aggregation="sum",
            dropout=dropout,
            use_attention=True,
        )
        # Layer 2 (VM 层): self_loop、r_host 和 r_traffic
        self.layer2 = HeteroGCNLayer(
            in_dim=hidden_dim,
            out_dim=hidden_dim,
            etypes=[("Vphy", "r_hosting", "Vvm"), ("Vvm", "r_traffic", "Vvm"), ("Vvm", "r_deployment", "Vbiz")],
            aggregation="sum",
            dropout=dropout,
            use_attention=True,
        )
        # Layer 3 (App 层): 仅 r_deployment，mean 聚合（移除 r_calling，消除度偏差）
        self.layer3 = HeteroGCNLayer(
            in_dim=hidden_dim,
            out_dim=hidden_dim,
            etypes=[("Vvm", "r_deployment", "Vbiz")],  # 仅 r_deployment
            aggregation="mean",  # 必须用 mean，消除多活部署的度偏差
            dropout=dropout,
            use_attention=False,
        )

        pred_hidden_1 = max(hidden_dim // 4, 32)
        pred_hidden_2 = max(pred_hidden_1 // 4, 8)

        # 单分支预测层: h_biz_3 (hidden_dim) -> hidden_dim/4 -> hidden_dim/16 -> 1
        self.predictor = nn.Sequential(
            nn.Linear(hidden_dim, pred_hidden_1),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(pred_hidden_1, pred_hidden_2),
            nn.ReLU(),
            nn.Linear(pred_hidden_2, 1),  # 输出原始 logits
        )

        self._init_weights()

    def _init_weights(self) -> None:
        # 使用标准 Xavier 初始化
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

    def _encode_features(
        self,
        ntype: str,
        static_feat: torch.Tensor,   # (B, N, D_s) 或 (N, D_s)
        temporal_feat: torch.Tensor, # (B, T, N, D_t) 或 (T, N, D_t)
    ) -> torch.Tensor:
        """编码单种节点类型的特征，返回 (B, N, hidden) 或 (N, hidden)

        使用双模态特征融合: 1D-CNN (动态) + Linear (静态) -> hidden_dim
        """
        has_batch = static_feat.dim() == 3

        if has_batch:
            B, N, D_s = static_feat.shape
            # 静态特征: (B, N, D_s) -> (B*N, D_s)
            static_flat = static_feat.reshape(B * N, D_s)

            # 时序特征: (B, T, N, D_t) -> (B*N, T, D_t)
            if temporal_feat.dim() == 4:
                tf = temporal_feat.permute(0, 2, 1, 3).reshape(B * N, -1, temporal_feat.shape[-1])
            else:
                tf = temporal_feat.permute(1, 0, 2)  # (N, T, D_t)
                tf = tf.unsqueeze(0).expand(B, -1, -1, -1).reshape(B * N, -1, tf.shape[-1])

            # 双模态融合: (B*N, hidden_dim)
            fused = self.feature_fusion[ntype](tf, static_flat)
            return fused.reshape(B, N, -1)  # (B, N, hidden_dim)
        else:
            N, D_s = static_feat.shape
            # 时序特征: (T, N, D_t) -> (N, T, D_t)
            if temporal_feat.dim() == 3:
                tf = temporal_feat.permute(1, 0, 2)  # (N, T, D_t)
            else:
                tf = temporal_feat  # already (N, T, D_t)

            # 双模态融合
            return self.feature_fusion[ntype](tf, static_feat)  # (N, hidden_dim)

    def _phy_lateral_diffusion(
        self,
        g: dgl.DGLGraph,
        h_phy: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """物理层横向扩散 

        实现公式: h_phy_self_total = sigma(W_self_phy * h_phy_input + sum(W_link * h_k_neighbor))

        Args:
            g: DGL异构图
            h_phy: 物理机节点特征 (N_phy, hidden)

        Returns:
            (聚合后的物理机特征, W_link贡献, W_self_phy贡献)
        """
        device = h_phy.device
        n_phy = h_phy.shape[0]

        # W_self_phy 贡献：对物理机自身特征进行变换
        phy_self_contrib = self.w_self_phy(h_phy)

        # 获取物理邻居的边 (r_link 边) - 向量化实现
        phy_link_contrib = torch.zeros_like(h_phy)
        try:
            if ("Vphy", "r_link", "Vphy") in g.canonical_etypes:
                sub_g = g.edge_type_subgraph([("Vphy", "r_link", "Vphy")])
                src, dst = sub_g.edges()
                if len(src) > 0:
                    src_max = src.max().item() if len(src) > 0 else 0
                    dst_max = dst.max().item() if len(dst) > 0 else 0
                    if src_max < n_phy and dst_max < n_phy:
                        src_indices = src.to(device)
                        dst_indices = dst.to(device)
                        neighbor_h = h_phy[src_indices]
                        neighbor_transformed = self.w_link(neighbor_h)
                        phy_link_contrib.index_add_(0, dst_indices, neighbor_transformed)
        except Exception:
            pass

        # 合并两者并应用归一化
        h_phy_total = self.phy_norm(F.relu(phy_self_contrib + phy_link_contrib))

        return h_phy_total, phy_link_contrib, phy_self_contrib

    def forward(
        self,
        g: dgl.DGLGraph,
        static_features: dict[str, torch.Tensor],
        temporal_features: dict[str, torch.Tensor],
        temperature: float = 1.0,
    ) -> TemporalModelOutput:
        """前向传播

        三层架构:
        - Layer 1 (Host): self_loop + r_link
        - Layer 2 (VM): self_loop + r_host + r_traffic
        - Layer 3 (App): 仅 r_deployment，mean 聚合

        Args:
            g: DGL 异构图
            static_features:  {ntype: (B, N, D_s)} 或 {ntype: (N, D_s)}
            temporal_features: {ntype: (B, T, N, D_t)} 或 {ntype: (T, N, D_t)}

        Returns:
            TemporalModelOutput
        """
        device = next(self.parameters()).device

        # ── 1. 编码各节点类型特征 ─────────────────────────────────────────────
        h0: dict[str, torch.Tensor] = {}
        for ntype in self.node_types:
            N = g.num_nodes(ntype)
            s_feat = static_features.get(ntype)
            t_feat = temporal_features.get(ntype)

            if s_feat is None:
                s_feat = torch.zeros(N, self.static_dims[ntype], device=device)
            if t_feat is None:
                t_feat = torch.zeros(1, N, self.temporal_dims.get(ntype, 6), device=device)

            h0[ntype] = self._encode_features(ntype, s_feat, t_feat)

        # ── [关键] 2. App 节点强制致盲 ────────────────────────────────────────
        # 无论是否传入了 App 特征，强制覆盖为零
        if "Vbiz" in h0:
            h0["Vbiz"] = torch.zeros_like(h0["Vbiz"], device=device)

        # ── 3. 物理层横向扩散 (W_link + W_self_phy) ─────────────────────────
        phy_link_contrib = None
        phy_self_contrib = None
        if self.enable_phy_link and "Vphy" in h0 and g.num_nodes("Vphy") > 0:
            h0["Vphy"], phy_link_contrib, phy_self_contrib = self._phy_lateral_diffusion(
                g, h0["Vphy"]
            )

        # ── 4. 三层图卷积传导 ─────────────────────────────────────────────────
        # h0: 初始特征 (Host/VM/Biz 都是 hidden_dim)
        # App 已被致盲为零

        # Layer 1: Host self_loop + r_link -> Host 更新
        h_phy = self.layer1(g, h0, "Vphy")

        # 构建中间特征字典（用于传递给下一层）
        h_intermediate = {
            "Vphy": h_phy,
            "Vvm": h0["Vvm"],  # VM 层使用原始特征
            "Vbiz": h0["Vbiz"],  # Biz 层已被致盲为零
        }

        # Layer 2: VM self_loop + r_host (from Host) + r_traffic (from VM) -> VM 更新
        h_vm = self.layer2(g, h_intermediate, "Vvm")

        # 更新中间特征字典
        h_intermediate["Vvm"] = h_vm

        # Layer 3: App 仅 r_deployment (from VM)，mean 聚合 -> App 更新
        # App 初始为零，来自 VM 的传导成为唯一信息来源
        h_biz = self.layer3(g, h_intermediate, "Vbiz")

        h3 = {
            "Vphy": h_phy,
            "Vvm": h_vm,
            "Vbiz": h_biz,
        }

        # ── 5. 风险评分 (单分支 MLP) ─────────────────────────────────────────
        if self.disable_graph_propagation:
            risk_h = h0["Vbiz"]  # 全零
        else:
            risk_h = h3["Vbiz"]

        risk_logit = self.predictor(risk_h).squeeze(-1)

        if risk_logit.dim() == 0:
            risk_logit = risk_logit.unsqueeze(0)

        # A1: temperature scaling (default 1.0 = no change; > 1.0 spreads probs)
        if temperature != 1.0:
            risk_logit = risk_logit / temperature

        risk_prob = torch.sigmoid(risk_logit)

        return TemporalModelOutput(
            risk_prob=risk_prob,
            risk_logit=risk_logit,
            vertical_score=risk_logit,  # 兼容旧接口
            horizontal_score=torch.zeros_like(risk_logit),  # 兼容旧接口
            node_embeddings=h3,
            phy_link_contribution=phy_link_contrib,
            phy_self_contribution=phy_self_contrib,
        )


class TenantRiskPredictor(nn.Module):
    """租户风险预测器 - 封装完整预测流程"""

    def __init__(
        self,
        static_dims: dict[str, int],
        temporal_dims: dict[str, int],
        hidden_dim: int = 256,  # 双模态融合后是 256d
        num_layers: int = 3,    # 方案要求3层
        dropout: float = 0.2,   # 方案要求0.2 dropout
        enable_phy_link: bool = True,
        enable_vm_traffic: bool = True,
        enable_layer3: bool = True,
        disable_graph_propagation: bool = False,
    ):
        super().__init__()
        self.model = TemporalRiskGCN(
            static_dims=static_dims,
            temporal_dims=temporal_dims,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            dropout=dropout,
            enable_phy_link=enable_phy_link,
            enable_vm_traffic=enable_vm_traffic,
            enable_layer3=enable_layer3,
            disable_graph_propagation=disable_graph_propagation,
        )

    def forward(
        self,
        g: dgl.DGLGraph,
        static_features: dict[str, torch.Tensor],
        temporal_features: dict[str, torch.Tensor],
        temperature: float = 1.0,
    ) -> TemporalModelOutput:
        return self.model(g, static_features, temporal_features, temperature=temperature)


    def forward_subgraph(
        self,
        g: dgl.DGLGraph,
        static_features: dict[str, torch.Tensor],
        temporal_features: dict[str, torch.Tensor],
        temperature: float = 1.0,
    ) -> TemporalModelOutput:
        """SCR: forward with subgraph (drop r_calling edges).

        Full-graph training keeps all 5 edge types. Online inference
        drops r_calling (per project scheme). SCR penalizes the
        difference between full and subgraph outputs.
        """
        if ("Vbiz", "r_calling", "Vbiz") in g.canonical_etypes:
            eids = g.edges(form="eid", etype=("Vbiz", "r_calling", "Vbiz"))
            g_sub = dgl.remove_edges(g, eids, etype=("Vbiz", "r_calling", "Vbiz"))
        else:
            g_sub = g
        return self.model(g_sub, static_features, temporal_features, temperature=temperature)

    def predict_risk_level(
        self,
        risk_prob: torch.Tensor,
        high_threshold: float = 0.8,
        medium_threshold: float = 0.5,
    ) -> torch.Tensor:
        """将概率映射到风险等级 0/1/2"""
        risk_level = torch.zeros_like(risk_prob, dtype=torch.long)
        risk_level[risk_prob >= high_threshold] = 2
        risk_level[(risk_prob >= medium_threshold) & (risk_prob < high_threshold)] = 1
        return risk_level

    def get_risk_description(self, level: int) -> str:
        return {
            0: "Low Risk (Ignore)",
            1: "Warning (Manual Check)",
            2: "High Risk (Block/Migrate)",
        }.get(level, "Unknown")
