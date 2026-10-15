# LayerTrace UI 详细操作指南

## 0. 选择操作路径并准备真实记录

观看已完成实验时，按第1、2、5、6节操作。打开材料包中的 `LayerTrace-materials/evidence/ui/UI-records-quick-guide.md`，其中列出正常任务、恢复任务、三类故障的 run / task / incident / revision 和可直接打开的页面链接。使用精确 ID 查询，历史诊断选择对应记录编号。原始记录以 `evidence-summary.json`、`demo-scenes.json` 及它们引用的 run manifest 为准。

本机快捷表位于 `D:\ZSvirt\layertrace-runtime\output\materials\UI-records-quick-guide.md`。材料包中的原始汇总与场景身份位于 `LayerTrace-materials/evidence/evidence-summary.json`、`LayerTrace-materials/evidence/demo-scenes.json`；本指南和口播在 `LayerTrace-materials/evidence/ui/`，源码包对应 `LayerTrace-source/docs/`。

重新运行独立实验时，先按第3节确认工作负载与健康基线，再按第4节运行一类故障。每次使用新的 run 和 task ID，并等恢复核查完成后再运行下一类。重新实验产生的新结果应独立保存；回看材料中的 ID 不会自动变成新实验的 ID。

## 1. Windows 启动与普通登录

以下启动步骤用于已配置的本机 ZSvirt 实验环境。材料包的 `LayerTrace-materials/evidence/ui/local-launcher/` 保存启动器及 `lab-runtime.py` 的公开副本；运行仍使用 `D:\ZSvirt` 中现有磁盘、数据库、私有配置和凭据。在其他机器部署时先按源码 `README.md` 和 [本机恢复说明](../deploy/local-lab/README.md) 配置真实资源。

移机部署按交付资产清单核对四份镜像 tar，以及 cp、probe、workload、client 四份 manifests 中的 SHA256 和镜像身份，再按对应说明导入。故障操作使用项目原生 `python3 -m scenarios.real_lab` 入口。本机 CPU/工具编排器 `run-real-fault-r4.py` 与进程编排器 `run-real-fault-r4-process-native.py` 依赖现有 runtime、SSH 和数据库配置；材料中的冻结 runner 用于审阅验收编排与证据，不能直接替代新机器的部署配置。

1. 在 Windows 文件资源管理器打开 `D:\ZSvirt`，双击 `打开LayerTrace.cmd`。
2. 等待启动检查结束，脚本会自动打开默认浏览器中的 `http://127.0.0.1:8000/`。启动检查失败时保留窗口中的错误信息；尚未登录时，网页进入“进入观测工作台”。
3. 在本机用记事本打开私有配置 `D:\ZSvirt\layertrace-runtime\control-plane.env`，找到 `API_TOKENS=` 行，复制等号后的第一项令牌；多项令牌用逗号分隔。此配置用于本机登录，不属于公开交付源码。
4. 回到网页，在“访问令牌”密码框中粘贴令牌，点击“登录工作台”。完成后关闭配置文件，录屏从登录成功之后开始。
5. 登录后回到总览或此前打开的页面，右上角出现“退出”。会话有效期为12小时；过期后在同一登录页重新登录即可。
6. 页面中的时间统一按北京时间显示。总览、状态、任务、工作负载、事件单和诊断每10秒更新；需要立即获取数据时点击“刷新数据”。证据展开、表单处于输入焦点或标签页在后台时，页面会自动暂停定时刷新。阅读完成后收起证据并移开表单焦点；手动“刷新数据”会立即重新获取内容。

出现“访问令牌无效”时检查本地部署配置；出现权限或连接错误时先检查状态页，不反复运行故障实验。

第3节中的 Compose 与单次业务命令在 ZSvirt 业务 VM 的 `/opt/layertrace` 执行；第4节的验收编排命令在本机 WSL 的 `/mnt/d/ZSvirt/layertrace-runtime` 执行，编排器通过已配置的 SSH 密钥调用业务 VM。跨主机网页访问使用受信 HTTPS 或 SSH 回环隧道。

