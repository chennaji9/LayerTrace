# ZSvirt 只读接入

控制面仅使用普通账户读取 `/hosts`、`/vm-instances`、`/zwatch/alarm-records`、`/zwatch/event-records`，不执行虚拟机写操作。平台连接使用 HTTPS 与受信任 CA；本地实验环境可使用绑定回环地址的 SSH 隧道。

在 ZSvirt 创建 `Normal` 类型的 `layertrace-observer` 账户，绑定只包含下列四项策略的自定义角色：

```text
.header.host.APIQueryHostMsg
.header.vm.APIQueryVmInstanceMsg
.zwatch.api.APIQueryAlarmRecordMsg
.zwatch.api.APIQueryEventRecordMsg
```

给观察账户共享需要读取的虚拟机、宿主机与相关告警定义，共享权限为 `READ`。ZSvirt 5.0 原生告警与事件记录另有 `accountUuid` 过滤，即使资源已共享也可能返回空列表。当前实验平台需要绑定原生记录可见性角色 `8550125df53c54edb33d1b8ae83ded55`（`predefined: sod-system-administrator`）。该角色的 API policies 必须为空；账户类型仍为 `Normal`，API 权限仍由四项自定义读取策略控制。接入时应实际验证记录可见性，并以同名 `updateVmInstance` 请求确认写权限被拒绝；ZSvirt 5.0 的异步拒绝结果可能是 HTTP 503、错误码 `SYS.1019`。

设置 `ZSVIRT_TOKEN` 可直接读取。需要持续采集时，增加 `ZSVIRT_ACCOUNT_NAME` 与 `ZSVIRT_PASSWORD_HASH`；后者是观察账户密码的 SHA-512 十六进制值，应按密码同等保护，仅存于本地环境配置。读取遇到 HTTP 401 或平台明确返回 `ID.1001` 时，适配器登录并重试一次；其他权限错误不触发重新登录。新会话在当前 worker 内存中复用，已知到期时间前一分钟续期。仅提供 token 时，过期显示 `permission_denied` 与 `session_expired`，保留已有资源、事件和游标。

状态接口只展示认证方式与会话到期时间。日志、证据文件与提交包均不得包含 token、密码或密码哈希。平台告警的 `dataUuid` 用于事件记录幂等键；`alarmUuid` 用于同一资源告警的恢复匹配，两者不能互换。
