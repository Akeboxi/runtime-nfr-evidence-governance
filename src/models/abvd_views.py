"""ABVD directional view builders.

Centralizes candidate upstream/downstream Biz-level edge views so training and
diagnostic scripts use the same definitions.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

import torch

ABVD_UPSTREAM_VIEW_CHOICES = (
    "same_vm_peer_biz",
    "calling_reverse_inbound",
    "same_host_peer_biz",
    "host_neighbor_peer_biz",
    "vm_traffic_peer_biz",
)


def _empty(device: torch.device | None = None) -> torch.Tensor:
    return torch.zeros((2, 0), dtype=torch.long, device=device)


def _pairwise_edges(
    groups: dict[int, set[int]],
    device: torch.device | None = None,
) -> torch.Tensor:
    pairs: set[tuple[int, int]] = set()
    for biz_nodes in groups.values():
        biz_list = sorted(biz_nodes)
        for src_idx in biz_list:
            for dst_idx in biz_list:
                if src_idx != dst_idx:
                    pairs.add((src_idx, dst_idx))
    if not pairs:
        return _empty(device=device)
    src = torch.tensor([pair[0] for pair in sorted(pairs)], dtype=torch.long, device=device)
    dst = torch.tensor([pair[1] for pair in sorted(pairs)], dtype=torch.long, device=device)
    return torch.stack([src, dst], dim=0)


def build_calling_downstream(
    graph,
    device: torch.device | None = None,
) -> torch.Tensor:
    if ("Vbiz", "r_calling", "Vbiz") not in graph.canonical_etypes:
        return _empty(device=device)
    src, dst = graph.edges(etype=("Vbiz", "r_calling", "Vbiz"))
    if device is not None:
        src = src.to(device)
        dst = dst.to(device)
    return torch.stack([src, dst], dim=0)


def build_calling_reverse_inbound(
    graph,
    device: torch.device | None = None,
) -> torch.Tensor:
    edges = build_calling_downstream(graph, device=device)
    if edges.numel() == 0:
        return edges
    return torch.stack([edges[1], edges[0]], dim=0)


def build_same_vm_peer_biz(
    graph,
    device: torch.device | None = None,
) -> torch.Tensor:
    if ("Vvm", "r_deployment", "Vbiz") not in graph.canonical_etypes:
        return _empty(device=device)
    vm_src, biz_dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))
    vm_to_biz: dict[int, set[int]] = defaultdict(set)
    for vm_idx, biz_idx in zip(vm_src.tolist(), biz_dst.tolist()):
        vm_to_biz[vm_idx].add(biz_idx)
    return _pairwise_edges(vm_to_biz, device=device)


def build_same_host_peer_biz(
    graph,
    device: torch.device | None = None,
) -> torch.Tensor:
    if (
        ("Vphy", "r_hosting", "Vvm") not in graph.canonical_etypes
        or ("Vvm", "r_deployment", "Vbiz") not in graph.canonical_etypes
    ):
        return _empty(device=device)

    host_src, vm_dst = graph.edges(etype=("Vphy", "r_hosting", "Vvm"))
    vm_src, biz_dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))

    vm_to_biz: dict[int, set[int]] = defaultdict(set)
    for vm_idx, biz_idx in zip(vm_src.tolist(), biz_dst.tolist()):
        vm_to_biz[vm_idx].add(biz_idx)

    host_to_biz: dict[int, set[int]] = defaultdict(set)
    for host_idx, vm_idx in zip(host_src.tolist(), vm_dst.tolist()):
        host_to_biz[host_idx].update(vm_to_biz.get(vm_idx, set()))
    return _pairwise_edges(host_to_biz, device=device)


def build_host_neighbor_peer_biz(
    graph,
    device: torch.device | None = None,
) -> torch.Tensor:
    if (
        ("Vphy", "r_link", "Vphy") not in graph.canonical_etypes
        or ("Vphy", "r_hosting", "Vvm") not in graph.canonical_etypes
        or ("Vvm", "r_deployment", "Vbiz") not in graph.canonical_etypes
    ):
        return _empty(device=device)

    host_link_src, host_link_dst = graph.edges(etype=("Vphy", "r_link", "Vphy"))
    host_src, vm_dst = graph.edges(etype=("Vphy", "r_hosting", "Vvm"))
    vm_src, biz_dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))

    vm_to_biz: dict[int, set[int]] = defaultdict(set)
    for vm_idx, biz_idx in zip(vm_src.tolist(), biz_dst.tolist()):
        vm_to_biz[vm_idx].add(biz_idx)

    host_to_biz: dict[int, set[int]] = defaultdict(set)
    for host_idx, vm_idx in zip(host_src.tolist(), vm_dst.tolist()):
        host_to_biz[host_idx].update(vm_to_biz.get(vm_idx, set()))

    pairs: set[tuple[int, int]] = set()
    for src_host, dst_host in zip(host_link_src.tolist(), host_link_dst.tolist()):
        src_biz = sorted(host_to_biz.get(src_host, set()))
        dst_biz = sorted(host_to_biz.get(dst_host, set()))
        for src_idx in src_biz:
            for dst_idx in dst_biz:
                if src_idx != dst_idx:
                    pairs.add((src_idx, dst_idx))
    if not pairs:
        return _empty(device=device)
    src = torch.tensor([pair[0] for pair in sorted(pairs)], dtype=torch.long, device=device)
    dst = torch.tensor([pair[1] for pair in sorted(pairs)], dtype=torch.long, device=device)
    return torch.stack([src, dst], dim=0)


def build_vm_traffic_peer_biz(
    graph,
    device: torch.device | None = None,
) -> torch.Tensor:
    if (
        ("Vvm", "r_traffic", "Vvm") not in graph.canonical_etypes
        or ("Vvm", "r_deployment", "Vbiz") not in graph.canonical_etypes
    ):
        return _empty(device=device)

    vm_link_src, vm_link_dst = graph.edges(etype=("Vvm", "r_traffic", "Vvm"))
    vm_src, biz_dst = graph.edges(etype=("Vvm", "r_deployment", "Vbiz"))

    vm_to_biz: dict[int, set[int]] = defaultdict(set)
    for vm_idx, biz_idx in zip(vm_src.tolist(), biz_dst.tolist()):
        vm_to_biz[vm_idx].add(biz_idx)

    pairs: set[tuple[int, int]] = set()
    for src_vm, dst_vm in zip(vm_link_src.tolist(), vm_link_dst.tolist()):
        for src_idx in sorted(vm_to_biz.get(src_vm, set())):
            for dst_idx in sorted(vm_to_biz.get(dst_vm, set())):
                if src_idx != dst_idx:
                    pairs.add((src_idx, dst_idx))
    if not pairs:
        return _empty(device=device)
    src = torch.tensor([pair[0] for pair in sorted(pairs)], dtype=torch.long, device=device)
    dst = torch.tensor([pair[1] for pair in sorted(pairs)], dtype=torch.long, device=device)
    return torch.stack([src, dst], dim=0)


UPSTREAM_VIEW_BUILDERS: dict[str, Callable] = {
    "same_vm_peer_biz": build_same_vm_peer_biz,
    "calling_reverse_inbound": build_calling_reverse_inbound,
    "same_host_peer_biz": build_same_host_peer_biz,
    "host_neighbor_peer_biz": build_host_neighbor_peer_biz,
    "vm_traffic_peer_biz": build_vm_traffic_peer_biz,
}


def build_upstream_edge_view(
    graph,
    view_name: str,
    device: torch.device | None = None,
) -> torch.Tensor:
    if view_name not in UPSTREAM_VIEW_BUILDERS:
        raise ValueError(
            f"Unknown ABVD upstream view {view_name!r}. "
            f"Choices: {sorted(UPSTREAM_VIEW_BUILDERS)}"
        )
    return UPSTREAM_VIEW_BUILDERS[view_name](graph, device=device)
