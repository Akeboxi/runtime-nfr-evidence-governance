# Runtime NFR 引用证据矩阵

_核验日期：2026-07-24。用途：将正文主张绑定到可解析的 DOI、标准页或官方规范；编号即正文首次出现顺序。_

| 编号 | 证据主线 | 可支撑的主张 | 来源 | DOI / 官方 URL | 核验 |
| ---: | --- | --- | --- | --- | --- |
| 1 | NFR 与运行时需求 | 需求工程必须区分环境事实、需求与系统规约 | Zave & Jackson, *Four Dark Corners of Requirements Engineering*, 1997 | https://doi.org/10.1145/237432.237434 | Crossref |
| 2 | NFR 与运行时需求 | “非功能需求”概念存在术语与边界歧义 | Glinz, *On Non-Functional Requirements*, 2007 | https://doi.org/10.1109/RE.2007.45 | Crossref |
| 3 | NFR 与运行时需求 | NFR 可按目标及其操作化关系组织 | Chung et al., *The NFR Framework in Action*, 2000 | https://doi.org/10.1007/978-1-4615-5269-7_2 | Crossref |
| 4 | NFR 与运行时需求 | 运行时监测可用于发现需求偏离 | Fickas & Feather, *Requirements Monitoring in Dynamic Environments*, 1995 | https://doi.org/10.1109/ISRE.1995.512555 | Crossref |
| 5 | NFR 与运行时需求 | 质量目标可采用部分满足而非二值满足 | Letier & van Lamsweerde, *Reasoning about Partial Goal Satisfaction*, 2004 | https://doi.org/10.1145/1029894.1029905 | Crossref |
| 6 | NFR 与运行时需求 | 自适应系统规约应显式表达不确定性 | Whittle et al., *RELAX*, 2009 | https://doi.org/10.1109/RE.2009.36 | Crossref |
| 7 | NFR 与运行时需求 | 需求可作为运行时实体参与系统推理 | Bencomo et al., *Requirements Reflection*, 2010 | https://doi.org/10.1145/1810295.1810329 | Crossref |
| 8 | NFR 与运行时需求 | 自适应系统的需求问题包含监测、偏离和恢复 | Jureta et al., *The Requirements Problem for Adaptive Systems*, 2015 | https://doi.org/10.1145/2629376 | Crossref |
| 9 | NFR 与运行时需求 | 模型可在运行时支持云服务监测 | García-Galán et al., *Models@runtime for Monitoring Cloud Services*, 2017 | https://doi.org/10.1109/SERVICES.2017.14 | Crossref |
| 10 | NFR 与运行时需求 | 运行时监测框架需明确采样与监测语义 | Tigris: A DSL and Framework for Monitoring Software Systems at Runtime, 2021 | https://doi.org/10.1016/j.jss.2021.110963 | Crossref |
| 11 | NFR/SLO | SLI 是测量，SLO 是由利益相关者目标决定的目标值 | Google SRE, *Service Level Objectives* | https://sre.google/sre-book/service-level-objectives/ | 官方 |
| 12 | NFR/SLO | SLO 实施应从用户关切反推指标和目标 | Google SRE Workbook, *Implementing SLOs* | https://sre.google/workbook/implementing-slos/ | 官方 |
| 13 | NFR/SLO | 软件质量模型用于定义、测量和评价质量需求 | ISO/IEC 25010:2023 | https://www.iso.org/standard/78176.html | 官方 |
| 14 | 遥测 | 指标、日志与追踪需有统一、可版本化的遥测语义 | OpenTelemetry Specification 1.59 | https://opentelemetry.io/docs/specs/otel/ | 官方 |
| 15 | 遥测 | 指标交换格式应保留类型、单位和标签语义 | OpenMetrics Specification | https://openmetrics.io/ | 官方 |
| 16 | 证据质量 | 数据质量不能被“数值准确”单一维度替代 | Wang & Strong, *Beyond Accuracy*, 1996 | https://doi.org/10.1080/07421222.1996.11518099 | Crossref |
| 17 | 测量质量 | 测量结果应伴随不确定性与可复核信息 | JCGM 100:2008, *Guide to the Expression of Uncertainty in Measurement* | https://www.bipm.org/documents/20126/2071204/JCGM_100_2008_E.pdf | 官方 |
| 18 | 阈值学习 | 极值理论可用于流式异常阈值而无需静态标签 | Siffer et al., *Anomaly Detection in Streams with Extreme Value Theory*, 2017 | https://doi.org/10.1145/3097983.3098144 | Crossref |
| 19 | 遥测异常 | 序列模型可从运行日志学习正常执行模式 | Du et al., *DeepLog*, 2017 | https://doi.org/10.1145/3133956.3134015 | Crossref |
| 20 | 遥测异常 | 日志异常检测需同时处理顺序和计数变化 | Meng et al., *LogAnomaly*, 2019 | https://doi.org/10.24963/ijcai.2019/658 | Crossref |
| 21 | 遥测异常 | 多变量时序异常检测可建模随机性与变量依赖 | Su et al., *OmniAnomaly*, 2019 | https://doi.org/10.1145/3292500.3330672 | Crossref |
| 22 | 遥测异常 | 无监督重构模型可用于多变量时序异常评分 | Audibert et al., *USAD*, 2020 | https://doi.org/10.1145/3394486.3403392 | Crossref |
| 23 | 遥测异常 | 日志表示变化会削弱异常检测的外部稳定性 | Zhang et al., *Robust Log-based Anomaly Detection on Unstable Log Data*, 2019 | https://doi.org/10.1145/3338906.3338931 | Crossref |
| 24 | 漂移 | 数据流模型必须区分平稳性能与概念漂移 | Gama et al., *A Survey on Concept Drift Adaptation*, 2014 | https://doi.org/10.1145/2523813 | Crossref |
| 25 | 基准 | 异常检测评估应使用有时间结构且可复现的基准 | Lavin & Ahmad, *Evaluating Real-Time Anomaly Detection Algorithms*, 2015 | https://doi.org/10.1109/MLSP.2015.7324013 | IEEE |
| 26 | 证据质量 | 缺失数据机制影响统计推断与可解释性 | Little & Rubin, *Statistical Analysis with Missing Data*, 2019 | https://doi.org/10.1002/9781119013563 | Crossref |
| 27 | 证据质量 | 数据质量改进需同时覆盖定义、测量和过程控制 | Batini & Scannapieco, *Data and Information Quality*, 2016 | https://doi.org/10.1007/978-3-319-24106-7 | Crossref |
| 28 | 测量质量 | 工程测量应报告分辨率、重复性与误差来源 | NIST/SEMATECH e-Handbook of Statistical Methods | https://www.itl.nist.gov/div898/handbook/ | 官方 |
| 29 | 证据质量 | 数据质量测量可纳入系统与软件质量评价 | ISO/IEC 25024:2015 | https://www.iso.org/standard/35749.html | 官方 |
| 30 | 人在回路 | 自动化应按决策阶段和风险配置不同人机参与水平 | Parasuraman et al., *A Model for Types and Levels of Human Interaction with Automation*, 2000 | https://doi.org/10.1109/3468.844354 | Crossref |
| 31 | 人在回路 | 系统应支持恰当依赖而非最大化对自动化的信任 | Lee & See, *Trust in Automation*, 2004 | https://doi.org/10.1518/hfes.46.1.50_30392 | Crossref |
| 32 | 人在回路 | AI 系统应在不确定或异常情况下支持校正、解释和退出 | Amershi et al., *Guidelines for Human-AI Interaction*, 2019 | https://doi.org/10.1145/3290605.3300233 | Crossref |
| 33 | 工程治理 | 数据依赖和隐式反馈会形成难以见到的系统技术债 | Sculley et al., *Hidden Technical Debt in Machine Learning Systems*, 2015 | https://papers.nips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems | 官方论文页 |
| 34 | 人在回路 | 人会因看到算法犯错而产生算法厌恶，需保留可修正入口 | Dietvorst et al., *Algorithm Aversion*, 2015 | https://doi.org/10.1037/xge0000033 | Crossref |
| 35 | 统计评价 | 类别不平衡时 PR 曲线与 ROC 曲线有确定关系 | Davis & Goadrich, 2006 | https://doi.org/10.1145/1143844.1143874 | Crossref |
| 36 | 统计评价 | 不平衡分类中 PR 曲线通常比 ROC 更能反映阳性识别质量 | Saito & Rehmsmeier, 2015 | https://doi.org/10.1371/journal.pone.0118432 | Crossref |
| 37 | 统计评价 | 高判别性能不保证概率校准良好 | Guo et al., *On Calibration of Modern Neural Networks*, 2017 | https://doi.org/10.48550/arXiv.1706.04599 | arXiv |
| 38 | 一致性 | 高原始一致率与低 κ 可在边际分布极端时并存 | Feinstein & Cicchetti, 1990 | https://doi.org/10.1016/0895-4356(90)90158-L | Crossref |
| 39 | 一致性 | 内容分析可靠性应结合量表、抽样和分布解释 | Krippendorff, *Content Analysis*, 2018 | https://doi.org/10.4135/9781071878781 | 出版社 |
| 40 | 微服务诊断 | 指标和服务依赖可联合用于性能问题根因定位 | Wu et al., *MicroRCA*, 2020 | https://doi.org/10.1109/NOMS47738.2020.9110353 | Crossref |
| 41 | 微服务诊断 | 大规模微服务根因定位可利用调用传播模式 | Wang et al., *MicroHECL*, 2021 | https://doi.org/10.1109/ICSE-SEIP52600.2021.00043 | Crossref |
| 42 | 微服务诊断 | 追踪谱分析可用于端到端延迟问题定位 | Yu et al., *MicroRank*, 2021 | https://doi.org/10.1145/3442381.3449905 | Crossref |
| 43 | 微服务诊断 | 多源遥测与图结构可用于端到端故障排查 | Lee et al., *Eadro*, 2023 | https://doi.org/10.1109/ICSE48619.2023.00150 | Crossref |
| 44 | 外部基准 | 微服务 RCA 的数据与评价协议需要统一基准 | Pham et al., *RCAEval*, 2025 | https://doi.org/10.1145/3701716.3715290 | Crossref |
| 45 | 可操作诊断 | 诊断模型的价值还取决于输出是否可解释、可操作 | *Making Fault Localization in Online Service Systems More Actionable and Interpretable*, 2025 | https://doi.org/10.1145/3714466 | Crossref |
| 46 | 多模态诊断 | 多模态微服务诊断需处理任务差异与视图不变性 | *TVDiag*, 2026 | https://doi.org/10.1145/3734868 | Crossref |
| 47 | 图表示 | 归纳式邻域聚合可支持未见节点的图表示 | Hamilton et al., *Inductive Representation Learning on Large Graphs*, 2017 | https://doi.org/10.48550/arXiv.1706.02216 | arXiv |
| 48 | 图表示 | 图卷积可作为拓扑信息融入预测的基础机制 | Kipf & Welling, *Semi-Supervised Classification with Graph Convolutional Networks*, 2017 | https://doi.org/10.48550/arXiv.1609.02907 | arXiv |

## 使用约束

- [11] 明确指出 SLO 目标选择不是纯技术活动，因此本文历史遥测输出只能称为“候选 NFR 边界”，不得称为正式 SLO。
- [16]、[17]、[26]—[29] 支撑“证据质量属于方法输出”的设计，但不为任何具体阈值背书。
- [30]—[34] 支撑拒绝操作化、转交利益相关者与上下文审查；本文没有研究人因负荷或自动决策授权。
- [35]—[39] 用于解释 PR-AUC、校准和一致性；全通过零方差情形只报告原始一致率，不计算或解释 κ/α。
- [40]—[48] 说明拓扑和多源遥测在诊断中的潜力；本文的负结果仅针对当前冻结的静态、不完整拓扑表示。
