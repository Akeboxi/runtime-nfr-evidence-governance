# B/D 责任边界修订独立验证包

状态：材料、原始回收件与分析均已冻结；72 条匿名判断已完成导入审计和预设分析。  

结果摘要见 `analysis/results/RESULTS_REPORT_ZH.md`，完整结果工作簿见 `analysis/BD_boundary_validation_results.xlsx`。冻结主分析仍按 24 张卡整体聚类 bootstrap；保持 6/6/4/4/4 配额的分层重抽只作为敏感性分析。卡片多数票见 `analysis/results/card_majority.csv`。姓名和签名不收集，匿名处理口径见 `ANONYMOUS_PARTICIPATION_AMENDMENT.md`。
实验类型：真实候选锚定的受控测量情景盲评。  
样本：24 张卡 × 3 名新专家 = 72 个判断。

## 先看这三个文件

1. `FEASIBILITY_AUDIT.md`：为什么必须使用受控情景；
2. `EXPERIMENT_PROTOCOL.md`：冻结设计、M1—M4、抽样和终点；
3. `EXPERT_DISPATCH_GUIDE.md`：具体给谁发、发什么、邮件正文和回收要求。

## 目录边界

- `expert_send/`：三个可直接发送的 ZIP；
- `researcher_only/`：答案键、源卡映射、随机顺序和合并模板，严禁发给专家；
- `responses/`：空白回收目录和统一 CSV 模板；
- `qa/`：工作簿与 DOCX 渲染检查，不属于专家材料；
- `scripts/`：冻结分析和验证脚本。

首份正式答卷产生后，不得重新运行构建脚本替换卡片或顺序。
