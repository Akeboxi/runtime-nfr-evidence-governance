# A02：治理规则触发、重叠与优先级敏感性

所有场景均为 `counterfactual audit`，主规则保持不变。

## 独立触发

| 规则 | 触发数 |
| --- | --- |
| history | 1054 |
| resolution | 2735 |
| tail | 1082 |

## 反事实状态变化

| 场景 | 变化卡 | insufficient_evidence | threshold_unresolved | needs_context_review | candidate_for_stakeholder_review |
| --- | --- | --- | --- | --- | --- |
| drop_history | 1054 | 0 | 2735 | 1082 | 4851 |
| drop_resolution | 2424 | 1054 | 0 | 948 | 6666 |
| drop_tail | 948 | 1054 | 2424 | 0 | 5190 |
| swap_history_resolution | 311 | 743 | 2735 | 948 | 4242 |
| swap_resolution_tail | 0 | 1054 | 2424 | 948 | 4242 |
| tail_cutoff_9.5 | 9 | 1054 | 2424 | 957 | 4233 |
| tail_cutoff_10.5 | 19 | 1054 | 2424 | 929 | 4261 |

不得据此声称某个优先级或长尾阈值“最优”。
