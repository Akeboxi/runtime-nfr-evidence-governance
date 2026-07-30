#!/usr/bin/env python3
"""
故障传播路径分析脚本

在模型训练完成后，对生成的数据集进行故障传播路径分析。

用法:
    python -m src.inference.fault_propagation_analysis \
        --model ./checkpoints/best_model.pt \
        --data-root ./mock_real_data/generated_dataset/catalog_id_01 \
        --tenant-id tenant_0000 \
        --output-dir ./fault_analysis
"""

import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np
import torch

from ..data.tenant_dataset import load_tenant_dataset
from .engine import TenantInferenceEngine
from .risk_pathway import NodeRiskInfo


def analyze_fault_propagation(
    model_path: str,
    data_root: str,
    tenant_id: str,
    window_size: int = 32,
    stride: int = 32,
    hidden_dim: int = 128,
    device: str = "cpu",
    max_samples: int = 10,
    output_dir: Optional[str] = None,
) -> dict:
    """分析故障传播路径

    Args:
        model_path: 模型路径
        data_root: 数据集根目录
        tenant_id: 租户ID
        window_size: 窗口大小
        stride: 滑动步长
        hidden_dim: 隐藏层维度
        device: 设备
        max_samples: 最大分析样本数
        output_dir: 输出目录

    Returns:
        分析结果字典
    """
    # 加载数据集
    dataset = load_tenant_dataset(
        data_root=data_root,
        tenant_id=tenant_id,
        window_size=window_size,
        stride=stride,
        use_loess_residual=False,
    )

    # 获取特征维度
    static_dims = dataset.get_feature_dims()
    temporal_dims = dataset.get_temporal_feature_dims()

    print(f"数据集: {len(dataset)} 样本")
    print(f"静态特征维度: {static_dims}")
    print(f"时序特征维度: {temporal_dims}")

    # 加载模型
    engine = TenantInferenceEngine.from_checkpoint(
        checkpoint_path=model_path,
        static_dims=static_dims,
        temporal_dims=temporal_dims,
        hidden_dim=hidden_dim,
        device=device,
        track_pathway=True,
    )

    results = []
    high_risk_samples = []

    print(f"\n开始分析 {min(max_samples, len(dataset))} 个样本...")
    print("=" * 80)

    for i in range(min(max_samples, len(dataset))):
        sample = dataset[i]
        result = engine.predict(sample)

        # 收集基本信息
        sample_info = {
            "sample_idx": i,
            "record_id": sample.metadata.record_id,
            "case": sample.metadata.case,
            "change_host_id": sample.metadata.change_host_id,
            "change_window": [sample.metadata.change_start_idx, sample.metadata.change_end_idx],
            "global_risk_score": float(result.global_risk_score),
            "high_risk_count": int((result.risk_probs >= 0.7).sum()),
            "total_nodes": len(result.risk_probs),
        }

        # 获取节点风险分布
        node_risks = []
        all_node_ids = []
        for ntype, ids in sample.node_ids.items():
            for nid in ids:
                all_node_ids.append((ntype, nid))

        for idx, (ntype, nid) in enumerate(all_node_ids):
            prob = float(result.risk_probs[idx]) if idx < len(result.risk_probs) else 0.0
            label = int(sample.labels[idx].item()) if idx < len(sample.labels) else 0
            node_risks.append({
                "node_id": nid,
                "node_type": ntype,
                "risk_prob": prob,
                "is_anomaly": label == 1,
                "is_high_risk": prob >= 0.7,
            })

        sample_info["node_risks"] = node_risks

        # 层贡献
        if result.layer_contributions:
            layer_data = {}
            for layer_id, contrib in result.layer_contributions.items():
                layer_data[f"layer_{layer_id}"] = {
                    "layer_name": contrib.layer_name,
                    "nodes": [
                        {"node_id": n.node_id, "risk_score": float(n.risk_score)}
                        for n in contrib.nodes[:5]  # 只保留前5个
                    ],
                }
            sample_info["layer_contributions"] = layer_data

        # 风险路径
        if result.risk_pathways:
            sample_info["risk_pathways"] = [
                {
                    "source_node": p.source_node,
                    "target_nodes": p.target_nodes,
                    "chain": [n.node_id for n in p.pathway_nodes],
                    "total_risk": float(p.total_risk),
                    "prisk": float(p.prisk),
                }
                for p in result.risk_pathways[:5]
            ]

        results.append(sample_info)

        # 高风险样本标记
        if sample_info["high_risk_count"] >= 2:
            high_risk_samples.append(sample_info)

        # 打印摘要
        print(f"\n样本 {i}: record={sample_info['record_id']}, case={sample_info['case']}")
        print(f"  Global Risk: {sample_info['global_risk_score']:.4f}")
        print(f"  High Risk Nodes: {sample_info['high_risk_count']}/{sample_info['total_nodes']}")

        # 打印高风险节点
        high_risk_nodes = [n for n in node_risks if n["is_high_risk"]]
        if high_risk_nodes:
            print(f"  High Risk Nodes:")
            for node in high_risk_nodes:
                status = "ANOMALY" if node["is_anomaly"] else "normal"
                print(f"    [{node['node_type']}] {node['node_id']}: {node['risk_prob']:.4f} ({status})")

        # 打印层贡献（简化）
        if sample_info.get("layer_contributions"):
            for layer_key, layer_val in sample_info["layer_contributions"].items():
                top_node = layer_val["nodes"][0] if layer_val["nodes"] else None
                if top_node:
                    print(f"  {layer_val['layer_name']}: {top_node['node_id']} ({top_node['risk_score']:.2f})")

    print("\n" + "=" * 80)
    print(f"分析完成: {len(results)} 个样本")
    print(f"高风险样本数: {len(high_risk_samples)}")

    # 保存结果
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # 保存JSON结果
        with open(output_path / "fault_analysis_results.json", "w", encoding="utf-8") as f:
            json.dump({
                "total_samples": len(results),
                "high_risk_samples": len(high_risk_samples),
                "samples": results,
            }, f, ensure_ascii=False, indent=2)

        # 保存高风险样本详情
        if high_risk_samples:
            with open(output_path / "high_risk_samples.json", "w", encoding="utf-8") as f:
                json.dump(high_risk_samples, f, ensure_ascii=False, indent=2)

        print(f"\n结果已保存到: {output_path}")

    return {
        "total_samples": len(results),
        "high_risk_samples": len(high_risk_samples),
        "samples": results,
    }


