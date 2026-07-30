"""Leakage-safe event-level data protocol for TopoIntent-Risk.

This module intentionally lives beside the legacy window dataset.  A sample is
one intervention event: all input telemetry ends before the event starts while
the post-event service impact remains a label only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

import dgl
import pandas as pd
import torch
from torch.utils.data import Dataset
from sklearn.model_selection import GroupKFold


CANONICAL_CHANNELS = (
    "cpu",
    "memory",
    "disk_read",
    "disk_write",
    "network_receive",
    "network_transmit",
    "process_load",
)

FAULT_FAMILIES = ("cpu", "memory", "disk", "network", "process", "config", "code", "unknown")
TARGET_TYPES = ("host", "pod", "service", "network")


@dataclass(frozen=True)
class IntentDescriptor:
    """Known intervention information available before execution."""

    category: str
    fault_type: str
    target_type: str
    target_node_ids: tuple[str, ...]
    source_node_ids: tuple[str, ...] = ()
    destination_node_ids: tuple[str, ...] = ()

    @property
    def fault_family(self) -> str:
        text = f"{self.category} {self.fault_type}".lower()
        if "cpu" in text:
            return "cpu"
        if "memory" in text or "mem" in text:
            return "memory"
        if "disk" in text or "io" in text:
            return "disk"
        if any(token in text for token in ("network", "dns", "delay", "loss", "corrupt")):
            return "network"
        if any(token in text for token in ("pod", "jvm", "kill", "failure", "exception")):
            return "process"
        if any(token in text for token in ("config", "port")):
            return "config"
        if "code" in text:
            return "code"
        return "unknown"


@dataclass(frozen=True)
class InterventionMetadata:
    event_id: str
    record_id: str
    segment_id: str
    event_start: datetime
    event_end: datetime
    pre_window_start: datetime
    topology_hash: str
    previous_topology_hash: str | None
    seconds_since_topology_change: float
    hosting_edges: frozenset[tuple[str, str]]
    source: str = "ccf"


@dataclass
class TopologyDelta:
    graph: dgl.DGLGraph
    added_edge_counts: dict[str, int]
    removed_edge_counts: dict[str, int]


@dataclass
class InterventionSample:
    current_graph: dgl.DGLGraph
    previous_graph: dgl.DGLGraph | None
    delta_graph: dgl.DGLGraph
    pre_features: dict[str, torch.Tensor]
    missing_masks: dict[str, torch.Tensor]
    labels: torch.Tensor
    node_ids: dict[str, list[str]]
    intent: IntentDescriptor
    metadata: InterventionMetadata
    has_previous_graph: bool


class InterventionDataset(Dataset[InterventionSample]):
    def __init__(self, samples: list[InterventionSample]) -> None:
        if not samples:
            raise ValueError("InterventionDataset requires at least one sample")
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> InterventionSample:
        return self.samples[index]


def _read_node_ids(graph_dir: Path, filename: str) -> list[str]:
    frame = pd.read_csv(graph_dir / "nodes" / filename)
    return frame["node_id"].astype(str).tolist()


def load_graph_from_graph_dir(graph_dir: str | Path) -> tuple[dgl.DGLGraph, dict[str, list[str]]]:
    """Load the project three-layer graph without constructing window samples."""

    graph_path = Path(graph_dir)
    raw_ids = {
        "host": _read_node_ids(graph_path, "host.csv"),
        "vm": _read_node_ids(graph_path, "vm.csv"),
        "app": _read_node_ids(graph_path, "app.csv"),
    }
    node_ids = {"Vphy": raw_ids["host"], "Vvm": raw_ids["vm"], "Vbiz": raw_ids["app"]}
    mappings = {kind: {node_id: index for index, node_id in enumerate(ids)} for kind, ids in raw_ids.items()}

    relation_specs = {
        "host_host": ("Vphy", "r_link", "Vphy", "host", "host", False),
        # CSV stores pod -> host; the model graph reverses this to host -> pod.
        "vm_host": ("Vphy", "r_hosting", "Vvm", "vm", "host", True),
        "vm_vm": ("Vvm", "r_traffic", "Vvm", "vm", "vm", False),
        "vm_app": ("Vvm", "r_deployment", "Vbiz", "vm", "app", False),
        "app_app": ("Vbiz", "r_calling", "Vbiz", "app", "app", False),
    }
    edge_data: dict[tuple[str, str, str], tuple[list[int], list[int]]] = {}
    for filename, (src_type, relation, dst_type, raw_src, raw_dst, reverse) in relation_specs.items():
        path = graph_path / "edges" / f"{filename}.csv"
        pairs: list[tuple[int, int]] = []
        if path.exists():
            for row in pd.read_csv(path).to_dict("records"):
                src_id, dst_id = str(row["src_id"]), str(row["dst_id"])
                if reverse:
                    src_id, dst_id = dst_id, src_id
                source_kind = raw_dst if reverse else raw_src
                target_kind = raw_src if reverse else raw_dst
                if src_id in mappings[source_kind] and dst_id in mappings[target_kind]:
                    pairs.append((mappings[source_kind][src_id], mappings[target_kind][dst_id]))
        edge_data[(src_type, relation, dst_type)] = (
            [pair[0] for pair in pairs],
            [pair[1] for pair in pairs],
        )
    graph = dgl.heterograph(edge_data, num_nodes_dict={kind: len(ids) for kind, ids in node_ids.items()})
    return graph, node_ids


def _edge_ids(graph: dgl.DGLGraph, node_ids: dict[str, list[str]], etype: tuple[str, str, str]) -> set[tuple[str, str]]:
    if etype not in graph.canonical_etypes:
        return set()
    src, dst = graph.edges(etype=etype)
    return {
        (node_ids[etype[0]][int(source)], node_ids[etype[2]][int(target)])
        for source, target in zip(src.tolist(), dst.tolist())
    }


def hosting_edges(graph: dgl.DGLGraph, node_ids: dict[str, list[str]]) -> frozenset[tuple[str, str]]:
    return frozenset(_edge_ids(graph, node_ids, ("Vphy", "r_hosting", "Vvm")))


def topology_hash(graph: dgl.DGLGraph, node_ids: dict[str, list[str]]) -> str:
    payload = "\n".join(f"{host}->{pod}" for host, pod in sorted(hosting_edges(graph, node_ids)))
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_topology_delta(
    previous_graph: dgl.DGLGraph | None,
    previous_node_ids: dict[str, list[str]] | None,
    current_graph: dgl.DGLGraph,
    current_node_ids: dict[str, list[str]],
) -> TopologyDelta:
    """Represent added and removed current-addressable edges as a heterograph."""

    current_index = {kind: {node_id: index for index, node_id in enumerate(ids)} for kind, ids in current_node_ids.items()}
    edge_data: dict[tuple[str, str, str], tuple[list[int], list[int]]] = {}
    added_counts: dict[str, int] = {}
    removed_counts: dict[str, int] = {}
    for etype in current_graph.canonical_etypes:
        relation = etype[1]
        current_edges = _edge_ids(current_graph, current_node_ids, etype)
        previous_edges = set()
        if previous_graph is not None and previous_node_ids is not None:
            previous_edges = _edge_ids(previous_graph, previous_node_ids, etype)
        added = current_edges - previous_edges
        removed = previous_edges - current_edges
        # Removed endpoints absent from the current snapshot cannot receive a delta feature.
        removed = {
            pair for pair in removed
            if pair[0] in current_index[etype[0]] and pair[1] in current_index[etype[2]]
        }
        for suffix, pairs, counts in (("added", added, added_counts), ("removed", removed, removed_counts)):
            delta_type = (etype[0], f"{relation}_{suffix}", etype[2])
            edge_data[delta_type] = (
                [current_index[etype[0]][source] for source, _ in pairs],
                [current_index[etype[2]][target] for _, target in pairs],
            )
            counts[relation] = len(pairs)
    graph = dgl.heterograph(edge_data, num_nodes_dict={kind: len(ids) for kind, ids in current_node_ids.items()})
    return TopologyDelta(graph=graph, added_edge_counts=added_counts, removed_edge_counts=removed_counts)


def topology_jaccard(left: Iterable[tuple[str, str]], right: Iterable[tuple[str, str]]) -> float:
    left_set, right_set = set(left), set(right)
    union = left_set | right_set
    return 1.0 if not union else len(left_set & right_set) / len(union)


def _parse_timestamp(value: str) -> datetime:
    return pd.Timestamp(value).to_pydatetime().replace(tzinfo=None)


def _channel_columns(columns: Iterable[str]) -> dict[str, list[str]]:
    out = {channel: [] for channel in CANONICAL_CHANNELS}
    for column in columns:
        lower = column.lower()
        if lower in {"node_type", "node_id", "timestamp", "time"}:
            continue
        if "cpu" in lower:
            out["cpu"].append(column)
        if "memory" in lower or "mem" in lower:
            out["memory"].append(column)
        if ("disk" in lower or "fs_" in lower) and "read" in lower:
            out["disk_read"].append(column)
        if ("disk" in lower or "fs_" in lower) and ("write" in lower or "written" in lower):
            out["disk_write"].append(column)
        if "network" in lower and ("receive" in lower or "rx" in lower):
            out["network_receive"].append(column)
        if "network" in lower and ("transmit" in lower or "tx" in lower):
            out["network_transmit"].append(column)
        if any(token in lower for token in ("process", "load", "sockstat")):
            out["process_load"].append(column)
    return out


def _load_node_pre_event_features(
    path: Path,
    event_start: datetime,
    pre_window_minutes: int,
    history_minutes: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    frame = pd.read_csv(path)
    if "timestamp" not in frame.columns:
        return torch.zeros(pre_window_minutes, len(CANONICAL_CHANNELS)), torch.zeros(pre_window_minutes, len(CANONICAL_CHANNELS))
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
    event_stamp = pd.Timestamp(event_start)
    grid = pd.date_range(end=event_stamp.floor("min") - pd.Timedelta(minutes=1), periods=pre_window_minutes, freq="min")
    cols_by_channel = _channel_columns(frame.columns)
    values = pd.DataFrame(index=frame.index)
    for channel, columns in cols_by_channel.items():
        if columns:
            values[channel] = frame[columns].apply(pd.to_numeric, errors="coerce").mean(axis=1)
        else:
            values[channel] = float("nan")
    values["timestamp"] = frame["timestamp"].dt.floor("min")
    values = values.groupby("timestamp", sort=True)[list(CANONICAL_CHANNELS)].mean()
    window = values.reindex(grid)
    history_start = event_stamp - pd.Timedelta(minutes=history_minutes)
    history = values.loc[(values.index >= history_start) & (values.index < event_stamp)]
    median = history.median(axis=0).fillna(0.0)
    scale = history.std(axis=0).replace(0.0, 1.0).fillna(1.0).clip(lower=1e-6)
    mask = window.notna().to_numpy(dtype="float32")
    normalized = ((window - median) / scale).clip(lower=-10.0, upper=10.0).fillna(0.0)
    return torch.tensor(normalized.to_numpy(dtype="float32")), torch.tensor(mask)


def _layer_pre_features(
    metrics_dir: Path,
    filenames: list[str],
    event_start: datetime,
    pre_window_minutes: int,
    history_minutes: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    features, masks = [], []
    for node_id in filenames:
        path = metrics_dir / f"{node_id}.csv"
        if path.exists():
            feature, mask = _load_node_pre_event_features(path, event_start, pre_window_minutes, history_minutes)
        else:
            feature = torch.zeros(pre_window_minutes, len(CANONICAL_CHANNELS))
            mask = torch.zeros_like(feature)
        features.append(feature)
        masks.append(mask)
    return torch.stack(features, dim=1), torch.stack(masks, dim=1)


def _label_path(record_dir: Path) -> Path | None:
    for filename in ("ccf_p_label.json", "p_label.json"):
        path = record_dir / "labels" / filename
        if path.exists():
            return path
    return None


def _app_label_id(node_id: str) -> str:
    return "redis-cart" if node_id == "redis" else node_id


def _read_labels(record_dir: Path, app_ids: list[str]) -> torch.Tensor | None:
    path = _label_path(record_dir)
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    scores = {str(app["node_id"]): float(app.get("impact_score", 0.0)) for app in payload.get("apps", [])}
    return torch.tensor([scores.get(_app_label_id(node_id), 0.0) for node_id in app_ids], dtype=torch.float32)


def _infer_target_type(label: dict[str, Any]) -> str:
    instance_type = str(label.get("instance_type") or label.get("target_type") or label.get("case") or "").lower()
    context = f"{instance_type} {label.get('fault_type', '')}".lower()
    if "network" in context:
        return "network"
    if any(token in context for token in ("node", "host")):
        return "host"
    if any(token in context for token in ("pod", "vm")):
        return "pod"
    return "service"


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def _intent_from_label(label: dict[str, Any]) -> IntentDescriptor:
    targets = _as_tuple(label.get("target_node_ids") or label.get("instance") or label.get("change_host_id") or label.get("service"))
    source = _as_tuple(label.get("source"))
    destination = _as_tuple(label.get("destination"))
    if source or destination:
        targets = tuple(dict.fromkeys([*targets, *source, *destination]))
    return IntentDescriptor(
        category=str(label.get("fault_category") or label.get("category") or "unknown"),
        fault_type=str(label.get("fault_type") or "unknown"),
        target_type=_infer_target_type(label),
        target_node_ids=targets,
        source_node_ids=source,
        destination_node_ids=destination,
    )


def _segment_id(tenant_dir: Path, meta: dict[str, Any]) -> str:
    return str(meta.get("segment_id") or tenant_dir.name.rsplit("_", 1)[-1])


def load_intervention_dataset(
    segment_root: str | Path,
    *,
    pre_window_minutes: int = 60,
    history_minutes: int = 24 * 60,
    require_labels: bool = True,
) -> InterventionDataset:
    """Create event-level samples from converted segment tenants.

    Existing Phase 1 tenants are accepted for smoke tests.  The same layout can
    later hold all 400 events without changing this public interface.
    """

    root = Path(segment_root)
    tenant_dirs = sorted(path for path in root.iterdir() if path.is_dir() and (path / "graph").exists())
    segment_graphs: list[tuple[Path, dict[str, Any], dgl.DGLGraph, dict[str, list[str]]]] = []
    for tenant_dir in tenant_dirs:
        meta_path = tenant_dir / "segment_meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        graph, node_ids = load_graph_from_graph_dir(tenant_dir / "graph")
        segment_graphs.append((tenant_dir, meta, graph, node_ids))
    segment_graphs.sort(key=lambda item: str(item[1].get("start_time", "")))

    samples: list[InterventionSample] = []
    previous: tuple[Path, dict[str, Any], dgl.DGLGraph, dict[str, list[str]]] | None = None
    for tenant_dir, segment_meta, graph, node_ids in segment_graphs:
        previous_graph = previous[2] if previous else None
        previous_node_ids = previous[3] if previous else None
        delta = build_topology_delta(previous_graph, previous_node_ids, graph, node_ids)
        current_edges = hosting_edges(graph, node_ids)
        current_hash = topology_hash(graph, node_ids)
        previous_hash = topology_hash(previous_graph, previous_node_ids) if previous_graph is not None and previous_node_ids is not None else None
        start_time = _parse_timestamp(str(segment_meta.get("start_time")))
        records_dir = tenant_dir / "records"
        for record_dir in sorted(path for path in records_dir.glob("record_*") if path.is_dir()):
            global_label_path = record_dir / "global_label.json"
            if not global_label_path.exists():
                continue
            label = json.loads(global_label_path.read_text(encoding="utf-8"))
            event_start = _parse_timestamp(str(label["change_start_time"]))
            event_end = _parse_timestamp(str(label["change_end_time"]))
            labels = _read_labels(record_dir, node_ids["Vbiz"])
            if labels is None and require_labels:
                continue
            if labels is None:
                labels = torch.zeros(len(node_ids["Vbiz"]), dtype=torch.float32)
            # Full-data conversion stores one immutable metric tree per segment
            # to avoid multiplying raw telemetry for every intervention.  The
            # legacy per-record layout remains accepted for Phase 1 artifacts.
            metrics_dir = record_dir / "metrics"
            if not metrics_dir.is_dir():
                metrics_dir = tenant_dir / "_staging" / "metrics"
            phy_feature, phy_mask = _layer_pre_features(metrics_dir / "host", node_ids["Vphy"], event_start, pre_window_minutes, history_minutes)
            vm_feature, vm_mask = _layer_pre_features(metrics_dir / "vm", node_ids["Vvm"], event_start, pre_window_minutes, history_minutes)
            app_feature = torch.zeros(pre_window_minutes, len(node_ids["Vbiz"]), len(CANONICAL_CHANNELS))
            app_mask = torch.zeros_like(app_feature)
            meta = InterventionMetadata(
                event_id=str(label.get("groundtruth_uuid") or f"{_segment_id(tenant_dir, segment_meta)}/{record_dir.name}"),
                record_id=record_dir.name,
                segment_id=_segment_id(tenant_dir, segment_meta),
                event_start=event_start,
                event_end=event_end,
                pre_window_start=event_start - timedelta(minutes=pre_window_minutes),
                topology_hash=current_hash,
                previous_topology_hash=previous_hash,
                seconds_since_topology_change=max(0.0, (event_start - start_time).total_seconds()),
                hosting_edges=current_edges,
            )
            samples.append(InterventionSample(
                current_graph=graph,
                previous_graph=previous_graph,
                delta_graph=delta.graph,
                pre_features={"Vphy": phy_feature, "Vvm": vm_feature, "Vbiz": app_feature},
                missing_masks={"Vphy": phy_mask, "Vvm": vm_mask, "Vbiz": app_mask},
                labels=labels,
                node_ids=node_ids,
                intent=_intent_from_label(label),
                metadata=meta,
                has_previous_graph=previous_graph is not None,
            ))
        previous = (tenant_dir, segment_meta, graph, node_ids)
    return InterventionDataset(samples)


def chronological_split(samples: list[InterventionSample], train_ratio: float = 0.6, val_ratio: float = 0.2) -> tuple[list[InterventionSample], list[InterventionSample], list[InterventionSample]]:
    if not 0.0 < train_ratio < 1.0 or not 0.0 < val_ratio < 1.0 or train_ratio + val_ratio >= 1.0:
        raise ValueError("train_ratio and val_ratio must be positive and sum to less than one")
    ordered = sorted(samples, key=lambda sample: sample.metadata.event_start)
    train_end = max(1, int(len(ordered) * train_ratio))
    val_end = max(train_end + 1, int(len(ordered) * (train_ratio + val_ratio)))
    return ordered[:train_end], ordered[train_end:val_end], ordered[val_end:]


def topology_group_folds(
    samples: list[InterventionSample],
    n_splits: int = 5,
    min_group_size: int = 5,
) -> list[tuple[list[int], list[int]]]:
    """Group folds over unseen hosting topologies.

    Small exact-topology groups are greedily merged into the most similar group
    by hosting-edge Jaccard similarity.  The resulting group label is local to
    this split and never enters the model.
    """

    if len(samples) < 2:
        raise ValueError("at least two samples are required for topology folds")
    groups: dict[str, list[int]] = {}
    group_edges: dict[str, frozenset[tuple[str, str]]] = {}
    for index, sample in enumerate(samples):
        key = sample.metadata.topology_hash
        groups.setdefault(key, []).append(index)
        group_edges.setdefault(key, sample.metadata.hosting_edges)
    while len(groups) > n_splits:
        small = min(groups, key=lambda key: len(groups[key]))
        if len(groups[small]) >= min_group_size:
            break
        candidates = [key for key in groups if key != small]
        target = max(candidates, key=lambda key: topology_jaccard(group_edges[small], group_edges[key]))
        groups[target].extend(groups.pop(small))
        group_edges.pop(small)
    effective_splits = min(n_splits, len(groups))
    if effective_splits < 2:
        raise ValueError("need at least two topology groups after merging")
    labels = [""] * len(samples)
    for group_id, (_, indices) in enumerate(sorted(groups.items())):
        for index in indices:
            labels[index] = f"group_{group_id}"
    splitter = GroupKFold(n_splits=effective_splits)
    indices = list(range(len(samples)))
    return [(train.tolist(), test.tolist()) for train, test in splitter.split(indices, groups=labels)]


def assert_no_future_telemetry(sample: InterventionSample) -> None:
    """Structural assertion used by tests and preparation commands.

    Features do not carry timestamps after loading; this verifies the declared
    pre-window is strictly before the event, the contract enforced by the loader.
    """

    if sample.metadata.pre_window_start >= sample.metadata.event_start:
        raise AssertionError("pre-window must start before event start")
    expected_steps = int((sample.metadata.event_start - sample.metadata.pre_window_start).total_seconds() // 60)
    for features in sample.pre_features.values():
        if features.shape[0] != expected_steps:
            raise AssertionError("feature window contains an unexpected number of time steps")
