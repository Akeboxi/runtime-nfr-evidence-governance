"""租户级数据集加载模块 - 适配 tenant_0000 数据格式"""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
import json

import torch
import numpy as np
import numpy.typing as npt
import dgl
import polars as pl

from .node_profile import NodeProfile, extract_profile_features


@dataclass
class TenantMetadata:
    """租户元数据"""
    tenant_id: str
    n_hosts: int
    n_vms: int
    n_apps: int
    graph_dir: Path
    records_dir: Path


@dataclass
class RecordMetadata:
    """记录元数据"""
    tenant_id: str
    record_id: str
    global_record_id: str
    case: str
    change_host_id: str
    change_start_idx: int
    change_start_time: str
    change_end_idx: int
    change_end_time: str
    n_hosts: int
    n_vms: int
    n_apps: int
    host_has_anom: int
    vm_has_anom: int
    app_has_anom: int
    anomaly_by_type: dict[str, int]
    days: int
    samples_per_day: int
    total_samples: int
    record_dir: Path
    segment_id: str = ""
    segment_index: int = -1
    segment_start_time: str = ""
    segment_end_time: str = ""

    @classmethod
    def from_global_label(cls, record_dir: Path, global_label: dict) -> "RecordMetadata":
        """从 global_label.json 创建元数据"""
        record_id = global_label["record"]
        return cls(
            tenant_id=global_label["tenant_id"],
            record_id=record_id,
            global_record_id=global_label.get("global_record_id", record_id),
            case=global_label.get("case", "unknown"),
            change_host_id=global_label["change_host_id"],
            change_start_idx=global_label.get("change_start_idx", 0),
            change_start_time=global_label["change_start_time"],
            change_end_idx=global_label.get("change_end_idx", 0),
            change_end_time=global_label["change_end_time"],
            n_hosts=global_label.get("n_hosts", 0),
            n_vms=global_label.get("n_vms", 0),
            n_apps=global_label.get("n_apps", 0),
            host_has_anom=global_label.get("host_has_anom", 0),
            vm_has_anom=global_label.get("vm_has_anom", 0),
            app_has_anom=global_label.get("app_has_anom", 0),
            anomaly_by_type=global_label.get("anomaly_by_type", {}),
            days=global_label.get("days", 0),
            samples_per_day=global_label.get("samples_per_day", 0),
            total_samples=global_label.get("total_samples", 0),
            segment_id=global_label.get("segment_id", ""),
            segment_index=global_label.get("segment_index", -1),
            segment_start_time=global_label.get("segment_start_time", ""),
            segment_end_time=global_label.get("segment_end_time", ""),
            record_dir=record_dir,
        )


@dataclass
class NodeLabel:
    """节点标签"""
    node_id: str
    node_type: str
    is_anomaly: int
    anomaly_level: str
    anomaly_metric: str
    anomaly_type: str
    start_idx: int
    end_idx: int
    is_change_host: int


@dataclass
class TemporalGraphData:
    """时序图数据容器"""
    graph: dgl.DGLGraph
    node_features: dict[str, torch.Tensor]
    temporal_features: dict[str, torch.Tensor]  # node_type -> (T, N, D)
    labels: torch.Tensor
    node_ids: dict[str, list[str]]
    metadata: RecordMetadata
    change_window: tuple[int, int]
    phase: str = "unknown"  # pre/during/post 窗口阶段

    def to(self, device: torch.device) -> "TemporalGraphData":
        """将数据移动到指定设备"""
        return TemporalGraphData(
            graph=self.graph.to(device),
            node_features={k: v.to(device) for k, v in self.node_features.items()},
            temporal_features={k: v.to(device) for k, v in self.temporal_features.items()},
            labels=self.labels.to(device),
            node_ids=self.node_ids,
            metadata=self.metadata,
            change_window=self.change_window,
        )


class MetricRegistry:
    """指标注册表 - 动态发现和管理指标

    用于支持不同节点具有不同指标名称和数量的场景。
    """

    def __init__(self) -> None:
        # 节点类型 -> 指标名称列表（按发现顺序排列）
        self._node_metrics: dict[str, list[str]] = {
            "Vphy": [],
            "Vvm": [],
            "Vbiz": [],
        }
        # 指标默认值
        self._metric_defaults: dict[str, float] = {}

    def register_metrics(self, ntype: str, metrics: list[str]) -> None:
        """注册指标到节点类型（去重）"""
        existing = set(self._node_metrics[ntype])
        for m in metrics:
            if m not in existing:
                self._node_metrics[ntype].append(m)
                existing.add(m)

    def get_metrics(self, ntype: str) -> list[str]:
        """获取指定节点类型的所有指标"""
        return self._node_metrics[ntype].copy()

    def get_temporal_dims(self) -> dict[str, int]:
        """获取每种节点类型的时序特征维度"""
        return {ntype: len(metrics) for ntype, metrics in self._node_metrics.items()}

    def set_default(self, metric_name: str, value: float) -> None:
        """设置指标的默认值"""
        self._metric_defaults[metric_name] = value

    def get_default(self, metric_name: str) -> float:
        """获取指标的默认值"""
        return self._metric_defaults.get(metric_name, 0.0)


