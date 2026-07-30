"""Leakage-safe runtime NFR boundary mining and early-impact samples.

The legacy CCF pipeline produces a retrospective ``impact_score``.  This
module deliberately does not read that score.  It mines per-application
operational boundaries from telemetry strictly before an event, observes the
first few minutes after the event, and labels only a later prediction horizon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import json
from typing import Any, Iterable

import dgl
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from .intervention_dataset import (
    CANONICAL_CHANNELS,
    IntentDescriptor,
    TopologyDelta,
    build_topology_delta,
    hosting_edges,
    load_graph_from_graph_dir,
    topology_group_folds as intervention_topology_group_folds,
    topology_hash,
)


RUNTIME_APP_CHANNELS = (
    "latency_z",
    "error_z",
    "log_request_z",
    "availability",
    "latency_exceedance",
    "error_exceedance",
    "is_early_observation",
)

SENSITIVITY_QUANTILES = (0.90, 0.95, 0.99)
SENSITIVITY_PERSISTENCE = (1, 3, 5)


@dataclass(frozen=True)
class NFRBoundary:
    app_id: str
    sli: str
    history_start: datetime
    history_end: datetime
    quantile_level: float
    quantile_value: float
    median: float
    mad: float
    robust_scale: float
    threshold: float
    valid_samples: int


@dataclass(frozen=True)
class RuntimeNFRMetadata:
    event_id: str
    record_id: str
    segment_id: str
    event_start: datetime
    event_minute: datetime
    event_end: datetime
    boundary_start: datetime
    boundary_end: datetime
    input_start: datetime
    input_end: datetime
    label_start: datetime
    label_end: datetime
    topology_hash: str
    previous_topology_hash: str | None
    seconds_since_topology_change: float
    hosting_edges: frozenset[tuple[str, str]]
    fault_family: str
    target_type: str


@dataclass
class RuntimeNFRTargets:
    breach: torch.Tensor
    severity_raw: torch.Tensor


@dataclass
class RuntimeNFRSample:
    current_graph: dgl.DGLGraph
    previous_graph: dgl.DGLGraph | None
    delta_graph: dgl.DGLGraph
    features: dict[str, torch.Tensor]
    masks: dict[str, torch.Tensor]
    breach_labels: torch.Tensor
    severity_raw: torch.Tensor
    label_mask: torch.Tensor
    early_request_volume: torch.Tensor
    node_ids: dict[str, list[str]]
    boundaries: dict[str, dict[str, NFRBoundary]]
    sensitivity_targets: dict[str, RuntimeNFRTargets]
    invalid_reasons: dict[str, str]
    metadata: RuntimeNFRMetadata
    has_previous_graph: bool


@dataclass
class RuntimeNFRPrediction:
    event_id: str
    app_ids: list[str]
    breach_probability: list[float]
    severity_score: list[float]
    ranking: list[str]
    node_aggregates: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)


class RuntimeNFRDataset(Dataset[RuntimeNFRSample]):
    def __init__(self, samples: list[RuntimeNFRSample]) -> None:
        if not samples:
            raise ValueError("RuntimeNFRDataset requires at least one sample")
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> RuntimeNFRSample:
        return self.samples[index]


@dataclass(frozen=True)
class _EventInterval:
    start: datetime
    end: datetime
    impacted_apps: frozenset[str]


def _parse_timestamp(value: object) -> datetime:
    return pd.Timestamp(value).to_pydatetime().replace(tzinfo=None)


def _app_alias(node_id: str) -> str:
    return "redis-cart" if node_id == "redis" else node_id


def _safe_float_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def load_app_sli_frame(path: str | Path) -> pd.DataFrame:
    """Load a minute-indexed application SLI frame from converted CSV."""

    raw = pd.read_csv(path)
    if "timestamp" not in raw.columns:
        return pd.DataFrame(columns=["request", "latency", "error_ratio", "availability"])
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce").dt.floor("min")
    raw = raw.dropna(subset=["timestamp"])
    request = _safe_float_series(raw, "request").clip(lower=0.0)
    error = _safe_float_series(raw, "error").fillna(0.0).clip(lower=0.0)
    timeout = _safe_float_series(raw, "timeout").fillna(0.0).clip(lower=0.0)
    bad = error + timeout
    ratio = pd.Series(np.nan, index=raw.index, dtype=float)
    eligible = request > 0.0
    ratio.loc[eligible] = (bad.loc[eligible] / request.loc[eligible]).clip(lower=0.0, upper=1.0)
    frame = pd.DataFrame(
        {
            "timestamp": raw["timestamp"],
            "request": request,
            "latency": _safe_float_series(raw, "rrt").clip(lower=0.0),
            "error_ratio": ratio,
            "availability": 1.0 - ratio,
        }
    )
    return frame.groupby("timestamp", sort=True).mean(numeric_only=True)


def fit_nfr_boundary(
    values: pd.Series,
    *,
    app_id: str,
    sli: str,
    history_start: datetime,
    history_end: datetime,
    quantile: float = 0.95,
) -> NFRBoundary:
    clean = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    if clean.empty:
        quantile_value = median = mad = threshold = 0.0
        robust_scale = 1e-6
    else:
        quantile_value = float(clean.quantile(quantile))
        median = float(clean.median())
        mad = float(np.median(np.abs(clean.to_numpy() - median)))
        robust_scale = max(1.4826 * mad, 1e-6)
        threshold = max(quantile_value, median + 3.0 * robust_scale)
        # A zero-only error history intentionally yields a zero boundary: the
        # first observed failed request becomes an auditable violation signal.
        if sli == "error_ratio" and float(clean.max()) == 0.0:
            threshold = 0.0
    return NFRBoundary(
        app_id=app_id,
        sli=sli,
        history_start=history_start,
        history_end=history_end,
        quantile_level=quantile,
        quantile_value=quantile_value,
        median=median,
        mad=mad,
        robust_scale=robust_scale,
        threshold=float(threshold),
        valid_samples=int(clean.size),
    )


def _exclude_overlaps(
    frame: pd.DataFrame,
    *,
    app_id: str,
    intervals: Iterable[_EventInterval],
) -> pd.DataFrame:
    keep = pd.Series(True, index=frame.index)
    for interval in intervals:
        if app_id not in interval.impacted_apps:
            continue
        start = pd.Timestamp(interval.start).floor("min")
        end = pd.Timestamp(interval.end).ceil("min")
        keep &= ~((frame.index >= start) & (frame.index < end))
    return frame.loc[keep]


def _severity_for_boundaries(
    future: pd.DataFrame,
    latency_boundary: NFRBoundary,
    error_boundary: NFRBoundary,
    *,
    persistence: int,
) -> tuple[bool, float, int]:
    latency = future["latency"]
    error = future["error_ratio"]
    latency_bad = latency.notna() & (latency > latency_boundary.threshold)
    error_bad = error.notna() & (error > error_boundary.threshold)
    bad = latency_bad | error_bad
    exceedance = np.maximum(
        ((latency - latency_boundary.threshold) / latency_boundary.robust_scale).fillna(0.0),
        ((error - error_boundary.threshold) / error_boundary.robust_scale).fillna(0.0),
    )
    positive = np.sort(np.clip(np.asarray(exceedance, dtype=float), 0.0, 1e6))[-3:]
    severity = float(positive.mean()) if positive.size else 0.0
    return bool(int(bad.sum()) >= persistence), severity, int(future["request"].notna().sum())


def _runtime_app_features(
    frame: pd.DataFrame,
    *,
    input_grid: pd.DatetimeIndex,
    event_minute: pd.Timestamp,
    latency_boundary: NFRBoundary,
    error_boundary: NFRBoundary,
    request_history: pd.Series,
) -> tuple[torch.Tensor, torch.Tensor, float, int]:
    window = frame.reindex(input_grid)
    log_request_history = np.log1p(pd.to_numeric(request_history, errors="coerce").dropna().clip(lower=0.0))
    request_median = float(log_request_history.median()) if not log_request_history.empty else 0.0
    request_mad = float(np.median(np.abs(log_request_history - request_median))) if not log_request_history.empty else 0.0
    request_scale = max(1.4826 * request_mad, 1e-6)
    latency_z = (window["latency"] - latency_boundary.median) / latency_boundary.robust_scale
    error_z = (window["error_ratio"] - error_boundary.median) / error_boundary.robust_scale
    request_z = (np.log1p(window["request"].clip(lower=0.0)) - request_median) / request_scale
    values = pd.DataFrame(
        {
            "latency_z": latency_z,
            "error_z": error_z,
            "log_request_z": request_z,
            "availability": window["availability"],
            "latency_exceedance": (window["latency"] > latency_boundary.threshold).astype(float),
            "error_exceedance": (window["error_ratio"] > error_boundary.threshold).astype(float),
            "is_early_observation": (input_grid >= event_minute).astype(float),
        },
        index=input_grid,
    )
    masks = values.notna().astype(float)
    # Error and availability do not exist without requests.
    no_traffic = window["request"].fillna(0.0) <= 0.0
    for column in ("error_z", "availability", "error_exceedance"):
        masks.loc[no_traffic, column] = 0.0
    values = values.clip(lower=-20.0, upper=20.0).fillna(0.0)
    early = window.loc[window.index >= event_minute]
    early_request = float(early["request"].fillna(0.0).sum())
    early_observed = int(early["latency"].notna().sum())
    return (
        torch.tensor(values.to_numpy(dtype="float32")),
        torch.tensor(masks.to_numpy(dtype="float32")),
        early_request,
        early_observed,
    )


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


def _infra_runtime_features(
    path: Path,
    *,
    history_grid: pd.DatetimeIndex,
    input_grid: pd.DatetimeIndex,
    frame_cache: dict[Path, pd.DataFrame],
) -> tuple[torch.Tensor, torch.Tensor]:
    if not path.exists():
        shape = (len(input_grid), len(CANONICAL_CHANNELS))
        return torch.zeros(shape), torch.zeros(shape)
    if path not in frame_cache:
        raw = pd.read_csv(path)
        if "timestamp" not in raw.columns:
            frame_cache[path] = pd.DataFrame(columns=list(CANONICAL_CHANNELS))
        else:
            raw["timestamp"] = pd.to_datetime(raw["timestamp"], errors="coerce").dt.floor("min")
            raw = raw.dropna(subset=["timestamp"])
            values = pd.DataFrame(index=raw.index)
            for channel, columns in _channel_columns(raw.columns).items():
                values[channel] = (
                    raw[columns].apply(pd.to_numeric, errors="coerce").mean(axis=1)
                    if columns
                    else float("nan")
                )
            values["timestamp"] = raw["timestamp"]
            frame_cache[path] = values.groupby("timestamp", sort=True)[list(CANONICAL_CHANNELS)].mean()
    values = frame_cache[path]
    history = values.reindex(history_grid)
    median = history.median(axis=0).fillna(0.0)
    mad = (history - median).abs().median(axis=0).fillna(0.0)
    scale = (1.4826 * mad).clip(lower=1e-6)
    window = values.reindex(input_grid)
    mask = window.notna().to_numpy(dtype="float32")
    normalized = ((window - median) / scale).clip(-20.0, 20.0).fillna(0.0)
    return torch.tensor(normalized.to_numpy(dtype="float32")), torch.tensor(mask)


def _stack_infra_layer(
    metrics_dir: Path,
    node_ids: list[str],
    *,
    history_grid: pd.DatetimeIndex,
    input_grid: pd.DatetimeIndex,
    frame_cache: dict[Path, pd.DataFrame],
) -> tuple[torch.Tensor, torch.Tensor]:
    values, masks = [], []
    for node_id in node_ids:
        value, mask = _infra_runtime_features(
            metrics_dir / f"{node_id}.csv",
            history_grid=history_grid,
            input_grid=input_grid,
            frame_cache=frame_cache,
        )
        values.append(value)
        masks.append(mask)
    return torch.stack(values, dim=1), torch.stack(masks, dim=1)


def _targets_from_ids(
    graph: dgl.DGLGraph,
    node_ids: dict[str, list[str]],
    targets: Iterable[str],
) -> frozenset[str]:
    raw_targets = {str(value) for value in targets if value not in (None, "")}
    aliases = {_app_alias(value) for value in raw_targets}
    apps = set(node_ids["Vbiz"]) & aliases
    vm_index = {node_id: index for index, node_id in enumerate(node_ids["Vvm"])}
    app_index = {index: node_id for index, node_id in enumerate(node_ids["Vbiz"])}
    host_index = {node_id: index for index, node_id in enumerate(node_ids["Vphy"])}
    targeted_vms = {vm_index[value] for value in raw_targets if value in vm_index}
    targeted_hosts = {host_index[value] for value in raw_targets if value in host_index}
    if targeted_hosts and ("Vphy", "r_hosting", "Vvm") in graph.canonical_etypes:
        src, dst = graph.edges(etype=("Vphy", "r_hosting", "Vvm"))
        targeted_vms |= {int(v) for h, v in zip(src.tolist(), dst.tolist()) if int(h) in targeted_hosts}
    if targeted_vms and ("Vvm", "r_deployment", "Vbiz") in graph.canonical_etypes:
        src, dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))
        apps |= {app_index[int(a)] for v, a in zip(src.tolist(), dst.tolist()) if int(v) in targeted_vms}
    for target in raw_targets:
        prefix = target.rsplit("-", 1)[0]
        if prefix in node_ids["Vbiz"]:
            apps.add(prefix)
    return frozenset(apps)


def _intent_from_label(label: dict[str, Any]) -> IntentDescriptor:
    context = f"{label.get('instance_type', '')} {label.get('fault_type', '')}".lower()
    if "network" in context:
        target_type = "network"
    elif any(token in context for token in ("node", "host")):
        target_type = "host"
    elif any(token in context for token in ("pod", "vm")):
        target_type = "pod"
    else:
        target_type = "service"
    value = label.get("target_node_ids") or label.get("instance") or label.get("change_host_id") or ()
    if not isinstance(value, (list, tuple)):
        value = (value,)
    return IntentDescriptor(
        category=str(label.get("fault_category") or "unknown"),
        fault_type=str(label.get("fault_type") or "unknown"),
        target_type=target_type,
        target_node_ids=tuple(str(item) for item in value if item not in (None, "")),
    )


def _read_segment_layout(root: Path) -> list[tuple[Path, dict[str, Any], dgl.DGLGraph, dict[str, list[str]]]]:
    layouts = []
    for tenant_dir in sorted(path for path in root.iterdir() if path.is_dir() and (path / "graph").exists()):
        meta_path = tenant_dir / "segment_meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        graph, node_ids = load_graph_from_graph_dir(tenant_dir / "graph")
        layouts.append((tenant_dir, meta, graph, node_ids))
    layouts.sort(key=lambda item: str(item[1].get("start_time", "")))
    return layouts


def _collect_event_intervals(
    layouts: list[tuple[Path, dict[str, Any], dgl.DGLGraph, dict[str, list[str]]]],
) -> list[_EventInterval]:
    intervals: list[_EventInterval] = []
    for tenant_dir, _, graph, node_ids in layouts:
        for label_path in sorted((tenant_dir / "records").glob("record_*/global_label.json")):
            label = json.loads(label_path.read_text(encoding="utf-8"))
            intent = _intent_from_label(label)
            extra = label.get("source") or []
            destination = label.get("destination") or []
            if not isinstance(extra, list):
                extra = [extra]
            if not isinstance(destination, list):
                destination = [destination]
            impacted = _targets_from_ids(
                graph,
                node_ids,
                [*intent.target_node_ids, *extra, *destination],
            )
            intervals.append(
                _EventInterval(
                    start=_parse_timestamp(label["change_start_time"]),
                    end=_parse_timestamp(label["change_end_time"]),
                    impacted_apps=impacted,
                )
            )
    return intervals


def _metric_dir(tenant_dir: Path, record_dir: Path) -> Path:
    record_metrics = record_dir / "metrics"
    return record_metrics if record_metrics.is_dir() else tenant_dir / "_staging" / "metrics"


def load_runtime_nfr_dataset(
    segment_root: str | Path,
    *,
    history_minutes: int = 24 * 60,
    pre_minutes: int = 60,
    observation_minutes: int = 5,
    horizon_minutes: int = 15,
    quantile: float = 0.95,
    persistence: int = 3,
    min_history_minutes: int = 120,
    min_early_minutes: int = 4,
    min_future_minutes: int = 8,
    max_events: int | None = None,
) -> RuntimeNFRDataset:
    """Load the full runtime-NFR task without reading legacy impact labels."""

    root = Path(segment_root)
    layouts = _read_segment_layout(root)
    intervals = _collect_event_intervals(layouts)
    frame_cache: dict[Path, pd.DataFrame] = {}
    infra_frame_cache: dict[Path, pd.DataFrame] = {}
    samples: list[RuntimeNFRSample] = []
    previous: tuple[Path, dict[str, Any], dgl.DGLGraph, dict[str, list[str]]] | None = None

    for tenant_dir, segment_meta, graph, node_ids in layouts:
        previous_graph = previous[2] if previous else None
        previous_node_ids = previous[3] if previous else None
        delta: TopologyDelta = build_topology_delta(previous_graph, previous_node_ids, graph, node_ids)
        current_hash = topology_hash(graph, node_ids)
        previous_hash = topology_hash(previous_graph, previous_node_ids) if previous_graph is not None else None
        segment_start = _parse_timestamp(segment_meta["start_time"])
        segment_id = str(segment_meta.get("segment_id") or tenant_dir.name.rsplit("_", 1)[-1])
        for record_dir in sorted(path for path in (tenant_dir / "records").glob("record_*") if path.is_dir()):
            label_path = record_dir / "global_label.json"
            if not label_path.exists():
                continue
            label = json.loads(label_path.read_text(encoding="utf-8"))
            intent = _intent_from_label(label)
            event_start = _parse_timestamp(label["change_start_time"])
            event_end = _parse_timestamp(label["change_end_time"])
            event_minute = pd.Timestamp(event_start).floor("min")
            aligned_event = event_minute.to_pydatetime()
            boundary_start = aligned_event - timedelta(minutes=history_minutes)
            input_start = aligned_event - timedelta(minutes=pre_minutes)
            input_end = aligned_event + timedelta(minutes=observation_minutes)
            label_start = input_end
            label_end = input_end + timedelta(minutes=horizon_minutes)
            history_grid = pd.date_range(
                start=pd.Timestamp(boundary_start).floor("min"),
                end=event_minute - pd.Timedelta(minutes=1),
                freq="min",
            )
            input_grid = pd.date_range(
                start=event_minute - pd.Timedelta(minutes=pre_minutes),
                periods=pre_minutes + observation_minutes,
                freq="min",
            )
            future_grid = pd.date_range(
                start=event_minute + pd.Timedelta(minutes=observation_minutes),
                periods=horizon_minutes,
                freq="min",
            )
            metrics_dir = _metric_dir(tenant_dir, record_dir)
            phy, phy_mask = _stack_infra_layer(
                metrics_dir / "host",
                node_ids["Vphy"],
                history_grid=history_grid,
                input_grid=input_grid,
                frame_cache=infra_frame_cache,
            )
            vm, vm_mask = _stack_infra_layer(
                metrics_dir / "vm",
                node_ids["Vvm"],
                history_grid=history_grid,
                input_grid=input_grid,
                frame_cache=infra_frame_cache,
            )
            app_values: list[torch.Tensor] = []
            app_masks: list[torch.Tensor] = []
            breach: list[float] = []
            severity: list[float] = []
            valid: list[float] = []
            request_volume: list[float] = []
            boundaries: dict[str, dict[str, NFRBoundary]] = {}
            invalid_reasons: dict[str, str] = {}
            sensitivity_values: dict[str, tuple[list[float], list[float]]] = {
                f"q{int(q * 100):02d}_p{p}": ([], [])
                for q in SENSITIVITY_QUANTILES
                for p in SENSITIVITY_PERSISTENCE
            }
            for raw_app_id in node_ids["Vbiz"]:
                app_id = _app_alias(raw_app_id)
                path = metrics_dir / "app" / f"{app_id}.csv"
                if path not in frame_cache:
                    frame_cache[path] = load_app_sli_frame(path) if path.exists() else pd.DataFrame(
                        columns=["request", "latency", "error_ratio", "availability"]
                    )
                frame = frame_cache[path]
                raw_history = frame.loc[(frame.index >= history_grid[0]) & (frame.index <= history_grid[-1])]
                history = _exclude_overlaps(raw_history, app_id=app_id, intervals=intervals)
                future = frame.reindex(future_grid)
                boundary_grid: dict[float, dict[str, NFRBoundary]] = {}
                for q in SENSITIVITY_QUANTILES:
                    boundary_grid[q] = {
                        "latency": fit_nfr_boundary(
                            history["latency"],
                            app_id=app_id,
                            sli="latency",
                            history_start=boundary_start,
                            history_end=aligned_event,
                            quantile=q,
                        ),
                        "error_ratio": fit_nfr_boundary(
                            history["error_ratio"],
                            app_id=app_id,
                            sli="error_ratio",
                            history_start=boundary_start,
                            history_end=aligned_event,
                            quantile=q,
                        ),
                    }
                primary = boundary_grid.get(quantile)
                if primary is None:
                    primary = {
                        "latency": fit_nfr_boundary(
                            history["latency"], app_id=app_id, sli="latency",
                            history_start=boundary_start, history_end=aligned_event, quantile=quantile,
                        ),
                        "error_ratio": fit_nfr_boundary(
                            history["error_ratio"], app_id=app_id, sli="error_ratio",
                            history_start=boundary_start, history_end=aligned_event, quantile=quantile,
                        ),
                    }
                boundaries[app_id] = primary
                app_feature, app_mask, early_request, early_observed = _runtime_app_features(
                    frame,
                    input_grid=input_grid,
                    event_minute=event_minute,
                    latency_boundary=primary["latency"],
                    error_boundary=primary["error_ratio"],
                    request_history=history["request"],
                )
                app_values.append(app_feature)
                app_masks.append(app_mask)
                primary_breach, primary_severity, future_observed = _severity_for_boundaries(
                    future,
                    primary["latency"],
                    primary["error_ratio"],
                    persistence=persistence,
                )
                reason = ""
                if min(primary["latency"].valid_samples, primary["error_ratio"].valid_samples) < min_history_minutes:
                    reason = "insufficient_history"
                elif early_observed < min_early_minutes:
                    reason = "insufficient_early_observation"
                elif future_observed < min_future_minutes:
                    reason = "insufficient_future_observation"
                is_valid = not reason
                if reason:
                    invalid_reasons[app_id] = reason
                breach.append(float(primary_breach))
                severity.append(primary_severity)
                valid.append(float(is_valid))
                request_volume.append(early_request)
                for q in SENSITIVITY_QUANTILES:
                    for p in SENSITIVITY_PERSISTENCE:
                        key = f"q{int(q * 100):02d}_p{p}"
                        q_breach, q_severity, _ = _severity_for_boundaries(
                            future,
                            boundary_grid[q]["latency"],
                            boundary_grid[q]["error_ratio"],
                            persistence=p,
                        )
                        sensitivity_values[key][0].append(float(q_breach))
                        sensitivity_values[key][1].append(q_severity)

            app_tensor = torch.stack(app_values, dim=1)
            app_mask_tensor = torch.stack(app_masks, dim=1)
            metadata = RuntimeNFRMetadata(
                event_id=str(label.get("groundtruth_uuid") or f"{segment_id}/{record_dir.name}"),
                record_id=record_dir.name,
                segment_id=segment_id,
                event_start=event_start,
                event_minute=aligned_event,
                event_end=event_end,
                boundary_start=boundary_start,
                boundary_end=aligned_event,
                input_start=input_start,
                input_end=input_end,
                label_start=label_start,
                label_end=label_end,
                topology_hash=current_hash,
                previous_topology_hash=previous_hash,
                seconds_since_topology_change=max(0.0, (event_start - segment_start).total_seconds()),
                hosting_edges=hosting_edges(graph, node_ids),
                fault_family=intent.fault_family,
                target_type=intent.target_type,
            )
            sample = RuntimeNFRSample(
                current_graph=graph,
                previous_graph=previous_graph,
                delta_graph=delta.graph,
                features={"Vphy": phy, "Vvm": vm, "Vbiz": app_tensor},
                masks={"Vphy": phy_mask, "Vvm": vm_mask, "Vbiz": app_mask_tensor},
                breach_labels=torch.tensor(breach, dtype=torch.float32),
                severity_raw=torch.tensor(severity, dtype=torch.float32),
                label_mask=torch.tensor(valid, dtype=torch.float32),
                early_request_volume=torch.tensor(request_volume, dtype=torch.float32),
                node_ids=node_ids,
                boundaries=boundaries,
                sensitivity_targets={
                    key: RuntimeNFRTargets(
                        breach=torch.tensor(values[0], dtype=torch.float32),
                        severity_raw=torch.tensor(values[1], dtype=torch.float32),
                    )
                    for key, values in sensitivity_values.items()
                },
                invalid_reasons=invalid_reasons,
                metadata=metadata,
                has_previous_graph=previous_graph is not None,
            )
            assert_runtime_nfr_temporal_contract(sample)
            samples.append(sample)
            if max_events is not None and len(samples) >= max_events:
                return RuntimeNFRDataset(samples)
        previous = (tenant_dir, segment_meta, graph, node_ids)
    return RuntimeNFRDataset(samples)


def chronological_runtime_split(
    samples: list[RuntimeNFRSample],
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
) -> tuple[list[RuntimeNFRSample], list[RuntimeNFRSample], list[RuntimeNFRSample]]:
    if train_ratio <= 0 or val_ratio <= 0 or train_ratio + val_ratio >= 1:
        raise ValueError("invalid chronological split ratios")
    ordered = sorted(samples, key=lambda sample: sample.metadata.event_start)
    train_end = max(1, int(len(ordered) * train_ratio))
    val_end = max(train_end + 1, int(len(ordered) * (train_ratio + val_ratio)))
    return ordered[:train_end], ordered[train_end:val_end], ordered[val_end:]


def topology_runtime_folds(
    samples: list[RuntimeNFRSample],
    n_splits: int = 5,
    min_group_size: int = 5,
) -> list[tuple[list[int], list[int]]]:
    # The existing implementation only requires metadata fields shared by the
    # runtime sample, so reuse its tested topology-merging protocol.
    return intervention_topology_group_folds(samples, n_splits=n_splits, min_group_size=min_group_size)  # type: ignore[arg-type]


def assert_runtime_nfr_temporal_contract(sample: RuntimeNFRSample) -> None:
    metadata = sample.metadata
    if metadata.boundary_end > metadata.event_start:
        raise AssertionError("NFR boundary uses future telemetry")
    if metadata.event_minute > metadata.event_start:
        raise AssertionError("aligned event minute must not follow the exact event start")
    if metadata.input_end > metadata.label_start:
        raise AssertionError("input and label windows overlap")
    if metadata.label_start < metadata.event_start:
        raise AssertionError("label window starts before the event")
    expected = int((metadata.input_end - metadata.input_start).total_seconds() // 60)
    for ntype in ("Vphy", "Vvm", "Vbiz"):
        if sample.features[ntype].shape[0] != expected:
            raise AssertionError(f"{ntype} contains an unexpected number of input steps")
        if sample.features[ntype].shape != sample.masks[ntype].shape:
            raise AssertionError(f"{ntype} feature/mask shape mismatch")
    apps = len(sample.node_ids["Vbiz"])
    for tensor in (sample.breach_labels, sample.severity_raw, sample.label_mask, sample.early_request_volume):
        if tensor.shape != (apps,):
            raise AssertionError("App target order does not match graph App order")


def aggregate_app_predictions(
    sample: RuntimeNFRSample,
    probabilities: np.ndarray,
    *,
    request_weights: np.ndarray | None = None,
) -> dict[str, dict[str, dict[str, float]]]:
    """Aggregate App risk to Pod and Host without creating new labels."""

    graph = sample.current_graph
    app_ids = sample.node_ids["Vbiz"]
    weights = (
        np.asarray(request_weights, dtype=float)
        if request_weights is not None
        else sample.early_request_volume.detach().cpu().numpy().astype(float)
    )
    app_risk = {app_id: float(probabilities[index]) for index, app_id in enumerate(app_ids)}
    app_weight = {app_id: float(weights[index]) for index, app_id in enumerate(app_ids)}
    vm_apps: dict[str, set[str]] = {node_id: set() for node_id in sample.node_ids["Vvm"]}
    if ("Vvm", "r_deployment", "Vbiz") in graph.canonical_etypes:
        src, dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))
        for vm_index, app_index in zip(src.tolist(), dst.tolist()):
            vm_apps[sample.node_ids["Vvm"][int(vm_index)]].add(app_ids[int(app_index)])
    host_apps: dict[str, set[str]] = {node_id: set() for node_id in sample.node_ids["Vphy"]}
    if ("Vphy", "r_hosting", "Vvm") in graph.canonical_etypes:
        src, dst = graph.edges(etype=("Vphy", "r_hosting", "Vvm"))
        for host_index, vm_index in zip(src.tolist(), dst.tolist()):
            host_apps[sample.node_ids["Vphy"][int(host_index)]] |= vm_apps[sample.node_ids["Vvm"][int(vm_index)]]

    def summarize(mapping: dict[str, set[str]]) -> dict[str, dict[str, float]]:
        result: dict[str, dict[str, float]] = {}
        for node_id, descendants in mapping.items():
            if not descendants:
                result[node_id] = {"max": 0.0, "request_weighted_mean": 0.0}
                continue
            risks = np.asarray([app_risk[app] for app in descendants], dtype=float)
            descendant_weights = np.asarray([app_weight[app] for app in descendants], dtype=float)
            weighted = float(np.average(risks, weights=descendant_weights)) if descendant_weights.sum() > 0 else float(risks.mean())
            result[node_id] = {"max": float(risks.max()), "request_weighted_mean": weighted}
        return result

    return {
        "app": {app_id: {"max": risk, "request_weighted_mean": risk} for app_id, risk in app_risk.items()},
        "pod": summarize(vm_apps),
        "host": summarize(host_apps),
    }


def boundary_to_dict(boundary: NFRBoundary) -> dict[str, Any]:
    payload = dict(boundary.__dict__)
    payload["history_start"] = boundary.history_start.isoformat()
    payload["history_end"] = boundary.history_end.isoformat()
    return payload
