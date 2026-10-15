# 离线镜像导入与固定部署

交付镜像目录含四份 tar 和四份精确镜像清单。按包中的 assets-manifest.json、镜像清单和 SHA256SUMS 核对实际字节。OCI 镜像身份与平台 config digest 分别核验；Docker 的两种镜像存储模式可能返回不同对象，导入工具按已校验 tar 的身份集合处理。

| 归档 | 内容 | 对应清单 |
|---|---|---|
| layertrace-control-plane-images.tar | 控制面及worker/migrate、PostgreSQL16.4、Prometheus3.5 | cp-images-manifest.json |
| layertrace-vm-images.tar | VM探针、只读Docker网关、locked CPU模型镜像 | probe-images-manifest.json |
| layertrace-workload-image.tar | 保留的Agent/工具镜像 | workload-images-manifest.json |
| layertrace-client-image.tar | 独立持续客户端镜像 | client-images-manifest.json |

控制面主机在解压的源码根目录执行：

    python3 scripts/load_offline_images.py --archive /path/to/layertrace-control-plane-images.tar --manifest /path/to/cp-images-manifest.json
    python3 scripts/configure_local_env.py --path .env --mode prod
    docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.offline.yaml -f deploy/compose.final-images.yaml up -d --no-build --pull never postgres migrate control-plane worker prometheus

跨主机、私有CA及凭据配置按 [README](../README.md) 和 [权限说明](permissions.md) 完成。

业务VM从源码根目录分别导入探针、工作负载和独立客户端：

    sudo python3 scripts/load_offline_images.py --archive /opt/layertrace-assets/layertrace-vm-images.tar --manifest /opt/layertrace-assets/probe-images-manifest.json
    sudo python3 scripts/load_offline_images.py --archive /opt/layertrace-assets/layertrace-workload-image.tar --manifest /opt/layertrace-assets/workload-images-manifest.json
    sudo python3 scripts/load_client_image.py --archive /opt/layertrace-assets/layertrace-client-image.tar --manifest /opt/layertrace-assets/client-images-manifest.json

任一导入命令追加 --check-archive-only 即只核对本地归档，不连接Docker。将模型包中的GGUF放到配置所指的模型目录，保持原许可证。按 [VM部署说明](../deploy/vm-probe/README.md) 填写真实VM UUID、持久安装身份、URL与注册凭据，然后使用六文件入口。探针固定覆盖文件选择新探针标签，独立客户端覆盖文件选择已核验镜像引用；禁止现场重建或网络拉取。

控制面镜像完成152个指定源文件的一致性核验。探针镜像的实际入口模块另通过镜像内导入和业务VM加载证明核对，构建引用同一冻结revision；这些证明不表示两个镜像包含完全相同的文件。Agent/工具工作负载与独立客户端分别保留其原镜像和独立来源证明。镜像成功导入不能代替完整真实故障、A/B采样和补传验收；结果以材料中的最终summary和实际证据链为准。
