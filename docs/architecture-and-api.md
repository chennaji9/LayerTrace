# 架构、数据与公开接口

LayerTrace 采用一个 FastAPI 控制面、独立 worker、PostgreSQL、Prometheus，以及 VM 内的探针和容器化 AI 业务。浏览器使用同一版本化 API，数据采集、诊断、事件单与界面之间共用事实来源。

```text
ZSvirt只读API ── host/VM/原生告警与事件 ──┐
                                        ▼
VM内 Docker → 只读代理 → 探针 → 公开库存/事件API → PostgreSQL
CPU模型 ← Agent → HTTP工具                  ▲         │
           ▲                                │         ▼
       独立业务客户端 ── 同task分段与结果 ────┘      正式worker
探针9108 → Prometheus → 数值时序/查询                  │
                                                     ▼
浏览器 ← 签名会话/API ← 任务/事件单/诊断/故障时拓扑/证据
```

## 身份与关联

VM 使用平台 UUID；容器使用完整64位 Docker ID；服务实例使用容器 ID 与 StartedAt。名称和 IP 是展示字段。资源关系具有生效时间，重建关闭旧代次关系，迟到事件按原时间解析。任务使用唯一 task_id，将独立客户端、Agent、模型和工具事件关联。告警聚合使用对象和时间约束，平台恢复按 source_alarm_uuid 精确匹配。

事实追加且幂等。worker 使用独立 EventReceipt 消费水位与 PostgreSQL advisory lock；重复提交和重复消费不重复生成业务结果。SQLite outbox 在整批 ACK 后删除，断线保留原事件 ID 和时间。数据缺口、过期、不可用和 unresolved 都有状态输出。

## 诊断

R0 容器进程异常退出；R1 直接 OOM 证据；R2 同容器配额、节流和超过健康 P95 两倍且增加500ms的模型/任务延迟；R3 同任务工具失败与任务失败；R4 需要对应 host 的直接资源证据。当前真实演示覆盖 R0/R2/R3；R1/R4 与 GPU 场景使用明确来源的回归/回放，不声称本次硬件实测。

诊断保存 revision、支持级别、数值与阈值、证据/反证引用、缺失项、任务实际影响和潜在影响。基线按相关服务实例限定，至少30成功样本，使用 P95。缺少身份、同任务或直接证据时保留不足结论。确认和静默不改变原事实，恢复需要业务证据、关联源新鲜与稳定窗口。

## API 与用户入口

| 用途 | 入口 |
|---|---|
| 页面会话 | /login、/logout；签名HttpOnly/SameSite cookie，写操作CSRF |
| 数据源状态 | GET /api/v1/status |
| 当前/历史拓扑 | GET /api/v1/topology，at参数与root_id参数 |
| 业务任务 | GET /api/v1/tasks、GET /api/v1/tasks/{id} |
| 工作负载 | GET /api/v1/workloads |
| 事件单 | GET /api/v1/incidents、GET /api/v1/incidents/{id} |
| 诊断修订 | GET /api/v1/incidents/{id}/diagnoses |
| 证据 | GET /api/v1/evidence/{id} |
| 确认/静默 | POST /api/v1/incidents/{id}:acknowledge、POST /api/v1/silences |
| 探针 | POST /api/v1/probes:register、/{id}:heartbeat、:inventory、:events、:revoke |
| 开发回放 | replay模式公开API入口，保留replayed来源；prod默认关闭lab |

以启动服务生成的 `/openapi.json` 为机器可读契约；路径参数、认证及字段详情见源码契约与 UI 指南。探针凭证绑定 VM、项目与过期时间。撤销后旧凭证被拒绝，安装身份 tombstone 阻止自动重新注册。

## 数据最小化

采集不保存提示词、模型生成正文或工具参数正文，仅保留必要身份、结果状态、错误类和耗时。脱敏保留稳定资源身份。Docker代理只允许有界GET列表/inspect/stats/events；实验CLI单独授权、限定标签、最长180秒、保存配置并使用独立恢复watchdog。故障操作通过Docker和真实业务HTTP，不以写数据库制造事实。
