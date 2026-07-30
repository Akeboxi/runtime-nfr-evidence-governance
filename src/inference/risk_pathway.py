"""风险传播路径追踪与可视化模块"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from enum import Enum
import numpy as np
import torch
import dgl
from src.models.temporal_rgcn import TenantRiskPredictor


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class NodeRiskInfo:
    """节点风险信息"""
    node_id: str
    node_type: str  # Vphy, Vvm, Vbiz
    risk_score: float  # 该节点对下游的风险贡献
    risk_prob: float = 0.0  # 该节点自身的风险概率
    layer: int = 0  # 所属层级 (0=物理层, 1=虚拟层, 2=业务层)
    parents: list[str] = field(default_factory=list)  # 上游节点ID列表
    contribution: float = 0.0  # 对最终风险预测的贡献度


@dataclass
class RiskPathway:
    """风险传播路径"""
    pathway_id: str
    source_node: str  # 源头物理机
    target_nodes: list[str]  # 受影响的业务节点
    pathway_nodes: list[NodeRiskInfo] = field(default_factory=list)
    pathway_edges: list[tuple[str, str, float]] = field(default_factory=list)  # (src, dst, weight)
    total_risk: float = 0.0
    prisk: float = 0.0  # 最终预测风险概率

    def to_dict(self) -> dict:
        return {
            "pathway_id": self.pathway_id,
            "source_node": self.source_node,
            "target_nodes": self.target_nodes,
            "total_risk": float(self.total_risk),
            "prisk": float(self.prisk),
            "pathway": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "risk_score": float(n.risk_score),
                    "layer": n.layer,
                    "parents": n.parents,
                    "contribution": float(n.contribution),
                }
                for n in self.pathway_nodes
            ],
            "edges": [
                {"src": e[0], "dst": e[1], "weight": float(e[2])}
                for e in self.pathway_edges
            ],
        }


@dataclass
class LayerRiskContribution:
    """各层风险贡献"""
    layer: int
    layer_name: str
    nodes: list[NodeRiskInfo]
    total_contribution: float


class RiskPathwayTracker:
    """风险传播路径追踪器

    在推理阶段追踪风险从物理层到业务层的传播路径，
    识别风险最高的节点和边。
    """

    def __init__(
        self,
        hidden_dim: int = 128,
        top_k_nodes: int = 5,
    ):
        """初始化追踪器

        Args:
            hidden_dim: 隐藏层维度
            top_k_nodes: 每层保留的风险最高的节点数量
        """
        self.hidden_dim = hidden_dim
        self.top_k_nodes = top_k_nodes

    def extract_pathways(
        self,
        model: "TenantRiskPredictor",
        graph: "dgl.DGLGraph",
        static_features: dict[str, torch.Tensor],
        temporal_features: dict[str, torch.Tensor],
        node_ids: dict[str, list[str]] = None,
        risk_probs: np.ndarray = None,
        change_host_ids: list[str] | None = None,
    ) -> tuple[list[RiskPathway], dict[int, LayerRiskContribution]]:
        """提取风险传播路径

        Args:
            model: 训练好的模型
            graph: DGL异构图
            static_features: 静态特征
            temporal_features: 时序特征
            node_ids: 节点ID映射 {ntype: [node_id, ...]}
            risk_probs: 业务节点的风险概率，用于筛选高风险路径
            change_host_ids: 发生变更的物理机ID列表

        Returns:
            (风险路径列表, 各层风险贡献)
        """
        device = next(model.parameters()).device
        model.eval()

        with torch.no_grad():
            # 1. 获取各层激活
            layer_activations = self._get_layer_activations(
                model, graph, static_features, temporal_features
            )

            # 2. 计算各节点的风险贡献
            contributions = self._compute_contributions(
                model, layer_activations, graph, node_ids
            )

            # 3. 提取每层的风险最高的节点（根据risk_probs筛选）
            biz_node_ids = node_ids.get("Vbiz", []) if node_ids else []
            top_nodes_by_layer = self._extract_top_nodes(
                contributions, biz_node_ids, risk_probs, change_host_ids
            )

            # 4. 构建风险路径
            pathways = self._build_pathways(
                top_nodes_by_layer, contributions, graph, change_host_ids
            )

            # 5. 计算各层汇总贡献
            layer_contributions = self._summarize_by_layer(contributions)

        return pathways, layer_contributions

    def _get_layer_activations(
        self,
        model: "TenantRiskPredictor",
        graph: "dgl.DGLGraph",
        static_features: dict[str, torch.Tensor],
        temporal_features: dict[str, torch.Tensor],
    ) -> dict[str, torch.Tensor]:
        """获取模型各层的激活值

        Returns:
            各层激活值字典，包含:
            - h0: 初始编码
            - h1_phy: 物理层扩散后
            - h1_vm: VM层 Layer1后
            - h2_vm: VM层 Layer2后
            - h2_biz: 业务层 Layer2后
            - h3_biz: 业务层 Layer3后
        """
        device = next(model.parameters()).device

        # 使用模型的内部计算逻辑，但获取中间结果
        g = graph.to(device)
        node_types = list(model.model.node_types)

        # 编码特征
        h0 = {}
        for ntype in node_types:
            N = g.num_nodes(ntype)
            s_feat = static_features.get(ntype)
            t_feat = temporal_features.get(ntype)

            if s_feat is None:
                s_feat = torch.zeros(N, model.model.static_dims[ntype], device=device)
            else:
                s_feat = s_feat.to(device)
            if t_feat is None:
                t_feat = torch.zeros(1, N, model.model.temporal_dims.get(ntype, 6), device=device)
            else:
                t_feat = t_feat.to(device)

            h0[ntype] = model.model._encode_features(ntype, s_feat, t_feat)

        # 物理层横向扩散
        if model.model.enable_phy_link and "Vphy" in h0 and g.num_nodes("Vphy") > 0:
            h0["Vphy"], _, _ = model.model._phy_lateral_diffusion(g, h0["Vphy"])

        # 构建中间特征字典（与 TemporalRiskGCN.forward 一致）
        h_intermediate = {
            "Vphy": h0["Vphy"],
            "Vvm": h0["Vvm"],
            "Vbiz": h0["Vbiz"],
        }

        # Layer 1: Vphy self_loop + r_link -> Vphy
        h_phy = model.model.layer1(g, h0, "Vphy")

        # Layer 2: Vphy -> Vvm (r_host), Vvm self_loop + r_traffic -> Vvm
        h_intermediate["Vphy"] = h_phy
        h_vm = model.model.layer2(g, h_intermediate, "Vvm")

        # Layer 3: Vvm -> Vbiz (r_deployment, mean 聚合)
        h_intermediate["Vvm"] = h_vm
        h_biz = model.model.layer3(g, h_intermediate, "Vbiz")

        # 用于计算贡献度的中间结果
        h_phy_transformed = model.model.w_host(h0["Vphy"])
        h_vm_transformed = model.model.w_deploy(h_vm)

        return {
            "h0": h0,
            "h_phy_transformed": h_phy_transformed,
            "h1_vm": h_vm,
            "h2_vm": h_vm_transformed,
            "h2_biz": h_biz,
            "h3_biz": h_biz,
        }

    def _compute_contributions(
        self,
        model: "TenantRiskPredictor",
        activations: dict[str, torch.Tensor],
        graph: "dgl.DGLGraph",
        node_ids: dict[str, list[str]] = None,
    ) -> dict[int, list[NodeRiskInfo]]:
        """计算各层节点的贡献度

        通过计算节点特征与权重矩阵的乘积来确定贡献
        """
        contributions: dict[int, list[NodeRiskInfo]] = {}

        # Layer 0: 物理层
        h_phy = activations["h0"]["Vphy"]
        phy_transformed = activations["h_phy_transformed"]
        phy_scores = torch.norm(phy_transformed, dim=1).cpu().numpy()

        # 使用node_ids中的实际节点ID
        if node_ids and "Vphy" in node_ids:
            phy_nids = node_ids["Vphy"]
        elif "Vphy" in graph.ntypes:
            num = graph.num_nodes("Vphy")
            phy_nids = [f"Vphy_{i}" for i in range(num)]
        else:
            phy_nids = []

        contributions[0] = []
        for i, (node_id, score) in enumerate(zip(phy_nids, phy_scores)):
            contributions[0].append(NodeRiskInfo(
                node_id=node_id,
                node_type="Vphy",
                risk_score=float(score),
                layer=0,
            ))

        # Layer 1: 虚拟层
        h_vm = activations["h1_vm"]
        vm_scores = torch.norm(h_vm, dim=1).cpu().numpy()

        if node_ids and "Vvm" in node_ids:
            vm_nids = node_ids["Vvm"]
        elif "Vvm" in graph.ntypes:
            num = graph.num_nodes("Vvm")
            vm_nids = [f"Vvm_{i}" for i in range(num)]
        else:
            vm_nids = []

        contributions[1] = []
        for i, (node_id, score) in enumerate(zip(vm_nids, vm_scores)):
            contributions[1].append(NodeRiskInfo(
                node_id=node_id,
                node_type="Vvm",
                risk_score=float(score),
                layer=1,
            ))

        # Layer 2: 业务层
        h_biz = activations["h3_biz"]
        biz_scores = torch.norm(h_biz, dim=1).cpu().numpy()

        if node_ids and "Vbiz" in node_ids:
            biz_nids = node_ids["Vbiz"]
        elif "Vbiz" in graph.ntypes:
            num = graph.num_nodes("Vbiz")
            biz_nids = [f"Vbiz_{i}" for i in range(num)]
        else:
            biz_nids = []

        contributions[2] = []
        for i, (node_id, score) in enumerate(zip(biz_nids, biz_scores)):
            contributions[2].append(NodeRiskInfo(
                node_id=node_id,
                node_type="Vbiz",
                risk_score=float(score),
                layer=2,
            ))

        return contributions

    def _extract_top_nodes(
        self,
        contributions: dict[int, list[NodeRiskInfo]],
        biz_node_ids: list[str],
        risk_probs: np.ndarray,
        change_host_ids: list[str] | None = None,
    ) -> dict[int, list[NodeRiskInfo]]:
        """提取每层风险最高的节点

        只选择 biz_node_ids 中模型预测概率较高的节点对应的路径节点
        """
        top_nodes = {}

        # 找出高风险的biz节点索引（概率 >= medium_threshold）
        high_risk_biz_indices = set()
        for i, p in enumerate(risk_probs):
            if p >= 0.3:  # medium_threshold
                high_risk_biz_indices.add(i)

        for layer, nodes in contributions.items():
            if not nodes:
                continue

            # 筛选：只保留与高风险biz节点有连接的节点
            if layer == 2:  # biz层
                # 只选择概率较高的节点
                filtered_nodes = []
                for i, node in enumerate(nodes):
                    if i in high_risk_biz_indices:
                        filtered_nodes.append(node)
                top_nodes[layer] = filtered_nodes[:self.top_k_nodes]
            else:
                # 其他层保持按risk_score排序，取前top_k
                sorted_nodes = sorted(nodes, key=lambda x: x.risk_score, reverse=True)
                # 如果有变更主机，优先保留
                if change_host_ids and layer == 0:
                    filtered = []
                    for n in sorted_nodes:
                        if any(cid in n.node_id for cid in change_host_ids):
                            filtered.append(n)
                    filtered.extend(sorted_nodes)
                    sorted_nodes = filtered[:self.top_k_nodes]
                else:
                    sorted_nodes = sorted_nodes[:self.top_k_nodes]
                top_nodes[layer] = sorted_nodes

        return top_nodes

    def _build_pathways(
        self,
        top_nodes_by_layer: dict[int, list[NodeRiskInfo]],
        contributions: dict[int, list[NodeRiskInfo]],
        graph: "dgl.DGLGraph",
        change_host_ids: list[str] | None = None,
    ) -> list[RiskPathway]:
        """构建风险传播路径，只考虑图中实际存在的边"""
        pathways = []

        # 构建节点ID到NodeRiskInfo的映射
        node_id_to_node = {}
        for layer_nodes in contributions.values():
            for node in layer_nodes:
                node_id_to_node[node.node_id] = node

        # 从图中提取实际存在的边，使用node_id字符串
        # 图中的边是(src_idx, dst_idx)，需要转换为node_id
        # Vphy -> Vvm (r_hosting) 边：src是Vphy索引，dst是Vvm索引
        phy_nodes_by_idx = {i: n.node_id for i, n in enumerate(contributions.get(0, []))}
        vm_nodes_by_idx = {i: n.node_id for i, n in enumerate(contributions.get(1, []))}
        biz_nodes_by_idx = {i: n.node_id for i, n in enumerate(contributions.get(2, []))}

        # 构建 node_id 到索引的映射
        idx_of_node_id = {}
        for layer, nodes in contributions.items():
            for i, node in enumerate(nodes):
                idx_of_node_id[node.node_id] = (layer, i)

        # 建立边连接关系：使用node_id字符串
        # biz_node_id -> [vm_node_id, ...]
        biz_to_vm = {}
        # vm_node_id -> [phy_node_id, ...]
        vm_to_phy = {}

        try:
            # Vphy -> Vvm (r_hosting): src是phy_idx, dst是vm_idx
            if ("Vphy", "r_hosting", "Vvm") in graph.canonical_etypes:
                subg = graph.edge_type_subgraph([("Vphy", "r_hosting", "Vvm")])
                src, dst = subg.edges()
                for i in range(len(src)):
                    phy_idx = src[i].item()
                    vm_idx = dst[i].item()
                    if phy_idx in phy_nodes_by_idx and vm_idx in vm_nodes_by_idx:
                        phy_id = phy_nodes_by_idx[phy_idx]
                        vm_id = vm_nodes_by_idx[vm_idx]
                        vm_to_phy.setdefault(vm_id, []).append(phy_id)
        except:
            pass

        try:
            # Vvm -> Vbiz (r_deployment): src是vm_idx, dst是biz_idx
            if ("Vvm", "r_deployment", "Vbiz") in graph.canonical_etypes:
                subg = graph.edge_type_subgraph([("Vvm", "r_deployment", "Vbiz")])
                src, dst = subg.edges()
                for i in range(len(src)):
                    vm_idx = src[i].item()
                    biz_idx = dst[i].item()
                    if vm_idx in vm_nodes_by_idx and biz_idx in biz_nodes_by_idx:
                        vm_id = vm_nodes_by_idx[vm_idx]
                        biz_id = biz_nodes_by_idx[biz_idx]
                        biz_to_vm.setdefault(biz_id, []).append(vm_id)
        except:
            pass

        # 以变更物理机为源头，构建到各业务节点的路径
        phy_nodes = top_nodes_by_layer.get(0, [])
        vm_nodes = top_nodes_by_layer.get(1, [])
        biz_nodes = top_nodes_by_layer.get(2, [])

        # 为每个高风险业务节点构建路径
        for biz_node in biz_nodes:
            if biz_node.node_id not in biz_to_vm:
                continue

            pathway_nodes = [biz_node]

            # 找到提供最大贡献的VM节点（必须与biz有实际边连接）
            source_vm = None
            max_contrib = 0.0
            for connected_vm_id in biz_to_vm.get(biz_node.node_id, []):
                if connected_vm_id not in node_id_to_node:
                    continue
                vm_node = node_id_to_node[connected_vm_id]
                contrib = vm_node.risk_score / (biz_node.risk_score + 1e-8)
                if contrib > max_contrib:
                    max_contrib = contrib
                    source_vm = vm_node

            if source_vm:
                pathway_nodes.insert(0, source_vm)

                # 找到提供最大贡献的物理机节点（必须与vm有实际边连接）
                source_phy = None
                max_contrib = 0.0
                for connected_phy_id in vm_to_phy.get(source_vm.node_id, []):
                    if connected_phy_id not in node_id_to_node:
                        continue
                    phy_node = node_id_to_node[connected_phy_id]
                    contrib = phy_node.risk_score / (source_vm.risk_score + 1e-8)
                    if contrib > max_contrib:
                        max_contrib = contrib
                        source_phy = phy_node

                if source_phy:
                    pathway_nodes.insert(0, source_phy)

            if len(pathway_nodes) > 1:
                pathway = RiskPathway(
                    pathway_id=f"pathway_{biz_node.node_id}",
                    source_node=pathway_nodes[0].node_id if pathway_nodes else "unknown",
                    target_nodes=[biz_node.node_id],
                    pathway_nodes=pathway_nodes,
                    pathway_edges=self._extract_pathway_edges(
                        pathway_nodes, graph, contributions
                    ),
                    total_risk=biz_node.risk_score,
                    prisk=biz_node.risk_prob,
                )
                pathways.append(pathway)

        return pathways

    def _extract_pathway_edges(
        self,
        pathway_nodes: list[NodeRiskInfo],
        graph: "dgl.DGLGraph",
        contributions: dict[int, list[NodeRiskInfo]],
    ) -> list[tuple[str, str, float]]:
        """提取路径上的边及权重"""
        edges = []

        # 建立节点ID到索引的映射
        node_id_to_idx = {}
        idx = 0
        for layer_nodes in contributions.values():
            for node in layer_nodes:
                node_id_to_idx[node.node_id] = idx
                idx += 1

        for i in range(len(pathway_nodes) - 1):
            src = pathway_nodes[i]
            dst = pathway_nodes[i + 1]

            # 根据层级确定边类型
            if src.layer == 0 and dst.layer == 1:
                edge_type = "r_hosting"
            elif src.layer == 1 and dst.layer == 2:
                edge_type = "r_deployment"
            elif src.layer == 2 and dst.layer == 2:
                edge_type = "r_calling"
            else:
                edge_type = "unknown"

            # 边的权重用源节点的风险得分
            weight = src.risk_score / (sum(n.risk_score for n in pathway_nodes) + 1e-8)
            edges.append((src.node_id, dst.node_id, weight))

        return edges

    def _summarize_by_layer(
        self,
        contributions: dict[int, list[NodeRiskInfo]],
    ) -> dict[int, LayerRiskContribution]:
        """汇总各层的风险贡献"""
        layer_names = {
            0: "物理层 (Vphy)",
            1: "虚拟层 (Vvm)",
            2: "业务层 (Vbiz)",
        }

        summary = {}
        for layer, nodes in contributions.items():
            if not nodes:
                continue

            total = sum(n.risk_score for n in nodes)
            summary[layer] = LayerRiskContribution(
                layer=layer,
                layer_name=layer_names.get(layer, f"Layer {layer}"),
                nodes=nodes,
                total_contribution=float(total),
            )

        return summary
