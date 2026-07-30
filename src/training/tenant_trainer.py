"""租户数据训练器"""

import json
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset

from ..data.tenant_dataset import TenantDataset, TemporalGraphData
from ..models.abvd_views import (
    ABVD_UPSTREAM_VIEW_CHOICES,
    build_calling_downstream,
    build_upstream_edge_view,
)
from ..models.temporal_rgcn import TenantRiskPredictor, TemporalModelOutput
from ..models.loss import FocalLoss, TopologyAwareMarginLoss, TACMLoss, TACMMaelLoss, TACMMseLoss


@dataclass
class CaseMetrics:
    """按Case分类的指标"""
    case: str
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    total_samples: int = 0
    total_positives: int = 0


@dataclass
class AnomalyLevelMetrics:
    """按异常层次分类的指标"""
    level: str  # host_anom, vm_anom, app_anom, normal
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    num_samples: int = 0


@dataclass
class PhaseMetrics:
    """按窗口阶段分类的指标"""
    phase: str  # pre, during, post
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    num_samples: int = 0


@dataclass
class TrainingMetrics:
    """训练指标"""
    epoch: int
    train_loss: float
    val_loss: float
    train_mae: float = 0.0
    train_acc: float = 0.0
    val_acc: float = 0.0
    train_f1: float = 0.0
    val_f1: float = 0.0
    val_auc: float = 0.0
    val_mae: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    learning_rate: float = 0.0
    eval_threshold: float = 0.15
    selection_metric: str = "val_mae"
    selection_score: float = 0.0
    checkpoint_acceptable: bool = True
    threshold_scan: dict[str, dict[str, float]] = None
    # 多维验证指标
    case_metrics: list[CaseMetrics] = None
    anomaly_level_metrics: list[AnomalyLevelMetrics] = None
    phase_metrics: list[PhaseMetrics] = None
    # 训练阶段按case分类的loss
    case_train_loss: dict[str, float] = None
    # 验证阶段按case分类的loss
    case_val_loss: dict[str, float] = None

    def __post_init__(self):
        if self.case_metrics is None:
            self.case_metrics = []
        if self.anomaly_level_metrics is None:
            self.anomaly_level_metrics = []
        if self.phase_metrics is None:
            self.phase_metrics = []
        if self.threshold_scan is None:
            self.threshold_scan = {}
        if self.case_train_loss is None:
            self.case_train_loss = {}
        if self.case_val_loss is None:
            self.case_val_loss = {}

    def to_dict(self) -> dict[str, float]:
        result = {
            "epoch": self.epoch,
            "train_loss": self.train_loss,
            "train_mae": self.train_mae,
            "val_loss": self.val_loss,
            "train_f1": self.train_f1,
            "val_f1": self.val_f1,
            "val_auc": self.val_auc,
            "val_mae": self.val_mae,
            "precision": self.precision,
            "recall": self.recall,
            "learning_rate": self.learning_rate,
            "eval_threshold": self.eval_threshold,
            "selection_metric": self.selection_metric,
            "selection_score": self.selection_score,
            "checkpoint_acceptable": self.checkpoint_acceptable,
            "threshold_scan": self.threshold_scan,
        }
        # 添加case分类指标
        for cm in self.case_metrics:
            prefix = f"case_{cm.case}"
            result[f"{prefix}_f1"] = cm.f1
            result[f"{prefix}_precision"] = cm.precision
            result[f"{prefix}_recall"] = cm.recall
            result[f"{prefix}_tp"] = cm.tp
            result[f"{prefix}_fp"] = cm.fp
            result[f"{prefix}_tn"] = cm.tn
            result[f"{prefix}_fn"] = cm.fn
        # 添加异常层次指标
        for am in self.anomaly_level_metrics:
            prefix = f"level_{am.level}"
            result[f"{prefix}_f1"] = am.f1
            result[f"{prefix}_precision"] = am.precision
            result[f"{prefix}_recall"] = am.recall
        # 添加阶段指标
        for pm in self.phase_metrics:
            prefix = f"phase_{pm.phase}"
            result[f"{prefix}_f1"] = pm.f1
            result[f"{prefix}_precision"] = pm.precision
            result[f"{prefix}_recall"] = pm.recall
        # 添加按case分类的训练loss
        for case_name, loss_val in self.case_train_loss.items():
            result[f"train_loss_{case_name}"] = loss_val
        # 添加按case分类的验证loss
        for case_name, loss_val in self.case_val_loss.items():
            result[f"val_loss_{case_name}"] = loss_val
        return result


