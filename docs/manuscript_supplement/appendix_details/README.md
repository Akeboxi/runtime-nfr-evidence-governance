# 论文附录补充记录

这里保存论文附录压缩后移出的明细，以及原五项问卷的完整名称映射。研究结果沿用既有记录，本次仅调整论文展示位置。

## 内容

| 文件 | 原论文位置及用途 |
| --- | --- |
| [supplementary_appendix.md](supplementary_appendix.md) | 原A1—A5及图A1的完整可读说明 |
| [evaluation_freeze_timeline.csv](evaluation_freeze_timeline.csv) | 原A1，七个阶段的工作与冻结边界 |
| [questionnaire_mapping_full.csv](questionnaire_mapping_full.csv) | 原A2，五项问卷原题、变量名与报告名称 |
| [formative_repair_cards.csv](formative_repair_cards.csv) | 原A3，24张形成性修复卡的状态分组与评分中位数 |
| [blinded_evaluation_metrics.csv](blinded_evaluation_metrics.csv) | 原A4，首次盲态评价的指标与解释 |
| [interface_conformance_cases.csv](interface_conformance_cases.csv) | 原A5，14个历史接口用例的预期与观察结果 |
| [figure_A1_rcaeval_refusal.png](figure_A1_rcaeval_refusal.png) | 原图A1，RCAEval字段适配、短历史与拒绝路径 |
| [appendix_numbering.csv](appendix_numbering.csv) | 原A1—A13与压缩后A1—A9的对应关系 |

论文保留原A7—A13的全部表格内容，压缩后编号为A3—A9。原A6编号为A2。原A2只保留正文报告的两项评分，编号为A1，完整五项题目在本目录保存。

## 核对

在本目录执行下列命令，无需安装第三方依赖。

```text
python verify_appendix_materials.py
```

程序核对文件哈希、记录数量、形成性修复卡总数、盲态一致率和接口用例结果。哈希清单记录的是本目录文件，不替代上游数据或完整实验的重建。

## 与实验材料的关系

发布仓库中的历史材料见 [artifacts/core/rcaeval](../../../artifacts/core/rcaeval/)、[首次盲态评价](../../../artifacts/blinded-routing/)、[受控B/D评价](../../../artifacts/bd-boundary-v1.1/)。当前方法实现及测量资料审计见 [revisions/slo-evidence-1.2.0](../../../revisions/slo-evidence-1.2.0/)。这些相对链接按GitHub仓库中的发布位置解析。

历史v1接口行为、v1.1受控人员评价、当前实现和测量资料审计分别解释。形成性修复卡用于复核形成性修改，RCAEval用例只检查短历史拒绝路径。总体路由归档能确认版本，首次查看总体分布的时间点未单独保存，相关分析按描述性结果报告。

本目录只包含论文已报告的汇总、协议说明和图，不含评价者身份或原始自由文本。图及派生统计保留相应上游数据的许可条件。AIOps 2025资料适用CC BY-NC 4.0，RCAEval资料适用CC BY 4.0；本目录不再次分发上游原始数据。