## 2. 确认采集与真实平台关联

1. 点击顶栏“状态”，查看“数据源状态”表。
2. 依次核对 PostgreSQL、ZSvirt、探针和指标来源。重点看“状态”“最后成功”“积压”“丢弃”和“错误”。最后成功时间应与本次部署及实验时间相符。
3. 展开“可用能力与权限”中的 ZSvirt 项，核对实际提供的 VM、宿主机、告警等能力。未配置、权限不足或未支持的能力保留原样，不能解释为可用。
4. 查看“待解析身份”。非零计数表示仍有资源身份待匹配，应检查平台 VM 身份与探针绑定。
5. 点击“拓扑”。核对宿主机 → 虚拟机 → 容器 → 服务实例的实际关系，以及 Agent 对模型、工具服务的依赖。
6. 点击虚拟机节点。下方“资源详情”显示资源 ID、平台身份、数据来源和最近发现时间。将“平台身份”与 ZSvirt 管理界面中该 VM 的 UUID 核对；资源显示名不能替代 UUID。
7. 点击容器节点，查看完整身份和关联资源；点击“查看容器健康”进入工作负载页。点击服务实例节点，再点击“查看此实例的任务”，可按实例筛选任务。
8. 拓扑拥挤时点击“适合窗口”。“关系时刻”可输入北京时间，点击“刷新拓扑”查询该时刻的关系；留空表示当前关系。

来源标识含义：绿色“观测”为采集事实，粉色“推断”为系统依据事实得出的状态，蓝色“回放”为保存场景重放，黄色“模拟”为模拟数据，灰色“人工”为人工标注。采集陈旧和空档不等同于业务恢复。

## 3. 运行正常任务，建立健康对照

独立客户端使用随附的[独立镜像与导入说明](client-image.md)，实际镜像、源码和代次与所选测量记录核对。

本次平台 API 返回版本 `1.0.0`；镜像来源标识为 `ZSvirt 1.0.0-h84r qcow2`。业务 VM 为2 vCPU、3 GiB，使用 KVM / host-passthrough，运行 Ubuntu 24.04.5 LTS、内核 `7.0.0-38-generic` 与 cgroup v2；这只是实际验证配置。libvirt 的 `period=1000000 / quota=1000000` 按每个 vCPU 生效，两个 vCPU 的预算不能解释为整台 VM 仅一核。实际版本、启动代次和内核设置以最终 `evidence-summary.json` 引用的环境快照为准。

本次 guest 操作账号没有 Docker 组权限，Compose 和真实实验使用 `sudo`。先在每个需要操作的终端定义完整合并入口；本次配置文件为受保护的 `workload.env`，镜像已离线导入，所有后续 Compose 命令沿用这六个文件：

```bash
cd /opt/layertrace
ltcompose() {
  sudo docker compose --project-name layertrace-vm --env-file workload.env \
    -f deploy/vm-probe/compose.yaml -f deploy/workload/compose.yaml \
    -f deploy/vm-probe/compose.host-network.yaml -f deploy/workload/compose.offline.yaml \
    -f deploy/workload/compose.client-fixed.yaml -f deploy/vm-probe/compose.final-images.yaml "$@"
}
ltcompose ps
```

核对实际运行参数：模型推理使用 `MODEL_THREADS=1`；Agent 的模型请求超时为 `MODEL_TIMEOUT_SECONDS=180`，独立客户端超时为 `CLIENT_TIMEOUT_SECONDS=400`；控制面健康样本窗口为 `BASELINE_WINDOW_SECONDS=3600`。这些是本次部署参数，Compose 模板的默认值可能不同。检查对应的受保护配置和运行容器，不在录屏中打印整份 env 文件。

