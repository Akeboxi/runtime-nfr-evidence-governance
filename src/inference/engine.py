"""在线推理引擎 - 时序增强版 R-GCN"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from enum import Enum
import numpy as np

import torch

from ..models.temporal_rgcn import TenantRiskPredictor, TemporalModelOutput
from ..data.tenant_dataset import TemporalGraphData
from .risk_pathway import (
    RiskPathwayTracker,
    RiskPathway,
    LayerRiskContribution,
    NodeRiskInfo,
)


def visualize_risk_graph(
    graph_dir: str,
    output_path: str,
    risk_pathways: list[RiskPathway],
    top_risky_nodes: dict[int, list[NodeRiskInfo]] = None,
    high_threshold: float = 0.5,
    title: str = None,
    true_anomaly_nodes: set = None,
    node_ids: list = None,
    risk_probs: list = None,
) -> str:
    """可视化风险传播图，高亮高风险节点和路径

    Args:
        graph_dir: 图结构目录（包含 nodes/ 和 edges/）
        output_path: 输出图片路径
        risk_pathways: 风险传播路径列表
        top_risky_nodes: 每层高风险节点（预测的）
        high_threshold: 高风险阈值
        title: 图表标题
        true_anomaly_nodes: 真实标注的异常节点集合，格式如 {"host_1", "vm_3"}
        node_ids: 业务层节点ID列表（Vbiz）
        risk_probs: 业务层节点的风险概率列表，与 node_ids 对应

    Returns:
        输出图片路径
    """
    import os
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    NODE_TYPES = ["host", "vm", "app"]
    NODE_COLORS = {"host": "#1f77b4", "vm": "#ff7f0e", "app": "#ffffff"}
    EDGE_COLORS = {
        "host_host": "#8c564b", "vm_host": "#7f7f7f",
        "vm_vm": "#bcbd22", "vm_app": "#17becf", "app_app": "#9467bd",
    }

    # 真实异常节点默认为空集合
    if true_anomaly_nodes is None:
        true_anomaly_nodes = set()

    def _read_nodes(nodes_dir):
        dfs = []
        for t in NODE_TYPES:
            p = os.path.join(nodes_dir, f"{t}.csv")
            if os.path.exists(p):
                dfs.append(pd.read_csv(p))
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    def _read_edges(edges_dir):
        edge_types = ["host_host", "vm_host", "vm_vm", "vm_app", "app_app"]
        dfs = []
        for t in edge_types:
            p = os.path.join(edges_dir, f"{t}.csv")
            if os.path.exists(p):
                try:
                    dfs.append(pd.read_csv(p))
                except:
                    continue
        return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    nodes_dir = os.path.join(graph_dir, "nodes")
    edges_dir = os.path.join(graph_dir, "edges")

    if not os.path.exists(nodes_dir):
        return ""

    nodes = _read_nodes(nodes_dir)
    edges = _read_edges(edges_dir)

    # 收集高亮节点和边
    risky_node_ids: set = set()
    pathway_edge_set: set = set()

    # risk_score 阈值（用于 host/vm 层，因为它们的 risk_prob 都是 0）
    # host层阈值降低以减少漏报
    RISK_SCORE_THRESHOLD = 10.0

    if risk_pathways:
        for pathway in risk_pathways:
            for node in pathway.pathway_nodes:
                # 对于 host/vm 层使用 risk_score 判断，对于 app 层使用 risk_prob 判断
                node_id = node.node_id.replace("Vphy_", "host_").replace("Vvm_", "vm_").replace("Vbiz_", "app_")
                is_high_risk = False
                if node.layer == 2:
                    # 业务层：用 risk_prob
                    is_high_risk = node.risk_prob > high_threshold
                else:
                    # 物理层/虚拟层：用 risk_score
                    is_high_risk = node.risk_score > RISK_SCORE_THRESHOLD
                if is_high_risk:
                    risky_node_ids.add(node_id)
            for src, dst, weight in pathway.pathway_edges:
                src_id = src.replace("Vphy_", "host_").replace("Vvm_", "vm_").replace("Vbiz_", "app_")
                dst_id = dst.replace("Vphy_", "host_").replace("Vvm_", "vm_").replace("Vbiz_", "app_")
                pathway_edge_set.add((src_id, dst_id))

    if top_risky_nodes:
        for layer, nodes_list in top_risky_nodes.items():
            for node in nodes_list:
                node_id = node.node_id.replace("Vphy_", "host_").replace("Vvm_", "vm_").replace("Vbiz_", "app_")
                is_high_risk = False
                if node.layer == 2:
                    # 业务层：用 risk_prob
                    is_high_risk = node.risk_prob > high_threshold
                else:
                    # 物理层/虚拟层：用 risk_score
                    is_high_risk = node.risk_score > RISK_SCORE_THRESHOLD
                if is_high_risk:
                    risky_node_ids.add(node_id)

    # 布局
    pos = {}
    for xi, t in enumerate(NODE_TYPES):
        ids = [str(x) for x in nodes[nodes["node_type"] == t]["node_id"].tolist()]
        if not ids:
            continue
        ys = np.linspace(0.05, 0.95, num=len(ids), endpoint=True) if len(ids) > 1 else np.array([0.5])
        for node_id, y in zip(ids, ys):
            x = float(xi) + np.random.normal(0.0, 0.04)
            y = float(y) + np.random.normal(0.0, 0.02)
            pos[node_id] = (x, y)

    fig, ax = plt.subplots(figsize=(14, 8))

    # 绘制边
    for _, r in edges.iterrows():
        src = str(r["src_id"])
        dst = str(r["dst_id"])
        et = str(r.get("edge_type", ""))

        if src not in pos or dst not in pos:
            continue

        if (src, dst) in pathway_edge_set or (dst, src) in pathway_edge_set:
            ax.plot([pos[src][0], pos[dst][0]], [pos[src][1], pos[dst][1]],
                    color="#d62728", linewidth=2.5, alpha=0.8, zorder=2)
        else:
            ax.plot([pos[src][0], pos[dst][0]], [pos[src][1], pos[dst][1]],
                    color=EDGE_COLORS.get(et, "#aaaaaa"), linewidth=0.8, alpha=0.4, zorder=1)

    # 绘制节点
    for t in NODE_TYPES:
        ids = [str(x) for x in nodes[nodes["node_type"] == t]["node_id"].tolist()]

        # 只处理在 pos 中的节点
        valid_ids = [i for i in ids if i in pos]
        if not valid_ids:
            continue

        xs = [pos[i][0] for i in valid_ids]
        ys = [pos[i][1] for i in valid_ids]

        edgecolors, linewidths, node_colors = [], [], []
        for i in valid_ids:
            is_true_anomaly = i in true_anomaly_nodes
            is_predicted_risky = i in risky_node_ids

            # VM 层：仅根据 L2 范数（risk_score）判断，不使用真实标签
            if t == "vm":
                if is_predicted_risky:
                    # 高风险 - 橙色/黄色高亮
                    edgecolors.append("#ff8c00")
                    linewidths.append(2.5)
                    node_colors.append("#ffd700")
                else:
                    # 低风险 - 蓝灰色
                    edgecolors.append("#333333")
                    linewidths.append(0.8)
                    node_colors.append("#6a7b8b")
            # Host/App 层：使用真实标签进行颜色分类
            elif is_true_anomaly and is_predicted_risky:
                # TP - 正确预测异常
                edgecolors.append("#006400")
                linewidths.append(2.5)
                node_colors.append("#90ee90")
            elif is_true_anomaly and not is_predicted_risky:
                # FN - 漏报（预测正常但实际异常）
                edgecolors.append("#1f77b4")
                linewidths.append(2.5)
                node_colors.append("#aec7e8")
            elif not is_true_anomaly and is_predicted_risky:
                # FP - 误报（预测异常但实际正常）
                edgecolors.append("#8b0000")
                linewidths.append(2.5)
                node_colors.append("#ff9999")
            else:
                # TN - 正确预测正常
                edgecolors.append("#333333")
                linewidths.append(0.8)
                node_colors.append(NODE_COLORS.get(t, "#000000"))

        ax.scatter(xs, ys, s=180, c=node_colors, edgecolors=edgecolors,
                   linewidths=linewidths, alpha=0.95, label=t, zorder=3)

        # 只对有效节点打印标签
        for i in valid_ids:
            # VM层：仅根据risky_node_ids判断（不依赖true_anomaly_nodes）
            # Host/App层：同时考虑true_anomaly_nodes和risky_node_ids
            show_label = (t == "vm" and i in risky_node_ids) or (t != "vm" and (i in true_anomaly_nodes or i in risky_node_ids))
            if show_label:
                x, y = pos[i]
                # 根据节点类型选择颜色
                if t == "vm":
                    color = "#ff8c00"  # 橙色 - VM层高风险
                elif i in true_anomaly_nodes and i in risky_node_ids:
                    color = "#006400"  # 深绿色 - TP
                elif i in true_anomaly_nodes:
                    color = "#1f77b4"  # 蓝色 - FN
                else:
                    color = "#8b0000"  # 深红色 - FP
                # 根据x位置调整标签偏移方向，避免超出边界
                offset_x = 0.05 if x < 1.5 else -0.08
                ax.text(x + offset_x, y, i, fontsize=7, va="center", color=color, fontweight="bold")

    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["host (Vphy)", "vm (Vvm)", "app (Vbiz)"], fontsize=10)
    ax.set_yticks([])
    ax.grid(False)
    ax.set_xlim(-0.35, 2.35)
    ax.set_ylim(-0.08, 1.08)

    legend_elements = [
        plt.scatter([], [], c=NODE_COLORS["host"], s=100, edgecolors="#333333", label="host"),
        plt.scatter([], [], c=NODE_COLORS["vm"], s=100, edgecolors="#333333", label="vm"),
        plt.scatter([], [], c=NODE_COLORS["app"], s=100, edgecolors="#333333", label="app"),
        plt.scatter([], [], c="#90ee90", s=100, edgecolors="#2ca02c", linewidths=2, label="correct (TP/TN)"),
        plt.scatter([], [], c="#aec7e8", s=100, edgecolors="#1f77b4", linewidths=2, label="missed (FN)"),
        plt.scatter([], [], c="#ff9999", s=100, edgecolors="#d62728", linewidths=2, label="false alarm (FP)"),
        plt.Line2D([0], [0], color="#d62728", linewidth=2, label="risk pathway"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=9)

    default_title = f"Risk Propagation Analysis (global_risk={0.0})"
    ax.set_title(title or default_title, fontsize=12, fontweight="bold")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


def visualize_label_comparison(
    record_id: str,
    true_labels: list[float],
    pred_labels: list[float],
    node_ids: list[str],
    output_path: str,
    top_k: int = 10,
    threshold: float = 0.5,
) -> str:
    """可视化真实标签与预测标签的对比（针对高风险节点）

    选取真实标签 top_k 的高风险节点，展示其真实软标签和预测值的对比折线图。

    Args:
        record_id: record ID
        case: case 类型
        true_labels: 真实软标签列表
        pred_labels: 预测概率列表
        node_ids: 节点 ID 列表
        output_path: 输出图片路径
        top_k: 显示前 top_k 个高风险节点
        threshold: 高风险阈值

    Returns:
        输出图片路径
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # 设置字体
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    # 转换为 numpy 数组
    true_labels = np.array(true_labels)
    pred_labels = np.array(pred_labels)

    # 按真实标签降序排序，选取 top_k 个节点
    sorted_indices = np.argsort(true_labels)[::-1]
    top_indices = sorted_indices[:top_k]

    # 获取对应的节点ID、真实标签、预测标签
    top_node_ids = [node_ids[i] for i in top_indices]
    top_true = true_labels[top_indices]
    top_pred = pred_labels[top_indices]

    # 计算误差
    errors = np.abs(top_true - top_pred)
    mean_mae = np.mean(errors)
    mean_mse = np.mean(errors ** 2)

    # 创建图表
    fig, ax = plt.subplots(figsize=(14, 6))

    x = np.arange(len(top_node_ids))

    # 绘制折线图
    ax.plot(x, top_true, 'o-', color='#1f77b4', linewidth=2, markersize=8, label='True Label', zorder=3)
    ax.plot(x, top_pred, 's--', color='#ff7f0e', linewidth=2, markersize=8, label='Predicted Label', zorder=3)

    # 填充预测误差区域
    ax.fill_between(x, top_true, top_pred, alpha=0.2, color='gray')

    # 设置标签
    ax.set_xlabel('Node ID', fontsize=11)
    ax.set_ylabel('Impact Score', fontsize=11)
    ax.set_title(f'Label Comparison - {record_id}\nMAE={mean_mae:.4f}, MSE={mean_mse:.4f}', fontsize=12, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(top_node_ids, rotation=45, ha='right', fontsize=9)
    ax.legend(loc='upper right')
    ax.set_ylim(0, 1.1)
    ax.grid(axis='y', alpha=0.3)

    # 添加阈值线
    ax.axhline(y=threshold, color='#888888', linestyle='--', linewidth=1, alpha=0.7)
    ax.text(len(top_node_ids)-1.5, threshold + 0.02, f'Threshold={threshold}', fontsize=9, color='#888888')

    # 添加统计信息
    correct_pred = np.sum((top_true > threshold) == (top_pred > threshold))
    stats_text = f'Top-{top_k} High-Risk Nodes\n'
    stats_text += f'Correct: {correct_pred}/{top_k}\n'
    stats_text += f'MAE: {mean_mae:.4f}'
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=9,
            verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_path


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class TenantInferenceResult:
    """推理结果"""
    record_id: str
    case: str
    risk_probs: np.ndarray          # (N_biz,)
    vertical_scores: np.ndarray     # (N_biz,)
    horizontal_scores: np.ndarray   # (N_biz,)
    risk_levels: list[RiskLevel]
    global_risk_score: float
    node_ids: list[str]
    # 新增：风险路径追踪
    risk_pathways: list[RiskPathway] = None  # 风险传播路径
    layer_contributions: dict[int, LayerRiskContribution] = None  # 各层贡献
    top_risky_nodes: dict[int, list[NodeRiskInfo]] = None  # 每层风险最高的节点

    def __post_init__(self):
        if self.risk_pathways is None:
            self.risk_pathways = []
        if self.layer_contributions is None:
            self.layer_contributions = {}
        if self.top_risky_nodes is None:
            self.top_risky_nodes = {}

    def to_dict(self) -> dict[str, Any]:
        result = {
            "record_id": self.record_id,
            "case": self.case,
            "global_risk_score": float(self.global_risk_score),
            "nodes": [
                {
                    "node_id": nid,
                    "risk_prob": float(p),
                    "risk_level": lvl.value,
                    "vertical_score": float(v),
                    "horizontal_score": float(h),
                }
                for nid, p, lvl, v, h in zip(
                    self.node_ids,
                    self.risk_probs,
                    self.risk_levels,
                    self.vertical_scores,
                    self.horizontal_scores,
                )
            ],
        }
        # 添加风险路径信息
        if self.risk_pathways:
            result["risk_pathways"] = [p.to_dict() for p in self.risk_pathways]
        if self.layer_contributions:
            result["layer_contributions"] = {
                layer: {
                    "layer_name": contrib.layer_name,
                    "total_contribution": float(contrib.total_contribution),
                    "top_nodes": [
                        {
                            "node_id": n.node_id,
                            "risk_score": float(n.risk_score),
                            "layer": n.layer,
                        }
                        for n in contrib.nodes[:5]  # 只保留前5个
                    ],
                }
                for layer, contrib in self.layer_contributions.items()
            }
        return result


class TenantInferenceEngine:
    """租户级时序风险推理引擎"""

    def __init__(
        self,
        model: TenantRiskPredictor,
        high_threshold: float = 0.7,
        medium_threshold: float = 0.3,
        device: str | torch.device = "cpu",
        track_pathway: bool = True,
        top_k_nodes: int = 5,
    ):
        """
        Args:
            model: 训练好的 TenantRiskPredictor
            high_threshold: 高风险概率阈值
            medium_threshold: 中风险概率阈值
            device: 推理设备
            track_pathway: 是否追踪风险传播路径
            top_k_nodes: 每层保留的风险最高的节点数量
        """
        self.device = torch.device(device)
        self.model = model.to(self.device)
        self.model.eval()
        self.high_threshold = high_threshold
        self.medium_threshold = medium_threshold
        self.track_pathway = track_pathway
        self.pathway_tracker = RiskPathwayTracker(top_k_nodes=top_k_nodes) if track_pathway else None

    @torch.no_grad()
    def predict(self, sample: TemporalGraphData) -> TenantInferenceResult:
        """对单个时序图样本进行风险推理

        Args:
            sample: TemporalGraphData 样本

        Returns:
            TenantInferenceResult
        """
        graph = sample.graph.to(self.device)

        static_features = {
            k: v.unsqueeze(0).to(self.device)   # (1, N, D)
            for k, v in sample.node_features.items()
        }
        temporal_features = {
            k: v.unsqueeze(0).to(self.device)   # (1, T, N, D) or (1, N, T, D)
            for k, v in sample.temporal_features.items()
        }

        output: TemporalModelOutput = self.model(
            graph, static_features, temporal_features
        )

        # output.risk_prob shape: (1, N_biz) or (N_biz,)
        risk_probs = output.risk_prob
        if risk_probs.dim() == 2:
            risk_probs = risk_probs[0]          # (N_biz,)
        vertical = output.vertical_score
        if vertical.dim() == 2:
            vertical = vertical[0]
        horizontal = output.horizontal_score
        if horizontal.dim() == 2:
            horizontal = horizontal[0]

        risk_probs_np = risk_probs.cpu().numpy()
        vertical_np = vertical.cpu().numpy()
        horizontal_np = horizontal.cpu().numpy()

        risk_levels = [
            RiskLevel.HIGH if p >= self.high_threshold
            else (RiskLevel.MEDIUM if p >= self.medium_threshold else RiskLevel.LOW)
            for p in risk_probs_np
        ]

        global_risk = self._compute_global_risk(risk_probs_np, risk_levels)

        # 风险路径追踪
        risk_pathways = []
        layer_contributions = {}
        top_risky_nodes = {}

        if self.track_pathway and self.pathway_tracker is not None:
            try:
                # 获取变更主机ID列表
                change_host_ids = self._get_change_host_ids(sample)

                # 提取风险路径
                # 注意：传递原始数据，让 extract_pathways 内部处理设备移动
                pathways, layer_contrib = self.pathway_tracker.extract_pathways(
                    self.model,
                    sample.graph,
                    sample.node_features,
                    sample.temporal_features,
                    node_ids=sample.node_ids,
                    risk_probs=risk_probs_np,
                    change_host_ids=change_host_ids,
                )

                # 为路径中的节点设置prisk
                biz_node_ids = sample.node_ids.get("Vbiz", [])
                for pathway in pathways:
                    for node in pathway.pathway_nodes:
                        if node.node_type == "Vbiz" and node.layer == 2:
                            # 找到对应的prisk
                            idx = biz_node_ids.index(node.node_id) if node.node_id in biz_node_ids else -1
                            if idx >= 0 and idx < len(risk_probs_np):
                                node.risk_prob = float(risk_probs_np[idx])

                risk_pathways = pathways
                layer_contributions = layer_contrib

                # 提取每层top节点（只从路径中的节点获取）
                for layer in range(3):
                    top_risky_nodes[layer] = []

                if pathways:
                    for pathway in pathways:
                        for node in pathway.pathway_nodes:
                            layer = node.layer
                            if node not in top_risky_nodes[layer]:
                                top_risky_nodes[layer].append(node)

            except Exception as e:
                print(f"风险路径追踪失败: {e}")

        return TenantInferenceResult(
            record_id=sample.metadata.record_id,
            case=sample.metadata.case,
            risk_probs=risk_probs_np,
            vertical_scores=vertical_np,
            horizontal_scores=horizontal_np,
            risk_levels=risk_levels,
            global_risk_score=global_risk,
            node_ids=sample.node_ids.get("Vbiz", []),
            risk_pathways=risk_pathways,
            layer_contributions=layer_contributions,
            top_risky_nodes=top_risky_nodes,
        )

    def _get_change_host_ids(self, sample: TemporalGraphData) -> list[str]:
        """获取变更主机的ID列表"""
        # 从metadata中获取变更主机ID
        if hasattr(sample.metadata, 'change_host_id'):
            return [sample.metadata.change_host_id]
        return []

    def _compute_global_risk(self, probs: np.ndarray, levels: list[RiskLevel]) -> float:
        """加权全局风险分数"""
        if len(probs) == 0:
            return 0.0
        weights = np.array([
            3.0 if lvl == RiskLevel.HIGH
            else (2.0 if lvl == RiskLevel.MEDIUM else 1.0)
            for lvl in levels
        ])
        return float(np.sum(weights * probs) / np.sum(weights))

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: Path,
        static_dims: dict[str, int],
        temporal_dims: dict[str, int],
        hidden_dim: int = 128,  # 方案要求128维隐藏层
        high_threshold: float = 0.7,
        medium_threshold: float = 0.3,
        device: str | torch.device = "cpu",
        track_pathway: bool = True,
    ) -> "TenantInferenceEngine":
        """从检查点加载推理引擎

        Args:
            checkpoint_path: 模型权重文件路径（best_model.pt）
            static_dims: 各节点类型静态特征维度
            temporal_dims: 各节点类型时序特征维度
            hidden_dim: 隐藏层维度
            high_threshold: 高风险阈值
            medium_threshold: 中风险阈值
            device: 推理设备
            track_pathway: 是否追踪风险传播路径
        """
        model = TenantRiskPredictor(
            static_dims=static_dims,
            temporal_dims=temporal_dims,
            hidden_dim=hidden_dim,
            num_layers=3,   # 方案要求3层
            dropout=0.0,    # 推理时关闭 dropout
        )
        state_dict = torch.load(
            checkpoint_path, map_location=device, weights_only=False
        )
        model.load_state_dict(state_dict)
        return cls(
            model=model,
            high_threshold=high_threshold,
            medium_threshold=medium_threshold,
            device=device,
            track_pathway=track_pathway,
        )
