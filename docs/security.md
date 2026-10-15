# 安全说明

## 传输与浏览器会话

控制面 Compose 将 8000 端口绑定宿主机 `127.0.0.1`。跨主机使用受信 HTTPS，或按 `deploy/vm-probe/README.md` 建立 SSH 隧道，使业务 VM 与控制面的 HTTP 端点都仅监听 loopback。私有 CA 必须显式配置，客户端保持证书校验；平台适配器拒绝访问非 loopback 的 HTTP 地址。

使用 Caddy 时挂载真实证书和密钥，入口文件为 `deploy/caddy/Caddyfile`。没有受信证书时，安装脚本仅启动本地入口；不能把 loopback 端口映射改为公网地址以绕过 TLS。Compose 允许固定网桥网关 `172.31.255.1/32` 的明文流量，用于宿主机 loopback 经 DNAT 到达控制面；这个例外必须与 loopback 端口绑定配套。

控制面仅采用 `TRUSTED_PROXIES` 中地址的转发头。直接运行时默认只信任 `127.0.0.1/32,::1/128`；Compose 显式设置其专用网段 `172.31.255.0/24`。其他来源的 `X-Forwarded-*` 被剥离，跨主机明文请求返回 426。部署人员应根据实际代理地址收紧此列表。

浏览器通过登录页输入主 API 令牌。登录请求检查同源与一次性 nonce，成功后签发 12 小时会话，令牌本身不写入 Cookie。会话 Cookie 为 HttpOnly、SameSite=Lax，HTTPS 下带 Secure；JavaScript 可读的 CSRF Cookie 与签名会话绑定。会话写操作必须携带匹配的 `X-CSRF-Token`；注销也检查 CSRF。令牌轮换后，旧令牌签名的会话失效。

## 认证、范围与撤销

主 API 接受 Bearer 令牌，使用恒定时间比较。当前每个配置的主令牌具有同一服务端租户、项目及 read/write/admin 范围；交付实现没有细分用户角色管理。`prod` 和 `lab` 均要求认证。只有无令牌配置的 `replay` 模式允许进程本地 loopback 匿名访问，Compose 交付入口要求配置令牌。

探针注册使用受保护的 `PROBE_REGISTRATION_SECRET`，签发凭据默认有效 600 秒，探针在到期前 60 秒续注册。注册 secret 本身不是自动过期令牌。探针安装 ID 绑定真实平台 VM UUID，真实平台模式要求平台同步已确认该 VM；同一安装 ID 不能改绑其他 VM。服务端保存凭据 SHA-256，探针本地凭据保存在专属状态卷的 0600 文件。

探针凭据只有采集写权限，限制到其绑定 VM 及已确认的容器、服务实例。服务端注入租户与项目，客户端不能覆盖。主 API 的资源与探针操作也检查服务端项目范围；状态页面仅展示当前项目的探针。来源状态输出屏蔽 credential、secret、token、password 字段。

管理员可调用 `POST /api/v1/probes/{probe_id}:revoke`。成功返回 204，并删除服务端凭据摘要、标记来源不可用、保存撤销时间；旧凭据被拒绝，同一安装 ID 自动续注册也被拒绝。历史库存、事件、任务与诊断继续保留。再次部署应采用明确授权的新安装身份，并核对其 VM 绑定。

## Docker 采集与真实实验

VM 探针没有直接挂载 Docker socket。单独的 Python 代理挂载 socket，并仅允许 GET `/containers/json`、`/events`、完整容器 ID 的 inspect 和 stats；写入、exec、kill、update、delete 均被拒绝。返回内容有 8 MiB 上限。代理在 Compose 内网运行，或在 SSH 回环配置中仅监听 `127.0.0.1:2375`，不发布 guest 外部端口。socket 挂载本身具有高权限，保护措施来自代理程序及其网络隔离。

探针以 UID 10001 运行，具有独立状态卷。工作负载只读挂载不含凭据的身份卷；只有 `layertrace.observe=true` 容器进入采集范围。指标标签包含完整容器 ID、服务实例、VM 和 probe ID，不包含 task ID 或提示词。

真实故障统一由业务 VM 的 `python3 -m scenarios.real_lab` 执行。运行人员需要该 VM 的 Docker 管理权限。CLI 只接受 `layertrace.lab=true` 且服务类型匹配的演示容器，最长 180 秒；仅有 `layertrace-test=true` 不赋予真实注入权限。白名单为 Agent 进程中断、模型 CPU 配额、工具 HTTP 503。操作保存完整容器 ID 与原配置，正常结束或可处理的终端中断执行恢复，并再次 inspect 验证。

注入前启动独立恢复进程，保存deadline并确认已经就绪；CLI被SIGKILL或终端失联时仍可到期恢复。VM断电或Docker不可用会阻止该进程执行，重启后使用独立 `restore --state ...`。恢复只操作保存的完整容器ID，同名替代容器被拒绝；仍需业务成功与事件单稳定恢复验证。控制面兼容场景 API 的 lab 通道受 MODE/LAB_ENABLED 限制，页面回放加载已保存事实，不承担真实 Docker 注入。

## 数据、离线补传与运行资产

采集事件在进入队列前过滤敏感键和值，服务端写入再执行脱敏。遥测排除提示词、完整模型回答、工具参数原文、Authorization、Cookie、API key、完整环境变量；记录被排除的键名以供审计。正常业务的回答保存在独立客户端结果文件，交付时仅选择必要且已经检查的业务样例。

探针使用持久 SQLite outbox。整批入库 ACK 确认 accepted + duplicate 等于发送数量后才删除记录；连接失败或不完整 ACK 保留记录，重启后继续补传。默认上限 100 MiB、10 分钟；容量或时间超限会累计丢弃数并记录数据空档。此缓存保证有界补传，不代表无限离线留存。

指标 9108 默认只监听 VM loopback，远端抓取通过 SSH 转发，或显式绑定受防火墙限制的 guest 地址。当前交付采用探针指标 exporter，不宣称已经部署 node_exporter 双向 TLS 或 remote-write。旧样本过期后 exporter 不继续提供旧样本，`probe_up=0`。

`scripts/configure_local_env.py` 生成独立随机凭据、同步的数据库密码与连接串，并保存为 0600 文件。`.env`、私钥、注册 secret、令牌与本地凭据不进入源码。打包脚本对源码及显式选择的证据执行凭据检查，校验每个归档的文件、摘要和必要入口。模型、镜像单独打包并记录来源、版本、SHA-256 与许可证，ZSvirt qcow2 不重复分发。

## 卸载与验证边界

`deploy/uninstall.sh` 要求明确选择 `--target vm` 或 `--target control-plane`。VM 卸载先暂停探针，撤销服务端凭据并验证旧 heartbeat 被拒绝，然后停止合并 Compose、删除状态卷中的精确凭据文件，清空采集侧配置的 secret/令牌值。服务器撤销失败时恢复探针运行，服务和本地配置保留。控制面卸载先撤销当前项目可见探针，再停止控制面。默认保留数据库、指标、SQLite 队列和业务结果卷；数据删除使用独立的 `deploy/purge-data.sh`。

实际平台、guest、实验、吞吐与开销证据以 `docs/release-evidence.md` 及交付 `evidence/` 为准。平台原生告警、容器指标、真实业务和回放来源分别展示。没有 GPU 的环境仅提供 GPU 回放验证，平台利用率/GPU 映射能力缺失会明确列出；不得据此推导生产最小容量或生产级安全认证。
