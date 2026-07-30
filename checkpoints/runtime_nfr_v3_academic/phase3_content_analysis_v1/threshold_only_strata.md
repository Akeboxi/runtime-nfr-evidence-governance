# P3-A01：threshold-only 责任暴露分层

最小 threshold-only 输出会隐藏 4,426/8,668（51.1%）张卡的证据状态或责任动作。该比例不是错误率或准确率。

## 按 SLI

| SLI | 卡片 | 隐藏责任 | 比例 |
| --- | --- | --- | --- |
| error_ratio | 4334 | 2951 | 68.1% |
| latency | 4334 | 1475 | 34.0% |

## 服务范围

11 个服务的隐藏比例为 11.9%–60.5%；完整 service、segment、date 和 coverage 分层见 JSON/CSV。
