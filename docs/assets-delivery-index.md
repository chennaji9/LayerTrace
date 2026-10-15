# LayerTrace 交付资产与提交索引

## 提交要求与当前记录入口

用户提供的比赛通知要求完整可运行代码仓库链接、作品介绍文档（推荐PDF）、3–5分钟作品演示视频链接及材料附件。截止时间为 **2026年10月16日24:00 Asia/Shanghai，即10月17日00:00**。报名确认邮件的附加要求由用户核对。

| 提交项 | 本地交付入口 | 提交记录 |
|---|---|---|
| 完整代码仓库 | `LayerTrace-source.zip`、`LayerTrace-source.bundle` | 已创建公开仓库 [chennaji9/LayerTrace](https://github.com/chennaji9/LayerTrace)；最终源码提交与快照一致性见独立发布记录 |
| 作品介绍PDF | `LayerTrace-materials.zip/documents/` | 实际文件名、大小和摘要见材料manifest |
| 演示视频 | `LayerTrace-materials.zip/videos/` | 实际时长见release-manifest.json；外部访问链接与校验结果见独立发布记录 |
| 材料附件 | 源码/材料归档、资产清单、证据与操作文档 | 归档路径、大小和SHA256见release-manifest.json及SHA256SUMS |
| 运行资产 | `LayerTrace-models.zip`、可选`LayerTrace-offline-images.zip` | 独立分发，下载来源、许可证与摘要见assets/manifest.json |

完整逐项核对与外部提交状态见 [提交核对清单](submission-checklist.md)。仓库发布、视频上传和比赛提交分别保存真实记录。本地文件与校验成功不代表这些外部步骤已执行。`release-manifest.json` 分别保存 `local_materials_complete`、`repository_published`、`video_published`、`mail_sent`；外部状态字段是本地打包时的快照，之后的源码提交、视频链接与回执以独立发布记录核对。

## 输出目录与归档内容

默认输出为 `D:\ZSvirt\LayerTrace-delivery-voiced`（WSL：`/mnt/d/ZSvirt/LayerTrace-delivery-voiced`），最终采用打包命令返回的实际目录。若同名归档已经存在，工具要求选择新的空目录，不覆盖既有交付。

```text
LayerTrace-delivery-voiced/
  LayerTrace-source.zip
    LayerTrace-source/            完整源码、部署、测试、许可证、最终说明
      README.md
      scripts/configure_local_env.py
      scripts/package_release.py
      scripts/uninstall.py
      docs/ui-operation-guide.md
      docs/video-narration.md
      manifest.json
      SHA256SUMS
  LayerTrace-source.bundle        同一份脱敏源码的独立Git快照
  LayerTrace-materials.zip
    LayerTrace-materials/
      documents/                 最终PDF
      videos/                    实际演示视频
      screenshots/               显式选择的最终UI截图
      assets/manifest.json       模型、镜像、平台来源、版本、摘要与许可
      evidence/environment.json
      evidence/runs/             全部实际运行、任务、故障与恢复证据
      evidence/heartbeat/        非空gap心跳原始异常、修复专项与源码指纹
      evidence/kernel/           控制台日志、QEMU事件及重启调查
      evidence/ui/               详细UI操作、口播、精确ID快捷表与本机快速入口
      evidence/ui/local-launcher/ 现有本机环境的启动器与公开恢复脚本
      evidence/latency/          三类正式实验的逐轮时延与统计
      evidence/reports/          环境、验收、开销及提交资产说明
      manifest.json
      SHA256SUMS
  LayerTrace-models.zip           独立模型权重与 models/licenses/ 原始许可
  LayerTrace-offline-images.zip   可选Docker镜像归档、offline-images/licenses/ 许可
                                 offline-images/manifests/cp-images-manifest.json
  release-manifest.json           实际包大小、摘要、视频时长、材料完整性
  SHA256SUMS                      全部实际归档与release-manifest摘要
```

只有显式提供对应输入的资产包才生成。qcow2为ZSvirt平台环境，不放入作品源码或附件；平台 GetVersion 实际返回1.0.0，源镜像标识为1.0.0-h84r，资产清单分别记录平台来源、版本和核验摘要。模型与镜像可以联网下载，也可以通过独立离线包提供，复现仍须Docker与平台环境，不能表述为零外部依赖。

控制面/worker交付 OCI 身份为 `sha256:18803bb39692e0b1d9d739233dcd84485a71c59b38814f809786679ab22bd616`，引用为 `layertrace-control-plane:delivery-20261015-r4`。控制面离线归档为297,582,080 bytes，SHA256为 `6c5d1bfeca425903cee4ec2bc8be7c82ac5cc4ada9285388f4d075b352f1f422`；3个具名引用的实际导入及152个指定运行源码/许可文件的一致性已有原始记录。源码 Core 364 项、E2E 20 项及空库冷启动 49 项均通过，入口为 [交付镜像验收](evidence/control-plane-acceptance/r4/README.md)。探针/只读代理复用已验证镜像，13个实际observer模块及两容器原生代次独立核对；Agent/工具保留原镜像并独立核对9个运行文件，客户端保留独立修复镜像与原build/load证明。复用证明不声称新探针构建、导入或部署。四份归档、四份精确镜像清单及模型/许可的最终核对入口为材料 `evidence/assets/proofs/delivery-assets-verification.json`；本机运行控制面为WSL虚拟环境的web/worker。

## 源码和说明入口

| 路径 | 用途 |
|---|---|
| `README.md` | 完整部署、正常业务、真实实验与验证顺序 |
| `apps/control_plane/`、`packages/contracts/` | API、Web、worker与数据契约 |
| `integrations/`、`agents/vm_probe/` | 平台只读同步、容器身份、事件/指标与持久补传 |
| `workloads/demo_agent/`、`scenarios/real_lab.py` | 真实CPU模型、Agent/工具/独立客户端、故障与恢复 |
| `rules/` | 版本化诊断规则、条件与建议 |
| `deploy/compose.yaml` | 控制面、PostgreSQL、worker、Prometheus等 |
| `deploy/vm-probe/README.md` | 合并Compose、SSH隧道、离线资产与卸载 |
| `deploy/workload/fetch-model.sh`、`deploy/models/manifest.yaml` | 模型下载、摘要与许可证 |
| `docs/submission-introduction.md` | 作品介绍内容源 |
| [UI 操作指南](ui-operation-guide.md)、[视频口播稿](video-narration.md) | 页面逐步操作、镜头表和口播；材料包同内容位于 `evidence/ui/` |
| 本机 `D:\ZSvirt\打开LayerTrace.cmd` | 现有实验环境启动入口；公开副本位于材料包 `evidence/ui/local-launcher/`，依赖本机既有磁盘和私有配置 |
| `docs/release-evidence.md`、`docs/environment-risk.md`、`docs/test-report.md` | 实际环境、能力边界及测试证据 |
| `docs/security.md`、`docs/permissions.md` | 会话CSRF、范围、凭据、采集和实验权限 |
| `LICENSE`、`THIRD_PARTY_NOTICES.md` | 开源许可和第三方清单 |
| `tests/`、`pyproject.toml` | 自动化验证及锁定依赖 |

旧截图、规划与交接文档、本地`.env`、SSH私钥、令牌、注册secret、证书私钥、缓存、虚拟环境和运行时状态不进入源码。最终UI截图必须通过显式`screenshot`参数选择，避免把保存过的页面误当作本次实际环境。

## 打包与校验

在WSL源码根目录运行，输入参数使用实际生成并完成脱敏的文件路径：

```sh
python3 scripts/package_release.py create \
  --output /mnt/d/ZSvirt/LayerTrace-delivery-voiced \
  --git-bundle \
  --evidence-root /path/to/redacted-evidence \
  --pdf /path/to/LayerTrace-introduction.pdf \
  --video /path/to/LayerTrace-demo.mp4 \
  --screenshot /path/to/final-overview.png \
  --asset-manifest /path/to/assets-manifest.json \
  --model-file /path/to/qwen2.5-0.5b-instruct-q4_k_m.gguf \
  --model-license /path/to/Qwen2.5-LICENSE.txt \
  --image-file /path/to/layertrace-vm-images.tar \
  --image-file /path/to/layertrace-workload-image.tar \
  --image-file /path/to/layertrace-client-image.tar \
  --image-file /path/to/layertrace-control-plane-images.tar \
  --image-license /path/to/llama.cpp-LICENSE.txt \
  --image-manifest /path/to/cp-images-manifest.json \
  --image-manifest /path/to/probe-images-manifest.json \
  --image-manifest /path/to/workload-images-manifest.json \
  --image-manifest /path/to/client-images-manifest.json \
  --secret-file /path/to/protected-runtime.env \
  --require-submission

python3 scripts/package_release.py verify /mnt/d/ZSvirt/LayerTrace-delivery-voiced
cd /mnt/d/ZSvirt/LayerTrace-delivery-voiced
sha256sum -c SHA256SUMS
```

`pdf`、`video`、`screenshot`、`model-file`、`image-file`、`model-license`、`image-license`、`image-manifest`和`secret-file`可以重复。分发权重时用`model-license`保留Qwen原始许可；分发CPU推理镜像时用`image-license`保留llama.cpp原始许可。配套许可只接受名称含LICENSE/LICENCE的UTF-8纯文本，镜像清单只接受与实际tar大小/SHA256吻合的JSON；它们执行同样的已知秘密与证据字段扫描，并拒绝凭据赋值、私钥及env/key类型。`image-manifest`交付精确image/config身份记录，供README中的离线导入命令使用。

`secret-file`只用于本地泄漏扫描，不归档、不输出内容；无需独立资产时省略对应资产和配套参数。需要可提交材料时启用`--require-submission`，它检查必要源码、实际PDF、3–5分钟视频、environment.json、场景证据与资产清单，并拒绝未填写的实测记录。它不替代内容审核或外部链接访问检查。跨主机控制面HTTPS见`deploy/caddy/README.md`；ZSvirt私有CA的宿主目录与只读挂载步骤见README，按需加入`deploy/compose.zsvirt-ca.yaml`，默认部署不挂载CA。

恢复源码仓库：

```sh
git clone LayerTrace-source.bundle LayerTrace
cd LayerTrace
git checkout submission
```

bundle来自源码ZIP的独立脱敏快照，默认分支为`submission`。它不包含开发工作区历史或本地配置。用户创建目标远端后，再按其选定的仓库策略推送。

## 实际证据与最终核对

每个真实run保留场景类型、run ID、平台VM UUID、完整容器ID、服务实例、task/incident ID、故障与恢复时间、原/实验/恢复配置、业务结果、事件/指标引用与诊断。证据区分平台原生告警、真实采集、推断和回放，未验证能力列为限制。CLI的restore_verified与业务成功、事件单恢复应能互相对照。

独立正常业务样本入口为 `evidence/final/baseline-business.jsonl`、对应summary/client及 `cpu-formal-baseline.json`。成功数、任务内模型累计耗时P95、任务总耗时P95和工具P95，以当前源码实际批次及独立审阅汇总为准；客户端往返时间另行记录。共享宿主记录不构成独占宿主性能基准，正式运行的滚动健康基线以同轮诊断引用核对。历史正常窗口、故障失败及中断manifest原样归档，不计为当前源码正式通过。进程退出允许合法Agent重启，每次使用当时的完整CID与StartedAt。非空gap心跳的原始异常、专项、真实恢复和源码哈希保留；当前队列0不代表历史drop/gap为0。

冲刺验收目标为R0/R2/R3三类各至少5次完整闭环；原规格SC-001的OOM、CPU、工具和同VM争用四类各5次另列覆盖状态。三类闭环通过不等同原SC-001全部通过。包的完整性由实际release-manifest与SHA256SUMS判断，外部提交状态仍由发布记录判断。

混有日志行的历史业务输出以 `.jsonl.log` 逐字保留，有效JSON记录行另存为严格 `.jsonl`。源码一致性原始JSON以 `.json.log` 保留，供读取的JSON将文件哈希映射表示为path/sha256记录列表。`evidence-export-manifest.json` 的transparent_derivations记录原始与派生SHA256、归档目标和转换内容；所有其他字段和值保留，两种文件都执行秘密扫描。

1. 按交付README在授权平台guest中复现，核对真实资源、模型回答、工具结果与诊断。
2. PDF、UI操作指南、口播、截图和证据采用相同实际身份与能力边界。
3. 播放视频核对3–5分钟、关键文字可读、无凭据，并说明等待片段的加速或剪辑。
4. 用verify和SHA256SUMS校验实际文件，不凭预期文件名宣称已交付。
5. GitHub 仅发布源码，视频、PDF、音轨、字幕和完整材料本地交付。参赛者按比赛通知处理视频外链及邮件提交。

独立客户端镜像另随 `layertrace-client-image.tar` 和 `client-images-manifest.json` 交付，核验与六文件启动步骤见 [独立客户端镜像说明](client-image.md)。