本机演示控制面使用 `MODE=lab`，恢复稳定窗口15秒、健康基线窗口3600秒。交付默认配置使用 `MODE=prod`，实验入口默认关闭；模板的恢复稳定窗口为60秒、基线窗口为900秒。重新部署须按实际配置与保存的验收时间核查。

正常批次使用独立客户端，结果同步保留到终端和 client-results 卷。若已经运行持续 `demo-client`，先停止它，再启动本次唯一客户端：

```bash
ltcompose stop demo-client
ltcompose run --no-deps --rm --pull never demo-client \
  python -m workloads.demo_agent.load_generator --count 35 --interval 0.1
```

确认没有其他独立客户端仍在运行。该命令计划发送35个请求，成功样本需达到控制面的实际最小数量。独立健康批次的成功样本数、任务内模型累计耗时 P95 和任务总耗时 P95，从最终 `evidence-summary.json` 的 `baseline` 及其 SHA256 绑定的批次汇总读取；两种耗时不能互换。失败/超时不计入健康成功样本，模型CPU较慢时等待真实响应完成，不把35次请求等同于35次成功。共享宿主机其他活动未完整记录，这组批次不作为隔离宿主的性能基准。

诊断使用保存于对应时刻的滚动服务基线，样本范围与独立健康批次不同。R2 预检的模型滚动基线从最终 summary 的 `baseline.service_baseline_preflight` 读取；预检快照只表示其保存时刻，不要求与独立批次 P95 相等，也不代表后续每条诊断沿用同一数字。实际判定应读取所选诊断的“判定值与条件”。

1. 点击“任务”，点击“刷新数据”，确认出现正常业务任务。
2. 在“状态”筛选中选择“成功”，点击“查找”。检查总耗时、模型耗时、工具耗时和“观测”来源。
3. 点击一条任务的“详情”。下方显示任务 ID、起止时间、分段耗时、错误类型、服务实例以及任务事件。
4. 点击服务实例链接可定位其拓扑节点。正常任务没有关联事件单属于正常情况。
5. 点击“工作负载”。确认 Agent、模型和工具容器有当前采集状态；查看 CPU、内存工作集和限制。
6. 查看工作负载页的“健康延迟基线”。此面板显示所选容器相关服务的任务总耗时口径，按当前窗口计算样本数和 P95；它与 R2诊断的任务内模型累计耗时口径分别核对。正常样本足够时显示“就绪”，不足时显示“正常样本不足”，不把默认值或另一种耗时的 P95 当作当前基线。

单次请求可使用以下命令，以随机 UUID 生成新的任务 ID：

```bash
LT_TASK_ID="manual-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
curl --silent --show-error --fail-with-body --max-time 400 -X POST http://127.0.0.1:8091/tasks \
  -H 'Content-Type: application/json' \
  -d "{\"task_id\":\"$LT_TASK_ID\",\"topic\":\"cpu\"}"
```

终端保留真实业务响应，网页保留该任务的事件与耗时。演示时使用实际任务 ID，避免复制一条历史失败任务冒充本次实验。

## 4. 执行三类真实故障

点击“实验”可查看原生场景与命令。原生 CLI 负责故障与配置恢复；本机验收编排器另外安排唯一客户端、保存身份、读取真实诊断并核查业务和事件单恢复。故障目标须具有 `layertrace.lab=true` 标签且服务类型匹配，最长180秒。注入前启动独立恢复进程并确认 deadline；CLI 被强杀或终端失联仍可到期恢复，VM断电/Docker失效时需重启后独立 restore。

打开 Windows 终端，输入 `wsl.exe -u cnj`，进入本机 Linux shell；随后执行下列准备命令。使用本地受保护的 SSH 密钥与配置，终端不打印令牌或密码。执行编排器时由它启动客户端，先确认第3节的正常批次已经结束，持续客户端与其他 one-off 客户端均已停止。

```bash
cd /mnt/d/ZSvirt/layertrace-runtime
```

