# 真实故障诊断时延

每类选择 5 次独立核验通过的原生故障。主口径为故障生效后原生检查时刻到首个具有同次直接证据的 high 诊断；进程退出另列容器 FinishedAt 至同一诊断的耗时。所有时间取保存的原始 UTC；time_confidence 按原值保留。

| 场景 | n | 最小 / 中位 / P95 / 最大（秒） | 时间质量计数 |
|---|---:|---|---|
| CPU 配额 | 5 | 111.193 / 118.368 / 130.091 / 130.091 | {"trusted":5} |
| Agent 进程退出 | 5 | 14.274 / 19.915 / 20.996 / 20.996 | {"trusted":3,"degraded":2} |
| 工具 HTTP 503 | 5 | 53.116 / 67.027 / 79.297 / 79.297 | {"degraded":5} |

| 场景 / 编号 | run ID | 主时延（秒） | 实际退出至诊断（秒） | 时间质量 |
|---|---|---:|---:|---|
| cpu-quota / 20 | `lab-8bff3868bb64` | 122.328 | - | trusted |
| cpu-quota / 21 | `lab-70cadba153cf` | 130.091 | - | trusted |
| cpu-quota / 22 | `lab-89ec0ef84e61` | 118.213 | - | trusted |
| cpu-quota / 23 | `lab-3589d7c2990b` | 111.193 | - | trusted |
| cpu-quota / 24 | `lab-3662671a424e` | 118.368 | - | trusted |
| process-failure / 16 | `lab-f43290b386ca` | 14.274 | 15.916 | trusted |
| process-failure / 17 | `lab-7146aaba8637` | 20.040 | 22.760 | trusted |
| process-failure / 18 | `lab-bfbdabbc8880` | 20.996 | 21.919 | degraded |
| process-failure / 19 | `lab-044536d8a5a8` | 18.277 | 18.997 | trusted |
| process-failure / 20 | `lab-d968a3a2199e` | 19.915 | 20.911 | degraded |
| tool-503 / 11 | `lab-ce00adc28f21` | 78.715 | - | degraded |
| tool-503 / 12 | `lab-a119217b89df` | 67.027 | - | degraded |
| tool-503 / 13 | `lab-ca0aac1adeec` | 79.297 | - | degraded |
| tool-503 / 14 | `lab-b26cface558b` | 53.116 | - | degraded |
| tool-503 / 15 | `lab-7706505db9c4` | 57.106 | - | degraded |

P95 使用排序后的 round(0.95 × (n-1)) 下标，五个样本的 P95 通常等于最大值。每类仅 5 次实验，结果是描述性统计，不代表性能 SLA。工程上的事件型 30 秒、持续型 90 秒目标需要逐轮比较，超过目标的记录保留。high 表示证据支持等级，degraded 时间质量不改写为可信时钟。

原始时间、诊断引用、逐轮恢复跨度和所有排除记录的原始状态，见材料 `evidence/final/formal-scenario-latency-statistics.json`、两份批次审计与 `evidence/runs/`。未选入统计的历史通过不会计为失败；CPU19 原生统计超时为失败，补偿恢复记录保留。
