# 回收数据字典

| 字段 | 类型/取值 | 说明 |
| --- | --- | --- |
| `evaluator_id` | E01/E02/E03 | 去标识评价者编号 |
| `presented_order` | 1–32 | 该评价者实际看到的顺序 |
| `blind_card_id` | `blind-*` | 盲化卡 ID |
| `blinded_choice` | A/B/C/D | 第一阶段独立处置 |
| `confidence_1_to_5` | 1–5 | 第一阶段判断信心 |
| `stage1_started_at` | ISO 8601 | 页面或案例开始时间；平台不能提供时可空 |
| `stage1_submitted_at` | ISO 8601 | 第一阶段提交时间 |
| `stage1_duration_seconds` | 非负数 | 单卡耗时；只作描述 |
| `stage1_comment` | 文本 | 可选，最多 80 汉字 |
| `post_reveal_decision` | unchanged/changed_to_protocol/changed_away_from_protocol | 揭示后的改变方向 |
| `final_choice` | A/B/C/D | 揭示后的最终选择；未改变时可等于第一阶段 |
| `stage2_comment` | 文本 | 可选改变原因 |
| `completed` | true/false | 是否完整完成正式流程 |

私有评价者登记表与答卷分开保存。论文和匿名审稿包中不得出现姓名、邮箱、具体单位或原始联系信息。