三个场景每次只启动一个独立客户端，`--during-count` 表示该客户端串行发送的请求数：完成一次后才发送下一次。请求数、故障时长和新实验编号从 `UI-records-quick-guide.md` 的所选通过 manifest 命令读取；表中的真实实验编号不代表连续通过范围。CPU 与工具场景先核验故障生效，再启动客户端；本次进程场景采用 `native-stop-before-client`，唯一客户端先预热并等待 stdin，原生停止 Agent、核实原容器退出与恢复 watchdog 就绪后，再放行业务请求。最终影响按实际退出、停机期间同身份请求失败及恢复链核验。每条命令输出独立目录和 run ID。共用的 `manifest.json`、`client-during.jsonl`、`native-before-client.json` 保留身份与故障事实；进程场景另保存 `client-prewarm-ready.json`、`client-prewarm-receipt.json`、`native-stopped-before-client.json`、`native-watchdog-before-client.json`、`client-release-command.json` 和 `client-release-observation.json`，分别核对 READY、停止确认和业务放行的顺序。请求与故障仅部分重叠时，按实际时间说明。

### 4.1 模型计算资源瓶颈

```bash
python3 run-real-fault-r4.py cpu-quota \
  --cpus 0.1 --seconds 180 --during-count 1 \
  --number "${LT_NEW_EXPERIMENT_NUMBER:?按快捷表填写新实验编号}"
```

1. 编排器先核对业务 VM、完整模型容器 ID 和启动代次，读取故障前配置。
2. 原生 CLI 收紧模型配额后，编排器读取仍处于 `running` 的 native 状态；确认生效值为0.1核、容器身份与启动代次一致，再启动唯一客户端。不要提前另开请求流。
3. 在“任务”点击“清除筛选”，将“状态”选择“运行中”，点击“查找”；核对本次开始时间，再点击该任务“详情”，记录完整 task ID。任务终结后把状态改回“全部”，按该 ID 精确查找。
4. 在“工作负载”核对模型容器状态、采集时效、CPU 用量和来源。该表展示 CPU 使用率、内存工作集与内存限制；模型 CPU 限额及节流增量应在下一步的诊断证据卡中查看。
5. 按第5节进入关联诊断，选择本次run对应的R2记录，展开模型配额、节流与同任务模型证据。健康基线的 `model_duration_ms` 是任务内initial与synthesis累计模型耗时，和任务总耗时分别核对。当前单个闭合模型phase的真实 `duration_ms` 可逐条作为该累计量的保守下界；不跨phase或task求和。在“判定值与条件”核对所用耗时字段/基线口径、样本数、健康P95与当前判定值；实际关联窗口150秒。R2要求至少30个健康成功样本、当前延迟严格大于健康P95两倍且绝对增加严格大于500ms，并具有同模型的正配额和正节流周期增量。缺失直接耗时或仅有CPU使用率不能成立。
6. 等命令完成，查看输出的六项门槛。只有注入、原配置恢复、故障期间业务观测、高支持诊断、新业务成功和事件单稳定恢复全部为 true，才把该次记录记为通过；否则保留失败原因与原始记录。

### 4.2 容器进程中断

优先从 `UI-records-quick-guide.md` 复制完整进程场景命令，核对所选 manifest 的 `process_injection_strategy` 为 `native-stop-before-client`。该策略的准备参数为45秒、3个串行请求，间隔2秒；运行前按所选 manifest 核对秒数和请求数，分别赋给 `LT_PROCESS_SECONDS`、`LT_PROCESS_DURING_COUNT`，将表中建议的新实验编号赋给 `LT_NEW_EXPERIMENT_NUMBER`。实际目录另外包含时间和随机 ID，保留每次真实结果。

