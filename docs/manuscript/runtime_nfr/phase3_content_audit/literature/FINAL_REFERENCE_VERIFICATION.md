# 新增最近邻参考文献核验

| 正文编号 | Crossref/出版方 | 第二来源 | 字段核对 | 结论 |
| --- | --- | --- | --- | --- |
| [65] | Crossref：`10.1109/ASE63991.2025.00009`；IEEE，ASE 2025 | DBLP `conf/kbse/YuMWLCPX25`、ASE 2025 程序页 | 7 名作者、题名、年份和页码 1—12 一致 | Verified |
| [66] | arXiv `2603.12268` | DBLP `journals/corr/abs-2603-12268` | 6 名作者、题名和 2026 年一致；仅按预印本引用 | Verified with preprint boundary |
| [67] | IEEE Xplore：`10.1109/DSA63982.2024.00046` | Crossref | 2 名作者、题名、DSA 2024 和页码 297—304 一致 | Verified |

核验规则：DOI 指向题名和作者必须一致；会议年份与在线发布日期分开处理；预印本不得写成同行评议会议或期刊论文。三条新增引用均已有正文落点。

## 全表收口

- `[1]`—`[48]` 复用 2026-07-24 的 `references/REFERENCE_EVIDENCE_MATRIX.md`；该矩阵逐条保存 DOI、标准页或官方规范入口。
- `[49]`—`[64]` 复用阶段二 `nearest_neighbor_verified.bib` 与 `nearest_neighbor_comparison.md` 的交叉核验结果；本轮未把已关闭的最近邻检索扩展为泛化综述。
- 本轮针对全表扫描中元数据不完整的 `[10]`、`[45]`、`[46]` 再查 Crossref，并补齐作者、完整题名、刊名、卷期和页码：

| 正文编号 | Crossref 记录 | 修复结果 |
| --- | --- | --- |
| `[10]` | `10.1016/j.jss.2021.110963` | Mertz 与 Nunes；JSS 177，文章号 110963 |
| `[45]` | `10.1145/3714466` | 7 名作者；TOSEM 34(6)，1–26 |
| `[46]` | `10.1145/3734868` | 7 名作者；补全“with multimodal data”；TOSEM 35(2)，1–39 |

## 公开数据集引用补充

| 正文编号 | 官方记录 | 核验结果 |
| --- | --- | --- |
| `[68]` | OpenAIOps AgenticOpsEval，`AIOps2025` commit `57c36fa46fb2f4dec19b5f5ca9cbf5a90f9c9e00` | 官方 README 给出 18 个按日期归档、校验方法及 CC BY-NC 4.0 |
| `[69]` | Zenodo record `14590730`，DOI `10.5281/zenodo.14590730` | 记录题名、创建者、`RE1-OB.zip`、MD5 与 CC BY 4.0 已通过 Zenodo API 核对 |

最终范围为 `[1]`—`[69]`。正文编号连续，新增 `[65]`—`[69]` 和补正的三条记录均有正文落点；预印本 `[66]` 保留预印本边界。
