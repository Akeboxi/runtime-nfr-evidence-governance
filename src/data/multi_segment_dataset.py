"""Multi-segment dataset wrapper for dynamic-topology training."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .tenant_dataset import TenantDataset, TemporalGraphData


@dataclass
class SegmentDatasetInfo:
    segment_id: str
    segment_index: int
    tenant_id: str
    tenant_dir: Path
    start_time: str
    end_time: str
    node_fault_count: int
    total_samples: int
    dataset_size: int
    record_count: int


class MultiSegmentTenantDataset:
    """Wrap multiple segment tenants into one sample-level dataset."""

    def __init__(
        self,
        segment_root: str | Path,
        window_size: int = 32,
        stride: int = 16,
        use_loess_residual: str | set[str] = False,
        enable_dynamic_metrics: bool = True,
        label_mode: str = "impact_static",
        include_zero_fault_segments: bool = False,
    ) -> None:
        self.segment_root = Path(segment_root)
        self.window_size = window_size
        self.stride = stride
        self.use_loess_residual = use_loess_residual
        self.enable_dynamic_metrics = enable_dynamic_metrics
        self.label_mode = label_mode
        self.include_zero_fault_segments = include_zero_fault_segments

        self.segment_dirs = self._discover_segment_dirs()
        self.segment_infos: list[SegmentDatasetInfo] = []
        self.segment_datasets: dict[str, TenantDataset] = {}
        self.samples: list[TemporalGraphData] = []
        self.segment_sample_counts: dict[str, int] = {}
        self.segment_record_counts: dict[str, int] = {}
        self.segment_graph_node_counts: dict[str, dict[str, int]] = {}

        self._load_segments()
        if not self.samples:
            raise RuntimeError(
                f"No segment samples loaded from {self.segment_root}. "
                "Check that segment tenants contain records/ with metrics."
            )

    def _discover_segment_dirs(self) -> list[Path]:
        if not self.segment_root.exists():
            raise FileNotFoundError(f"Segment root not found: {self.segment_root}")
        return sorted(
            [
                path for path in self.segment_root.iterdir()
                if path.is_dir() and path.name.startswith("tenant_ccf_0606_0614_seg_")
            ]
        )

    def _read_segment_meta(self, tenant_dir: Path) -> dict[str, Any]:
        import json

        meta_path = tenant_dir / "segment_meta.json"
        with open(meta_path, encoding="utf-8") as handle:
            return json.load(handle)

    def _load_segments(self) -> None:
        for segment_index, tenant_dir in enumerate(self.segment_dirs):
            meta = self._read_segment_meta(tenant_dir)
            segment_id = meta["segment_id"]
            has_records = (tenant_dir / "records").exists()
            node_fault_count = int(meta.get("node_fault_count", 0))

            if not include_segment(
                include_zero_fault_segments=self.include_zero_fault_segments,
                has_records=has_records,
                node_fault_count=node_fault_count,
            ):
                continue

            dataset = TenantDataset(
                tenant_dir=tenant_dir,
                window_size=self.window_size,
                stride=self.stride,
                normalize=True,
                use_loess_residual=self.use_loess_residual,
                enable_dynamic_metrics=self.enable_dynamic_metrics,
                label_mode=self.label_mode,
            )
            if len(dataset) == 0:
                continue

            record_count = len([path for path in dataset.metadata.records_dir.iterdir() if path.is_dir() and path.name.startswith("record_")])
            info = SegmentDatasetInfo(
                segment_id=segment_id,
                segment_index=segment_index,
                tenant_id=dataset.metadata.tenant_id,
                tenant_dir=tenant_dir,
                start_time=meta.get("start_time", ""),
                end_time=meta.get("end_time", ""),
                node_fault_count=node_fault_count,
                total_samples=int(meta.get("total_samples", 0)),
                dataset_size=len(dataset),
                record_count=record_count,
            )
            self.segment_infos.append(info)
            self.segment_datasets[segment_id] = dataset
            self.segment_sample_counts[segment_id] = len(dataset)
            self.segment_record_counts[segment_id] = record_count
            self.segment_graph_node_counts[segment_id] = {
                "Vphy": int(dataset.graph.num_nodes("Vphy")),
                "Vvm": int(dataset.graph.num_nodes("Vvm")),
                "Vbiz": int(dataset.graph.num_nodes("Vbiz")),
            }

            for sample in dataset.samples:
                meta_copy = replace(
                    sample.metadata,
                    segment_id=segment_id,
                    segment_index=segment_index,
                    segment_start_time=info.start_time,
                    segment_end_time=info.end_time,
                    global_record_id=f"{segment_id}/{sample.metadata.record_id}",
                )
                self.samples.append(
                    TemporalGraphData(
                        graph=sample.graph,
                        node_features=sample.node_features,
                        temporal_features=sample.temporal_features,
                        labels=sample.labels,
                        node_ids=sample.node_ids,
                        metadata=meta_copy,
                        change_window=sample.change_window,
                        phase=sample.phase,
                    )
                )

    @property
    def included_segment_ids(self) -> list[str]:
        return [info.segment_id for info in self.segment_infos]

    @property
    def total_record_count(self) -> int:
        return sum(self.segment_record_counts.values())

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> TemporalGraphData:
        return self.samples[idx]

    def get_feature_dims(self) -> dict[str, int]:
        first_dataset = self.segment_datasets[self.included_segment_ids[0]]
        return first_dataset.get_feature_dims()

    def get_temporal_feature_dims(self) -> dict[str, int]:
        first_dataset = self.segment_datasets[self.included_segment_ids[0]]
        return first_dataset.get_temporal_feature_dims()

    def split_by_segment(
        self,
        val_segment: str,
    ) -> tuple[list[TemporalGraphData], list[TemporalGraphData], dict[str, Any]]:
        if val_segment not in self.included_segment_ids:
            raise ValueError(
                f"Validation segment {val_segment!r} not in included segments: {self.included_segment_ids}"
            )

        train_samples = [sample for sample in self.samples if sample.metadata.segment_id != val_segment]
        val_samples = [sample for sample in self.samples if sample.metadata.segment_id == val_segment]

        train_segment_ids = sorted({sample.metadata.segment_id for sample in train_samples})
        val_segment_ids = sorted({sample.metadata.segment_id for sample in val_samples})
        split_meta = {
            "train_segment_ids": train_segment_ids,
            "val_segment_id": val_segment,
            "num_train_segments": len(train_segment_ids),
            "num_val_segments": len(val_segment_ids),
            "segment_sample_counts": {
                segment_id: self.segment_sample_counts[segment_id]
                for segment_id in [*train_segment_ids, val_segment]
            },
            "segment_record_counts": {
                segment_id: self.segment_record_counts[segment_id]
                for segment_id in [*train_segment_ids, val_segment]
            },
            "included_segment_ids": self.included_segment_ids,
        }
        return train_samples, val_samples, split_meta


def include_segment(
    include_zero_fault_segments: bool,
    has_records: bool,
    node_fault_count: int,
) -> bool:
    if not has_records:
        return False
    if include_zero_fault_segments:
        return True
    return node_fault_count > 0


def load_multi_segment_dataset(
    segment_root: str | Path,
    window_size: int = 32,
    stride: int = 16,
    use_loess_residual: str | set[str] = False,
    enable_dynamic_metrics: bool = True,
    label_mode: str = "impact_static",
    include_zero_fault_segments: bool = False,
) -> MultiSegmentTenantDataset:
    return MultiSegmentTenantDataset(
        segment_root=segment_root,
        window_size=window_size,
        stride=stride,
        use_loess_residual=use_loess_residual,
        enable_dynamic_metrics=enable_dynamic_metrics,
        label_mode=label_mode,
        include_zero_fault_segments=include_zero_fault_segments,
    )