```bash
python3 run-real-fault-r4-process-native.py process-failure \
  --process-injection-strategy native-stop-before-client \
  --seconds "${LT_PROCESS_SECONDS:?请先按真实记录填写进程故障秒数}" \
  --during-count "${LT_PROCESS_DURING_COUNT:?请先按真实记录填写串行请求数}" \
  --number "${LT_NEW_EXPERIMENT_NUMBER:?按快捷表填写新实验编号}"
```

1. 编排器先启动唯一原始客户端，导入其 `run_client_task` 后输出 READY 并等待 stdin；预热阶段不发送 healthz 或业务请求。核对客户端镜像、入口源码和目标 Agent 的完整容器 ID / 启动代次。原生故障入口停止 Agent 后，编排器确认同一容器为 `exited`、非零退出、PID为0且未被自动重启，并核实独立恢复 watchdog 就绪、仍在截止时间内，才通过 stdin 放行原业务函数。3个请求由同一客户端严格串行执行，间隔2秒。
2. 从快捷表读取正式验收选中的停机失败 task ID，在“任务”精确查询并点击“详情”。结合放行后的客户端原始响应、保存的退出与恢复时刻，核对请求失败发生于目标 Agent 停机期间，目标容器与服务实例身份一致。每个串行请求的真实结果按各自 task ID 核查，并保留失败或不可达的原始响应。
3. 在“关联诊断”区域点击对应“事件单 ××××”，选择本次 R0 诊断记录，核对同一容器真实退出、本场景原始 R0 告警、停机请求失败和任务影响。其他历史告警中的上下文 R0 候选不能替代本场景的原始告警关联。
4. 原生恢复会重新启动 Agent。启动代次发生变化时保留故障前后的实例身份；恢复后的新成功任务应属于恢复后的实际实例。
5. 将场景准确描述为容器进程中断。退出码137或进程停止本身不能证明内核 OOM；缺少 OOM 标记时，诊断须保留缺失项或相应支持限制。
6. 用 `native.started_at` 核对操作开始下界，用 `native.injected_at` 核对停止后的原生观测时刻，再按 `process_stop_observed_at`、`client_launched_at` 与放行回执核对“停止确认 → 放行 → 请求结果”的顺序及截止时间。`client_process_started_at`、`client_ready_at` 和 `client_ready_received_at` 分别记录客户端进程启动、READY产生与收到时间；`client_launched_at` 在此策略中表示业务放行时间。独立审计保留退出、正式停机失败任务及恢复后新任务，验收范围为真实退出、停机业务失败及 R0 链恢复；在途模型计算中断需要另外的直接证据。

另一个受支持策略是 `active-model-before-kill`：先发业务请求，经公开任务事件的模型阶段守卫后停止 Agent。仅选择该策略时，读取 `client-task-before-injection.json` 和 `in-flight-public-polls.json`，并单独保存守卫观察任务的实际终结结果。两种策略的时间和证据按各自 manifest 核对；公开 `running` 观察不能单独证明模型计算已被中断。

### 4.3 工具服务失败

优先从 `UI-records-quick-guide.md` 复制完整工具场景命令。手动填写时，将所选 manifest 的秒数、请求数分别赋给 `LT_TOOL_SECONDS`、`LT_TOOL_DURING_COUNT`，将建议的新实验编号赋给 `LT_NEW_EXPERIMENT_NUMBER`：

```bash
python3 run-real-fault-r4.py tool-503 \
  --seconds "${LT_TOOL_SECONDS:?请先按真实记录填写工具故障秒数}" \
  --during-count "${LT_TOOL_DURING_COUNT:?请先按真实记录填写串行请求数}" \
  --number "${LT_NEW_EXPERIMENT_NUMBER:?按快捷表填写新实验编号}"
```

