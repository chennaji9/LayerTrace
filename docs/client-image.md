# 独立客户端镜像与离线导入

客户端使用独立镜像，负责串行发送请求并保存业务结果。Agent、模型、工具与控制面的运行身份分别由实验记录绑定；控制面152个指定文件的源码一致性范围不包含独立客户端源码。

| 内容 | 交付记录 |
|---|---|
| 镜像名称 | `layertrace-demo-client:delivery-20261015-sleepfix` |
| 客机已核验的 Compose 引用 | `sha256:f9908323235308c10f5728ffe65ee938b33835219ae36638d1f944de5d0a6fb8` |
| 平台配置摘要 | `sha256:5f6fe8a86a975038cbe693a1891216c0ea9c7dcfc09fa328dba46035edcd26ca` |
| 客户端源码 | `workloads/demo_agent/load_generator.py`；SHA256 `87f522e71a15cea8b8b9fb580bfad4c97aa756d8cfafe99213c487f7f0441fd6` |
| 派生构建文件 | [Dockerfile.demo-client](../deploy/Dockerfile.demo-client) |
| 离线归档 | `layertrace-client-image.tar`，47,413,248 bytes；SHA256 `4c9c8bb3c7f7bb9073a8db5742190097b694439559d422e742a0d900d5b4c794` |
| 精确资产清单 | `client-images-manifest.json`，分别记录镜像身份、配置摘要、源文件和构建/加载证明的实际 SHA256 |

OCI 镜像身份与平台配置摘要是不同对象，按清单分别核对。上述 raw imageID 已在业务客机完成隔离容器运行及客户端源码摘要核验，隔离容器随后删除。构建与加载证明不表示正式 A/B 或补传实验已经完成；实际部署、连续客户端代次、采样和恢复另按其独立记录核验。

## 导入和启动

将模型及全部离线镜像归档传到业务 VM，保留已核验的资产清单。独立客户端导入使用[专用导入工具](../scripts/load_client_image.py)，它校验归档大小、SHA256 和导入后的镜像身份，不启动服务：

```sh
sudo python3 scripts/load_client_image.py \
  --archive /opt/layertrace-assets/layertrace-client-image.tar \
  --manifest /opt/layertrace-assets/client-images-manifest.json
```

只核对本地归档时追加 `--check-archive-only`，该选项不访问 Docker。正式材料中的离线镜像包包含上述归档及 `offline-images/manifests/client-images-manifest.json`，部署时将清单放到命令所指的路径。

业务部署使用六个 Compose 文件，独立客户端覆盖文件后加载探针固定镜像覆盖文件：

```sh
sudo docker compose --project-name layertrace-vm --env-file workload.env \
  -f deploy/vm-probe/compose.yaml -f deploy/workload/compose.yaml \
  -f deploy/vm-probe/compose.host-network.yaml -f deploy/workload/compose.offline.yaml \
  -f deploy/workload/compose.client-fixed.yaml -f deploy/vm-probe/compose.final-images.yaml \
  up -d --no-build --pull never
```

[客户端覆盖文件](../deploy/workload/compose.client-fixed.yaml)仅设置 `demo-client` 的镜像、禁止构建和禁止拉取，其他服务仍使用各自记录的镜像。维护客户端时，在同一六文件入口执行 `up -d --no-deps --no-build --pull never demo-client`；真实采样和故障窗口内不运行部署命令。Compose 需要支持 `!reset` 的 v2.24 或以上版本。

## 等待循环与验证范围

默认请求间隔为2秒。等待循环每次只读取一次单调时钟计算剩余时长，非正值立即结束等待，传给 `time.sleep` 的数值保持为正。确定性边界测试覆盖剩余时间在时钟读取间跨过零点的情况；修复和原源码反例记录在材料 `evidence/client/repair-review/`，实际构建、加载和部署证明在 `evidence/client/`。

实验验收记录中的独立客户端来源按原 manifest 保留。正式开销采样使用同一连续客户端容器、启动代次、默认间隔和设置；被排除的采样窗口、原始异常与恢复证据仍在材料 `evidence/reliability/failed-first-on/`，不能计入正式统计。历史排除记录以 `evidence/overhead/retry-selection.json` 保留；正式选择以 `evidence/final-20261015/overhead/windows.json` 及独立客户端交付核验为准。

被排除窗口的原始 tar 在材料包内保存为 `evidence/reliability/failed-first-on/overhead-on-6f90acb7e0ac.tar.raw`；扩展名用于通过材料归档的文件筛选，文件内容保持原 tar 字节，摘要由导出清单核对，可直接交给 tar 工具读取。
