# 数据字典

## 专家可见字段

| 字段 | 含义 |
| --- | --- |
| `card_id` | 盲化卡编号 |
| `service` / `sli` | 服务与运行时质量指标 |
| `candidate_value` | 真实候选值（不等于已批准 SLO） |
| `history_coverage` / `valid_samples` | 历史覆盖与有效分钟样本数 |
| `Q95` / `median` / `MAD` | 冻结历史统计量 |
| `M1_metric_definition` | 指标来源、定义和单位 |
| `M2_sampling` | 采样周期与观测分辨率 |
| `M3_aggregation` | 原始观测形成统计量的规则 |
| `M4_special_value` | 零值/尺度下限的解释；含“情景设定”的内容不是原数据集字段 |
| `runtime_context` | 长尾、负载或业务上下文提示 |
| `choice` | A/B/C/D 单选 |
| `confidence` | 1—5 |
| `reason` | 1—2 句主要依据 |

## 作者私有字段

`stratum`、M1—M4 判定值、`measurement_explainable`、`v1_route`、`v1_1_route`、源卡 ID、事件 ID、源治理状态和源标志只存在于 `researcher_only/`。
