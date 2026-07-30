"""数据加载模块 - DGL异构图构建和弱监督标签生成"""

from pathlib import Path
from dataclasses import dataclass
from typing import Any
from collections import defaultdict

import torch
import numpy as np
import numpy.typing as npt
import dgl

from .models import (
    NodeType,
    RelationType,
    NodeMetadata,
    EdgeRelation,
    ChangeEvent,
    ChangeStatus,
    GraphStructure,
    TrainingSample,
)


@dataclass
class GraphData:
    """图数据容器"""
    graph: dgl.DGLGraph
    node_features: dict[str, torch.Tensor]
    labels: torch.Tensor | None
    node_ids: dict[str, list[str]]
    metadata: dict[str, Any]


def build_dgl_heterograph(
    graph_structure: GraphStructure,
) -> dgl.DGLGraph:
    """构建DGL异构图

    Args:
        graph_structure: 图结构数据

    Returns:
        DGL异构图对象
    """
    node_data: dict[str, list[str]] = {
        NodeType.PHYSICAL.value: list(graph_structure.nodes[NodeType.PHYSICAL].keys()),
        NodeType.VIRTUAL.value: list(graph_structure.nodes[NodeType.VIRTUAL].keys()),
        NodeType.BUSINESS.value: list(graph_structure.nodes[NodeType.BUSINESS].keys()),
    }

    edges_dict: dict[tuple[str, str, str], list[tuple[int, int]]] = {}

    for edge in graph_structure.edges:
        src_type = edge.relation_type.value.split("_")[0]
        if src_type == "r":
            src_type = edge.src_id.split("-")[0]
            if "host" in edge.src_id.lower():
                src_type = "Vphy"
            elif "vm" in edge.src_id.lower():
                src_type = "Vvm"
            else:
                src_type = "Vbiz"

        src_ntype = _get_ntype_from_id(edge.src_id, graph_structure)
        dst_ntype = _get_ntype_from_id(edge.dst_id, graph_structure)

        key = (src_ntype, edge.relation_type.value, dst_ntype)

        src_idx = node_data[src_ntype].index(edge.src_id)
        dst_idx = node_data[dst_ntype].index(edge.dst_id)

        if key not in edges_dict:
            edges_dict[key] = []
        edges_dict[key].append((src_idx, dst_idx))

    edge_data: dict[str, list[tuple[int, int]]] = {}
    for (src, etype, dst), pairs in edges_dict.items():
        edge_data[(src, etype, dst)] = pairs

    try:
        # 构建图结构，包含所有边类型（包括新增的 r_link）
        g = dgl.heterograph(edge_data, num_nodes_dict={
            ntype: len(ids) for ntype, ids in node_data.items()
        })
    except Exception as e:
        # 回退方案：包含所有边类型
        g = dgl.heterograph({
            ("Vphy", "r_hosting", "Vvm"): ([], []),
            ("Vvm", "r_traffic", "Vvm"): ([], []),
            ("Vvm", "r_deployment", "Vbiz"): ([], []),
            ("Vbiz", "r_calling", "Vbiz"): ([], []),
            # 新增：物理连接边
            ("Vphy", "r_link", "Vphy"): ([], []),
        }, num_nodes_dict={
            "Vphy": len(node_data["Vphy"]) if "Vphy" in node_data else 0,
            "Vvm": len(node_data["Vvm"]) if "Vvm" in node_data else 0,
            "Vbiz": len(node_data["Vbiz"]) if "Vbiz" in node_data else 0,
        })

    return g


def _get_ntype_from_id(node_id: str, graph: GraphStructure) -> str:
    """从节点ID推断节点类型"""
    if "host" in node_id.lower():
        return NodeType.PHYSICAL.value
    elif "vm" in node_id.lower() and "service" not in node_id.lower():
        return NodeType.VIRTUAL.value
    else:
        return NodeType.BUSINESS.value


def generate_weak_supervision_labels(
    graph: GraphStructure,
    change_event: ChangeEvent,
    anomaly_scores: dict[str, float],
    anomaly_threshold: float = 2.0,
) -> dict[str, int]:
    """生成弱监督标签

    根据变更事件和异常检测生成分组标签。

    正样本 (Y=1) 条件:
    1. 业务节点是变更物理机的下游节点 (拓扑约束)
    2. 业务节点的异常分数超过阈值 (状态约束)
    3. 时间落在变更影响窗口内

    负样本 (Y=0):
    4. 非变更期间的异常
    5. 变更期间但无拓扑关联的异常

    Args:
        graph: 图结构
        change_event: 变更事件
        anomaly_scores: 各节点的异常分数
        anomaly_threshold: 异常阈值

    Returns:
        节点ID到标签的映射
    """
    labels: dict[str, int] = {}

    downstream_nodes = set()
    for host_id in change_event.target_resource_ids:
        downstream = graph.get_downstream_nodes(host_id, max_depth=3)
        downstream_nodes.update(downstream)

    for node_id, score in anomaly_scores.items():
        is_downstream = node_id in downstream_nodes
        is_anomalous = score > anomaly_threshold
        is_biz_node = _is_business_node(node_id, graph)

        if is_biz_node and is_downstream and is_anomalous:
            labels[node_id] = 1
        else:
            labels[node_id] = 0

    return labels


def _is_business_node(node_id: str, graph: GraphStructure) -> bool:
    """判断是否为业务节点"""
    return node_id in graph.nodes[NodeType.BUSINESS]


