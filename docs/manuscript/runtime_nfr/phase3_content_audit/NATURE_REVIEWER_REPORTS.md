# Runtime NFR 三审稿人预审报告

## Review setup

- **Input scope:** 完整 Markdown 稿件、阶段二冻结分析、最近邻核验表、W01—W11 关闭报告及图表设计报告。
- **Assessment boundary:** 本报告评估原创性、重要性、跨领域可读性、技术可靠性和非专业读者可理解性；不作编辑录用决定，也不假设未提供的外部 SLO、利益相关者批准或部署结果。
- **Shared manuscript claim summary:** 在缺少显式 SLO 时，历史遥测应被处理为带证据责任的候选边界，而不是自动规约；证据不足、分辨率不足和长尾上下文需要产生不同的拒绝、补证或转交动作。
- **Visible evidence base:** 8,668 张治理卡；threshold-only 信息差异；规则反事实；两轮专家；四人人工核查；RCAEval 拒绝链；六模型滚动 OOF；拓扑覆盖审计；正式哈希和完整性审计。
- **Missing materials affecting confidence:** 2025—2026 高风险最近邻的最终定向检索；阈值门禁的外部/规范依据；充分历史外部系统中的正向结果；利益相关者批准和线上效用；完整资源消耗记录。

## Reviewer 1

- **Overall assessment:** 论文具有清楚且有价值的技术伦理边界：观测事实不能直接替代规范目标。但当前最强结果主要证明协议按定义运行，而不是证明所选治理门槛具有独立效度。技术案例在经过一次实质内容修订后可能成立。
- **Who would be interested in the results, and why:** 运行时需求、SRE/AIOps、软件质量和人机协同治理研究者会关心，因为论文把“不能自动决定”建模为正式输出，并展示了可追溯拒绝链。
- **Major strengths:**
  - 原始阈值冻结，负结果和外部拒绝未被调参消除；
  - threshold-only、规则删除/换序、五折 OOF 和事件级 bootstrap 提供了多种反事实检查；
  - 专家阈值评分和治理评分被分开，避免把处置认可误写成阈值批准；
  - 人工核查、输入哈希和硬失败规则提供了较强复现基础。
- **Major concerns:**
  - 正文此前的长尾公式与实现不一致，说明当前自动数字审计没有覆盖方法定义；
  - 0.5、720 和 10.0 是冻结政策门槛，但没有被证明为自然或最优边界；
  - 51.1% 差异部分由“基线不输出治理字段”的定义直接产生；
  - 48/48 高治理中位数伴随天花板效应和负 α，且存在一条明确的硬边界状态异议；
  - 外部全拒绝不能建立正向外部适用性。
- **Technical failings that need to be addressed before the case is established:**
  1. 对候选公式、稳健尺度、零错误例外和全部治理门槛建立代码—正文一致性审计；
  2. 将 threshold-only 与规则触发交集、迁移和专家反例联合解释，避免把定义性信息差异写成效果；
  3. 报告专家状态/队列分层和唯一 `STATE_ERROR`；
  4. 把外部、预测和拓扑明确降为边界证据。
- **Assessment against Nature-style criteria:**
  - **Originality:** 责任协议组合有可辨识性，但单个机制并不新。
  - **Scientific importance:** 对需求规约责任的提醒具有领域价值；更广意义取决于能否证明该失败模式普遍存在。
  - **Interdisciplinary readership:** 人在回路和“拒绝是有效输出”的思想可超出软件工程，但当前证据仍是单一云原生环境。
  - **Technical soundness:** 冻结与审计设计较强，规则效度和方法定义仍需修复。
  - **Readability for nonspecialists:** 核心矛盾易懂，但摘要同时列出外部、人工、预测和拓扑，负担过重。
- **Recommendation posture:** 需要一次以方法一致性和证据角色为中心的重大内容修订。

## Reviewer 2

- **Overall assessment:** 论文最有潜力的原创点不是阈值算法，而是把描述性遥测与规范性目标之间的责任断点变成可审计协议。当前相关工作已经比初稿强，但正文对比表没有完全使用内部六维核验成果，因而创新性仍容易被概括为“分位数后加人工规则”。
- **Who would be interested in the results, and why:** Requirements at runtime、NFR operationalization、SLO engineering、可信自动化和软件治理社区会关心，因为论文讨论谁有权把数值升级为目标。
- **Major strengths:**
  - 明确否认“自动发现正确 SLO”，主张范围较诚实；
  - 将拒绝、补证、上下文审查和利益相关者确认区分为责任动作；
  - 最近邻核验已覆盖告警反馈、测量停止、SLO 扩散和故障预测；
  - 专家结果表明阈值可信度低与治理适当性高可以同时出现。
- **Major concerns:**
  - 2025—2026 年无显式 SLO 的数据驱动目标生成、选择性拒绝和运行时量化需求工作尚未完成最终排查；
  - 正文表 1 缺少证据审计、批准权和独立/外部验证的完整维度；
  - 第三项贡献将外部拒绝、预测和拓扑并列，削弱唯一核心贡献；
  - 预测部分技术完整度较高，反而可能诱导审稿人按预测论文要求评价。
