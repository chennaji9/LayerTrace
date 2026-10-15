# 权限说明

## 组件权限

| 组件 | 实际权限与用途 | 边界 |
|---|---|---|
| control-plane / worker | PostgreSQL读写、Prometheus查询、只读平台API | 不访问业务VM Docker socket；令牌范围由服务端注入 |
| ZSvirt适配器 | 查询host、VM、原生告警与事件记录 | HTTPS验证CA，HTTP限本地SSH隧道；利用率与GPU映射列为能力缺失 |
| Python Docker代理 | 挂载guest Docker socket，转发允许的GET | 容器列表、完整ID inspect/stats、有界events；拒绝Docker写操作 |
| vm-probe | 受限代理读取、单VM采集写入、状态卷读写 | UID10001；安装ID绑定VM；凭据TTL与服务器撤销；不向工作负载共享凭据 |
| 探针SQLite outbox | 持久保存未ACK事件与游标 | 默认100MiB/10分钟；整批ACK后删除；超限丢弃和gap可见 |
| 探针指标exporter | 9108提供当前容器样本 | 默认loopback，SSH转发或guest防火墙限制；不包含task ID |
| Prometheus | 读取显式targets及探针指标、保存时间序列 | 静态/file_sd配置；部署人员保证实际抓取地址可达 |
| Caddy（选用） | 读取实际证书和私钥，终止HTTPS | 可信代理地址显式配置；密钥不入源码或证据 |
| demo Agent / client | 业务HTTP与项目遥测写入、各自状态卷 | 身份卷只读；遥测排除业务正文与凭据；客户端单独保存任务结果 |
| demo model / tool | CPU推理、工具HTTP、隔离演示状态 | 模型权重只读；模型与工具默认仅容器网，SSH配置只监听loopback |
| 真实实验CLI | 执行人员的guest Docker管理权限 | `layertrace.lab=true`、服务匹配、完整ID、≤180秒、原配置与独立恢复 |
| 浏览器会话 | 当前项目主API令牌交换签名会话 | HttpOnly/SameSite、HTTPS Secure、12小时、写入CSRF、注销检查 |

## 凭据与服务端范围

主 API 当前为配置令牌模型：每个主令牌均具有同一租户/项目的 read、write、admin 权限。Web 登录采用同一范围。交付实现不提供用户角色编辑、企业 SSO 或多租户管理界面。

探针注册 secret 仅用于签发凭据，不能直接调用采集写接口。凭据默认 600 秒，到期前 60 秒续注册；服务端仅保存摘要。探针只能为其绑定 VM、已登记完整容器 ID 与服务实例写入，无法读取管理 API 或撤销其他探针。真实平台模式须先由平台同步确认 VM；同一安装 ID 改绑被拒绝。

`POST /api/v1/probes/{probe_id}:revoke` 要求管理员及 write 范围，并检查 VM 所属租户与项目。撤销保留历史数据，旧凭据与同安装 ID 的自动续注册被拒绝。`/status` 只展示当前项目的探针，敏感凭据字段被屏蔽。

## 部署与卸载

控制面通过 loopback HTTP、SSH隧道或受信HTTPS访问。直接运行默认只信任loopback转发代理，Compose信任其专用网段；不要把整个内网添加为可信代理。私有CA按实际路径挂载，保持证书校验。当前指标部署使用探针exporter；node_exporter的mTLS与remote-write不是已实测交付能力。

VM 运行 UID 10001 的探针与独立 Python Docker 代理。代理容器为读取 socket 使用 root，真实实验CLI需要Docker管理权限；这两项是部署边界，应限制可执行人员。模型运行镜像以模型资产清单为准，不笼统宣称全部容器均为非root。

在源码根目录执行明确目标的卸载：

```sh
# 业务VM：服务器撤销成功且旧凭据被拒绝后，停止VM全部合并服务。
bash deploy/uninstall.sh --target vm --env-file deploy/vm-probe/.env

# SSH回环与离线部署追加与安装一致的覆盖文件。
bash deploy/uninstall.sh --target vm --env-file deploy/vm-probe/.env \
  --compose-overlay deploy/vm-probe/compose.host-network.yaml \
  --compose-overlay deploy/workload/compose.offline.yaml

# 控制面：先撤销当前项目探针，再停止控制面。
bash deploy/uninstall.sh --target control-plane --env-file .env
```

私有CA可通过 `--ca-file` 提供脚本所在主机的真实证书路径。无法从探针容器读取凭据时，可提供受保护的 `--credential-file`；该文件必须来自选定探针。服务器连接、认证、撤销或旧凭据拒绝验证失败时，卸载不继续停止服务和删除凭据。

卸载保留数据库、指标、业务结果和SQLite队列卷，控制面数据库配置保留在受保护的本地文件。独立数据删除入口为 `deploy/purge-data.sh`，执行前需核对精确数据卷与备份。性能与容量仅按实际环境报告，推荐资源量不等于已经实测的生产最低配置。