1. 编排器先核验原生工具故障处于 `running` 且身份一致，再启动唯一客户端；该任务仍会先完成模型阶段。模型调用延迟可能导致工具阶段晚于故障结束，须按实际记录判定。
2. 在“任务”精确查找本次失败 task ID，查看工具阶段错误和总任务结果。
3. 在“关联诊断”区域点击对应“事件单 ××××”，选择本次 R3 诊断记录，展开工具失败支持证据，确认 HTTP 状态码为503。
4. 核对工具失败和最终任务失败使用同一 task ID，并检查故障时的 Agent → 工具服务依赖关系。点击证据中的资源链接，定位真正故障的工具资源。

## 5. 从任务走到证据与故障时拓扑

1. 点击顶栏“任务”。点击“清除筛选”，将“状态”保持为“全部”，在“任务 ID”输入框填入交付证据中对应故障 run 的真实 task ID，点击“查找”。
2. 点击该行“详情”。下方“任务”区域显示业务状态、来源、总耗时、模型/工具耗时、服务实例及任务事件。
3. 找到详情中的“关联诊断”标题，点击下面的“事件单 ××××”链接。它直接打开对应事件单的证据诊断。若出现“此任务尚未关联事件单”，先确认采集和诊断生成状态；成功任务无需故障诊断。
4. 在“事件单”标题核对短 ID、严重度、状态和发生时间；地址中的 `incident_id` 为完整 UUID，可与交付证据逐字核对。需要等待诊断时，页面显示“正在等待诊断生成，请稍后刷新”。
5. 在“诊断记录”下拉框中选择交付证据所列的记录编号。默认“最新记录”随新诊断更新；查看故障实验时，应选择该 run 对应的历史记录，并核对生成时间。页面会注明“历史诊断记录”，地址增加 `revision=<编号>`，刷新或浏览器后退后仍保留此记录。事件单顶部的“当前状态”持续显示当前状态。
6. 在“候选根因”查看标题、支持等级和规则 ID。“高支持”表示证据充分程度，不能视为数学概率。恢复后的最新记录可能支持降低，不能把历史高支持当作当前仍在发生的故障。
7. 点击“支持证据”下某张证据卡片的摘要行，展开具体数值、来源、发生时间、资源和证据引用。候选根因、任务影响与证据索引均对应当前所选诊断记录。
8. 在展开卡片末尾的“发生时间 / 资源 / 来源引用 / 证据 ID”区域，点击“资源”右侧的链接。摘要数值中也可能有资源链接，按末尾这组元数据中的链接进入历史拓扑。进入后，“关系时刻（北京时间）”自动填入该证据发生时间，地址同时含 `resource_id` 与 `at`；下方资源详情保留选中对象的完整身份。点击“适合窗口”检查对应时刻的依赖关系。
9. 用浏览器“后退”返回同一诊断，核对所选记录编号，继续展开“反证”，阅读“缺失证据”和“全局缺失项”。没有反证记录不等于其他层已经被排除，缺少信息时允许证据不足。
10. 在“任务影响”点击已确认失败任务，核查真实业务结果。潜在影响单独展示，不能作为已确认失败数量。
11. 在“故障时间线”核对先后顺序、同一资源和任务。时间线展示事件单当前累计事实。检查所选记录的“规则与时间可信度”、规则版本、时间可信度与上下文截断状态，再阅读“处置建议”和“恢复验证”。

也可从顶栏“事件单”按“状态”“严重度”筛选，结合短 ID、指纹和发生时间定位记录，再点击该行“诊断”。任务详情中的服务实例链接用于当前拓扑；证据卡片末尾的资源链接自带证据发生时刻。页面列表展示最近200项，查找旧任务时使用精确 task ID。

完整路径：**任务 → 详情 → 关联诊断中的事件单链接 → 选择实验对应诊断记录 → 展开证据 → 证据资源的历史拓扑 → 返回同一诊断 → 返回事件单 → 确认 / 静默 → 恢复后新任务**。

## 6. 确认、静默与验证恢复

