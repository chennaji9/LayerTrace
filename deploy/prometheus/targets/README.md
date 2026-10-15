# Prometheus 静态发现

此目录只读挂载到 Prometheus 的 `/etc/prometheus/targets`。`probes`、`nodes` 与 `workloads` 中的 JSON 默认是空数组，避免抓取不存在的目标。

真实 VM 探针在 9108 暴露指标。在本地部署配置中创建 `probes/actual.json`：

```json
[{"targets": ["<实际业务VM地址>:9108"], "labels": {"environment": "zsvirt-lab"}}]
```

目标必须能够从 Prometheus 容器访问，并通过防火墙限定到控制面采集源。指标中的 VM、probe 与完整容器身份由探针提供，不要用显示名覆盖。跨主机 TLS 使用受信证书或受控加密隧道；直接开放到公网不属于默认部署方式。

`file_sd` 每 30 秒重新读取文件，抓取和规则评估每 10 秒执行。添加后核查 Prometheus `/api/v1/targets` 中的实际状态。不要把空配置解释为指标已经采集成功。项目不提供 demo-workload 的通用 exporter，只有确实部署的 exporter 才加入目标。