class TenantDataset:
    """租户级时序图数据集

    加载固定图结构的租户数据，支持时序特征和变更窗口。
    支持动态指标发现，自动适配不同指标名称和数量的节点特征输入。
    """

    def __init__(
        self,
        tenant_dir: str | Path,
        records: list[str] | None = None,
        window_size: int = 32,
        stride: int = 16,
        context_radius: int = 200,
        normalize: bool = True,
        use_loess_residual: str | set[str] = False,
        enable_dynamic_metrics: bool = True,
        label_mode: str = "impact_static",
    ):
        """初始化租户数据集

        Args:
            tenant_dir: 租户数据目录，如 src/data/tenant_0000
            records: 要加载的记录列表，None 表示加载全部
            window_size: 时序窗口大小
            stride: 滑动窗口步长
            context_radius: 变更窗口前后各读取的时间步数（减少 IO）
            normalize: 是否归一化特征
            use_loess_residual: 对哪些节点类型使用LOESS残差。
                - False/空集: 不使用残差
                - "Vbiz": 仅业务层
                - {"Vvm", "Vbiz"} (或 "VM+Biz"): VM和业务层
                - "all": 所有层
            enable_dynamic_metrics: 是否启用动态指标发现（默认True）。
                启用后会自动发现并注册所有指标，支持不同节点有不同指标。
            label_mode: 窗口标签模式。
                - "impact_static": 复现旧行为，整条 record 共用一份 impact_score 标签
                - "phase_aware": pre 窗口默认置零，仅在窗口与节点异常区间重叠时保留正监督
        """
        self.tenant_dir = Path(tenant_dir)
        self.window_size = window_size
        self.stride = stride
        self.context_radius = context_radius
        self.normalize = normalize
        self.enable_dynamic_metrics = enable_dynamic_metrics
        if label_mode not in {"impact_static", "phase_aware"}:
            raise ValueError(f"Unsupported label_mode={label_mode!r}")
        self.label_mode = label_mode
        self.metric_registry = MetricRegistry() if enable_dynamic_metrics else None

        # 解析 loess_node_types
        if use_loess_residual is False or use_loess_residual == "none" or use_loess_residual == "":
            self.loess_node_types: set[str] = set()
        elif isinstance(use_loess_residual, str):
            if use_loess_residual.lower() == "vbiz":
                self.loess_node_types = {"Vbiz"}
            elif use_loess_residual.lower() in ("vm+biz", "vm_biz"):
                self.loess_node_types = {"Vvm", "Vbiz"}
            elif use_loess_residual.lower() == "all":
                self.loess_node_types = {"Vphy", "Vvm", "Vbiz"}
            else:
                self.loess_node_types = set()
        else:
            self.loess_node_types = set(use_loess_residual)

        # 兼容旧属性
        self.use_loess_residual = bool(self.loess_node_types)

        # 初始化LOESS检测器（如果启用）
        # 注意：LOESS残差计算在 _compute_node_loess_residuals 中直接实现
        self.loess_detector = None  # 暂不使用外部检测器，使用简化实现

        # 加载租户元数据
        self.metadata = self._load_tenant_metadata()

        # 加载固定图结构
        self.graph, self.node_ids = self._build_graph()

        # 加载节点标签映射
        self.node_to_idx = self._build_node_mapping()

        # 动态指标发现（在加载记录之前）
        if self.enable_dynamic_metrics:
            self._discover_metrics()

        # 加载指定的记录
        if records is None:
            records = self._list_all_records()

        self.samples: list[TemporalGraphData] = []
        self._load_records(records)

        # 计算归一化统计量
        if self.normalize:
            self._compute_normalization_stats()

    def _load_tenant_metadata(self) -> TenantMetadata:
        """加载租户元数据"""
        meta_path = self.tenant_dir / "graph" / "meta_graph.json"
        with open(meta_path) as f:
            meta = json.load(f)

        return TenantMetadata(
            tenant_id=meta["tenant_id"],
            n_hosts=meta["n_hosts"],
            n_vms=meta["n_vms"],
            n_apps=meta["n_apps"],
            graph_dir=self.tenant_dir / "graph",
            records_dir=self.tenant_dir / "records",
        )

    def _build_graph(self) -> tuple[dgl.DGLGraph, dict[str, list[str]]]:
        """构建 DGL 异构图

        节点类型映射:
            host -> Vphy
            vm  -> Vvm
            app -> Vbiz

        边类型映射:
            vm_host -> r_hosting (Vphy <- Vvm)
            vm_vm   -> r_traffic (Vvm -> Vvm)
            vm_app  -> r_deployment (Vvm -> Vbiz)
            app_app -> r_calling (Vbiz -> Vbiz)
            host_host -> r_link (Vphy -> Vphy, 物理连接/机架位邻近性)
        """
        # 加载节点
        nodes = {
            "host": self._load_nodes("host.csv"),
            "vm": self._load_nodes("vm.csv"),
            "app": self._load_nodes("app.csv"),
        }

        node_ids = {
            "Vphy": nodes["host"],
            "Vvm": nodes["vm"],
            "Vbiz": nodes["app"],
        }

        # 加载边
        edges = self._load_edges()

        # 构建 DGL 异构图
        edge_data: dict[tuple[str, str, str], list[tuple[int, int]]] = {}

        # host_host: host -> host (物理连接边 r_link，新增)
        if "host_host" in edges:
            edge_data[("Vphy", "r_link", "Vphy")] = [
                (nodes["host"].index(e["src_id"]), nodes["host"].index(e["dst_id"]))
                for e in edges["host_host"]
            ]

        # vm_host: vm -> host (反向，用于从 host 聚合到 vm)
        if "vm_host" in edges:
            edge_data[("Vphy", "r_hosting", "Vvm")] = [
                (nodes["host"].index(e["dst_id"]), nodes["vm"].index(e["src_id"]))
                for e in edges["vm_host"]
            ]

        # vm_vm: vm -> vm
        if "vm_vm" in edges:
            edge_data[("Vvm", "r_traffic", "Vvm")] = [
                (nodes["vm"].index(e["src_id"]), nodes["vm"].index(e["dst_id"]))
                for e in edges["vm_vm"]
            ]

        # vm_app: vm -> app
        if "vm_app" in edges:
            edge_data[("Vvm", "r_deployment", "Vbiz")] = [
                (nodes["vm"].index(e["src_id"]), nodes["app"].index(e["dst_id"]))
                for e in edges["vm_app"]
            ]

        # app_app: app -> app
        if "app_app" in edges:
            edge_data[("Vbiz", "r_calling", "Vbiz")] = [
                (nodes["app"].index(e["src_id"]), nodes["app"].index(e["dst_id"]))
                for e in edges["app_app"]
            ]

        # 处理空边类型（包含新增的 r_link）
        canonical_etypes = [
            ("Vphy", "r_link", "Vphy"),       # 新增：物理连接边
            ("Vphy", "r_hosting", "Vvm"),
            ("Vvm", "r_traffic", "Vvm"),
            ("Vvm", "r_deployment", "Vbiz"),
            ("Vbiz", "r_calling", "Vbiz"),
        ]

        for etype in canonical_etypes:
            if etype not in edge_data:
                edge_data[etype] = ([], [])

        num_nodes_dict = {
            "Vphy": len(nodes["host"]),
            "Vvm": len(nodes["vm"]),
            "Vbiz": len(nodes["app"]),
        }

        g = dgl.heterograph(edge_data, num_nodes_dict=num_nodes_dict)

        return g, node_ids

    def _load_nodes(self, filename: str) -> list[str]:
        """加载节点列表"""
        path = self.metadata.graph_dir / "nodes" / filename
        df = pl.read_csv(path)
        return df["node_id"].to_list()

    def _load_edges(self) -> dict[str, list[dict]]:
        """加载边关系"""
        edges = {}
        edge_types = ["host_host", "vm_host", "vm_vm", "vm_app", "app_app"]  # 新增 host_host

        for edge_type in edge_types:
            path = self.metadata.graph_dir / "edges" / f"{edge_type}.csv"
            if path.exists():
                df = pl.read_csv(path)
                edges[edge_type] = df.to_dicts()
            else:
                edges[edge_type] = []

        return edges

    def _build_node_mapping(self) -> dict[str, dict[str, int]]:
        """构建节点ID到索引的映射"""
        mapping = {}
        for ntype, ids in self.node_ids.items():
            mapping[ntype] = {node_id: i for i, node_id in enumerate(ids)}
        return mapping

    def _list_all_records(self) -> list[str]:
        """列出所有可用的记录"""
        records_dir = self.metadata.records_dir
        return [d.name for d in records_dir.iterdir() if d.is_dir() and d.name.startswith("record_")]

    def _load_records(self, record_names: list[str]) -> None:
        """加载记录数据，只读取变更窗口附近的时间步以减少 IO"""
        # 设置随机种子确保标签可复现
        import random
        random.seed(42)

        for record_name in record_names:
            record_dir = self.metadata.records_dir / record_name
            if not record_dir.exists():
                continue

            global_label_path = record_dir / "global_label.json"
            if not global_label_path.exists():
                continue
            with open(global_label_path) as f:
                global_label = json.load(f)

            record_meta = RecordMetadata.from_global_label(record_dir, global_label)
            node_labels = self._load_node_labels(record_dir)
            soft_labels = self._load_soft_labels(record_dir)  # 加载软标签

            # 只读取变更窗口 ± context_radius 的时间步
            cs = record_meta.change_start_idx
            ce = record_meta.change_end_idx
            context = getattr(self, 'context_radius', 200)
            t_start = max(0, cs - context)
            t_end = ce + context

            temporal_features, node_id_mapping = self._load_temporal_features(
                record_dir, time_range=(t_start, t_end)
            )
            if not temporal_features:
                continue

            # 数据完整性检查：确保包含所有节点类型
            required_node_types = {"Vphy", "Vvm", "Vbiz"}
            if not required_node_types.issubset(temporal_features.keys()):
                missing = required_node_types - temporal_features.keys()
                print(f"Warning: {record_name} missing node types {missing}, skipping")
                continue

            # 加载完整时间序列用于计算长期画像（变更前的历史数据）
            # 使用变更前足够长的历史数据来建立基准画像
            profile_start = max(0, cs - 2000)  # 变更前2000个时间步作为历史基线
            full_timeseries, _ = self._load_temporal_features(
                record_dir, time_range=(profile_start, cs)
            )
            # 确保 full_timeseries 的节点数与 temporal_features 一致
            if full_timeseries:
                for ntype in full_timeseries:
                    if ntype in temporal_features:
                        n_full = full_timeseries[ntype].shape[1]
                        n_temp = temporal_features[ntype].shape[1]
                        if n_full < n_temp:
                            # 填充 full_timeseries
                            padding = np.zeros((full_timeseries[ntype].shape[0], n_temp - n_full, full_timeseries[ntype].shape[2]), dtype=np.float32)
                            full_timeseries[ntype] = np.concatenate([full_timeseries[ntype], padding], axis=1)
                        elif n_full > n_temp:
                            full_timeseries[ntype] = full_timeseries[ntype][:, :n_temp, :]

            # 本地坐标偏移
            local_cs = cs - t_start
            local_ce = ce - t_start

            # 计算静态特征（包含长期画像）
            static_features = self._compute_static_features(
                temporal_features,
                full_timeseries=full_timeseries if full_timeseries else None
            )

            samples = self._create_window_samples(
                record_meta, temporal_features, static_features,
                node_labels, soft_labels, local_cs, local_ce, node_id_mapping,
            )
            self.samples.extend(samples)

    def _load_node_labels(self, record_dir: Path) -> dict[str, NodeLabel]:
        """加载节点标签

        直接从 node_labels.csv 读取各节点自己的异常区间 (start_idx, end_idx)。
        """
        labels_path = record_dir / "labels" / "node_labels.csv"
        if not labels_path.exists():
            return {}

        df = pl.read_csv(labels_path)
        node_labels = {}

        for row in df.to_dicts():
            # 直接使用 node_labels.csv 中每行自己的 start_idx 和 end_idx
            # 这些是该节点的异常区间，不是变更事件的全局时间
            label = NodeLabel(
                node_id=row["node_id"],
                node_type=row["node_type"],
                is_anomaly=row["is_anomaly"],
                anomaly_level=row.get("anomaly_level", "none"),
                anomaly_metric=row.get("anomaly_metric", ""),
                anomaly_type=row.get("anomaly_type", ""),
                start_idx=row.get("start_idx", 0),
                end_idx=row.get("end_idx", 0),
                is_change_host=row.get("is_change_host", 0),
            )
            node_labels[label.node_id] = label

        return node_labels

    def _load_soft_labels(self, record_dir: Path) -> dict[str, float]:
        """加载软标签

        从 p_label.json 或 ccf_p_label.json 读取各业务节点的 impact_score 作为软标签。

        Returns:
            dict[str, float]: node_id -> impact_score (0~1)
        """
        # 支持多种标签文件名格式
        for label_filename in ["p_label.json", "ccf_p_label.json"]:
            soft_labels_path = record_dir / "labels" / label_filename
            if soft_labels_path.exists():
                with open(soft_labels_path) as f:
                    p_label = json.load(f)

                # 从 apps 数组中提取 impact_score
                soft_labels = {}
                for app in p_label.get("apps", []):
                    node_id = app["node_id"]
                    impact_score = app.get("impact_score", 0.0)
                    soft_labels[node_id] = float(impact_score)

                return soft_labels

        return {}

    def _discover_metrics(self) -> None:
        """遍历所有记录和节点，收集所有指标名称

        扫描所有节点CSV文件，发现每种节点类型的指标并集，
        并注册到 metric_registry 中。
        """
        if self.metric_registry is None:
            return

        type_to_prefix = {"Vphy": "host", "Vvm": "vm", "Vbiz": "app"}
        records_dir = self.metadata.records_dir

        # 遍历每种节点类型
        for ntype, prefix in type_to_prefix.items():
            all_metrics: set[str] = set()

            # 遍历所有记录目录
            if not records_dir.exists():
                continue

            for record_dir in records_dir.iterdir():
                if not record_dir.is_dir() or not record_dir.name.startswith("record_"):
                    continue

                node_dir = record_dir / "metrics" / prefix
                if not node_dir.exists():
                    continue

                # 遍历该目录下所有CSV文件，收集指标
                for csv_file in node_dir.glob("*.csv"):
                    try:
                        # 只读取第一行获取列名
                        df = pl.read_csv(csv_file, n_rows=1)
                        # 清理列名中的BOM和不可见字符
                        df.columns = [c.strip() if isinstance(c, str) else c for c in df.columns]
                        cols = set(df.columns) - {"node_type", "node_id", "timestamp"}
                        all_metrics.update(cols)
                    except Exception:
                        continue

            # 注册发现的指标
            if all_metrics:
                # 排序以保证一致性
                sorted_metrics = sorted(all_metrics)
                self.metric_registry.register_metrics(ntype, sorted_metrics)

    def _interpolate_missing(
        self,
        node_data: npt.NDArray[np.float32],
        missing_idx: int,
    ) -> npt.NDArray[np.float32]:
        """对缺失列进行前向填充插值，同时处理 NaN

        Args:
            node_data: (T, D) 时间序列数据
            missing_idx: 缺失列的索引

        Returns:
            (T,) 插值后的列数据
        """
        col = node_data[:, missing_idx].copy()

        # 处理 NaN：把 NaN 替换为 0 用于后续插值
        nan_mask = np.isnan(col)
        if nan_mask.any():
            # 先用 0 暂时填充 NaN
            col[nan_mask] = 0.0

        # 然后进行前向填充
        non_zero_mask = col != 0
        if non_zero_mask.any():
            # 找到最后一个有效值的索引
            last_valid_idx = np.where(non_zero_mask)[0][-1]
            # 从最后一个有效值之后使用前向填充
            if last_valid_idx + 1 < len(col):
                col[last_valid_idx + 1:] = col[last_valid_idx]
        else:
            # 如果全为0或空，使用同一时间步的其他指标均值填充
            valid_cols = [i for i in range(node_data.shape[1]) if i != missing_idx]
            if valid_cols:
                col[:] = np.mean(node_data[:, valid_cols], axis=1)

        # 确保没有残留的 NaN
        col = np.where(np.isfinite(col), col, 0.0)

        return col

    def _load_temporal_features(
        self,
        record_dir: Path,
        time_range: tuple[int, int] | None = None,
    ) -> tuple[dict[str, npt.NDArray[np.float32]], dict[str, list[str]]]:
        """加载时序特征，time_range=(t_start, t_end) 只读取该区间以减少 IO

        Args:
            record_dir: 记录目录
            time_range: 时间范围 (t_start, t_end)

        Returns:
            (temporal_features, node_id_mapping) 元组
            - temporal_features: 时序特征字典
            - node_id_mapping: 节点ID映射 {ntype: [node_ids]} 文件名格式的节点ID列表
        """
        metrics_dir = record_dir / "metrics"
        type_to_prefix = {"Vphy": "host", "Vvm": "vm", "Vbiz": "app"}
        temporal_features: dict[str, npt.NDArray[np.float32]] = {}
        node_id_mapping: dict[str, list[str]] = {}  # 记录每个节点类型的节点ID

        # 需要LOESS处理的节点类型（使用实例级别配置）
        loess_node_types = self.loess_node_types

        for ntype, prefix in type_to_prefix.items():
            node_dir = metrics_dir / prefix
            if not node_dir.exists():
                continue

            # 优先使用 graph/nodes 中的节点顺序（与 _build_graph 一致）
            # 这样可以确保 temporal_features 的节点顺序与图结构一致
            graph_nodes_path = self.metadata.graph_dir / "nodes" / f"{prefix}.csv"
            if graph_nodes_path.exists():
                graph_df = pl.read_csv(graph_nodes_path)
                actual_node_ids = graph_df["node_id"].to_list()
            else:
                # 降级：从 metrics 目录获取文件顺序
                csv_files = list(node_dir.glob("*.csv"))
                if not csv_files:
                    continue
                actual_node_ids = [f.stem for f in csv_files]

            node_id_mapping[ntype] = actual_node_ids

            # 确定目标指标列表
            if self.enable_dynamic_metrics and self.metric_registry is not None:
                target_metrics = self.metric_registry.get_metrics(ntype)
                # 清理列名中的BOM和不可见字符
                target_metrics = [m.strip() if isinstance(m, str) else m for m in target_metrics]
            else:
                # 原有逻辑：从第一个节点获取特征列名
                first_df = pl.read_csv(csv_files[0])
                # 清理列名中的BOM和不可见字符
                first_df.columns = [c.strip() if isinstance(c, str) else c for c in first_df.columns]
                target_metrics = [
                    c for c in first_df.columns
                    if c not in ("node_type", "node_id", "timestamp")
                ]

            if not target_metrics:
                continue

            # [DEBUG] print(f"[DEBUG] _load_temporal_features: {ntype}, target_metrics count: {len(target_metrics)}")

            # 获取时间范围（用于全局索引）
            if time_range is not None:
                t0_global = max(0, time_range[0])
                t1_global = time_range[1]
            else:
                t0_global, t1_global = 0, None

            D = len(target_metrics)
            N = len(actual_node_ids)

            # 加载原始时序数据，支持动态指标和缺失插值
            # 注意：每个节点文件的时间步数可能不同，需要分别处理
            raw_features: dict[str, np.ndarray] = {}

            for i, node_id in enumerate(actual_node_ids):
                node_file = node_dir / f"{node_id}.csv"
                if not node_file.exists():
                    continue

                df = pl.read_csv(node_file)
                # 清理列名中的BOM和不可见字符
                df.columns = [c.strip() if isinstance(c, str) else c for c in df.columns]
                actual_T = len(df)  # 该文件实际的时间步数

                # 计算该文件在全局范围内的有效切片
                t0 = max(0, t0_global) if t0_global else 0
                t1 = min(actual_T, t1_global) if t1_global else actual_T

                if t1 <= t0:
                    continue

                available_cols = [c for c in target_metrics if c in df.columns]
                missing_cols = [c for c in target_metrics if c not in df.columns]

                # 初始化该节点的特征数组（使用实际时间步数）
                node_features = np.zeros((t1 - t0, D), dtype=np.float32)

                # 填充可用列，同时处理 NaN
                for col in available_cols:
                    col_idx = target_metrics.index(col)
                    col_data = df[col].to_numpy().astype(np.float32)[t0:t1]
                    # 处理 NaN：用前向填充
                    nan_mask = np.isnan(col_data)
                    if nan_mask.any():
                        # 用前一个有效值填充 NaN
                        col_data_filled = col_data.copy()
                        for i in range(len(col_data_filled)):
                            if nan_mask[i]:
                                if i > 0:
                                    col_data_filled[i] = col_data_filled[i-1]
                                else:
                                    # 如果第一个就是 NaN，找下一个有效值
                                    for j in range(i+1, len(col_data_filled)):
                                        if not nan_mask[j]:
                                            col_data_filled[i] = col_data_filled[j]
                                            break
                                    else:
                                        col_data_filled[i] = 0.0
                        col_data = col_data_filled
                    node_features[:, col_idx] = col_data

                # 对缺失列进行前向填充插值
                for col in missing_cols:
                    col_idx = target_metrics.index(col)
                    node_features[:, col_idx] = self._interpolate_missing(
                        node_features, col_idx
                    )

                raw_features[node_id] = node_features

            if not raw_features:
                continue

            # 找出所有节点中最小的时间步数，确保一致性
            min_T = min(f.shape[0] for f in raw_features.values())
            N_actual = len(raw_features)

            # [DEBUG] print(f"[DEBUG] {ntype}: min_T={min_T}, N={N_actual}, D={D}")

            # 裁剪到统一长度
            features = np.zeros((min_T, N_actual, D), dtype=np.float32)
            for i, (node_id, node_feat) in enumerate(raw_features.items()):
                if node_feat.shape[0] >= min_T:
                    features[:, i, :] = node_feat[:min_T, :]
                else:
                    features[:node_feat.shape[0], i, :] = node_feat

            # 对VM/业务节点应用LOESS残差处理
            if ntype in loess_node_types:
                loess_features = np.zeros((min_T, N_actual, D), dtype=np.float32)
                # 对每个节点计算残差
                for i, (node_id, node_feat) in enumerate(raw_features.items()):
                    if node_feat.shape[0] >= min_T:
                        node_ts = node_feat[:min_T, :]
                    else:
                        node_ts = node_feat
                    # 使用LOESS计算残差
                    residuals = self._compute_node_loess_residuals(node_ts)
                    loess_features[:residuals.shape[0], i, :] = residuals
                temporal_features[ntype] = loess_features
            else:
                temporal_features[ntype] = features

            # 最终安全检查：确保没有 NaN 或 Inf
            final_feat = temporal_features[ntype]
            nan_count = np.isnan(final_feat).sum()
            inf_count = np.isinf(final_feat).sum()
            if nan_count > 0 or inf_count > 0:
                print(f"[WARNING] {ntype} 包含 NaN({nan_count}) 或 Inf({inf_count})，已替换为0")
                temporal_features[ntype] = np.where(
                    np.isfinite(final_feat), final_feat, 0.0
                )

        return temporal_features, node_id_mapping

    def _compute_node_loess_residuals(
        self,
        timeseries: npt.NDArray[np.float32],
    ) -> npt.NDArray[np.float32]:
        """计算单个节点的LOESS残差

        Args:
            timeseries: (T, D) 时间序列

        Returns:
            (T, D) 残差序列
        """
        if not self.use_loess_residual:
            return timeseries

        T, D = timeseries.shape
        residuals = np.zeros((T, D), dtype=np.float32)

        for d in range(D):
            ts_1d = timeseries[:, d]
            # 使用滑动窗口计算残差
            window_size = min(50, T)
            for t in range(T):
                if t < window_size:
                    start, end = 0, window_size
                else:
                    start, end = t - window_size, t + 1

                window = ts_1d[start:end]
                if len(window) < 5:
                    residuals[t, d] = 0.0
                    continue

                # 简化LOESS：使用均值作为拟合值
                mean_val = np.mean(window[:-1]) if len(window) > 1 else np.mean(window)
                residuals[t, d] = ts_1d[t] - mean_val

        return residuals

    def _compute_static_features(
        self,
        temporal_features: dict[str, npt.NDArray[np.float32]],
        full_timeseries: dict[str, npt.NDArray[np.float32]] | None = None,
    ) -> dict[str, torch.Tensor]:
        """从时序特征计算静态特征（使用统计量和长期画像）

        Args:
            temporal_features: 窗口内的时序特征
            full_timeseries: 完整时间序列用于计算长期画像（可选）
        """
        static_features: dict[str, torch.Tensor] = {}
        PROFILE_DIM = 66  # NodeProfile.to_numpy() 返回固定 66 维

        for ntype, features in temporal_features.items():
            # T, N, D -> N, D*6 (mean, std, min, max, median, skew)
            T, N, D = features.shape

            # 替换 NaN 和 Inf 为 0
            features = np.where(np.isfinite(features), features, 0.0)

            # 1. 基本统计量（窗口内）
            feat_mean = np.mean(features, axis=0)  # N, D
            feat_std = np.std(features, axis=0)    # N, D
            feat_min = np.min(features, axis=0)    # N, D
            feat_max = np.max(features, axis=0)    # N, D

            # 使用分位数代替中位数以提高性能
            feat_median = np.quantile(features, 0.5, axis=0)  # N, D

            # 防止 std 为 0 导致的问题
            feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)

            basic_stats = np.concatenate([
                feat_mean, feat_std, feat_min, feat_max, feat_median
            ], axis=1)  # (N, 5*D)

            # 2. 长期画像特征（如果提供了完整时间序列）
            if full_timeseries is not None and ntype in full_timeseries:
                full_ts = full_timeseries[ntype]  # (T_full, N_full, D)
                # 确保维度匹配
                if full_ts.shape[1] != N or full_ts.shape[2] != D:
                    # 维度不匹配，用零填充
                    profile_zeros = np.zeros((N, PROFILE_DIM), dtype=np.float32)
                    static_feat = np.concatenate([basic_stats, profile_zeros], axis=1)
                else:
                    # 为每个节点计算画像特征
                    profile_features = []
                    for node_idx in range(N):
                        node_ts = full_ts[:, node_idx, :]  # (T_full, D)
                        # 替换 NaN 和 Inf
                        node_ts = np.where(np.isfinite(node_ts), node_ts, 0.0)
                        profile = NodeProfile.from_timeseries(node_ts)
                        profile_features.append(profile.to_numpy())  # (66,)

                    profile_arr = np.stack(profile_features, axis=0)  # (N, 66)
                    # 拼接: (N, 5*D) + (N, 66)
                    static_feat = np.concatenate([basic_stats, profile_arr], axis=1)
            else:
                # 如果没有完整时间序列，用零填充画像特征（固定 66 维）
                profile_zeros = np.zeros((N, PROFILE_DIM), dtype=np.float32)
                static_feat = np.concatenate([basic_stats, profile_zeros], axis=1)

            # 再次检查并替换 NaN 和 Inf
            static_feat = np.where(np.isfinite(static_feat), static_feat, 0.0)

            static_features[ntype] = torch.from_numpy(static_feat).float()

        return static_features

    def _create_window_samples(
        self,
        record_meta: RecordMetadata,
        temporal_features: dict[str, npt.NDArray[np.float32]],
        static_features: dict[str, torch.Tensor],
        node_labels: dict[str, NodeLabel],
        soft_labels: dict[str, float],
        local_change_start: int,
        local_change_end: int,
        node_id_mapping: dict[str, list[str]],
    ) -> list[TemporalGraphData]:
        """创建滑动窗口样本。"""
        # 使用 Vbiz 的 T 作为参考（因为它是业务层）
        T_ref = temporal_features["Vbiz"].shape[0]
        cs = local_change_start
        ce = local_change_end

        # 使用实际加载的节点数而非 graph/nodes 中的预定义节点
        n_biz = temporal_features["Vbiz"].shape[1]

        # 预构建 Vbiz 节点的本地异常区间
        context = getattr(self, 'context_radius', 200)
        t_start_global = max(0, record_meta.change_start_idx - context)

        # 使用 node_id_mapping 中的实际文件名
        biz_ids = node_id_mapping.get("Vbiz", [f"biz_{i}" for i in range(n_biz)])

        # 构建 node_id 转换映射：文件名格式 -> node_labels 格式
        # 新数据集 (catalog_all_like_42): 文件名和标签中的 ID 都是 "app_1" 格式，直接匹配
        # 旧数据集 (IP格式): "113.44.169.60_8761" -> "113.44.169.60:8761"
        file_to_label_id: dict[str, str] = {}
                # Static overrides for graph_id -> label_id name mismatches (CCF uses
        # `redis` in graph/nodes/app.csv but `redis-cart` in ccf_p_label.json).
        _NAME_OVERRIDES = {"redis": "redis-cart"}
        for file_id in biz_ids:
            if file_id in _NAME_OVERRIDES:
                label_id = _NAME_OVERRIDES[file_id]
            elif "." in file_id and "_" in file_id:
                label_id = file_id.replace("_", ":")
            else:
                label_id = file_id
            file_to_label_id[file_id] = label_id

        # 调试：检查标签加载情况
        total_nodes = len(biz_ids)
        found_labels = 0
        found_anomaly = 0
        valid_interval = 0

        node_anom_intervals: list[tuple[int, int]] = []
        node_is_anomaly: list[bool] = []  # 记录节点是否有异常标记
        for file_id in biz_ids:
            # 尝试在 node_labels 中查找对应的标签
            label_id = file_to_label_id.get(file_id, file_id)
            nl = node_labels.get(label_id)
            if nl is not None:
                found_labels += 1
                if nl.is_anomaly == 1:
                    found_anomaly += 1
                    # 当 start_idx == end_idx == 0 时，可能是占位值表示整个时间范围
                    # 此时认为是一个有效的异常区间
                    if nl.end_idx > nl.start_idx or (nl.start_idx == 0 and nl.end_idx == 0):
                        valid_interval += 1
                        if nl.start_idx == 0 and nl.end_idx == 0:
                            # 占位值：使用全局时间范围
                            local_s = 0
                            local_e = T_ref
                        else:
                            local_s = nl.start_idx - t_start_global
                            local_e = nl.end_idx - t_start_global
                        node_anom_intervals.append((local_s, local_e))
                        node_is_anomaly.append(True)
                    else:
                        node_anom_intervals.append((-1, -1))
                        node_is_anomaly.append(False)
                else:
                    node_anom_intervals.append((-1, -1))
                    node_is_anomaly.append(False)
            else:
                # 标签未找到
                node_anom_intervals.append((-1, -1))
                node_is_anomaly.append(False)

        # 调试输出
        # [DEBUG] print(f"[DEBUG] 节点标签统计: total={total_nodes}, found={found_labels}, anomaly={found_anomaly}, valid_interval={valid_interval}")
        # [DEBUG] print(f"[DEBUG] node_labels 包含 {len(node_labels)} 个标签")
        # [DEBUG] if node_labels:
        # [DEBUG]     sample_keys = list(node_labels.keys())[:3]
        # [DEBUG]     print(f"[DEBUG] node_labels 示例 keys: {sample_keys}")
        # [DEBUG] print(f"[DEBUG] soft_labels 包含 {len(soft_labels)} 个标签")

        # 构建 biz_ids 对应的软标签数组（按 biz_ids 顺序）
        biz_soft_labels: list[float] = []
        for file_id in biz_ids:
            label_id = file_to_label_id.get(file_id, file_id)
            impact_score = soft_labels.get(label_id, 0.0)
            biz_soft_labels.append(impact_score)

        def make_window_labels(win_start: int, win_end: int, phase: str, case: str) -> torch.Tensor:
            """基于配置的标签模式生成窗口标签。"""
            if self.label_mode == "impact_static":
                return torch.tensor(biz_soft_labels, dtype=torch.float)

            labels: list[float] = []
            for idx, impact_score in enumerate(biz_soft_labels):
                interval_start, interval_end = node_anom_intervals[idx]
                has_overlap = (
                    node_is_anomaly[idx]
                    and interval_start >= 0
                    and interval_end >= 0
                    and interval_start < win_end
                    and interval_end > win_start
                )

                if phase == "pre" and not has_overlap:
                    labels.append(0.0)
                    continue

                label_value = impact_score
                if has_overlap:
                    # 保证与节点异常区间重叠的窗口至少保留正监督。
                    label_value = max(label_value, 0.5)
                labels.append(float(label_value))

            return torch.tensor(labels, dtype=torch.float)

        sample_indices: list[tuple[int, int, str]] = []

        # pre：变更前最近 2 个窗口
        pre_start = max(0, cs - self.window_size)
        pre_idxs = list(range(pre_start, min(cs, T_ref - self.window_size + 1), self.stride))
        for i in pre_idxs[-2:]:
            sample_indices.append((i, i + self.window_size, "pre"))

        # during：变更窗口内密集采样
        for i in range(cs, min(ce, T_ref - self.window_size + 1), max(1, self.stride // 2)):
            sample_indices.append((i, i + self.window_size, "during"))

        # post：变更后最近 2 个窗口
        post_idxs = list(range(ce, min(T_ref - self.window_size + 1, T_ref), self.stride))
        for i in post_idxs[:2]:
            sample_indices.append((i, i + self.window_size, "post"))

        # 优先保留 during，最多 20 个样本
        if len(sample_indices) > 20:
            sample_indices = sorted(
                sample_indices,
                key=lambda x: 0 if x[2] == "during" else (1 if x[2] == "pre" else 2)
            )[:20]

        samples = []
        # 固定窗口大小为 self.window_size
        fixed_window_size = self.window_size
        D_default = 11  # 默认特征维度

        for start, end, phase in sample_indices:
            # 对齐 temporal_features 与图的节点数
            aligned_temporal = {}

            # 使用固定窗口大小，并确保不超过数据范围
            actual_start = min(start, T_ref - fixed_window_size)
            actual_end = actual_start + fixed_window_size

            for ntype in self.graph.ntypes:
                if ntype in temporal_features:
                    feat = temporal_features[ntype]
                    T_feat = feat.shape[0]
                    # 使用固定窗口大小
                    window_start = min(actual_start, T_feat - fixed_window_size)
                    window_end = min(window_start + fixed_window_size, T_feat)
                    if window_end <= window_start:
                        # 如果窗口超出范围，跳过
                        continue
                    window_feat = feat[window_start:window_end]  # (T_window, N_actual, D)
                    T_window, N_actual, D = window_feat.shape
                    N_graph = self.graph.num_nodes(ntype)

                    if N_actual < N_graph:
                        # 用零填充缺失节点
                        padding = np.zeros((T_window, N_graph - N_actual, D), dtype=np.float32)
                        window_feat = np.concatenate([window_feat, padding], axis=1)
                    elif N_actual > N_graph:
                        # 截断多余节点
                        window_feat = window_feat[:, :N_graph, :]

                    # 如果 T_window < fixed_window_size，填充到固定大小
                    if T_window < fixed_window_size:
                        pad_T = fixed_window_size - T_window
                        padding = np.zeros((pad_T, window_feat.shape[1], window_feat.shape[2]), dtype=np.float32)
                        window_feat = np.concatenate([window_feat, padding], axis=0)

                    aligned_temporal[ntype] = torch.from_numpy(window_feat).float()
                else:
                    # 如果该节点类型在 temporal_features 中不存在，创建零填充
                    N_graph = self.graph.num_nodes(ntype)
                    if ntype in self.node_ids and len(self.node_ids[ntype]) > 0:
                        # 获取其他已有节点类型的 D 作为参考
                        D = D_default
                        for other_ntype, other_feat in temporal_features.items():
                            if len(other_feat.shape) == 3:
                                D = other_feat.shape[2]
                                break
                    else:
                        D = D_default
                    zeros = np.zeros((fixed_window_size, N_graph, D), dtype=np.float32)
                    aligned_temporal[ntype] = torch.from_numpy(zeros).float()

            # 跳过空的 temporal_features
            if not aligned_temporal:
                continue

            # 对齐 static_features 与图的节点数
            aligned_static = {}
            for ntype in self.graph.ntypes:
                if ntype in static_features:
                    feat = static_features[ntype]
                    # 确保 feat 是 numpy 数组
                    if isinstance(feat, torch.Tensor):
                        feat_np = feat.numpy()
                    else:
                        feat_np = feat
                    N_actual = feat_np.shape[0]
                    N_graph = self.graph.num_nodes(ntype)

                    if N_actual < N_graph:
                        # 用零填充缺失节点
                        padding = np.zeros((N_graph - N_actual, feat_np.shape[1]), dtype=np.float32)
                        feat_np = np.concatenate([feat_np, padding], axis=0)
                    elif N_actual > N_graph:
                        # 截断多余节点
                        feat_np = feat_np[:N_graph, :]

                    aligned_static[ntype] = torch.from_numpy(feat_np).float()
                else:
                    # 如果该节点类型不存在，用零填充
                    N_graph = self.graph.num_nodes(ntype)
                    if ntype in aligned_temporal:
                        D = aligned_temporal[ntype].shape[-1]
                    else:
                        D = 5 * D_default + 66  # 静态特征维度
                    zeros = np.zeros((N_graph, D), dtype=np.float32)
                    aligned_static[ntype] = torch.from_numpy(zeros).float()

            window_labels = make_window_labels(start, end, phase, record_meta.case)
            #调整标签数量以匹配图的节点数
            N_graph = self.graph.num_nodes("Vbiz")
            if window_labels.shape[0] < N_graph:
                padding_labels = torch.zeros(N_graph - window_labels.shape[0], dtype=torch.float)
                window_labels = torch.cat([window_labels, padding_labels])
            elif window_labels.shape[0] > N_graph:
                window_labels = window_labels[:N_graph]

            samples.append(TemporalGraphData(
                graph=self.graph,
                node_features=aligned_static,
                temporal_features=aligned_temporal,
                labels=window_labels,
                node_ids=self.node_ids,
                metadata=record_meta,
                change_window=(record_meta.change_start_idx, record_meta.change_end_idx),
                phase=phase,
            ))
        return samples

    def _compute_normalization_stats(self) -> None:
        """计算归一化统计量 - 同时处理静态特征和时序特征"""
        # 1. 静态特征归一化
        all_static_features = {ntype: [] for ntype in self.node_ids.keys()}

        for sample in self.samples:
            for ntype, features in sample.node_features.items():
                all_static_features[ntype].append(features.numpy())

        self.norm_stats: dict[str, dict[str, np.ndarray]] = {}
        for ntype, feat_list in all_static_features.items():
            if not feat_list:
                continue

            stacked = np.vstack(feat_list)
            std_vals = np.std(stacked, axis=0)
            std_vals = np.where(std_vals < 1e-8, 1.0, std_vals)  # 防止除以零
            self.norm_stats[ntype] = {
                "mean": np.mean(stacked, axis=0),
                "std": std_vals,
            }

        # 2. 时序特征归一化
        all_temporal_features = {ntype: [] for ntype in self.node_ids.keys()}

        for sample in self.samples:
            for ntype, features in sample.temporal_features.items():
                # 将 (T, N, D)  reshape 为 (T*N, D) 进行统计
                T, N, D = features.shape
                all_temporal_features[ntype].append(features.numpy().reshape(T * N, D))

        self.temporal_norm_stats: dict[str, dict[str, np.ndarray]] = {}
        for ntype, feat_list in all_temporal_features.items():
            if not feat_list:
                continue

            stacked = np.vstack(feat_list)
            # 对标准差设置上下界，避免极端值
            std_vals = np.std(stacked, axis=0) + 1e-8
            std_vals = np.clip(std_vals, 1e-4, 1e8)

            self.temporal_norm_stats[ntype] = {
                "mean": np.mean(stacked, axis=0),
                "std": std_vals,
            }

        # 应用归一化 - 静态特征
        for sample in self.samples:
            for ntype, features in sample.node_features.items():
                if ntype in self.norm_stats:
                    stats = self.norm_stats[ntype]
                    feat_np = features.numpy()
                    # 替换 NaN/Inf 为 0
                    feat_np = np.where(np.isfinite(feat_np), feat_np, 0.0)
                    normalized = (feat_np - stats["mean"]) / stats["std"]
                    normalized = np.clip(normalized, -10, 10)  # 裁剪极端值
                    # 归一化后再次检查并替换 NaN 和 Inf
                    normalized = np.where(np.isfinite(normalized), normalized, 0.0)
                    sample.node_features[ntype] = torch.from_numpy(normalized).float()

            # 应用归一化 - 时序特征
            for ntype, features in sample.temporal_features.items():
                if ntype in self.temporal_norm_stats:
                    stats = self.temporal_norm_stats[ntype]
                    feat_np = features.numpy()
                    # 替换 NaN/Inf 为 0
                    feat_np = np.where(np.isfinite(feat_np), feat_np, 0.0)
                    # features shape: (T, N, D) -> normalize along D
                    normalized = (feat_np - stats["mean"]) / stats["std"]
                    normalized = np.clip(normalized, -10, 10)  # 裁剪极端值
                    # 归一化后再次检查并替换 NaN 和 Inf
                    normalized = np.where(np.isfinite(normalized), normalized, 0.0)
                    sample.temporal_features[ntype] = torch.from_numpy(normalized).float()

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> TemporalGraphData:
        return self.samples[idx]

    def get_feature_dims(self) -> dict[str, int]:
        """获取特征维度"""
        if not self.samples:
            return {"Vphy": 32, "Vvm": 32, "Vbiz": 32}
        return {
            ntype: feat.shape[1]
            for ntype, feat in self.samples[0].node_features.items()
        }

    def get_temporal_feature_dims(self) -> dict[str, int]:
        """获取时序特征维度

        如果启用了动态指标发现，返回注册表中的维度；
        否则返回实际数据的维度。
        """
        if self.enable_dynamic_metrics and self.metric_registry is not None:
            return self.metric_registry.get_temporal_dims()

        if not self.samples:
            return {"Vphy": 6, "Vvm": 6, "Vbiz": 6}
        return {
            ntype: feat.shape[-1]
            for ntype, feat in self.samples[0].temporal_features.items()
        }


def load_tenant_dataset(
    data_root: str | Path,
    tenant_id: str = "tenant_0000",
    window_size: int = 32,
    stride: int = 16,
    use_loess_residual: str | set[str] = False,
    enable_dynamic_metrics: bool = True,
    label_mode: str = "impact_static",
) -> TenantDataset:
    """加载租户数据集的便捷函数

    Args:
        data_root: 数据根目录
        tenant_id: 租户ID
        window_size: 时序窗口大小
        stride: 滑动窗口步长
        use_loess_residual: 对哪些节点类型使用LOESS残差。
            - False/空集: 不使用残差
            - "Vbiz": 仅业务层
            - "VM+Biz": VM和业务层
            - "all": 所有层
        enable_dynamic_metrics: 是否启用动态指标发现（默认True）。
            启用后会自动发现并注册所有指标，支持不同节点有不同指标。
        label_mode: 窗口标签模式，支持 "impact_static" 和 "phase_aware"。

    Returns:
        TenantDataset 实例
    """
    data_root = Path(data_root)
    tenant_dir = data_root / tenant_id

    return TenantDataset(
        tenant_dir=tenant_dir,
        window_size=window_size,
        stride=stride,
        normalize=True,
        use_loess_residual=use_loess_residual,
        enable_dynamic_metrics=enable_dynamic_metrics,
        label_mode=label_mode,
    )


def collateTenant_fn(batch: list[TemporalGraphData]) -> dict[str, Any]:
    """租户数据批次整理函数

    由于图结构相同，可以批量处理
    """
    if not batch:
        return {}

    # 堆叠特征
    node_features = {
        ntype: torch.stack([s.node_features[ntype] for s in batch])
        for ntype in batch[0].node_features.keys()
    }

    temporal_features = {
        ntype: torch.stack([s.temporal_features[ntype] for s in batch])
        for ntype in batch[0].temporal_features.keys()
    }

    labels = torch.stack([s.labels for s in batch])

    return {
        "graph": batch[0].graph,  # 所有样本共享同一图结构
        "node_features": node_features,
        "temporal_features": temporal_features,
        "labels": labels,
        "metadata": [s.metadata for s in batch],
        "change_windows": [s.change_window for s in batch],
    }
