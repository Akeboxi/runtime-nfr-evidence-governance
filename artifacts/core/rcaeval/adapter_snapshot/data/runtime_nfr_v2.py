"""Runtime-NFR v2 research artifacts and validity audits.

This module deliberately keeps the v2 construct-validity study separate from
the frozen ``runtime_nfr_v1`` prediction protocol.  It creates auditable
candidate NFR specifications, blinded expert-review packets, matched
non-event controls, topology provenance, and protocol metadata.  Fault intent
is used only by audits/stratification and never becomes a predictor feature.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import math
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .runtime_nfr_dataset import (
    NFRBoundary,
    RuntimeNFRSample,
    _app_alias,
    _parse_timestamp,
    _read_segment_layout,
    _severity_for_boundaries,
    fit_nfr_boundary,
    load_app_sli_frame,
    topology_runtime_folds,
)


RUNTIME_NFR_V2_PROTOCOL = "runtime_nfr_v2"
BOUNDARY_CARD_SCHEMA = "runtime-nfr-boundary-card/v1"
EXPERT_DIMENSIONS = (
    "clarity",
    "measurability",
    "traceability",
    "threshold_plausibility",
    "runtime_actionability",
)


@dataclass(frozen=True)
class NFRBoundaryCard:
    """A candidate operational NFR specification, not a business-approved SLO."""

    card_id: str
    schema: str
    status: Literal["candidate", "expert_reviewed", "stakeholder_approved"]
    subject_type: str
    subject_id: str
    sli: str
    sli_semantics: str
    unit: str
    measurement_point: str
    zero_traffic_rule: str
    history_start: datetime
    history_end: datetime
    valid_samples: int
    quantile_level: float
    quantile_value: float
    median: float
    mad: float
    robust_scale: float
    threshold: float
    persistence_minutes: int
    observation_context: dict[str, Any]
    evidence_provenance: dict[str, Any]
    quality: dict[str, Any]

    def to_dict(self, *, blinded: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        payload["history_start"] = self.history_start.isoformat()
        payload["history_end"] = self.history_end.isoformat()
        if blinded:
            provenance = dict(payload["evidence_provenance"])
            for key in ("event_id", "record_id", "fault_family", "target_type"):
                provenance.pop(key, None)
            payload["evidence_provenance"] = provenance
            context = dict(payload["observation_context"])
            context.pop("segment_id", None)
            context.pop("topology_hash", None)
            payload["observation_context"] = context
        return payload


@dataclass(frozen=True)
class TopologyEdgeEvidence:
    relation: str
    source_type: str
    destination_type: str
    edge_count: int
    source: str
    observed_at: datetime
    confidence: float
    causal_cutoff: datetime

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["observed_at"] = self.observed_at.isoformat()
        payload["causal_cutoff"] = self.causal_cutoff.isoformat()
        return payload


@dataclass(frozen=True)
class TopologyEvidence:
    event_id: str
    topology_hash: str
    node_counts: dict[str, int]
    observation_coverage: dict[str, float]
    relations: tuple[TopologyEdgeEvidence, ...]
    target_mapping_status: str = "not_a_model_input"
    unresolved_target_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["relations"] = [item.to_dict() for item in self.relations]
        return payload


@dataclass(frozen=True)
class ExperimentProtocol:
    protocol: str
    parent_protocol: str
    registered_at: datetime
    dataset_hash: str
    test_exposure_status: str
    development_event_ids: tuple[str, ...]
    legacy_holdout_event_ids: tuple[str, ...]
    confirmatory_claims: tuple[str, ...]
    exploratory_claims: tuple[str, ...]
    forbidden_model_inputs: tuple[str, ...]
    primary_metrics: tuple[str, ...]
    graph_gate: dict[str, Any]
    output_namespace: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["registered_at"] = self.registered_at.isoformat()
        return payload


def _card_id(event_id: str, app_id: str, sli: str) -> str:
    value = f"{BOUNDARY_CARD_SCHEMA}|{event_id}|{app_id}|{sli}"
    return f"nfr-{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _boundary_quality(boundary: NFRBoundary, history_minutes: int) -> dict[str, Any]:
    coverage = min(1.0, boundary.valid_samples / max(1, history_minutes))
    if boundary.valid_samples >= 720 and coverage >= 0.5:
        confidence = "high"
    elif boundary.valid_samples >= 120:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "history_coverage": coverage,
        "confidence": confidence,
        "finite_threshold": bool(math.isfinite(boundary.threshold)),
        "candidate_only": True,
        "requires_stakeholder_approval": True,
    }


def build_boundary_cards(
    samples: Sequence[RuntimeNFRSample],
    *,
    dataset_hash: str,
    persistence_minutes: int,
    history_minutes: int,
) -> list[NFRBoundaryCard]:
    cards: list[NFRBoundaryCard] = []
    semantics = {
        "latency": ("minute-level mean request response time upper bound", "ms"),
        "error_ratio": ("(error + timeout) / request upper bound", "ratio"),
    }
    for sample in samples:
        for app_id, app_boundaries in sample.boundaries.items():
            for sli, boundary in app_boundaries.items():
                meaning, unit = semantics[sli]
                cards.append(
                    NFRBoundaryCard(
                        card_id=_card_id(sample.metadata.event_id, app_id, sli),
                        schema=BOUNDARY_CARD_SCHEMA,
                        status="candidate",
                        subject_type="application_service",
                        subject_id=app_id,
                        sli=sli,
                        sli_semantics=meaning,
                        unit=unit,
                        measurement_point="application APM, one-minute aggregation",
                        zero_traffic_rule=(
                            "minutes with request=0 are excluded from availability/error judgement"
                            if sli == "error_ratio"
                            else "latency requires an observed request response"
                        ),
                        history_start=boundary.history_start,
                        history_end=boundary.history_end,
                        valid_samples=boundary.valid_samples,
                        quantile_level=boundary.quantile_level,
                        quantile_value=boundary.quantile_value,
                        median=boundary.median,
                        mad=boundary.mad,
                        robust_scale=boundary.robust_scale,
                        threshold=boundary.threshold,
                        persistence_minutes=persistence_minutes,
                        observation_context={
                            "segment_id": sample.metadata.segment_id,
                            "topology_hash": sample.metadata.topology_hash,
                            "history_minutes_requested": history_minutes,
                            "workload_scope": "per-service historical request workload",
                        },
                        evidence_provenance={
                            "protocol": RUNTIME_NFR_V2_PROTOCOL,
                            "dataset_hash": dataset_hash,
                            "event_id": sample.metadata.event_id,
                            "record_id": sample.metadata.record_id,
                            "query_version": BOUNDARY_CARD_SCHEMA,
                            "evidence_cutoff": sample.metadata.event_start.isoformat(),
                            "fault_family": sample.metadata.fault_family,
                            "target_type": sample.metadata.target_type,
                        },
                        quality=_boundary_quality(boundary, history_minutes),
                    )
                )
    return cards


def _traffic_quartiles(samples: Sequence[RuntimeNFRSample]) -> dict[tuple[str, str], int]:
    rows: list[tuple[str, str, float]] = []
    for sample in samples:
        for index, raw_app_id in enumerate(sample.node_ids["Vbiz"]):
            app_id = _app_alias(raw_app_id)
            rows.append((sample.metadata.event_id, app_id, float(sample.early_request_volume[index])))
    values = np.asarray([row[2] for row in rows], dtype=float)
    cuts = np.quantile(values, [0.25, 0.5, 0.75]) if values.size else np.asarray([0.0, 0.0, 0.0])
    return {(event, app): int(np.searchsorted(cuts, value, side="right")) for event, app, value in rows}


def select_blinded_expert_cards(
    samples: Sequence[RuntimeNFRSample],
    cards: Sequence[NFRBoundaryCard],
    *,
    count: int = 48,
    seed: int = 20260722,
) -> tuple[list[NFRBoundaryCard], list[dict[str, Any]]]:
    """Deterministic round-robin sampling over prespecified expert-study strata."""

    sample_by_event = {sample.metadata.event_id: sample for sample in samples}
    card_lookup = {
        (card.evidence_provenance["event_id"], card.subject_id, card.sli): card for card in cards
    }
    traffic = _traffic_quartiles(samples)
    strata: dict[tuple[Any, ...], list[tuple[NFRBoundaryCard, dict[str, Any]]]] = defaultdict(list)
    for (event_id, app_id, sli), card in card_lookup.items():
        sample = sample_by_event[event_id]
        app_index = next(
            index
            for index, raw_app_id in enumerate(sample.node_ids["Vbiz"])
            if _app_alias(raw_app_id) == app_id
        )
        if not bool(sample.label_mask[app_index]):
            continue
        private = {
            "card_id": card.card_id,
            "event_id": event_id,
            "app_id": app_id,
            "sli": sli,
            "breach": int(sample.breach_labels[app_index]),
            "fault_family": sample.metadata.fault_family,
            "target_type": sample.metadata.target_type,
            "traffic_quartile": traffic[(event_id, app_id)],
        }
        key = (sli, private["breach"], private["target_type"], private["traffic_quartile"])
        strata[key].append((card, private))
    rng = np.random.default_rng(seed)
    for values in strata.values():
        rng.shuffle(values)
    selected: list[tuple[NFRBoundaryCard, dict[str, Any]]] = []
    keys = sorted(strata, key=str)
    while len(selected) < min(count, sum(len(value) for value in strata.values())):
        progressed = False
        for key in keys:
            if strata[key] and len(selected) < count:
                selected.append(strata[key].pop())
                progressed = True
        if not progressed:
            break
    selected.sort(key=lambda item: item[0].card_id)
    return [item[0] for item in selected], [item[1] for item in selected]


def write_boundary_card_package(
    output_dir: str | Path,
    *,
    cards: Sequence[NFRBoundaryCard],
    expert_cards: Sequence[NFRBoundaryCard],
    private_manifest: Sequence[dict[str, Any]],
) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    all_payload = [card.to_dict() for card in cards]
    blinded_payload = [card.to_dict(blinded=True) for card in expert_cards]
    paths = {
        "all_json": output / "boundary_cards.json",
        "all_yaml": output / "boundary_cards.yaml",
        "expert_cards": output / "expert_review_cards.json",
        "expert_template": output / "expert_ratings_template.csv",
        "private_manifest": output / "expert_sampling_manifest_private.json",
    }
    paths["all_json"].write_text(json.dumps(all_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["all_yaml"].write_text(yaml.safe_dump(all_payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    paths["expert_cards"].write_text(json.dumps(blinded_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    template_rows = []
    for card in expert_cards:
        row = {"reviewer_id": "", "card_id": card.card_id}
        row.update({dimension: "" for dimension in EXPERT_DIMENSIONS})
        row["comments"] = ""
        template_rows.append(row)
    pd.DataFrame(template_rows).to_csv(paths["expert_template"], index=False, encoding="utf-8-sig")
    paths["private_manifest"].write_text(
        json.dumps(list(private_manifest), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {key: str(path.resolve()) for key, path in paths.items()}


def krippendorff_alpha_ordinal(rows: Sequence[Sequence[float]]) -> float | None:
    """Ordinal Krippendorff alpha for item-wise ratings with missing values."""

    clean_rows = [np.asarray([float(v) for v in row if pd.notna(v)], dtype=float) for row in rows]
    clean_rows = [row for row in clean_rows if row.size >= 2]
    if not clean_rows:
        return None
    all_values = np.concatenate(clean_rows)
    categories = np.asarray(sorted(set(all_values.tolist())), dtype=float)
    if categories.size <= 1:
        return 1.0
    frequencies = np.asarray([(all_values == value).sum() for value in categories], dtype=float)
    cumulative_distance: dict[tuple[float, float], float] = {}
    for left in categories:
        for right in categories:
            lo, hi = sorted((left, right))
            mask = (categories >= lo) & (categories <= hi)
            mass = frequencies[mask].sum() - (frequencies[categories == lo].sum() + frequencies[categories == hi].sum()) / 2.0
            cumulative_distance[(left, right)] = float(mass * mass)

    observed_num = observed_den = 0.0
    for row in clean_rows:
        for i, left in enumerate(row):
            for j, right in enumerate(row):
                if i == j:
                    continue
                observed_num += cumulative_distance[(left, right)]
                observed_den += 1.0
    expected_num = expected_den = 0.0
    for i, left in enumerate(all_values):
        for j, right in enumerate(all_values):
            if i == j:
                continue
            expected_num += cumulative_distance[(left, right)]
            expected_den += 1.0
    observed = observed_num / max(1.0, observed_den)
    expected = expected_num / max(1.0, expected_den)
    return 1.0 if expected == 0.0 else float(1.0 - observed / expected)


def summarize_expert_ratings(frame: pd.DataFrame) -> dict[str, Any]:
    required = {"reviewer_id", "card_id", *EXPERT_DIMENSIONS}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"expert ratings missing columns: {missing}")
    if frame["reviewer_id"].replace("", np.nan).isna().any():
        raise ValueError("reviewer_id must be filled for every expert rating")
    numeric = frame.copy()
    for dimension in EXPERT_DIMENSIONS:
        numeric[dimension] = pd.to_numeric(numeric[dimension], errors="coerce")
        invalid = numeric[dimension].notna() & ~numeric[dimension].between(1, 5)
        if invalid.any():
            raise ValueError(f"{dimension} ratings must be in [1, 5]")
    reviewer_count = int(numeric["reviewer_id"].nunique())
    if reviewer_count < 2:
        raise ValueError("at least two reviewers are required for agreement analysis")
    per_dimension: dict[str, Any] = {}
    for dimension in EXPERT_DIMENSIONS:
        grouped = [group[dimension].dropna().tolist() for _, group in numeric.groupby("card_id")]
        values = numeric[dimension].dropna().to_numpy(dtype=float)
        per_dimension[dimension] = {
            "median": float(np.median(values)) if values.size else None,
            "q1": float(np.quantile(values, 0.25)) if values.size else None,
            "q3": float(np.quantile(values, 0.75)) if values.size else None,
            "krippendorff_alpha_ordinal": krippendorff_alpha_ordinal(grouped),
        }
    card_medians = numeric.groupby("card_id")[list(EXPERT_DIMENSIONS)].median()
    core = card_medians[["clarity", "measurability", "traceability"]]
    supportive = (core >= 4.0).all(axis=1)
    supportive_rate = float(supportive.mean()) if len(supportive) else 0.0
    return {
        "protocol": RUNTIME_NFR_V2_PROTOCOL,
        "reviewers": reviewer_count,
        "cards": int(numeric["card_id"].nunique()),
        "ratings": int(len(numeric)),
        "dimensions": per_dimension,
        "supportive_card_rate": supportive_rate,
        "supportive_threshold": 0.75,
        "supportive_gate_pass": supportive_rate >= 0.75,
        "interpretation_limit": "expert review assesses candidate-specification quality, not business SLO approval",
    }


def topology_evidence_for_sample(sample: RuntimeNFRSample) -> TopologyEvidence:
    graph = sample.current_graph
    relations = []
    for source, relation, destination in graph.canonical_etypes:
        relations.append(
            TopologyEdgeEvidence(
                relation=relation,
                source_type=source,
                destination_type=destination,
                edge_count=int(graph.num_edges((source, relation, destination))),
                source="segment graph snapshot CSV",
                observed_at=sample.metadata.event_minute,
                confidence=1.0,
                causal_cutoff=sample.metadata.input_end,
            )
        )
    coverage = {
        ntype: float(sample.masks[ntype].mean().detach().cpu())
        for ntype in ("Vphy", "Vvm", "Vbiz")
    }
    return TopologyEvidence(
        event_id=sample.metadata.event_id,
        topology_hash=sample.metadata.topology_hash,
        node_counts={ntype: len(sample.node_ids[ntype]) for ntype in ("Vphy", "Vvm", "Vbiz")},
        observation_coverage=coverage,
        relations=tuple(relations),
    )


def topology_feature_matrix(
    samples: Sequence[RuntimeNFRSample],
) -> tuple[np.ndarray, list[tuple[int, int]], list[str]]:
    """Causal topology/observability covariates in event/App order."""

    names = [
        "app_early_observation",
        "app_missingness",
        "host_observation",
        "pod_observation",
        "deployed_pod_count",
        "shared_host_peer_apps",
        "app_call_in_degree",
        "app_call_out_degree",
        "topology_changed",
        "log_seconds_since_topology_change",
    ]
    rows: list[list[float]] = []
    mapping: list[tuple[int, int]] = []
    for event_index, sample in enumerate(samples):
        graph = sample.current_graph
        early_steps = max(1, int((sample.metadata.input_end - sample.metadata.event_minute).total_seconds() // 60))
        app_mask = sample.masks["Vbiz"][-early_steps:]
        host_observation = float(sample.masks["Vphy"][-early_steps:].mean())
        pod_observation = float(sample.masks["Vvm"][-early_steps:].mean())
        deployed = defaultdict(set)
        host_vms = defaultdict(set)
        if ("Vvm", "r_deployment", "Vbiz") in graph.canonical_etypes:
            src, dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))
            for vm, app in zip(src.tolist(), dst.tolist()):
                deployed[int(app)].add(int(vm))
        if ("Vphy", "r_hosting", "Vvm") in graph.canonical_etypes:
            src, dst = graph.edges(etype=("Vphy", "r_hosting", "Vvm"))
            for host, vm in zip(src.tolist(), dst.tolist()):
                host_vms[int(host)].add(int(vm))
        vm_hosts = {vm: host for host, vms in host_vms.items() for vm in vms}
        app_hosts = {app: {vm_hosts[vm] for vm in vms if vm in vm_hosts} for app, vms in deployed.items()}
        in_degree = Counter()
        out_degree = Counter()
        if ("Vbiz", "r_calling", "Vbiz") in graph.canonical_etypes:
            src, dst = graph.edges(etype=("Vbiz", "r_calling", "Vbiz"))
            in_degree.update(int(value) for value in dst.tolist())
            out_degree.update(int(value) for value in src.tolist())
        for app_index in range(len(sample.node_ids["Vbiz"])):
            if not bool(sample.label_mask[app_index]):
                continue
            app_observation = float(app_mask[:, app_index].mean())
            peers = sum(
                1
                for other, hosts in app_hosts.items()
                if other != app_index and hosts & app_hosts.get(app_index, set())
            )
            rows.append(
                [
                    app_observation,
                    1.0 - app_observation,
                    host_observation,
                    pod_observation,
                    float(len(deployed.get(app_index, set()))),
                    float(peers),
                    float(in_degree[app_index]),
                    float(out_degree[app_index]),
                    float(sample.metadata.previous_topology_hash != sample.metadata.topology_hash),
                    float(np.log1p(sample.metadata.seconds_since_topology_change)),
                ]
            )
            mapping.append((event_index, app_index))
    return np.asarray(rows, dtype=float), mapping, names


def _label_target_ids(label: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("target_node_ids", "instance", "change_host_id", "source", "destination"):
        value = label.get(key)
        if value in (None, ""):
            continue
        values.extend(value if isinstance(value, list) else [value])
    return sorted({str(value) for value in values if value not in (None, "")})


def _resolve_target(target: str, sample: RuntimeNFRSample) -> tuple[str | None, str | None]:
    for ntype in ("Vphy", "Vvm", "Vbiz"):
        if target in sample.node_ids[ntype]:
            return ntype, target
    alias = _app_alias(target)
    if alias in sample.node_ids["Vbiz"]:
        return "Vbiz", alias
    prefix = target.rsplit("-", 1)[0]
    if prefix in sample.node_ids["Vbiz"]:
        return "Vbiz", prefix
    return None, None


def load_event_labels(segment_root: str | Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tenant_dir, meta, _, _ in _read_segment_layout(Path(segment_root)):
        segment_id = str(meta.get("segment_id") or tenant_dir.name.rsplit("_", 1)[-1])
        for path in sorted((tenant_dir / "records").glob("record_*/global_label.json")):
            label = json.loads(path.read_text(encoding="utf-8"))
            event_id = str(label.get("groundtruth_uuid") or f"{segment_id}/{path.parent.name}")
            result[event_id] = label
    return result


def target_mapping_audit(
    samples: Sequence[RuntimeNFRSample],
    *,
    segment_root: str | Path,
) -> dict[str, Any]:
    labels = load_event_labels(segment_root)
    rows: list[dict[str, Any]] = []
    for sample in samples:
        targets = _label_target_ids(labels.get(sample.metadata.event_id, {}))
        resolved = []
        unresolved = []
        for target in targets:
            ntype, node_id = _resolve_target(target, sample)
            if ntype is None:
                unresolved.append(target)
            else:
                resolved.append({"target": target, "node_type": ntype, "node_id": node_id})
        rows.append(
            {
                "event_id": sample.metadata.event_id,
                "fault_family": sample.metadata.fault_family,
                "target_type": sample.metadata.target_type,
                "target_ids": targets,
                "resolved": resolved,
                "unresolved": unresolved,
                "any_target_mapped": bool(resolved),
                "all_targets_mapped": bool(targets) and not unresolved,
            }
        )
    targeted = [row for row in rows if row["target_ids"]]
    by_target_type: dict[str, Any] = {}
    for target_type in sorted({row["target_type"] for row in targeted}):
        group = [row for row in targeted if row["target_type"] == target_type]
        by_target_type[target_type] = {
            "events": len(group),
            "any_target_mapping_coverage": float(np.mean([row["any_target_mapped"] for row in group])),
            "all_target_mapping_coverage": float(np.mean([row["all_targets_mapped"] for row in group])),
            "unresolved_ids": sorted({value for row in group for value in row["unresolved"]}),
        }
    return {
        "events": len(rows),
        "events_with_targets": len(targeted),
        "event_any_target_mapping_coverage": float(np.mean([row["any_target_mapped"] for row in targeted])) if targeted else 0.0,
        "event_all_targets_mapping_coverage": float(np.mean([row["all_targets_mapped"] for row in targeted])) if targeted else 0.0,
        "by_target_type": by_target_type,
        "rows": rows,
        "usage_restriction": "fault targets are audit/stratification fields and are forbidden model inputs",
    }


def relation_diversity_audit(samples: Sequence[RuntimeNFRSample]) -> dict[str, Any]:
    relation_sets: dict[str, set[tuple[tuple[int, ...], tuple[int, ...]]]] = defaultdict(set)
    for sample in samples:
        for source, relation, destination in sample.current_graph.canonical_etypes:
            src, dst = sample.current_graph.edges(etype=(source, relation, destination))
            relation_sets[f"{source}:{relation}:{destination}"].add(
                (tuple(int(value) for value in src.tolist()), tuple(int(value) for value in dst.tolist()))
            )
    return {
        relation: {"unique_edge_sets": len(edge_sets), "varies": len(edge_sets) > 1}
        for relation, edge_sets in sorted(relation_sets.items())
    }


def development_rolling_folds(
    samples: Sequence[RuntimeNFRSample],
    *,
    n_splits: int = 5,
    development_ratio: float = 0.8,
    minimum_train_ratio: float = 0.3,
) -> list[tuple[list[int], list[int]]]:
    ordered = sorted(range(len(samples)), key=lambda index: samples[index].metadata.event_start)
    development_count = max(n_splits + 2, int(len(ordered) * development_ratio))
    development = ordered[:development_count]
    minimum_train = max(2, int(development_count * minimum_train_ratio))
    evaluation = development[minimum_train:]
    blocks = [list(block) for block in np.array_split(evaluation, n_splits) if len(block)]
    folds = []
    for block in blocks:
        first_position = development.index(block[0])
        train = development[:first_position]
        folds.append((train, block))
    return folds


def development_topology_oof_folds(
    samples: Sequence[RuntimeNFRSample],
    *,
    n_splits: int = 5,
    development_ratio: float = 0.8,
) -> list[tuple[list[int], list[int]]]:
    """Development-only topology folds with exactly one OOF assignment per event."""

    ordered = sorted(range(len(samples)), key=lambda index: samples[index].metadata.event_start)
    development_indices = ordered[: max(n_splits, int(len(ordered) * development_ratio))]
    development = [samples[index] for index in development_indices]
    local_folds = topology_runtime_folds(development, n_splits=n_splits)
    folds = [
        (
            [development_indices[index] for index in train],
            [development_indices[index] for index in validation],
        )
        for train, validation in local_folds
    ]
    validation_indices = [index for _, validation in folds for index in validation]
    if len(validation_indices) != len(set(validation_indices)):
        raise AssertionError("an event appears in more than one topology OOF fold")
    if set(validation_indices) != set(development_indices):
        raise AssertionError("topology OOF folds do not cover every development event")
    return folds


def topology_residual_audit(
    samples: Sequence[RuntimeNFRSample],
    *,
    n_splits: int = 5,
) -> dict[str, Any]:
    """Compare local and local+topology classifiers on rolling development folds."""

    # Private import avoids making the v1 trainer's flattened representation a
    # new public API while guaranteeing an exactly matched App-only baseline.
    from ..training.runtime_nfr_trainer import _event_feature_matrix

    folds = development_rolling_folds(samples, n_splits=n_splits)
    results = []
    oof_rows = []
    feature_names: list[str] = []
    for fold_index, (train_indices, val_indices) in enumerate(folds):
        train = [samples[index] for index in train_indices]
        val = [samples[index] for index in val_indices]
        x_train, y_train, _, train_mapping = _event_feature_matrix(train)
        x_val, y_val, _, val_mapping = _event_feature_matrix(val)
        topo_train, topo_train_mapping, feature_names = topology_feature_matrix(train)
        topo_val, topo_val_mapping, _ = topology_feature_matrix(val)
        if train_mapping != topo_train_mapping or val_mapping != topo_val_mapping:
            raise AssertionError("topology and App feature row order differ")
        if np.unique(y_train).size < 2 or np.unique(y_val).size < 2:
            results.append({"fold": fold_index, "status": "insufficient_class_variation"})
            continue
        local = make_pipeline(
            StandardScaler(),
            LogisticRegression(class_weight="balanced", max_iter=2000, random_state=20260722),
        )
        augmented = make_pipeline(
            StandardScaler(),
            LogisticRegression(class_weight="balanced", max_iter=2000, random_state=20260722),
        )
        local.fit(x_train, y_train)
        augmented.fit(np.concatenate([x_train, topo_train], axis=1), y_train)
        local_probability = local.predict_proba(x_val)[:, 1]
        augmented_probability = augmented.predict_proba(np.concatenate([x_val, topo_val], axis=1))[:, 1]
        local_pr = float(average_precision_score(y_val, local_probability))
        augmented_pr = float(average_precision_score(y_val, augmented_probability))
        results.append(
            {
                "fold": fold_index,
                "train_events": len(train),
                "validation_events": len(val),
                "local_pr_auc": local_pr,
                "local_plus_topology_pr_auc": augmented_pr,
                "pr_auc_delta": augmented_pr - local_pr,
                "direction_positive": augmented_pr > local_pr,
            }
        )
        for row_index, (event_index, app_index) in enumerate(val_mapping):
            sample = val[event_index]
            oof_rows.append(
                {
                    "fold": fold_index,
                    "event_id": sample.metadata.event_id,
                    "app_id": sample.node_ids["Vbiz"][app_index],
                    "label": int(y_val[row_index]),
                    "local_probability": float(local_probability[row_index]),
                    "local_plus_topology_probability": float(augmented_probability[row_index]),
                }
            )
    valid = [row for row in results if "direction_positive" in row]
    positive_folds = sum(bool(row["direction_positive"]) for row in valid)
    return {
        "design": "development-only rolling-origin residual-informativeness audit",
        "feature_names": feature_names if valid else [],
        "folds": results,
        "positive_direction_folds": positive_folds,
        "required_positive_direction_folds": 4,
        "direction_gate_pass": len(valid) >= 4 and positive_folds >= 4,
        "oof_rows": oof_rows,
        "legacy_test_used": False,
    }


@dataclass(frozen=True)
class _RawInterval:
    start: datetime
    end: datetime


def _raw_event_intervals(segment_root: str | Path, buffer_minutes: int) -> list[_RawInterval]:
    labels = load_event_labels(segment_root)
    return [
        _RawInterval(
            start=_parse_timestamp(label["change_start_time"]) - timedelta(minutes=buffer_minutes),
            end=_parse_timestamp(label["change_end_time"]) + timedelta(minutes=buffer_minutes),
        )
        for label in labels.values()
    ]


def _overlaps_any(start: datetime, end: datetime, intervals: Sequence[_RawInterval]) -> bool:
    return any(start < interval.end and end > interval.start for interval in intervals)


def _cluster_bootstrap(
    rows: Sequence[dict[str, Any]],
    *,
    cluster_key: str,
    iterations: int,
    seed: int,
) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[str(row[cluster_key])].append(float(row["event_minus_control"]))
    cluster_values = np.asarray([np.mean(values) for values in grouped.values()], dtype=float)
    if not cluster_values.size:
        return {"estimate": 0.0, "lower": 0.0, "upper": 0.0, "clusters": 0}
    rng = np.random.default_rng(seed)
    boot = [
        float(cluster_values[rng.integers(0, cluster_values.size, cluster_values.size)].mean())
        for _ in range(iterations)
    ]
    return {
        "estimate": float(cluster_values.mean()),
        "lower": float(np.quantile(boot, 0.025)),
        "upper": float(np.quantile(boot, 0.975)),
        "clusters": int(cluster_values.size),
    }


def matched_non_event_control_audit(
    samples: Sequence[RuntimeNFRSample],
    *,
    segment_root: str | Path,
    history_minutes: int,
    observation_minutes: int,
    horizon_minutes: int,
    quantile: float,
    persistence: int,
    min_history_minutes: int,
    min_future_minutes: int,
    controls_per_pair: int = 3,
    buffer_minutes: int = 30,
    bootstrap_iterations: int = 2000,
    seed: int = 20260722,
) -> dict[str, Any]:
    """Match non-event windows without using any post-window selection data."""

    root = Path(segment_root)
    layouts = _read_segment_layout(root)
    intervals = _raw_event_intervals(root, buffer_minutes)
    app_frames: dict[str, list[pd.DataFrame]] = defaultdict(list)
    for tenant_dir, _, _, node_ids in layouts:
        for raw_app_id in node_ids["Vbiz"]:
            app_id = _app_alias(raw_app_id)
            path = tenant_dir / "_staging" / "metrics" / "app" / f"{app_id}.csv"
            if path.exists():
                frame = load_app_sli_frame(path)
                if not frame.empty:
                    app_frames[app_id].append(frame)
    frame_cache = {
        app_id: pd.concat(frames).groupby(level=0, sort=True).mean(numeric_only=True)
        for app_id, frames in app_frames.items()
    }
    control_cache: dict[tuple[str, datetime], tuple[bool, int, int]] = {}
    rows: list[dict[str, Any]] = []
    available_rows: list[dict[str, Any]] = []
    without_three = 0
    without_any = 0
    control_reuse: Counter[str] = Counter()
    available_control_reuse: Counter[str] = Counter()

    for sample in samples:
        for app_index, raw_app_id in enumerate(sample.node_ids["Vbiz"]):
            if not bool(sample.label_mask[app_index]):
                continue
            app_id = _app_alias(raw_app_id)
            frame = frame_cache.get(app_id, pd.DataFrame())
            if frame.empty:
                without_three += 1
                without_any += 1
                continue
            event_minute = sample.metadata.event_minute
            event_history = frame.loc[
                (frame.index >= pd.Timestamp(event_minute - timedelta(minutes=60)))
                & (frame.index < pd.Timestamp(event_minute))
            ]
            event_pre_request = float(event_history.get("request", pd.Series(dtype=float)).fillna(0.0).sum())
            first = frame.index.min() + pd.Timedelta(minutes=max(min_history_minutes, 60))
            last = frame.index.max() - pd.Timedelta(minutes=observation_minutes + horizon_minutes)
            if first > last:
                without_three += 1
                without_any += 1
                continue
            candidates = []
            for timestamp in pd.date_range(first, last, freq="5min"):
                candidate = timestamp.to_pydatetime()
                hour_distance = min(abs(candidate.hour - event_minute.hour), 24 - abs(candidate.hour - event_minute.hour))
                if hour_distance > 1:
                    continue
                if _overlaps_any(
                    candidate,
                    candidate + timedelta(minutes=observation_minutes + horizon_minutes),
                    intervals,
                ):
                    continue
                pre = frame.loc[
                    (frame.index >= timestamp - pd.Timedelta(minutes=60)) & (frame.index < timestamp)
                ]
                if int(pre["request"].notna().sum()) < min(30, min_history_minutes):
                    continue
                request = float(pre["request"].fillna(0.0).sum())
                score = hour_distance + abs(math.log1p(request) - math.log1p(event_pre_request))
                candidates.append((score, candidate))
            chosen = [candidate for _, candidate in sorted(candidates)[:controls_per_pair]]
            outcomes = []
            control_ids = []
            for candidate in chosen:
                outcome_key = (app_id, candidate)
                if outcome_key not in control_cache:
                    history_start = candidate - timedelta(minutes=history_minutes)
                    history = frame.loc[
                        (frame.index >= pd.Timestamp(history_start)) & (frame.index < pd.Timestamp(candidate))
                    ]
                    keep = pd.Series(True, index=history.index)
                    for interval in intervals:
                        keep &= ~(
                            (history.index >= pd.Timestamp(interval.start))
                            & (history.index < pd.Timestamp(interval.end))
                        )
                    history = history.loc[keep]
                    latency_boundary = fit_nfr_boundary(
                        history["latency"], app_id=app_id, sli="latency",
                        history_start=history_start, history_end=candidate, quantile=quantile,
                    )
                    error_boundary = fit_nfr_boundary(
                        history["error_ratio"], app_id=app_id, sli="error_ratio",
                        history_start=history_start, history_end=candidate, quantile=quantile,
                    )
                    future_grid = pd.date_range(
                        candidate + timedelta(minutes=observation_minutes),
                        periods=horizon_minutes,
                        freq="min",
                    )
                    future = frame.reindex(future_grid)
                    control_breach, _, future_observed = _severity_for_boundaries(
                        future, latency_boundary, error_boundary, persistence=persistence
                    )
                    history_valid = min(latency_boundary.valid_samples, error_boundary.valid_samples)
                    control_cache[outcome_key] = (bool(control_breach), history_valid, future_observed)
                control_breach, history_valid, future_observed = control_cache[outcome_key]
                if history_valid >= min_history_minutes and future_observed >= min_future_minutes:
                    outcomes.append(float(control_breach))
                    control_ids.append(
                        sha256(f"{app_id}|{candidate.isoformat()}".encode("utf-8")).hexdigest()[:16]
                    )
            if not outcomes:
                without_three += 1
                without_any += 1
                continue
            event_breach = float(sample.breach_labels[app_index])
            control_mean = float(np.mean(outcomes))
            row = {
                "event_id": sample.metadata.event_id,
                "event_date": sample.metadata.event_start.date().isoformat(),
                "segment_id": sample.metadata.segment_id,
                "app_id": app_id,
                "event_breach": event_breach,
                "control_breach_rate": control_mean,
                "event_minus_control": event_breach - control_mean,
                "matched_controls": len(outcomes),
                "control_window_ids": control_ids,
            }
            available_rows.append(row)
            available_control_reuse.update(control_ids)
            if len(outcomes) != controls_per_pair:
                without_three += 1
                continue
            rows.append(row)
            control_reuse.update(control_ids)

    def summarize(
        selected_rows: Sequence[dict[str, Any]],
        reuse: Counter[str],
        *,
        seed_offset: int,
    ) -> dict[str, Any]:
        return {
            "matched_app_event_pairs": len(selected_rows),
            "unique_control_windows": len(reuse),
            "max_control_window_reuse": max(reuse.values(), default=0),
            "control_windows_reused_more_than_once": sum(value > 1 for value in reuse.values()),
            "event_breach_rate": float(np.mean([row["event_breach"] for row in selected_rows])) if selected_rows else 0.0,
            "control_breach_rate": float(np.mean([row["control_breach_rate"] for row in selected_rows])) if selected_rows else 0.0,
            "event_cluster_bootstrap": _cluster_bootstrap(
                selected_rows,
                cluster_key="event_id",
                iterations=bootstrap_iterations,
                seed=seed + seed_offset,
            ),
            "date_cluster_sensitivity": _cluster_bootstrap(
                selected_rows,
                cluster_key="event_date",
                iterations=bootstrap_iterations,
                seed=seed + seed_offset + 1,
            ),
            "segment_cluster_sensitivity": _cluster_bootstrap(
                selected_rows,
                cluster_key="segment_id",
                iterations=bootstrap_iterations,
                seed=seed + seed_offset + 2,
            ),
        }

    complete_summary = summarize(rows, control_reuse, seed_offset=0)
    available_summary = summarize(available_rows, available_control_reuse, seed_offset=100)
    return {
        "design": "same-App, hour-of-day and pre-window-request matched non-event controls",
        "controls_per_pair_requested": controls_per_pair,
        "event_buffer_minutes": buffer_minutes,
        "selection_uses_future_data": False,
        "complete_three_control_analysis": True,
        "pairs_without_three_valid_controls": without_three,
        "pairs_without_any_valid_control": without_any,
        **complete_summary,
        "available_control_sensitivity": available_summary,
        "rows": rows,
        "available_control_rows": available_rows,
    }