def prepare_node_features(
    graph: GraphStructure,
    phy_features: dict[str, npt.NDArray[np.float64]] | None = None,
    vm_features: dict[str, npt.NDArray[np.float64]] | None = None,
    biz_features: dict[str, npt.NDArray[np.float64]] | None = None,
    default_dim: int = 32,
) -> dict[str, torch.Tensor]:
    """准备节点特征

    Args:
        graph: 图结构
        phy_features: 物理机特征
        vm_features: 虚拟机特征
        biz_features: 业务特征
        default_dim: 默认特征维度

    Returns:
        节点类型到特征张量的映射
    """
    features: dict[str, torch.Tensor] = {}

    phy_nodes = list(graph.nodes[NodeType.PHYSICAL].keys())
    vm_nodes = list(graph.nodes[NodeType.VIRTUAL].keys())
    biz_nodes = list(graph.nodes[NodeType.BUSINESS].keys())

    if phy_features is not None and len(phy_features) > 0:
        phy_dim = len(next(iter(phy_features.values())))
        phy_tensor = torch.zeros(len(phy_nodes), phy_dim)
        for i, node_id in enumerate(phy_nodes):
            if node_id in phy_features:
                phy_tensor[i] = torch.from_numpy(phy_features[node_id]).float()
        features[NodeType.PHYSICAL.value] = phy_tensor
    else:
        features[NodeType.PHYSICAL.value] = torch.randn(len(phy_nodes), default_dim)

    if vm_features is not None and len(vm_features) > 0:
        vm_dim = len(next(iter(vm_features.values())))
        vm_tensor = torch.zeros(len(vm_nodes), vm_dim)
        for i, node_id in enumerate(vm_nodes):
            if node_id in vm_features:
                vm_tensor[i] = torch.from_numpy(vm_features[node_id]).float()
        features[NodeType.VIRTUAL.value] = vm_tensor
    else:
        features[NodeType.VIRTUAL.value] = torch.randn(len(vm_nodes), default_dim)

    if biz_features is not None and len(biz_features) > 0:
        biz_dim = len(next(iter(biz_features.values())))
        biz_tensor = torch.zeros(len(biz_nodes), biz_dim)
        for i, node_id in enumerate(biz_nodes):
            if node_id in biz_features:
                biz_tensor[i] = torch.from_numpy(biz_features[node_id]).float()
        features[NodeType.BUSINESS.value] = biz_tensor
    else:
        features[NodeType.BUSINESS.value] = torch.randn(len(biz_nodes), default_dim)

    return features


class RiskDataset:
    """风险预测数据集"""

    def __init__(
        self,
        samples: list[TrainingSample],
        anomaly_threshold: float = 2.0,
    ):
        """初始化数据集

        Args:
            samples: 训练样本列表
            anomaly_threshold: 异常阈值
        """
        self.samples = samples
        self.anomaly_threshold = anomaly_threshold
        self._graph_data: list[GraphData] = []

        self._process_samples()

    def _process_samples(self) -> None:
        """处理样本，构建图数据"""
        for sample in self.samples:
            g = build_dgl_heterograph(sample.graph)

            node_ids = {
                NodeType.PHYSICAL.value: list(sample.graph.nodes[NodeType.PHYSICAL].keys()),
                NodeType.VIRTUAL.value: list(sample.graph.nodes[NodeType.VIRTUAL].keys()),
                NodeType.BUSINESS.value: list(sample.graph.nodes[NodeType.BUSINESS].keys()),
            }

            node_features = prepare_node_features(
                graph=sample.graph,
                default_dim=32,
            )

            biz_labels = []
            for node_id in node_ids[NodeType.BUSINESS.value]:
                biz_labels.append(sample.labels.get(node_id, 0))
            labels = torch.tensor(biz_labels, dtype=torch.long)

            self._graph_data.append(GraphData(
                graph=g,
                node_features=node_features,
                labels=labels,
                node_ids=node_ids,
                metadata={"change_event": sample.change_event},
            ))

    def __len__(self) -> int:
        return len(self._graph_data)

    def __getitem__(self, idx: int) -> GraphData:
        return self._graph_data[idx]

    def get_feature_dims(self) -> dict[str, int]:
        """获取各节点类型的特征维度"""
        if not self._graph_data:
            return {
                NodeType.PHYSICAL.value: 32,
                NodeType.VIRTUAL.value: 32,
                NodeType.BUSINESS.value: 32,
            }

        first = self._graph_data[0]
        return {
            ntype: feat.shape[1]
            for ntype, feat in first.node_features.items()
        }


def create_data_splits(
    dataset: RiskDataset,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: int = 42,
) -> tuple[list[int], list[int], list[int]]:
    """创建数据划分索引

    Args:
        dataset: 数据集
        train_ratio: 训练集比例
        val_ratio: 验证集比例
        test_ratio: 测试集比例
        seed: 随机种子

    Returns:
        (train_indices, val_indices, test_indices)
    """
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6

    n = len(dataset)
    indices = list(range(n))

    rng = np.random.default_rng(seed)
    rng.shuffle(indices)

    train_end = int(n * train_ratio)
    val_end = train_end + int(n * val_ratio)

    train_indices = indices[:train_end]
    val_indices = indices[train_end:val_end]
    test_indices = indices[val_end:]

    return train_indices, val_indices, test_indices


def collate_fn(batch: list[GraphData]) -> GraphData:
    """批次整理函数

    由于图结构可能不同，这里返回第一个样本
    实际应用中可能需要更复杂的处理
    """
    return batch[0]


def load_mock_dataset(num_samples: int = 50) -> RiskDataset:
    """加载模拟数据集

    Args:
        num_samples: 样本数量

    Returns:
        RiskDataset实例
    """
    from .mock_data import generate_sample_dataset

    samples = generate_sample_dataset(num_samples=num_samples, seed=42)
    return RiskDataset(samples, anomaly_threshold=2.0)
