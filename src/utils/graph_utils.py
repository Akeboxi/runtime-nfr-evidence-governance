"""图结构工具函数"""

from collections import defaultdict
from typing import Any
import networkx as nx

from ..data.models import NodeType, RelationType, GraphStructure, EdgeRelation


def to_networkx(graph: GraphStructure) -> nx.DiGraph:
    """转换为NetworkX图

    Args:
        graph: 图结构数据

    Returns:
        NetworkX有向图
    """
    G = nx.DiGraph()

    for ntype, nodes in graph.nodes.items():
        for node_id, node_meta in nodes.items():
            G.add_node(
                node_id,
                node_type=ntype.value,
                cluster_id=node_meta.cluster_id,
                is_active=node_meta.is_active,
                **node_meta.metadata,
            )

    for edge in graph.edges:
        G.add_edge(
            edge.src_id,
            edge.dst_id,
            relation_type=edge.relation_type.value,
            weight=edge.weight,
            **edge.metadata,
        )

    return G


def compute_node_centrality(
    graph: GraphStructure,
    centrality_type: str = "betweenness",
) -> dict[str, float]:
    """计算节点中心性

    Args:
        graph: 图结构数据
        centrality_type: 中心性类型 (betweenness, degree, closeness, pagerank)

    Returns:
        节点ID到中心性分数的映射
    """
    G = to_networkx(graph)

    match centrality_type:
        case "betweenness":
            centrality = nx.betweenness_centrality(G)
        case "degree":
            centrality = nx.degree_centrality(G)
        case "closeness":
            centrality = nx.closeness_centrality(G)
        case "pagerank":
            centrality = nx.pagerank(G)
        case _:
            centrality = nx.degree_centrality(G)

    return centrality


def find_shortest_path(
    graph: GraphStructure,
    source: str,
    target: str,
) -> list[str] | None:
    """查找最短路径

    Args:
        graph: 图结构数据
        source: 源节点ID
        target: 目标节点ID

    Returns:
        节点ID列表，如果不存在路径则返回None
    """
    G = to_networkx(graph)

    try:
        return nx.shortest_path(G, source=source, target=target)
    except nx.NetworkXNoPath:
        return None


def get_subgraph(
    graph: GraphStructure,
    center_node: str,
    radius: int = 2,
) -> GraphStructure:
    """获取以某节点为中心的子图

    Args:
        graph: 图结构数据
        center_node: 中心节点ID
        radius: 半径（跳数）

    Returns:
        子图结构
    """
    G = to_networkx(graph)

    if center_node not in G:
        return GraphStructure(nodes={}, edges=[])

    subgraph_nodes = {center_node}
    current_level = {center_node}

    for _ in range(radius):
        next_level = set()
        for node in current_level:
            next_level.update(G.predecessors(node))
            next_level.update(G.successors(node))
        subgraph_nodes.update(next_level)
        current_level = next_level

    filtered_nodes: dict[NodeType, dict[str, Any]] = {
        ntype: {} for ntype in NodeType
    }
    filtered_edges = []

    for ntype, nodes in graph.nodes.items():
        for node_id, node_meta in nodes.items():
            if node_id in subgraph_nodes:
                filtered_nodes[ntype][node_id] = node_meta

    for edge in graph.edges:
        if edge.src_id in subgraph_nodes and edge.dst_id in subgraph_nodes:
            filtered_edges.append(edge)

    return GraphStructure(
        nodes=filtered_nodes,
        edges=filtered_edges,
    )


def analyze_graph_connectivity(graph: GraphStructure) -> dict[str, Any]:
    """分析图连通性

    Args:
        graph: 图结构数据

    Returns:
        连通性分析结果
    """
    G = to_networkx(graph)

    num_nodes = G.number_of_nodes()
    num_edges = G.number_of_edges()

    is_weakly_connected = nx.is_weakly_connected(G)
    is_strongly_connected = nx.is_strongly_connected(G)

    density = nx.density(G)

    avg_degree = sum(dict(G.degree()).values()) / num_nodes if num_nodes > 0 else 0

    degree_counts = defaultdict(int)
    for _, degree in G.degree():
        if degree == 0:
            degree_counts["isolated"] += 1
        elif degree == 1:
            degree_counts["leaf"] += 1
        else:
            degree_counts["connected"] += 1

    return {
        "num_nodes": num_nodes,
        "num_edges": num_edges,
        "is_weakly_connected": is_weakly_connected,
        "is_strongly_connected": is_strongly_connected,
        "density": density,
        "avg_degree": avg_degree,
        "degree_distribution": dict(degree_counts),
    }


def get_node_neighbors(
    graph: GraphStructure,
    node_id: str,
    edge_type: RelationType | None = None,
) -> dict[str, list[str]]:
    """获取节点的邻居

    Args:
        graph: 图结构数据
        node_id: 节点ID
        edge_type: 边类型过滤

    Returns:
        邻居节点字典 {relation_type: [neighbor_ids]}
    """
    neighbors: dict[str, list[str]] = defaultdict(list)

    for edge in graph.edges:
        if edge.src_id == node_id:
            if edge_type is None or edge.relation_type == edge_type:
                neighbors[edge.relation_type.value].append(edge.dst_id)

    return dict(neighbors)


def validate_graph_structure(graph: GraphStructure) -> list[str]:
    """验证图结构

    Args:
        graph: 图结构数据

    Returns:
        错误信息列表
    """
    errors = []

    for ntype in NodeType:
        if ntype not in graph.nodes:
            errors.append(f"Missing node type: {ntype.value}")
            continue

        for node_id, node_meta in graph.nodes[ntype].items():
            if node_meta.node_id != node_id:
                errors.append(f"Node ID mismatch for {node_id}")
            if node_meta.node_type != ntype:
                errors.append(f"Node type mismatch for {node_id}")

    node_ids = set()
    for ntype, nodes in graph.nodes.items():
        for node_id in nodes:
            if node_id in node_ids:
                errors.append(f"Duplicate node ID: {node_id}")
            node_ids.add(node_id)

    for edge in graph.edges:
        if edge.src_id not in node_ids:
            errors.append(f"Edge source node not found: {edge.src_id}")
        if edge.dst_id not in node_ids:
            errors.append(f"Edge destination node not found: {edge.dst_id}")

    return errors