1. 在诊断页点击右上角“返回事件单”，按短 ID、指纹和发生时间找回同一行。待处理记录才显示“确认”；点击后状态变为“已确认”，告警和诊断仍保留。已确认或已恢复记录无需重复确认。
2. 点击该行“静默通知”。页面自动填入该事件单的指纹，并滚动至静默表单。
3. 填写理由，例如“已定位工具故障，正在恢复”，将“操作者”填写为“演示用户”，将“时长（分钟）”改为5，再点击“创建通知静默”。
4. 确认出现“通知静默已创建”，事件单行显示截止时间。静默只抑制通知；原始告警和事件单继续更新。
   已恢复事件单也能按指纹设置静默。该操作针对窗口内后续匹配的通知；没有活跃通知发生时，不能将创建静默说成已实际压制一次通知。
5. 实验命令结束时执行自动恢复。需要独立恢复时，在业务 VM 的 `/opt/layertrace` 使用本次 run ID 对应的状态文件：

```bash
sudo python3 -m scenarios.real_lab restore \
  --state evidence/lab/<run_id>/state.json
```

6. 检查恢复后的配置，再提交一个新的唯一任务 ID。确认真实业务请求成功，分段耗时回到健康范围。
7. 在“任务”确认恢复后的成功结果，在“工作负载”确认当前采集与容器状态。
8. 在“事件单”等待全部相关告警恢复并满足稳定窗口，再点击“刷新数据”，确认“已恢复”。本次演示控制面设置 `INCIDENT_RECOVERY_STABLE_SECONDS=15`；项目默认60秒。CLI配置恢复、成功新任务和事件单已恢复必须分别核对，不能把15秒等待直接等同于恢复成功。
9. 若页面仍显示采集陈旧或空档，先恢复采集。不要把没有新告警解释为故障已经恢复。

## 7. 可选回放与反例

1. 点击“实验”，滚动至“回放演示”。该区域会显著显示蓝色“回放”标识。
2. 选择“退出码 137 反例”，点击“运行回放”，然后点击“验证回放”和“查看回放诊断”。
3. 核对诊断没有把退出码 137 单独当作容器 OOM 的高支持证明。
4. “容器 OOM 证据”“宿主资源压力”“CUDA OOM”也可回放。R1、R4及GPU/CUDA相关能力本次仅有回放或回归验证，展示对应来源；未安装 GPU 的环境只能称 CUDA OOM 回放，不称 GPU 实测。
5. 三类真实场景演示完成后再打开回放，避免在一屏中混淆真实实验与保存场景。

## 8. 录屏准备与交付检查

- 提交主片为1600×900、25fps，带中文配音与同步字幕；画面来自真实网页，登录在录制前完成。手工重新录制可使用1920×1080、浏览器100%缩放，保留关键页面和来源标识。
- 准备状态、拓扑、任务、事件单四个浏览器页签，以及业务请求和故障实验两个终端。
- 为每类实验保存 run ID、task ID、incident ID、诊断记录编号、恢复结果与时间。录屏中展示的记录与交付证据使用同一组 ID 和记录编号。
- 提交视频回看已经完成并核验的三类真实实验，标注“真实实验记录”。历史诊断对应交付记录中的精确身份；实时注入镜头须另行录制真实终端流程并保留实验记录。
- 交付主片 `LayerTrace-demo.mp4` 为270秒，已含中文配音与同步字幕。随附独立WAV音轨、SRT字幕和逐镜头口播；需要本人配音时见 [配音与导出操作](video-editing-guide.md)。29.96秒真实确认短片为独立证据素材。提交前播放正式MP4，检查音画同步、关键文字及凭据遮蔽。
- 完成后点击右上角“退出”。交付负责人实际核验公开仓库和视频链接的访问权限与可用性后，写入最终材料，再按比赛通知提交；视频文件不能用截图代替。

## 9. 按钮与控件定位表

手工操作按左列名称定位。右列是页面真实控件的 CSS 选择器，供自动化、故障排查与录制核对使用；`TASK_ID`、`INCIDENT_UUID`、`EVIDENCE_UUID` 和 `FINGERPRINT` 应替换为第0节的实际值。