def print_fault_propagation_summary(results: dict) -> None:
    """打印故障传播摘要"""
    print("\n" + "=" * 80)
    print("故障传播路径分析摘要")
    print("=" * 80)

    samples = results.get("samples", [])
    high_risk = results.get("high_risk_samples", 0)

    # 按case统计
    case_stats = {}
    for s in samples:
        case = s.get("case", "unknown")
        if case not in case_stats:
            case_stats[case] = {"count": 0, "high_risk": 0, "avg_risk": 0}
        case_stats[case]["count"] += 1
        case_stats[case]["high_risk"] += 1 if s["high_risk_count"] >= 2 else 0
        case_stats[case]["avg_risk"] += s["global_risk_score"]

    print("\n按Case统计:")
    print("-" * 60)
    for case, stats in sorted(case_stats.items()):
        avg_risk = stats["avg_risk"] / stats["count"] if stats["count"] > 0 else 0
        print(f"  {case}: {stats['count']} 样本, {stats['high_risk']} 高风险, 平均风险: {avg_risk:.4f}")

    # 高风险传播路径示例
    print("\n高风险传播路径示例:")
    print("-" * 60)
    for s in samples:
        if s["high_risk_count"] >= 2:
            print(f"\n  Record: {s['record_id']} (Case: {s['case']})")
            print(f"  Change Host: {s['change_host_id']}")
            print(f"  Global Risk: {s['global_risk_score']:.4f}")

            # 找到高风险节点
            high_risk_nodes = [n for n in s["node_risks"] if n["is_high_risk"]]
            print(f"  High Risk Nodes ({len(high_risk_nodes)}):")
            for node in high_risk_nodes:
                print(f"    - [{node['node_type']}] {node['node_id']}: {node['risk_prob']:.4f}")

            # 风险路径
            if s.get("risk_pathways"):
                print(f"  Propagation Pathways:")
                for pathway in s["risk_pathways"][:3]:
                    chain = " -> ".join(pathway["chain"])
                    print(f"    {pathway['source_node']} -> ... -> {pathway['target_nodes'][0] if pathway['target_nodes'] else 'N/A'}")
                    print(f"      Chain: {chain}")
                    print(f"      Risk: {pathway['total_risk']:.4f}, P_risk: {pathway['prisk']:.4f}")
            break


def main():
    parser = argparse.ArgumentParser(description="故障传播路径分析")
    parser.add_argument("--model", type=str, default="./checkpoints/best_model.pt", help="模型路径")
    parser.add_argument("--data-root", type=str, default="./mock_real_data/generated_dataset/catalog_id_01", help="数据集根目录")
    parser.add_argument("--tenant-id", type=str, default="tenant_0000", help="租户ID")
    parser.add_argument("--window-size", type=int, default=32, help="窗口大小")
    parser.add_argument("--stride", type=int, default=32, help="滑动步长")
    parser.add_argument("--hidden-dim", type=int, default=128, help="隐藏层维度")
    parser.add_argument("--device", type=str, default="cpu", help="设备")
    parser.add_argument("--max-samples", type=int, default=10, help="最大分析样本数")
    parser.add_argument("--output-dir", type=str, default="./fault_analysis", help="输出目录")
    parser.add_argument("--summary-only", action="store_true", help="只打印摘要")

    args = parser.parse_args()

    results = analyze_fault_propagation(
        model_path=args.model,
        data_root=args.data_root,
        tenant_id=args.tenant_id,
        window_size=args.window_size,
        stride=args.stride,
        hidden_dim=args.hidden_dim,
        device=args.device,
        max_samples=args.max_samples,
        output_dir=args.output_dir if not args.summary_only else None,
    )

    if args.summary_only:
        print_fault_propagation_summary(results)


if __name__ == "__main__":
    main()