@dataclass
class TrainingState:
    """训练状态"""
    model: TenantRiskPredictor
    optimizer: optim.Optimizer
    epoch: int = 0
    best_val_loss: float = float("inf")
    best_val_mae: float = float("inf")
    best_val_auc: float = 0.0
    best_epoch: int = -1
    best_selection_score: float | None = None
    best_checkpoint_acceptable: bool = False
    patience_counter: int = 0
    metrics_history: list[TrainingMetrics] = field(default_factory=list)

    def save_checkpoint(self, path: Path) -> None:
        """保存检查点"""
        checkpoint = {
            "epoch": self.epoch,
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "best_val_loss": self.best_val_loss,
            "best_val_mae": self.best_val_mae,
            "best_val_auc": self.best_val_auc,
            "best_epoch": self.best_epoch,
            "best_selection_score": self.best_selection_score,
            "best_checkpoint_acceptable": self.best_checkpoint_acceptable,
            "patience_counter": self.patience_counter,
            "metrics_history": [m.to_dict() for m in self.metrics_history],
        }
        torch.save(checkpoint, path)

    @classmethod
    def load_checkpoint(
        cls,
        path: Path,
        model: TenantRiskPredictor,
        optimizer: optim.Optimizer,
    ) -> "TrainingState":
        """加载检查点"""
        checkpoint = torch.load(path, weights_only=False)

        model.load_state_dict(checkpoint["model_state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])

        metrics_history = [
            TrainingMetrics(**m) for m in checkpoint.get("metrics_history", [])
        ]

        return cls(
            model=model,
            optimizer=optimizer,
            epoch=checkpoint["epoch"],
            best_val_loss=checkpoint["best_val_loss"],
            best_val_mae=checkpoint.get("best_val_mae", float("inf")),
            best_val_auc=checkpoint.get("best_val_auc", 0.0),
            best_epoch=checkpoint.get("best_epoch", -1),
            best_selection_score=checkpoint.get("best_selection_score"),
            best_checkpoint_acceptable=checkpoint.get("best_checkpoint_acceptable", False),
            patience_counter=checkpoint.get("patience_counter", 0),
            metrics_history=metrics_history,
        )


class EarlyStopping:
    """早停机制"""

    def __init__(
        self,
        patience: int = 10,
        min_delta: float = 0.001,
        mode: str = "min",
    ):
        """初始化早停

        Args:
            patience: 容忍epoch数
            min_delta: 最小变化量
            mode: 'min' 或 'max'
        """
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.counter = 0
        self.best_score = None
        self.early_stop = False

    def __call__(self, score: float) -> bool:
        """检查是否应该早停"""
        if self.best_score is None:
            self.best_score = score
            return False

        if self.mode == "min":
            improved = score < self.best_score - self.min_delta
        else:
            improved = score > self.best_score + self.min_delta

        if improved:
            self.best_score = score
            self.counter = 0
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True

        return self.early_stop


class SegmentBatchSampler:
    """Yield single-segment batches while interleaving segments."""

    def __init__(
        self,
        samples: list[TemporalGraphData],
        batch_size: int,
        shuffle: bool,
        seed: int,
    ) -> None:
        self.samples = samples
        self.batch_size = batch_size
        self.shuffle = shuffle
        self.seed = seed
        self._iteration = 0

    def __iter__(self):
        rng = random.Random(self.seed + self._iteration)
        self._iteration += 1
        segment_to_indices: dict[str, list[int]] = {}
        for idx, sample in enumerate(self.samples):
            segment_id = sample.metadata.segment_id or "single_segment"
            segment_to_indices.setdefault(segment_id, []).append(idx)

        batches: list[list[int]] = []
        for segment_id in sorted(segment_to_indices):
            indices = list(segment_to_indices[segment_id])
            if self.shuffle:
                rng.shuffle(indices)
            for start in range(0, len(indices), self.batch_size):
                batches.append(indices[start:start + self.batch_size])

        if self.shuffle:
            rng.shuffle(batches)
        return iter(batches)

    def __len__(self) -> int:
        total = 0
        segment_to_count: dict[str, int] = {}
        for sample in self.samples:
            segment_id = sample.metadata.segment_id or "single_segment"
            segment_to_count[segment_id] = segment_to_count.get(segment_id, 0) + 1
        for count in segment_to_count.values():
            total += (count + self.batch_size - 1) // self.batch_size
        return total


class TenantTrainer:
    """租户数据训练器 - 优化版本 V2

    优化点 (2026-04-01):
    - 使用 BCE Loss 配合 pos_weight（更适合清晰标签）
    - 使用 AdamW 优化器 (更好的正则化)
    - 使用余弦退火学习率调度
    - 使用混合精度训练
    - 增加 batch size
    """

    def __init__(
        self,
        model: TenantRiskPredictor,
        learning_rate: float = 0.001,  # 提高学习率
        weight_decay: float = 1e-4,   # 增加权重衰减
        loss_type: str = "tacm",  # focal | tacm | tacm_mae | tacm_mse | tacm_scr | tacm_abvd | tacm_scr_abvd
        tacm_kappa: float = 3.0,
        tacm_alpha: float = 2.0,
        tacm_beta: float = 5.0,
        lambda_scr: float = 0.5,  # weight for SCR term (only used if loss_type=tacm_scr)
        abvd_beta_up: float = 3.0,
        abvd_beta_down: float = 4.0,
        abvd_upstream_view: str = "same_host_peer_biz",
        focal_alpha: float = 0.75,
        focal_gamma: float = 2.0,
        eval_threshold: float = 0.15,
        selection_metric: str = "val_mae",
        device: str | torch.device = "cuda" if torch.cuda.is_available() else "cpu",
    ):
        """初始化训练器

        Args:
            model: 风险预测模型
            learning_rate: 学习率
            weight_decay: 权重衰减
            focal_alpha: Focal Loss alpha 参数
            focal_gamma: Focal Loss gamma 参数
            device: 训练设备
        """
        self.device = torch.device(device)
        self.model = model.to(self.device)

        # 使用 TACM Loss (Topology-Aware Continuous Margin Loss)
        # 专为真实场景设计：少数高风险孤立节点需要矫枉过正来对抗网络平滑
        # Build criterion based on loss_type
        from ..models.loss import (
            FocalLoss,
            TACMLoss,
            TACMMaelLoss,
            TACMMseLoss,
            TACMSCRLoss,
            TACMABVDLoss,
            TACMSCRABVDLoss,
        )
        self.loss_type = loss_type
        self.eval_threshold = eval_threshold
        self.label_threshold = 0.5
        if selection_metric not in {"val_mae", "val_auc"}:
            raise ValueError(f"Unsupported selection_metric={selection_metric!r}")
        if abvd_upstream_view not in ABVD_UPSTREAM_VIEW_CHOICES:
            raise ValueError(
                f"Unsupported abvd_upstream_view={abvd_upstream_view!r}; "
                f"choices={ABVD_UPSTREAM_VIEW_CHOICES}"
            )
        self.selection_metric = selection_metric
        self.abvd_upstream_view = abvd_upstream_view
        self.last_fit_context: dict[str, Any] = {}
        if loss_type == "focal":
            self.criterion = FocalLoss(alpha=focal_alpha, gamma=focal_gamma)
        elif loss_type == "tacm":
            self.criterion = TACMLoss(
                kappa=tacm_kappa, alpha=tacm_alpha, beta=tacm_beta, reduction="mean",
            )
        elif loss_type == "tacm_mae":
            self.criterion = TACMMaelLoss(
                kappa=tacm_kappa, alpha=tacm_alpha, beta=tacm_beta,
                mae_weight=0.5, reduction="mean",
            )
        elif loss_type == "tacm_mse":
            self.criterion = TACMMseLoss(
                kappa=tacm_kappa, alpha=tacm_alpha, beta=tacm_beta,
                mse_weight=0.7, reduction="mean",
            )
        elif loss_type == "tacm_scr":
            self.criterion = TACMSCRLoss(
                kappa=tacm_kappa, alpha=tacm_alpha, beta=tacm_beta,
                lambda_scr=lambda_scr, reduction="mean",
            )
        elif loss_type == "tacm_abvd":
            self.criterion = TACMABVDLoss(
                kappa=tacm_kappa,
                alpha=tacm_alpha,
                beta=tacm_beta,
                beta_up=abvd_beta_up,
                beta_down=abvd_beta_down,
                reduction="mean",
            )
        elif loss_type == "tacm_scr_abvd":
            self.criterion = TACMSCRABVDLoss(
                kappa=tacm_kappa,
                alpha=tacm_alpha,
                beta=tacm_beta,
                beta_up=abvd_beta_up,
                beta_down=abvd_beta_down,
                lambda_scr=lambda_scr,
                reduction="mean",
            )
        else:
            raise ValueError(f"Unknown loss_type={loss_type!r}")

        # 使用 AdamW 优化器 (更好的正则化)
        self.optimizer = optim.AdamW(
            self.model.parameters(),
            lr=learning_rate,
            weight_decay=1e-4,  # 适中的权重衰减
            betas=(0.9, 0.999),
            eps=1e-8,
        )

        # 使用余弦退火学习率调度
        self.scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
            self.optimizer,
            T_0=12,
            T_mult=2,
            eta_min=1e-6,
        )

        self.early_stopping = EarlyStopping(
            patience=15,
            min_delta=0.001,
            mode="min" if selection_metric == "val_mae" else "max",
        )

        self.state = TrainingState(
            model=self.model,
            optimizer=self.optimizer,
        )

        # 混合精度训练
        self.scaler = torch.cuda.amp.GradScaler() if device == "cuda" and torch.cuda.is_available() else None

    def _reset_fit_state(self) -> None:
        """Reset fit-time state before a new training run."""
        self.state.best_val_loss = float("inf")
        self.state.best_val_mae = float("inf")
        self.state.best_val_auc = 0.0
        self.state.best_epoch = -1
        self.state.best_selection_score = None
        self.state.best_checkpoint_acceptable = False
        self.state.patience_counter = 0
        self.state.metrics_history = []
        self.early_stopping.counter = 0
        self.early_stopping.best_score = None
        self.early_stopping.early_stop = False

    def _sample_loss(
        self,
        logits_full: torch.Tensor,
        logits_sub: torch.Tensor | None,
        labels: torch.Tensor,
        calling_edge_index: torch.Tensor,
        upstream_edge_index: torch.Tensor,
        num_biz: int,
    ) -> torch.Tensor:
        if self.loss_type == "focal":
            return self.criterion(logits_full, labels)
        if self.loss_type == "tacm_scr":
            if logits_sub is None:
                raise RuntimeError("tacm_scr requires subgraph logits")
            return self.criterion(logits_full, logits_sub, labels, calling_edge_index, num_biz)
        if self.loss_type == "tacm_abvd":
            return self.criterion(
                logits_full, labels, calling_edge_index, upstream_edge_index, num_biz
            )
        if self.loss_type == "tacm_scr_abvd":
            if logits_sub is None:
                raise RuntimeError("tacm_scr_abvd requires subgraph logits")
            return self.criterion(
                logits_full,
                logits_sub,
                labels,
                calling_edge_index,
                upstream_edge_index,
                num_biz,
            )
        return self.criterion(logits_full, labels, calling_edge_index, num_biz)

    def _build_topology_edges(
        self,
        graph,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Build Biz-only directional edge views for topology-aware losses."""
        device = self.device
        calling_edge_index = build_calling_downstream(graph, device=device)
        upstream_edge_index = build_upstream_edge_view(
            graph,
            self.abvd_upstream_view,
            device=device,
        )
        return calling_edge_index, upstream_edge_index

    def _compute_threshold_stats(
        self,
        preds_arr: np.ndarray,
        labels_arr: np.ndarray,
        threshold: float | None = None,
    ) -> tuple[float, float, float, float, int, int, int, int]:
        """基于连续概率预测计算阈值指标。"""
        if threshold is None:
            threshold = self.eval_threshold

        binary_preds = (preds_arr >= threshold).astype(int)
        binary_labels = (labels_arr >= self.label_threshold).astype(int)

        tp = int(np.sum((binary_preds == 1) & (binary_labels == 1)))
        fp = int(np.sum((binary_preds == 1) & (binary_labels == 0)))
        fn = int(np.sum((binary_preds == 0) & (binary_labels == 1)))
        tn = int(np.sum((binary_preds == 0) & (binary_labels == 0)))

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        accuracy = (tp + tn) / len(preds_arr) if len(preds_arr) > 0 else 0.0
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
        else:
            f1 = 0.0

        return float(f1), float(precision), float(recall), float(accuracy), tp, fp, tn, fn

    def _compute_threshold_scan(
        self,
        preds: list[float],
        labels: list[float],
    ) -> dict[str, dict[str, float]]:
        """扫描多个预测阈值，记录 F1/Precision/Recall/Accuracy。"""
        preds_arr = np.array(preds, dtype=float)
        labels_arr = np.array(labels, dtype=float)
        if len(preds_arr) == 0:
            return {}

        scan: dict[str, dict[str, float]] = {}
        for threshold in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
            f1, precision, recall, accuracy, _, _, _, _ = self._compute_threshold_stats(
                preds_arr, labels_arr, threshold=threshold
            )
            scan[f"{threshold:.2f}"] = {
                "f1": f1,
                "precision": precision,
                "recall": recall,
                "accuracy": accuracy,
            }
        return scan

    def _metrics_snapshot(self, metrics: TrainingMetrics) -> dict[str, Any]:
        """Extract a compact checkpoint snapshot for diagnostics."""
        return {
            "epoch": metrics.epoch,
            "train_loss": metrics.train_loss,
            "val_loss": metrics.val_loss,
            "val_mae": metrics.val_mae,
            "val_auc": metrics.val_auc,
            "val_f1": metrics.val_f1,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "selection_metric": metrics.selection_metric,
            "selection_score": metrics.selection_score,
            "checkpoint_acceptable": metrics.checkpoint_acceptable,
        }

    def _edge_count_stats(
        self,
        counts: list[int],
        nonzero_records: set[str] | None = None,
    ) -> dict[str, Any]:
        """Summarize edge counts across a sample subset."""
        if not counts:
            return {
                "num_samples": 0,
                "min": 0,
                "max": 0,
                "mean": 0.0,
                "nonzero_samples": 0,
                "nonzero_ratio": 0.0,
                "nonzero_records": [],
            }
        nonzero = sum(1 for count in counts if count > 0)
        return {
            "num_samples": len(counts),
            "min": int(min(counts)),
            "max": int(max(counts)),
            "mean": float(sum(counts) / len(counts)),
            "nonzero_samples": int(nonzero),
            "nonzero_ratio": float(nonzero / len(counts)),
            "nonzero_records": sorted(nonzero_records or []),
        }

    def _summarize_edge_views(
        self,
        samples: list[TemporalGraphData],
    ) -> dict[str, Any]:
        """Summarize ABVD-relevant edge views for a subset."""
        calling_counts: list[int] = []
        upstream_counts: list[int] = []
        calling_nonzero_records: set[str] = set()
        upstream_nonzero_records: set[str] = set()
        phase_counts: dict[str, int] = {}

        for sample in samples:
            calling_edge_index, upstream_edge_index = self._build_topology_edges(sample.graph)
            calling_count = int(calling_edge_index.shape[1])
            upstream_count = int(upstream_edge_index.shape[1])
            calling_counts.append(calling_count)
            upstream_counts.append(upstream_count)
            phase_counts[sample.phase] = phase_counts.get(sample.phase, 0) + 1
            if calling_count > 0:
                calling_nonzero_records.add(sample.metadata.record_id)
            if upstream_count > 0:
                upstream_nonzero_records.add(sample.metadata.record_id)

        return {
            "phase_counts": phase_counts,
            "upstream_view_name": self.abvd_upstream_view,
            "calling_downstream": self._edge_count_stats(calling_counts, calling_nonzero_records),
            "upstream_selected_view": self._edge_count_stats(upstream_counts, upstream_nonzero_records),
        }

    def _get_selection_score(self, metrics: TrainingMetrics) -> float:
        return metrics.val_mae if self.selection_metric == "val_mae" else metrics.val_auc

    def _is_better_selection(self, current_score: float, best_score: float | None) -> bool:
        if best_score is None:
            return True
        if self.selection_metric == "val_mae":
            return current_score < best_score
        return current_score > best_score

    def _build_collate_fn(self):
        """Build collate_fn shared by single- and multi-segment training."""

        def collate_fn(batch):
            if not batch:
                return {}

            segment_ids = {
                sample.metadata.segment_id or sample.metadata.tenant_id
                for sample in batch
            }
            if len(segment_ids) > 1:
                raise ValueError(
                    f"Mixed-segment batch is not allowed: {sorted(segment_ids)}"
                )

            all_node_types = set()
            for sample in batch:
                all_node_types.update(sample.temporal_features.keys())
                all_node_types.update(sample.node_features.keys())

            def pad_3d_tensor(tensor, max_t, max_n, pad_value=0.0):
                T, N, D = tensor.shape
                if T < max_t:
                    pad_t = max_t - T
                    tensor = torch.cat(
                        [tensor, torch.full((pad_t, N, D), pad_value, dtype=tensor.dtype)],
                        dim=0,
                    )
                if tensor.shape[1] < max_n:
                    pad_n = max_n - tensor.shape[1]
                    tensor = torch.cat(
                        [
                            tensor,
                            torch.full((tensor.shape[0], pad_n, D), pad_value, dtype=tensor.dtype),
                        ],
                        dim=1,
                    )
                if tensor.shape[0] > max_t or tensor.shape[1] > max_n:
                    tensor = tensor[:max_t, :max_n, :]
                return tensor

            padded_temporal_features = {}
            for ntype in all_node_types:
                tensors = [s.temporal_features[ntype] for s in batch if s.temporal_features.get(ntype) is not None]
                if tensors:
                    max_t = max(t.shape[0] for t in tensors)
                    max_n = max(t.shape[1] for t in tensors)
                    padded_temporal_features[ntype] = torch.stack(
                        [pad_3d_tensor(t, max_t, max_n) for t in tensors]
                    )

            padded_node_features = {}
            for ntype in all_node_types:
                tensors = [s.node_features[ntype] for s in batch if s.node_features.get(ntype) is not None]
                if tensors:
                    max_n = max(t.shape[0] for t in tensors)
                    tensors_padded = []
                    for tensor in tensors:
                        if tensor.shape[0] < max_n:
                            tensors_padded.append(
                                torch.cat(
                                    [tensor, torch.zeros(max_n - tensor.shape[0], tensor.shape[1], dtype=tensor.dtype)],
                                    dim=0,
                                )
                            )
                        else:
                            tensors_padded.append(tensor[:max_n])
                    padded_node_features[ntype] = torch.stack(tensors_padded)

            graph = batch[0].graph
            batch_graph_counts = {
                "Vphy": int(graph.num_nodes("Vphy")),
                "Vvm": int(graph.num_nodes("Vvm")),
                "Vbiz": int(graph.num_nodes("Vbiz")),
            }
            return {
                "graph": graph,
                "node_features": padded_node_features,
                "temporal_features": padded_temporal_features,
                "labels": torch.stack([s.labels for s in batch]),
                "metadata": [s.metadata for s in batch],
                "change_windows": [s.change_window for s in batch],
                "phase": [s.phase for s in batch],
                "segment_id": next(iter(segment_ids)),
                "batch_graph_counts": batch_graph_counts,
            }

        return collate_fn

    def _make_data_loaders(
        self,
        train_subset: list[TemporalGraphData],
        val_subset: list[TemporalGraphData],
        seed: int,
        segment_aware_batching: bool,
        batch_size: int = 8,
    ) -> tuple[DataLoader, DataLoader]:
        collate_fn = self._build_collate_fn()
        if segment_aware_batching:
            train_loader = DataLoader(
                train_subset,
                batch_sampler=SegmentBatchSampler(train_subset, batch_size=batch_size, shuffle=True, seed=seed),
                collate_fn=collate_fn,
            )
            val_loader = DataLoader(
                val_subset,
                batch_sampler=SegmentBatchSampler(val_subset, batch_size=batch_size, shuffle=False, seed=seed),
                collate_fn=collate_fn,
            )
        else:
            train_loader = DataLoader(
                train_subset,
                batch_size=batch_size,
                shuffle=True,
                collate_fn=collate_fn,
            )
            val_loader = DataLoader(
                val_subset,
                batch_size=batch_size,
                shuffle=False,
                collate_fn=collate_fn,
            )
        return train_loader, val_loader

    def _fit_subsets(
        self,
        train_subset: list[TemporalGraphData],
        val_subset: list[TemporalGraphData],
        num_epochs: int,
        save_dir: Path,
        seed: int,
        fit_context: dict[str, Any],
        segment_aware_batching: bool,
    ) -> TrainingState:
        if not train_subset:
            raise ValueError("Training subset is empty")
        if not val_subset:
            raise ValueError("Validation subset is empty")

        self._reset_fit_state()
        self.last_fit_context = fit_context
        self.last_fit_context["edge_view_stats"] = {
            "train": self._summarize_edge_views(train_subset),
            "val": self._summarize_edge_views(val_subset),
        }

        train_loader, val_loader = self._make_data_loaders(
            train_subset,
            val_subset,
            seed=seed,
            segment_aware_batching=segment_aware_batching,
        )
        print(f"Training on {len(train_subset)} samples, validating on {len(val_subset)}")

        for epoch in range(num_epochs):
            self.state.epoch = epoch

            train_loss, train_mae, train_acc, train_f1, train_prec, train_rec, case_train_loss = self.train_epoch(train_loader)
            val_loss, val_mae, val_acc, val_f1, val_auc, val_prec, val_rec, case_metrics, level_metrics, phase_metrics, case_val_loss, threshold_scan = self.validate(val_loader)

            current_lr = self.optimizer.param_groups[0]["lr"]
            checkpoint_acceptable = val_f1 > 0.0

            metrics = TrainingMetrics(
                epoch=epoch,
                train_loss=train_loss,
                val_loss=val_loss,
                train_mae=train_mae,
                train_acc=train_acc,
                val_acc=val_acc,
                train_f1=train_f1,
                val_f1=val_f1,
                val_auc=val_auc,
                val_mae=val_mae,
                precision=val_prec,
                recall=val_rec,
                learning_rate=current_lr,
                eval_threshold=self.eval_threshold,
                selection_metric=self.selection_metric,
                checkpoint_acceptable=checkpoint_acceptable,
                threshold_scan=threshold_scan,
                case_metrics=case_metrics,
                anomaly_level_metrics=level_metrics,
                phase_metrics=phase_metrics,
                case_train_loss=case_train_loss,
                case_val_loss=case_val_loss,
            )
            metrics.selection_score = self._get_selection_score(metrics)
            self.state.metrics_history.append(metrics)

            self.scheduler.step()

            is_best = checkpoint_acceptable and self._is_better_selection(
                metrics.selection_score, self.state.best_selection_score
            )
            if is_best:
                self.state.best_val_loss = val_loss
                self.state.best_val_mae = val_mae
                self.state.best_val_auc = val_auc
                self.state.best_epoch = epoch
                self.state.best_selection_score = metrics.selection_score
                self.state.best_checkpoint_acceptable = True
                self.state.patience_counter = 0
                self._save_model(save_dir / "best_model.pt")

            self._print_metrics(metrics, is_best, val_mae)

            if self.early_stopping(metrics.selection_score):
                print(f"Early stopping at epoch {epoch}")
                break

        self._save_history(save_dir / "training_history.json")
        self.plot_loss_curves(save_dir / "loss_curves.png")
        return self.state

    def train_epoch(
        self,
        train_loader: DataLoader,
    ) -> tuple[float, float, float, float, float, float, dict[str, float]]:
        """训练一个epoch

        Returns:
            (loss, mae, acc, f1, precision, recall, case_train_loss)
        """
        self.model.train()
        total_loss = 0.0
        all_preds = []
        all_labels = []

        # 按case分类收集loss
        case_loss_sum: dict[str, float] = {"case1": 0.0, "case2": 0.0, "case3": 0.0}
        case_count: dict[str, int] = {"case1": 0, "case2": 0, "case3": 0}

        use_amp = self.scaler is not None

        for batch in train_loader:
            graph = batch["graph"].to(self.device)
            labels = batch["labels"].to(self.device)  # (B, N_biz)

            # 获取特征
            static_features = {
                k: v.to(self.device) for k, v in batch["node_features"].items()
            }
            temporal_features = {
                k: v.to(self.device) for k, v in batch["temporal_features"].items()
            }

            self.optimizer.zero_grad()

            # 混合精度训练
            if use_amp:
                with torch.amp.autocast('cuda'):
                    output: TemporalModelOutput = self.model(
                        graph, static_features, temporal_features
                    )
                    out_sub = (
                        self.model.forward_subgraph(graph, static_features, temporal_features)
                        if self.loss_type in {"tacm_scr", "tacm_scr_abvd"}
                        else None
                    )

                    if len(output.risk_prob) == 0:
                        continue

                    num_biz = graph.num_nodes("Vbiz")
                    calling_edge_index, upstream_edge_index = self._build_topology_edges(graph)

                    # 对每个样本单独计算 TACM Loss，然后求平均
                    batch_size = output.risk_logit.shape[0]
                    loss = 0.0
                    for i in range(batch_size):
                        sample_loss = self._sample_loss(
                            output.risk_logit[i],  # (N_biz,)
                            None if out_sub is None else out_sub.risk_logit[i],
                            labels[i].float(),      # (N_biz,)
                            calling_edge_index,
                            upstream_edge_index,
                            num_biz,
                        )
                        loss = loss + sample_loss
                    loss = loss / batch_size

                # 缩放损失并反向传播
                self.scaler.scale(loss).backward()
                self.scaler.unscale_(self.optimizer)
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                output: TemporalModelOutput = self.model(
                    graph, static_features, temporal_features
                )
                out_sub = (
                    self.model.forward_subgraph(graph, static_features, temporal_features)
                    if self.loss_type in {"tacm_scr", "tacm_scr_abvd"}
                    else None
                )

                if len(output.risk_prob) == 0:
                    continue

                num_biz = graph.num_nodes("Vbiz")
                calling_edge_index, upstream_edge_index = self._build_topology_edges(graph)

                # 对每个样本单独计算 TACM Loss，然后求平均
                batch_size = output.risk_logit.shape[0]
                loss = 0.0
                for i in range(batch_size):
                    sample_loss = self._sample_loss(
                        output.risk_logit[i],  # (N_biz,)
                        None if out_sub is None else out_sub.risk_logit[i],
                        labels[i].float(),      # (N_biz,)
                        calling_edge_index,
                        upstream_edge_index,
                        num_biz,
                    )
                    loss = loss + sample_loss
                loss = loss / batch_size

                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()

            total_loss += loss.item()

            probs = output.risk_prob.detach().cpu().numpy()
            all_preds.extend(probs.flatten())
            all_labels.extend(labels.cpu().numpy().flatten())

            # 收集按case分类的loss
            metadata_list = batch.get("metadata", [])
            for i in range(len(metadata_list)):
                meta = metadata_list[i]
                case = meta.case
                # 计算该样本的loss (TACM Loss 需要 edge_index)
                sample_loss = self._sample_loss(
                    output.risk_logit[i],
                    None if out_sub is None else out_sub.risk_logit[i],
                    labels[i].float(),
                    calling_edge_index,
                    upstream_edge_index,
                    num_biz,
                ).item()
                if case in case_loss_sum:
                    case_loss_sum[case] += sample_loss
                    case_count[case] += 1

        avg_loss = total_loss / max(1, len(train_loader))

        mae, f1, precision, recall, acc = self._compute_metrics(
            all_preds, all_labels
        )

        # 计算每个case的平均loss
        case_train_loss = {}
        for case in case_loss_sum:
            if case_count[case] > 0:
                case_train_loss[case] = case_loss_sum[case] / case_count[case]
            else:
                case_train_loss[case] = 0.0

        return avg_loss, mae, acc, f1, precision, recall, case_train_loss

    @torch.no_grad()
    def validate(
        self,
        val_loader: DataLoader,
    ) -> tuple[float, float, float, float, float, float, float, list, list, list, dict[str, float], dict[str, dict[str, float]]]:
        """验证模型

        Returns:
            (loss, mae, acc, f1, auc, precision, recall, case_metrics, anomaly_level_metrics, phase_metrics, case_val_loss, threshold_scan)
        """
        self.model.eval()
        total_loss = 0.0
        all_preds = []
        all_labels = []
        all_probs = []

        # 按case分类收集
        case_data: dict[str, dict] = {
            "case1": {"preds": [], "labels": []},
            "case2": {"preds": [], "labels": []},
            "case3": {"preds": [], "labels": []},
        }

        # 按case分类收集loss
        case_loss_sum: dict[str, float] = {"case1": 0.0, "case2": 0.0, "case3": 0.0}
        case_count: dict[str, int] = {"case1": 0, "case2": 0, "case3": 0}

        # 按异常层次分类收集
        level_data: dict[str, dict] = {
            "host_anom": {"preds": [], "labels": []},
            "vm_anom": {"preds": [], "labels": []},
            "app_anom": {"preds": [], "labels": []},
            "normal": {"preds": [], "labels": []},
        }

        # 按窗口阶段分类收集
        phase_data: dict[str, dict] = {
            "pre": {"preds": [], "labels": []},
            "during": {"preds": [], "labels": []},
            "post": {"preds": [], "labels": []},
        }

        for batch in val_loader:
            graph = batch["graph"].to(self.device)
            labels = batch["labels"].to(self.device)

            static_features = {
                k: v.to(self.device) for k, v in batch["node_features"].items()
            }
            temporal_features = {
                k: v.to(self.device) for k, v in batch["temporal_features"].items()
            }

            output: TemporalModelOutput = self.model(
                graph, static_features, temporal_features
            )
            out_sub = (
                self.model.forward_subgraph(graph, static_features, temporal_features)
                if self.loss_type in {"tacm_scr", "tacm_scr_abvd"}
                else None
            )

            if len(output.risk_prob) == 0:
                continue

            num_biz = graph.num_nodes("Vbiz")
            calling_edge_index, upstream_edge_index = self._build_topology_edges(graph)

            # 对每个样本单独计算 TACM Loss，然后求平均
            batch_size = output.risk_logit.shape[0]
            loss = 0.0
            for i in range(batch_size):
                sample_loss = self._sample_loss(
                    output.risk_logit[i],
                    None if out_sub is None else out_sub.risk_logit[i],
                    labels[i].float(),
                    calling_edge_index,
                    upstream_edge_index,
                    num_biz,
                )
                loss = loss + sample_loss
            loss = loss / batch_size

            total_loss += loss.item()

            probs_np = output.risk_prob.detach().cpu().numpy()
            labels_np = labels.cpu().numpy()

            # 收集整体指标
            all_preds.extend(probs_np.flatten().tolist())
            all_labels.extend(labels_np.flatten().tolist())
            all_probs.extend(probs_np.flatten().tolist())

            # 收集按case分类的loss
            metadata_list = batch.get("metadata", [])
            for i in range(len(metadata_list)):
                meta = metadata_list[i]
                case = meta.case
                # 计算该样本的loss (TACM Loss 需要 edge_index)
                sample_loss = self._sample_loss(
                    output.risk_logit[i],
                    None if out_sub is None else out_sub.risk_logit[i],
                    labels[i].float(),
                    calling_edge_index,
                    upstream_edge_index,
                    num_biz,
                ).item()
                if case in case_loss_sum:
                    case_loss_sum[case] += sample_loss
                    case_count[case] += 1

            # 收集多维指标
            metadata_list = batch.get("metadata", [])
            phase_list = batch.get("phase", ["unknown"] * len(metadata_list))

            for i in range(len(metadata_list)):
                meta = metadata_list[i]
                phase = phase_list[i] if i < len(phase_list) else "unknown"

                # 按case分类
                case = meta.case
                if case in case_data:
                    case_data[case]["preds"].extend(probs_np[i].flatten().tolist())
                    case_data[case]["labels"].extend(labels_np[i].flatten().tolist())

                # 按异常层次分类
                if meta.host_has_anom == 1:
                    level_data["host_anom"]["preds"].extend(probs_np[i].flatten().tolist())
                    level_data["host_anom"]["labels"].extend(labels_np[i].flatten().tolist())
                if meta.vm_has_anom == 1:
                    level_data["vm_anom"]["preds"].extend(probs_np[i].flatten().tolist())
                    level_data["vm_anom"]["labels"].extend(labels_np[i].flatten().tolist())
                if meta.app_has_anom == 1:
                    level_data["app_anom"]["preds"].extend(probs_np[i].flatten().tolist())
                    level_data["app_anom"]["labels"].extend(labels_np[i].flatten().tolist())
                if meta.host_has_anom == 0 and meta.vm_has_anom == 0 and meta.app_has_anom == 0:
                    level_data["normal"]["preds"].extend(probs_np[i].flatten().tolist())
                    level_data["normal"]["labels"].extend(labels_np[i].flatten().tolist())

                # 按窗口阶段分类
                if phase in phase_data:
                    phase_data[phase]["preds"].extend(probs_np[i].flatten().tolist())
                    phase_data[phase]["labels"].extend(labels_np[i].flatten().tolist())

        avg_loss = total_loss / max(1, len(val_loader))

        mae, f1, precision, recall, acc = self._compute_metrics(
            all_preds, all_labels
        )

        try:
            from sklearn.metrics import roc_auc_score

            labels_arr = np.array(all_labels)
            binary_labels = (labels_arr >= self.label_threshold).astype(int)
            if len(np.unique(binary_labels)) >= 2:
                val_auc = float(roc_auc_score(binary_labels, np.array(all_probs)))
            else:
                val_auc = 0.5
        except Exception:
            val_auc = 0.5

        threshold_scan = self._compute_threshold_scan(all_probs, all_labels)

        # 计算case分类指标
        case_metrics_list = []
        for case, data in case_data.items():
            mae_c, f1_c, prec_c, rec_c, _ = self._compute_metrics(data["preds"], data["labels"])
            tp, fp, tn, fn = self._compute_confusion(data["preds"], data["labels"])
            case_metrics_list.append(CaseMetrics(
                case=case,
                f1=f1_c,
                precision=prec_c,
                recall=rec_c,
                tp=tp,
                fp=fp,
                tn=tn,
                fn=fn,
                total_samples=len(data["preds"]),
                total_positives=sum(data["labels"]) if data["labels"] else 0,
            ))

        # 计算异常层次指标
        level_metrics_list = []
        for level, data in level_data.items():
            mae_l, f1_l, prec_l, rec_l, _ = self._compute_metrics(data["preds"], data["labels"])
            level_metrics_list.append(AnomalyLevelMetrics(
                level=level,
                f1=f1_l,
                precision=prec_l,
                recall=rec_l,
                tp=0, fp=0, tn=0, fn=0,  # 简化
                num_samples=len(data["labels"]),
            ))

        # 计算窗口阶段指标
        phase_metrics_list = []
        for phase, data in phase_data.items():
            mae_p, f1_p, prec_p, rec_p, _ = self._compute_metrics(data["preds"], data["labels"])
            phase_metrics_list.append(PhaseMetrics(
                phase=phase,
                f1=f1_p,
                precision=prec_p,
                recall=rec_p,
                num_samples=len(data["labels"]),
            ))

        # 计算每个case的平均loss
        case_val_loss = {}
        for case in case_loss_sum:
            if case_count[case] > 0:
                case_val_loss[case] = case_loss_sum[case] / case_count[case]
            else:
                case_val_loss[case] = 0.0

        return avg_loss, mae, acc, f1, val_auc, precision, recall, case_metrics_list, level_metrics_list, phase_metrics_list, case_val_loss, threshold_scan

    def _compute_metrics(
        self,
        preds: list[float],
        labels: list[float],
        threshold: float | None = None,
    ) -> tuple[float, float, float, float, float]:
        """计算评估指标 (mae, f1, precision, recall, accuracy)

        支持软标签和二元标签:
        - 软标签: 计算 MAE 作为主要连续精度指标
        - 二元标签: 计算分类指标 (F1, Precision, Recall, Accuracy)
        """

        preds_arr = np.array(preds)
        labels_arr = np.array(labels)

        if len(preds_arr) == 0:
            return 0.0, 0.0, 0.0, 0.0, 0.0

        # 计算 MAE (连续精度)
        mae = float(np.mean(np.abs(preds_arr - labels_arr)))

        f1, precision, recall, accuracy, _, _, _, _ = self._compute_threshold_stats(
            preds_arr, labels_arr, threshold=threshold
        )

        return mae, float(f1), float(precision), float(recall), float(accuracy)

    def _compute_confusion(
        self,
        preds: list[float],
        labels: list[float],
        threshold: float | None = None,
    ) -> tuple[int, int, int, int]:
        """计算混淆矩阵元素 TP, FP, TN, FN

        支持软标签：使用阈值0.5将连续值转换为二值
        """

        preds_arr = np.array(preds)
        labels_arr = np.array(labels)

        _, _, _, _, tp, fp, tn, fn = self._compute_threshold_stats(
            preds_arr, labels_arr, threshold=threshold
        )
        return tp, fp, tn, fn

    def fit(
        self,
        dataset: TenantDataset,
        val_split: float = 0.2,
        num_epochs: int = 100,
        save_dir: Path | None = None,
        seed: int = 42,
    ) -> TrainingState:
        """训练模型

        Args:
            dataset: 训练数据集
            val_split: 验证集比例
            num_epochs: 训练轮数
            save_dir: 保存目录
            seed: 随机种子

        Returns:
            训练状态
        """
        # 设置随机种子确保可复现
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        if save_dir is None:
            save_dir = Path("checkpoints")

        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        # 划分数据集：按 record 分组，最后几个 record 作为验证集
        # 收集所有 record_id 及其对应的样本索引
        record_to_indices: dict[str, list[int]] = {}
        for i in range(len(dataset)):
            record_id = dataset[i].metadata.global_record_id or dataset[i].metadata.record_id
            if record_id not in record_to_indices:
                record_to_indices[record_id] = []
            record_to_indices[record_id].append(i)

        # 按 record_id 排序（确保可复现性）
        sorted_records = sorted(record_to_indices.keys())

        # 计算验证集需要的 record 数量
        num_records = len(sorted_records)
        num_val_records = max(1, int(num_records * val_split))

        # 最后 num_val_records 个 record 作为验证集
        val_records = sorted_records[-num_val_records:]
        train_records = sorted_records[:-num_val_records]

        val_indices = []
        for record_id in val_records:
            val_indices.extend(record_to_indices[record_id])

        train_indices = []
        for record_id in train_records:
            train_indices.extend(record_to_indices[record_id])

        print(f"[数据集划分] 总样本数: {len(dataset)}, 总record数: {num_records}")
        print(f"[数据集划分] 训练集: {len(train_indices)} 样本 ({len(train_records)} records: {train_records[:3]}...)")
        print(f"[数据集划分] 验证集: {len(val_indices)} 样本 ({len(val_records)} records: {val_records})")

        fit_context = {
            "seed": seed,
            "val_split": val_split,
            "train_records": train_records,
            "val_records": val_records,
            "train_samples": len(train_indices),
            "val_samples": len(val_indices),
            "loss_type": self.loss_type,
            "abvd_upstream_view": self.abvd_upstream_view,
            "eval_threshold": self.eval_threshold,
            "selection_metric": self.selection_metric,
            "label_mode": getattr(dataset, "label_mode", "impact_static"),
        }

        train_subset = [dataset[i] for i in train_indices]
        val_subset = [dataset[i] for i in val_indices]
        return self._fit_subsets(
            train_subset=train_subset,
            val_subset=val_subset,
            num_epochs=num_epochs,
            save_dir=save_dir,
            seed=seed,
            fit_context=fit_context,
            segment_aware_batching=False,
        )

    def fit_segment_loso(
        self,
        dataset,
        val_segment: str,
        num_epochs: int = 100,
        save_dir: Path | None = None,
        seed: int = 42,
    ) -> TrainingState:
        """Train on all included segments except one validation segment."""
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        if save_dir is None:
            save_dir = Path("checkpoints")

        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        train_subset, val_subset, split_meta = dataset.split_by_segment(val_segment=val_segment)
        print(f"[Segment Split] included segments: {dataset.included_segment_ids}")
        print(f"[Segment Split] train segments : {split_meta['train_segment_ids']}")
        print(f"[Segment Split] val segment    : {split_meta['val_segment_id']}")
        print(f"[Segment Split] train records  : {sum(split_meta['segment_record_counts'][s] for s in split_meta['train_segment_ids'])}")
        print(f"[Segment Split] val records    : {split_meta['segment_record_counts'][split_meta['val_segment_id']]}")

        fit_context = {
            "seed": seed,
            "loss_type": self.loss_type,
            "abvd_upstream_view": self.abvd_upstream_view,
            "eval_threshold": self.eval_threshold,
            "selection_metric": self.selection_metric,
            "label_mode": getattr(dataset, "label_mode", "impact_static"),
            "train_segment_ids": split_meta["train_segment_ids"],
            "val_segment_id": split_meta["val_segment_id"],
            "num_train_segments": split_meta["num_train_segments"],
            "num_val_segments": split_meta["num_val_segments"],
            "segment_sample_counts": split_meta["segment_sample_counts"],
            "segment_record_counts": split_meta["segment_record_counts"],
            "included_segment_ids": split_meta["included_segment_ids"],
            "train_records": sorted({sample.metadata.global_record_id for sample in train_subset}),
            "val_records": sorted({sample.metadata.global_record_id for sample in val_subset}),
            "train_samples": len(train_subset),
            "val_samples": len(val_subset),
        }
        return self._fit_subsets(
            train_subset=train_subset,
            val_subset=val_subset,
            num_epochs=num_epochs,
            save_dir=save_dir,
            seed=seed,
            fit_context=fit_context,
            segment_aware_batching=True,
        )

    def _print_metrics(self, metrics: TrainingMetrics, is_best: bool, val_mae: float = None) -> None:
        """打印训练指标"""
        best_mark = " *BEST*" if is_best else ""
        acceptable_mark = "" if metrics.checkpoint_acceptable else " [F1=0, checkpoint rejected]"
        print(
            f"Epoch {metrics.epoch:3d} | "
            f"Train Loss: {metrics.train_loss:.4f} | "
            f"Train MAE: {metrics.train_mae:.4f} | "
            f"Val Loss: {metrics.val_loss:.4f} | "
            f"Val MAE: {val_mae:.4f} | "
            f"Val Acc: {metrics.val_acc:.4f} | "
            f"Val F1: {metrics.val_f1:.4f} | "
            f"Val AUC: {metrics.val_auc:.4f} | "
            f"P: {metrics.precision:.4f} | "
            f"R: {metrics.recall:.4f} | "
            f"Sel({metrics.selection_metric}): {metrics.selection_score:.4f} | "
            f"LR: {metrics.learning_rate:.2e}"
            f"{acceptable_mark}"
            f"{best_mark}"
        )

        # 打印case分类指标（仅在关键epoch或best时）
        if metrics.case_metrics and is_best:
            print("  [Case Metrics]")
            for cm in metrics.case_metrics:
                if cm.total_samples > 0:
                    print(f"    {cm.case}: F1={cm.f1:.4f}, P={cm.precision:.4f}, R={cm.recall:.4f}, TP={cm.tp}, FP={cm.fp}, FN={cm.fn}")

        # 打印异常层次指标（仅在best时）
        if metrics.anomaly_level_metrics and is_best:
            print("  [Anomaly Level Metrics]")
            for am in metrics.anomaly_level_metrics:
                if am.num_samples > 0:
                    print(f"    {am.level}: F1={am.f1:.4f}, P={am.precision:.4f}, R={am.recall:.4f}")

        # 打印窗口阶段指标（仅在best时）
        if metrics.phase_metrics and is_best:
            print("  [Phase Metrics]")
            for pm in metrics.phase_metrics:
                if pm.num_samples > 0:
                    print(f"    {pm.phase}: F1={pm.f1:.4f}, P={pm.precision:.4f}, R={pm.recall:.4f}")

    def _save_model(self, path: Path) -> None:
        """保存模型"""
        torch.save(self.model.state_dict(), path)

    def _save_history(self, path: Path) -> None:
        """保存训练历史"""
        history = [m.to_dict() for m in self.state.metrics_history]
        acceptable_history = [m for m in self.state.metrics_history if m.checkpoint_acceptable]
        candidate_history = acceptable_history or self.state.metrics_history
        best_by_mae = min(candidate_history, key=lambda m: m.val_mae) if candidate_history else None
        best_by_auc = max(candidate_history, key=lambda m: m.val_auc) if candidate_history else None
        last_epoch = self.state.metrics_history[-1] if self.state.metrics_history else None
        payload = {
            "config": self.last_fit_context,
            "summary": {
                "best_epoch": self.state.best_epoch,
                "best_val_loss": self.state.best_val_loss,
                "best_val_mae": self.state.best_val_mae,
                "best_val_auc": self.state.best_val_auc,
                "best_selection_metric": self.selection_metric,
                "best_selection_score": self.state.best_selection_score,
                "best_checkpoint_acceptable": self.state.best_checkpoint_acceptable,
                "num_epochs_ran": len(self.state.metrics_history),
                "best_by_mae": self._metrics_snapshot(best_by_mae) if best_by_mae else None,
                "best_by_auc": self._metrics_snapshot(best_by_auc) if best_by_auc else None,
                "last_epoch": self._metrics_snapshot(last_epoch) if last_epoch else None,
                "selection_diagnostics": {
                    "best_by_mae_epoch": best_by_mae.epoch if best_by_mae else None,
                    "best_by_auc_epoch": best_by_auc.epoch if best_by_auc else None,
                    "best_by_mae_auc_gap": (
                        float(best_by_auc.val_auc - best_by_mae.val_auc)
                        if best_by_mae and best_by_auc else None
                    ),
                },
            },
            "history": history,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def plot_loss_curves(self, save_path: Path | str | None = None) -> None:
        """绘制训练过程中的loss曲线，包括总loss和各case的loss

        Args:
            save_path: 图片保存路径，默认为 None 时保存到 checkpoints/loss_curves.png
        """
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

        if not self.state.metrics_history:
            print("No metrics history to plot.")
            return

        epochs = [m.epoch for m in self.state.metrics_history]
        train_loss = [m.train_loss for m in self.state.metrics_history]
        val_loss = [m.val_loss for m in self.state.metrics_history]

        # 获取每个case的训练和验证loss
        case_train_losses: dict[str, list] = {"case1": [], "case2": [], "case3": []}
        case_val_losses: dict[str, list] = {"case1": [], "case2": [], "case3": []}

        for m in self.state.metrics_history:
            for case in ["case1", "case2", "case3"]:
                case_train_losses[case].append(m.case_train_loss.get(case, 0.0))
                case_val_losses[case].append(m.case_val_loss.get(case, 0.0))

        # 创建图表
        fig, axes = plt.subplots(2, 1, figsize=(12, 10))

        # 子图1: 总loss曲线
        ax1 = axes[0]
        ax1.plot(epochs, train_loss, "b-", label="Train Loss", linewidth=2)
        ax1.plot(epochs, val_loss, "r-", label="Val Loss", linewidth=2)
        ax1.set_xlabel("Epoch", fontsize=12)
        ax1.set_ylabel("Loss", fontsize=12)
        ax1.set_title("Training and Validation Loss", fontsize=14)
        ax1.legend(fontsize=10)
        ax1.grid(True, alpha=0.3)

        # 子图2: 按case分类的loss曲线 (train)
        ax2 = axes[1]
        colors = {"case1": "#1f77b4", "case2": "#ff7f0e", "case3": "#2ca02c"}
        linestyles = {"train": "-", "val": "--"}

        for case in ["case1", "case2", "case3"]:
            ax2.plot(epochs, case_train_losses[case], color=colors[case],
                    linestyle=linestyles["train"], label=f"{case} Train",
                    linewidth=2, marker="o", markersize=4)
            ax2.plot(epochs, case_val_losses[case], color=colors[case],
                    linestyle=linestyles["val"], label=f"{case} Val",
                    linewidth=2, marker="x", markersize=4)

        ax2.set_xlabel("Epoch", fontsize=12)
        ax2.set_ylabel("Loss", fontsize=12)
        ax2.set_title("Per-Case Training and Validation Loss", fontsize=14)
        ax2.legend(fontsize=9, ncol=3, loc="upper right")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()

        if save_path is None:
            save_path = Path("checkpoints/loss_curves.png")
        else:
            save_path = Path(save_path)

        save_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Loss curves saved to {save_path}")


def train_tenant_model(
    data_root: str | Path,
    tenant_id: str = "tenant_0000",
    hidden_dim: int = 128,  # 方案要求128维隐藏层
    learning_rate: float = 0.001,
    num_epochs: int = 100,
    window_size: int = 32,
    device: str | torch.device = "cuda" if torch.cuda.is_available() else "cpu",
) -> TenantTrainer:
    """训练租户模型的便捷函数

    Args:
        data_root: 数据根目录
        tenant_id: 租户ID
        hidden_dim: 隐藏层维度
        learning_rate: 学习率
        num_epochs: 训练轮数
        window_size: 时序窗口大小
        device: 训练设备

    Returns:
        训练好的训练器
    """
    from ..data.tenant_dataset import load_tenant_dataset

    # 加载数据集
    dataset = load_tenant_dataset(
        data_root=data_root,
        tenant_id=tenant_id,
        window_size=window_size,
    )

    # 获取特征维度
    static_dims = dataset.get_feature_dims()
    temporal_dims = dataset.get_temporal_feature_dims()

    # 创建模型
    model = TenantRiskPredictor(
        static_dims=static_dims,
        temporal_dims=temporal_dims,
        hidden_dim=hidden_dim,
        num_layers=2,
        dropout=0.1,
    )

    # 训练
    trainer = TenantTrainer(
        model=model,
        learning_rate=learning_rate,
        device=device,
    )

    trainer.fit(dataset, num_epochs=num_epochs)

    return trainer


def evaluate_soft_labels(
    logits: torch.Tensor,
    y_soft: torch.Tensor,
    threshold: float = 0.5,
) -> dict[str, float]:
    """双轨制评估框架

    同时评估:
    1. 连续精度 (MAE): 预测概率与软标签的接近程度
    2. 阈值分类 (Accuracy, Precision, Recall, F1): 二分类性能

    Args:
        logits: 模型预测值，shape (N,)，未经 sigmoid 的原始 logits
        y_soft: 软标签，shape (N,)，值为 [0, 1] 连续的 impact_score
        threshold: 分类阈值

    Returns:
        包含连续精度和分类指标的字典
    """
    import torch.nn.functional as F
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

    # 确保数据在 CPU 上
    if logits.is_cuda:
        logits = logits.cpu()
    if y_soft.is_cuda:
        y_soft = y_soft.cpu()

    # 转换为 numpy
    logits_np = logits.detach().flatten().numpy()
    y_soft_np = y_soft.detach().flatten().numpy()

    # 计算概率
    p_risk = 1.0 / (1.0 + np.exp(-logits_np))  # sigmoid
    p_risk = np.clip(p_risk, 0.0, 1.0)

    # 1. 连续精度 (MAE)
    mae = float(np.mean(np.abs(p_risk - y_soft_np)))

    # 2. 阈值分类指标
    pred_label = (p_risk >= threshold).astype(int)
    true_label = (y_soft_np >= threshold).astype(int)

    # 处理边界情况
    if len(np.unique(pred_label)) == 1 and len(np.unique(true_label)) == 1:
        # 全部预测为同一类
        if pred_label[0] == true_label[0]:
            accuracy = 1.0
        else:
            accuracy = 0.0
    else:
        accuracy = accuracy_score(true_label, pred_label)

    precision = precision_score(true_label, pred_label, zero_division=0)
    recall = recall_score(true_label, pred_label, zero_division=0)
    f1 = f1_score(true_label, pred_label, zero_division=0)

    # 计算 AUC-ROC（如果有足够的类别变化）
    try:
        from sklearn.metrics import roc_auc_score
        if len(np.unique(y_soft_np)) > 1 and len(np.unique(pred_label)) > 1:
            auc_roc = roc_auc_score(y_soft_np, p_risk)
        else:
            auc_roc = 0.5
    except Exception:
        auc_roc = 0.5

    return {
        "mae": mae,
        "accuracy": float(accuracy),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "auc_roc": float(auc_roc),
    }