| 操作 | 页面控件 | 成功后核对 |
|---|---|---|
| 输入访问令牌并登录 | `#access-token`；`#login-form button[type="submit"]` | 右上角出现“退出” |
| 手动刷新 | `#refresh-button` | “更新于”时间改变；请求失败时显示错误 |
| 清除任务筛选 | `#task-reset` | 任务 ID 为空，状态为全部 |
| 精确查找任务 | `#task-id`；`#task-status`；`#task-filter button[type="submit"]` | 列表与输入的完整 task ID 一致 |
| 展开指定任务 | `#task-table button[data-task="TASK_ID"]` | `#task-detail` 中出现该任务和真实状态 |
| 进入关联诊断 | `#task-detail a[href="/pages/diagnosis?incident_id=INCIDENT_UUID"]` | 地址中的完整 `incident_id` 与记录一致 |
| 固定诊断记录 | `#diagnosis-revision` | 选择对应数字；地址含 `revision`；候选区域注明该编号 |
| 展开证据 | `#d-candidates details[data-evidence-id="EVIDENCE_UUID"] > summary` | 数值、来源、发生时间、资源及引用可读 |
| 从证据进入拓扑 | `#d-candidates details[data-evidence-id="EVIDENCE_UUID"] > dl.facts:last-of-type a[href^="/pages/topology?resource_id="]` | 地址含 `resource_id` 和 `at`；`#topo-at` 自动填入证据发生时间；身份相符 |
| 适配拓扑范围 | `#topo-fit` | 当前拓扑节点完整进入图框 |
| 查询历史关系 | `#topo-at`；`#topo-reload` | 关系时刻为北京时间，空值表示当前关系 |
| 回到事件单列表 | `.page-head a[href="/pages/incidents"]` | 同一短 ID、指纹和发生时间的行可定位 |
| 确认待处理事件单 | `#incident-table button[data-ack="INCIDENT_UUID"]` | 同一行变为“已确认”；已恢复行没有此按钮 |
| 选中静默对象 | `#incident-table button[data-silence="FINGERPRINT"]` | `#silence-fingerprint` 自动填入完整指纹 |
| 填理由、操作者与分钟数 | `#silence-reason`；`#silence-actor`；`#silence-minutes` | 填写5分钟时核对指纹、理由和操作者 |
| 创建通知静默 | `#silence-form button[type="submit"]` | `#silence-result` 显示成功，同一事件单行显示截止时间 |
| 退出会话 | `#logout-button` | 返回登录页 |


时间记录核对：虚拟机中的停止、业务放行回执和恢复时间属于同一时钟；WSL 中的命令发送与接收时间单独排序。两台机器可能存在时钟偏差，不直接用跨主机时间差推定先后；放行还须通过单调时钟期限、同一身份回执和虚拟机内截止时间检查。


## 本次交付的真实记录

按下表定位页面；完整命令与原件 SHA256 见材料 `evidence/ui/UI-records-quick-guide.md`。

| 场景 | 任务 ID | 事件单 ID | 诊断轮次 |
|---|---|---|---:|
| CPU 配额 | `task-efbafb89aac0432c9a758ac71cf7f0d9` | `a72c7605-085f-4148-81b6-cd6810257811` | 239 |
| 容器进程退出 | `task-ae216e58d10447b8997cdcc9ffc1060b` | `ae899a52-7f37-420d-83ae-073623ae4ed8` | 38 |
| 工具 HTTP 503 | `task-b5cb10c81f7048ed808b1a14eb307e35` | `b6506dbf-0611-4358-98bf-13ed78d44ed8` | 10 |

正常任务：`task-20a7e0469ec8475688404c9b878a0dd7`。恢复任务：`task-33fcc12e657c4fed82a809ed3aa244f6`。已恢复事件单应按当前状态展示，故障结论通过表中历史诊断轮次复核。
