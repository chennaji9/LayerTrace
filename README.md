# 层迹 LayerTrace

以虚拟机为边界，从失败的 AI 任务出发，用可追溯的证据链区分容器进程异常、模型计算资源瓶颈和工具服务失败。

控制面与浏览器、VM探针、只读平台适配器、真实CPU模型/Agent/工具/独立客户端、实验与恢复CLI、测试和交付工具均随源码提供。实际验证记录见 `docs/release-evidence.md`，实验环境与能力边界见 `docs/environment-risk.md`。回放、观测、推断和未知状态分别标记。

项目仓库：[github.com/chennaji9/LayerTrace](https://github.com/chennaji9/LayerTrace)。GitHub 仅提供源码、部署配置和项目说明。视频、音轨、字幕、PDF、实测材料、模型与离线镜像保存在本地交付包。

## 源码范围与环境要求

本仓库包含控制面与 Web UI、VM 探针、ZSvirt 只读适配器、AI 演示工作负载、诊断规则、实验与恢复 CLI、数据库迁移、测试、配置模板、部署脚本和许可证。运行配置由部署脚本在本机生成；模型权重按清单单独下载。

建议使用 Linux x86_64、Python 3.12、Docker 与 Compose 插件；CPU 模型推理需要支持 AVX2。ZSvirt 平台、业务 VM、模型权重和运行凭据需要自行准备。验证环境与支持边界见 [环境说明](docs/environment-risk.md)，完整操作见 [UI 指南](docs/ui-operation-guide.md)。文档中的本机实验路径与历史记录 ID 用于说明已保存的实验，首次部署以本 README 的命令和自己环境的配置为准。

## 从源码部署控制面

Linux x86_64、Python3.12、Docker与Compose插件。源码的直接Python版本及镜像摘要固定；内层CPU推理需要兼容AVX2的宿主/VMCPU。控制面配置默认prod，关闭lab。

```bash
python3 scripts/configure_local_env.py --path .env --mode prod
docker compose --env-file .env -f deploy/compose.yaml up -d --build postgres migrate control-plane worker prometheus
```

脚本生成随机数据库口令、API令牌和注册秘密，配置文件mode600，已有文件拒绝覆盖。所有API读取均需认证；浏览器使用登录表单，机器客户端使用Bearer令牌。HTTPS、CA和跨主机部署见 `deploy/caddy/README.md`，只监听loopback的HTTP实验链路可通过已认证SSH端口转发连接。

## 离线控制面部署

先解压源码、独立镜像包与交付资产清单。导入前核验SHA256；导入脚本按清单再次检查镜像身份。

```bash
python3 scripts/load_offline_images.py \
  --archive ../LayerTrace-offline-images/offline-images/layertrace-control-plane-images.tar \
  --manifest ../LayerTrace-offline-images/offline-images/manifests/cp-images-manifest.json
python3 scripts/configure_local_env.py --path .env --mode prod
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.offline.yaml -f deploy/compose.final-images.yaml up -d --no-build --pull never postgres migrate control-plane worker prometheus
```

以上命令从解压后的 `LayerTrace-source` 目录执行，独立镜像归档解压到同级 `LayerTrace-offline-images`。导入脚本先检查 tar 的大小与SHA256，再核验具名tag对应的image/config身份。精确控制面清单随镜像包位于 `offline-images/manifests/`，推理许可位于 `offline-images/licenses/`；模型包的原始许可位于 `models/licenses/`。固定核心镜像涵盖PostgreSQL16.4、Prometheus3.5.0及最终控制面。可选Grafana、Tempo、Caddy等未包含在核心离线镜像归档中，需按各自许可证和固定摘要另行准备。

## ZSvirt只读接入

配置 `ZSVIRT_ENABLED=true`、真实URL、只读账户名及受保护的SHA512口令摘要；可使用初始OAuth token。适配器支持会话失效的有界重登录，未配置或权限不足如实显示状态。真实四个查询接口和Normal账户权限机制见 `docs/zsvirt-readonly.md`；平台写请求已核验拒绝。当前支持host/VM和原生告警/事件，不声称GPU映射、平台利用率或quota API已接入。

ZSvirt使用私有CA时，在宿主准备一个只包含公共CA证书的目录，目录权限0755、证书权限0644，供非root控制面和worker读取。在受保护的`.env`中填写宿主绝对目录 `ZSVIRT_CA_DIRECTORY=/absolute/host/zsvirt-ca` 和容器路径 `ZSVIRT_CA_PATH=/etc/layertrace/ca/zsvirt-ca.crt`，将CA证书置于该宿主目录的 `zsvirt-ca.crt`，然后追加可选覆盖文件：

```bash
docker compose --env-file .env -f deploy/compose.yaml \
  -f deploy/compose.zsvirt-ca.yaml up -d control-plane worker
```

离线部署同时追加 `-f deploy/compose.offline.yaml` 和 `-f deploy/compose.final-images.yaml`，并使用 `--no-build --pull never`。证书目录不得放入私钥；挂载为只读，宿主目录缺失会拒绝启动。使用系统信任的CA时，留空 `ZSVIRT_CA_PATH` 并省略此覆盖文件。

## VM探针和CPU AI业务

将源码部署到 `/opt/layertrace`，将单独模型归档中的GGUF及许可置于 `/opt/layertrace-models`。导入探针/模型归档 `layertrace-vm-images.tar`、Agent/工具归档 `layertrace-workload-image.tar` 和独立客户端归档 `layertrace-client-image.tar`，探针和工作负载按[离线镜像导入说明](docs/offline-images.md)核验；客户端按[独立镜像说明](docs/client-image.md)核验并导入。参考 `deploy/vm-probe/env.example` 创建mode600的 `workload.env`，填真实VMUUID、持久installationID、控制面地址和凭证。模型文件、SHA256、来源、许可和推理版本见 `deploy/models/manifest.yaml`。

通用HTTPS网络部署可使用 `sudo bash deploy/vm-probe/install.sh /opt/layertrace/workload.env`。本机SSH隧道/host-network离线实验采用如下六份配置：

```bash
cd /opt/layertrace
sudo docker compose --project-name layertrace-vm --env-file workload.env \
  -f deploy/vm-probe/compose.yaml -f deploy/workload/compose.yaml \
  -f deploy/vm-probe/compose.host-network.yaml -f deploy/workload/compose.offline.yaml \
  -f deploy/workload/compose.client-fixed.yaml -f deploy/vm-probe/compose.final-images.yaml \
  up -d --no-build --pull never docker-proxy vm-probe demo-model demo-tool demo-agent
```

探针走受限只读Docker代理，导出9108数值指标，事件/库存经认证API与worker处理。业务使用本地llama.cpp/Qwen模型、真实HTTP工具和独立客户端。提示词与生成正文留在业务结果文件，遥测只记录必要身份、状态和分段耗时。

## 真实实验与UI

[UI 操作指南](docs/ui-operation-guide.md)提供完整终端与页面步骤，[视频口播稿](docs/video-narration.md)提供4分30秒口播和镜头表。正常基线需要同一依赖路径至少30个成功任务；超时不计成功。R2要求同一模型资源的真实配额与节流，健康基线采用任务内模型累计耗时（initial 与 synthesis 累计）的P95。当前任务的模型累计耗时或单个闭合模型阶段的真实 duration_ms，可逐条作该累计量的保守下界比较；不跨阶段或任务求和。关联窗口为150秒，延迟须严格超过健康P95两倍且增加超过500ms；证据不足保持不足。

```bash
cd /opt/layertrace
sudo python3 -m scenarios.real_lab run cpu-quota --target FULL_MODEL_CONTAINER_ID --seconds 180 --cpus 0.1
sudo python3 -m scenarios.real_lab run process-failure --target FULL_AGENT_CONTAINER_ID --seconds 45
sudo python3 -m scenarios.real_lab run tool-503 --target FULL_TOOL_CONTAINER_ID --seconds 45
sudo python3 -m scenarios.real_lab restore --state /absolute/path/to/state.json
```

仅带 `layertrace.lab=true` 的对应服务容器可注入，最长180秒；保存完整ID与原配置、独立watchdog负责到期恢复，恢复后验证新业务任务。进程SIGKILL的退出137不冒称OOM。确认和静默保留事实，事件单恢复需要业务证据、来源新鲜与稳定窗口（正式默认60秒；本机演示15秒）。

## 验证与交付

测试使用独立数据库，不能对正在使用的交付数据库运行清表夹具。核心契约、单元、集成、安全及反例测试见 `docs/test-report.md`；真实PostgreSQL16.4空库冷启动可用 `scripts/cold_smoke.py` 复验。浏览器验收使用普通会话并覆盖页面导航、证据、确认与静默。

`scripts/package_release.py create` 生成源码ZIP、可导入Git bundle、材料ZIP和独立模型/镜像归档；`verify` 检查路径、成员及SHA256，扫描本机已知秘密和残留占位。源包排除env、私钥、运行状态、qcow2、依赖环境、缓存与旧规划材料。交付目录和外部仓库/视频链接要求见 `docs/assets-delivery-index.md`。逐项核对见 [提交清单](docs/submission-checklist.md)。材料包 `LayerTrace-materials/evidence/ui-final/voiced-media-qa.json` 记录媒体完成状态及截图、PDF、视频、快捷表各 manifest 的实际 SHA256；`evidence-summary.json` 中的 `media_ready` / `media_remaining` 属于实验验收事实快照，媒体完成状态由该独立 QA 记录给出。`release-manifest.json` 的仓库、视频与邮件布尔字段是本地打包时的状态快照，后续对外发布以独立发布记录和实际访问链接核验。本机演示控制面使用 `MODE=lab`、15秒稳定恢复窗口和3600秒基线窗口；交付默认配置为 `MODE=prod`，实验功能默认关闭。

卸载入口 `deploy/uninstall.sh` / `scripts/uninstall.py` 先停止采集、撤销并验证旧探针凭证拒绝，再移除指定组件，默认保留数据。永久清理需明确卷清单与确认，见 `docs/permissions.md`。

## 许可证

项目Apache-2.0。第三方说明见 `THIRD_PARTY_NOTICES.md`；模型Apache-2.0、llama.cpp MIT，原始上游许可随独立资产保留。源码、完整可运行代码仓库链接、作品介绍PDF和3–5分钟视频链接分别按比赛通知准备；截止为2026-10-17 00:00，Asia/Shanghai。
