"""数据模型定义模块"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime
import numpy as np
import numpy.typing as npt


class NodeType(str, Enum):
    """节点类型枚举"""
    PHYSICAL = "Vphy"      # 物理机
    VIRTUAL = "Vvm"        # 虚拟机
    BUSINESS = "Vbiz"      # 业务应用


class RelationType(str, Enum):
    """边关系类型枚举"""
    HOSTING = "r_hosting"      # 物理承载: Vphy -> Vvm
    TRAFFIC = "r_traffic"      # 流量交互: Vvm -> Vvm
    DEPLOYMENT = "r_deployment"  # 应用部署: Vvm -> Vbiz
    CALLING = "r_calling"      # 业务调用: Vbiz -> Vbiz
    LINK = "r_link"           # 物理连接: Vphy -> Vphy (机架位/交换机层面的物理邻近性)


class OperationType(str, Enum):
    """变更操作类型"""
    HOST_REBOOT = "Host_Reboot"
    OS_PATCH = "OS_Patch"
    SWITCH_CONFIG = "Switch_Config"
    FIRMWARE_UPGRADE = "Firmware_Upgrade"
    VM_MIGRATION = "VM_Migration"


class ChangeStatus(str, Enum):
    """变更状态"""
    SUCCESS = "Success"
    FAILED = "Failed"
    ROLLBACK = "Rollback"


@dataclass
class NodeMetadata:
    """节点元数据"""
    node_id: str
    node_type: NodeType
    cluster_id: str
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.node_id)


@dataclass
class EdgeRelation:
    """边关系数据"""
    src_id: str
    dst_id: str
    relation_type: RelationType
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str, str]:
        """转换为 (src, relation, dst) 元组格式"""
        return (self.src_id, self.relation_type.value, self.dst_id)


@dataclass
class PhysicalMetrics:
    """物理机监控指标"""
    node_id: str
    timestamp: datetime
    cpu_usage_total: float      # CPU使用率 0-100
    disk_io_await: float        # I/O等待时间
    nic_drop_rate: float        # 网卡丢包率 0-1
    load_15m: float             # 15分钟负载
    memory_usage: float = 0.0   # 内存使用率
    nic_bytes_in: float = 0.0   # 网络入流量
    nic_bytes_out: float = 0.0  # 网络出流量

    def to_array(self) -> npt.NDArray[np.float64]:
        """转换为numpy数组"""
        return np.array([
            self.cpu_usage_total,
            self.disk_io_await,
            self.nic_drop_rate,
            self.load_15m,
            self.memory_usage,
            self.nic_bytes_in,
            self.nic_bytes_out,
        ], dtype=np.float64)

    @classmethod
    def feature_names(cls) -> list[str]:
        """获取特征名称列表"""
        return ["cpu_usage_total", "disk_io_await", "nic_drop_rate",
                "load_15m", "memory_usage", "nic_bytes_in", "nic_bytes_out"]


@dataclass
class VirtualMetrics:
    """虚拟机监控指标"""
    node_id: str
    timestamp: datetime
    vm_cpu_usage: float        # VM CPU使用率
    vm_cpu_ready: float        # CPU就绪时间
    vm_network_in: float       # 网络入流量
    vm_network_out: float      # 网络出流量
    vm_disk_read_ops: float    # 磁盘读IOPS
    vm_disk_write_ops: float   # 磁盘写IOPS
    vm_memory_usage: float = 0.0  # 内存使用率
    vm_cpu_steal: float = 0.0    # CPU Steal时间

    def to_array(self) -> npt.NDArray[np.float64]:
        """转换为numpy数组"""
        return np.array([
            self.vm_cpu_usage,
            self.vm_cpu_ready,
            self.vm_network_in,
            self.vm_network_out,
            self.vm_disk_read_ops,
            self.vm_disk_write_ops,
            self.vm_memory_usage,
            self.vm_cpu_steal,
        ], dtype=np.float64)

    @classmethod
    def feature_names(cls) -> list[str]:
        """获取特征名称列表"""
        return ["vm_cpu_usage", "vm_cpu_ready", "vm_network_in", "vm_network_out",
                "vm_disk_read_ops", "vm_disk_write_ops", "vm_memory_usage", "vm_cpu_steal"]


@dataclass
class BusinessMetrics:
    """业务应用黄金指标"""
    node_id: str
    timestamp: datetime
    avg_response_time: float    # 平均响应时间
    error_rate: float           # 错误率 0-1
    qps_count: float            # 每秒请求数
    throughput: float = 0.0     # 吞吐量
    timeout_rate: float = 0.0   # 超时率

    def to_array(self) -> npt.NDArray[np.float64]:
        """转换为numpy数组"""
        return np.array([
            self.avg_response_time,
            self.error_rate,
            self.qps_count,
            self.throughput,
            self.timeout_rate,
        ], dtype=np.float64)

    @classmethod
    def feature_names(cls) -> list[str]:
        """获取特征名称列表"""
        return ["avg_response_time", "error_rate", "qps_count", "throughput", "timeout_rate"]


@dataclass
class ResidualFeatures:
    """残差特征（用于GNN输入）"""
    node_id: str
    node_type: NodeType
    timestamp: datetime
    residuals: npt.NDArray[np.float64]
    feature_names: list[str]
    anomaly_score: float = 0.0

    def __len__(self) -> int:
        return len(self.residuals)


@dataclass
class ChangeEvent:
    """变更事件数据"""
    change_trace_id: str
    operation_type: OperationType
    target_resource_ids: list[str]   # 目标物理机ID列表
    exec_time_start: datetime
    exec_time_end: datetime
    change_status: ChangeStatus
    biz_impact_label: bool = False   # 业务影响标签
    impact_latency: int = 0          # 影响滞后时间（分钟）
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_minutes(self) -> float:
        """变更持续时间（分钟）"""
        delta = self.exec_time_end - self.exec_time_start
        return delta.total_seconds() / 60

    @property
    def impact_window_end(self) -> datetime:
        """影响窗口结束时间"""
        from ..utils.config import get_config
        config = get_config()
        return self.exec_time_end + __import__('datetime').timedelta(
            minutes=config.data.change_window_minutes + self.impact_latency
        )


@dataclass
class GraphStructure:
    """异构图结构"""
    nodes: dict[NodeType, dict[str, NodeMetadata]]
    edges: list[EdgeRelation]

    def get_node_ids(self, node_type: NodeType) -> list[str]:
        """获取指定类型的所有节点ID"""
        return list(self.nodes.get(node_type, {}).keys())

    def get_node_count(self, node_type: NodeType) -> int:
        """获取指定类型的节点数量"""
        return len(self.nodes.get(node_type, {}))

    def get_edges_by_type(
        self,
        relation_type: RelationType
    ) -> list[tuple[str, str]]:
        """获取指定类型的所有边"""
        return [
            (e.src_id, e.dst_id)
            for e in self.edges
            if e.relation_type == relation_type
        ]

    def get_downstream_nodes(
        self,
        node_id: str,
        max_depth: int = 3
    ) -> set[str]:
        """获取节点的下游节点（拓扑约束）"""
        downstream = set()
        current_level = {node_id}
        visited = set()

        for _ in range(max_depth):
            if not current_level:
                break
            next_level = set()
            for edge in self.edges:
                if edge.src_id in current_level and edge.dst_id not in visited:
                    downstream.add(edge.dst_id)
                    next_level.add(edge.dst_id)
                    visited.add(edge.dst_id)
            current_level = next_level

        return downstream


@dataclass
class TrainingSample:
    """训练样本"""
    graph: GraphStructure
    change_event: ChangeEvent
    node_features: dict[str, npt.NDArray[np.float64]]  # node_id -> features
    labels: dict[str, int]  # node_id -> 0/1 label
    anomaly_scores: dict[str, float]  # node_id -> anomaly score