- **Technical failings that need to be addressed before the case is established:**
  1. 完成一次受限的 2025—2026 最近邻检索并达到机制饱和；
  2. 用六维逐类/逐论文表替换当前弱化表 1；
  3. 将贡献改为“协议、机制证据、边界审计”层级，不把预测列为贡献；
  4. 在讨论中明确阈值门禁是可替换的治理政策，论文贡献是责任接口和审计方式。
- **Assessment against Nature-style criteria:**
  - **Originality:** 有限但可辩护；取决于最近邻排查和责任协议的精确定义。
  - **Scientific importance:** 对智能化需求工程有直接意义，但不应包装成普适自动化方法。
  - **Interdisciplinary readership:** 描述性数据与规范性决策的区分有更广兴趣，当前稿尚未充分提炼。
  - **Technical soundness:** 实证量大，创新对照仍不够直接。
  - **Readability for nonspecialists:** “历史表现不等于应当表现”是很好的入口，随后术语和边界实验过多。
- **Recommendation posture:** 原创性定位有希望，但在最近邻和贡献层级修订前尚未完全建立。

## Reviewer 3

- **Overall assessment:** 核心叙事对非专业读者是可理解的：系统拥有大量数据，却没有人授权这些数据成为目标。稿件的主要可读性问题不是语言粗糙，而是同时展示太多不同验证对象，使读者难以判断哪一个结果最重要。
- **Who would be interested in the results, and why:** 除需求工程和云原生系统外，研究可信自动化、数据治理、测量不确定性和人工审批的读者也会关心候选、证据和批准权的分离。
- **Major strengths:**
  - 问题具有直观的规范性冲突；
  - 首页图已经使用相同/相近候选值的不同处置案例；
  - 负结果被保留，增强可信度；
  - 有效性威胁明确承认构念、统计、外部和测量限制。
- **Major concerns:**
  - 中文摘要过密，连续出现专家、人工、外部、预测和拓扑数字；
  - 首页图仍以卡片摘要为主，没有显示原始遥测为何导致不同证据；
  - 图 6 同时承担预测信号、校准和拓扑负结果，主判断不够单一；
  - 工程制品规模和复现成本没有一个简洁入口。
- **Technical failings that need to be addressed before the case is established:**
  1. 将摘要压缩为问题、机制、三项核心证据和边界；
  2. 首页图加入冻结原始遥测缩略图，而不是再增加流程框；
  3. 将工作量与复现链整理为一张表；
  4. 把预测校准和拓扑完整性分开承载，避免多主张图。
- **Assessment against Nature-style criteria:**
  - **Originality:** 核心责任分离易于理解，但需要更清楚的最近邻对照。
  - **Scientific importance:** 领域意义明确，跨领域重要性目前是潜在而非已证明。
  - **Interdisciplinary readership:** 具有可扩展的治理思想，但单环境证据限制广泛外推。
  - **Technical soundness:** 证据链丰富；方法定义和门槛性质需要更透明。
  - **Readability for nonspecialists:** 开篇可读，结果段过载，主次需进一步收束。
- **Recommendation posture:** 经过结构性压缩和证据图重构后可显著改善；当前不宜直接语言润色。

## Cross-review synthesis

- **Consensus strengths:** 描述性遥测与规范性目标的责任分离清楚；拒绝和补证被建模为有效输出；冻结、哈希、负结果和多层审计增强可信度。
- **Consensus technical risks:** 规则门槛是作者定义的政策；threshold-only 差异存在定义性成分；专家天花板和硬边界反例未充分进入正文；正向外部效度缺失；最近邻正文对照不足。
- **Where emphasis differs across reviewers:** Reviewer 1 最关注方法一致性和规则效度；Reviewer 2 最关注原创性是否被 2025—2026 最近邻削弱；Reviewer 3 最关注主线过载和图表对非专业读者的解释能力。
- **Broad-interest / significance readout:** “数据能描述过去，但无权自动规定未来目标”具有超出 AIOps 的治理意义；当前单环境、无正式 SLO 终点的证据尚不足以证明广泛适用。
- **Most important issues to resolve before a strong case is established:**
  1. 固定并审计方法定义；
  2. 完成受限最近邻检索和六维正文对比；
  3. 以 threshold-only＋规则反事实＋专家反例重建核心证据链；
  4. 将外部、预测和拓扑降为边界；
  5. 最后才重构图片和润色。

## Risk / unsupported claims

- 不支持“治理比 threshold-only 更准确或更有效”；
- 不支持 0.5、720 或 10.0 为最优或自然阈值；
- 不支持专家批准原始阈值；
- 不支持 RCAEval 上的正向外部阈值效度；
- 不支持预测结果验证标签构念；
- 不支持拓扑或 GNN 普遍无效；
- 运行时间、CPU、内存和完整复现成本目前不可由现有材料可靠评估，标记为 `AUTHOR_INPUT_NEEDED` 或 `Unknown`。

