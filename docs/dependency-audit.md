# 依赖、镜像与许可证终审

**审计日期**：2026-10-15  
**结论**：源码、固定推理镜像和Qwen模型分别打包；本地CPU推理、原始上游许可与资产SHA256已核验。

## 项目与准入

- 项目许可证为 Apache-2.0，根目录 `LICENSE` SHA-256：
  `271aa7f313350927468787eadc755309a5c238fd391b056c68796a0bcc7f5280`。
- 准入以 `docs/license-policy.md` 为准：MIT、Apache-2.0、BSD-2/3、ISC、0BSD；
  MPL-2.0 逐案审查。
- 运行时依赖中发现的 psycopg/LGPL 冲突已整改：Alembic 改用 asyncpg；psycopg 仅留在
  `dev` 测试依赖，不进入发布镜像。
- Grafana 和 Tempo 为未修改的可选独立 AGPL 容器，经 HTTP/OTLP 边界交互；发布时保留
  上游许可证与源码获取信息，不将其代码链接进 LayerTrace。

## Python 直接依赖

运行时版本全部在 `pyproject.toml` 精确锁定：FastAPI 0.115.0、uvicorn 0.30.6、
Pydantic 2.9.2、pydantic-settings 2.5.2、SQLAlchemy 2.0.35、asyncpg 0.29.0、
Alembic 1.13.2、Jinja2 3.1.4、httpx 0.27.2、click 8.1.7、PyYAML 6.0.2、
jsonschema 4.23.0。开发依赖与浏览器依赖同样固定版本。

本项目当前没有生成完整的哈希锁文件；可复现性由精确版本和镜像构建层提供，但这弱于
`pip --require-hashes`。对外发布 wheel/镜像前应生成目标平台锁文件；本报告不把版本固定
表述为传递依赖哈希锁定。

## 容器镜像摘要

以下为多架构 manifest digest；2026-09-22 通过 DaoCloud registry 代理查询，避免 Docker Hub
网络超时。Compose 与 Dockerfile 已按摘要固定。

| 镜像 | Manifest SHA-256 |
|---|---|
| python:3.12.6-slim | `ad48727987b259854d52241fac3bc633574364867b8e20aec305e6e7f4028b26` |
| postgres:16.4-alpine3.20 | `5660c2cbfea50c7a9127d17dc4e48543eedd3d7a41a595a2dfa572471e37e64c` |
| prom/prometheus:v3.5.0 | `63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996` |
| grafana/grafana:11.2.0 | `408afb9726de5122b00a2576763a8a57a3c86d5b0eff5305bc994ceb3eb96c3f` |
| caddy:2.8.4-alpine | `af32e97399febea808609119bb21544d0265c58a02836576e32a2d082c262c17` |
| grafana/tempo:2.6.0 | `f55a8a1937fff0af3a760d376b476c8327fb30e432d5e7630d7938b67691e822` |
| otel/opentelemetry-collector-contrib:0.109.0 | `af0f72b2d2493fc17f06cf3bc136548240cd7ebb2c8b1c8a7be6f3eb03068389` |
| prom/node-exporter:v1.8.2 | `4032c6d5bfd752342c3e631c2f1de93ba6b86c41db6b167b9a35372c139e7706` |
| ghcr.io/tecnativa/docker-socket-proxy:0.3.0 | `9e4b9e7517a6b660f2cc903a19b257b1852d5b3344794e3ea334ff00ae677ac2` |

## 离线前端资产

| 文件 | 版本 | 许可证 | SHA-256 |
|---|---:|---|---|
| `htmx.min.js` | 1.9.12 | MIT/0BSD | `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452` |
| `cytoscape.min.js` | 3.28.1 | MIT | `92d752b48ea949720675865197fd2a0001c95bc5888545e990af60321712d4c6` |
| `echarts.min.js` | 5.5.0 | Apache-2.0 | `42f8329d989b6f6539dd2b15bbdf0d82025762ac112fbb60dc57b27d7bcf3946` |

## 模型资产与运行时

`deploy/models/manifest.yaml` 是有效 YAML，记录 Qwen GGUF 文件、大小、SHA256、模型来源及推理镜像摘要。模型文件491400032 bytes，SHA256为 `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`。llama.cpp CPU镜像固定为 `09bf53de6c74c0d6600444375d257803269a4a182061c3e4a0db4f57218faf8a`，build11610。

两项上游原始许可文本已获取，模型和VM镜像独立交付，不混入源码ZIP。Ubuntu cloud image与ZSvirt系统盘属于环境资产，不进入这些归档。完整实际摘要以交付 `SHA256SUMS` 与 `assets-manifest.json` 为准。

本次受限 Docker 代理来自本仓库，不依赖 Tecnativa 镜像。上表额外exporter、Grafana、Tempo、Caddy为可选配置，本次真实指标来自VM探针，本地运行没有启用Tempo/GPU。

## 兼容性与遗留风险

- WSL 测试环境使用 Python 3.12，项目声明支持 Python 3.11+；全量测试报告覆盖当前环境，
  尚未形成 Python 3.11 的独立兼容性证据。
- Docker Hub 直连超时，摘要通过镜像代理读取；摘要与上游标签绑定，部署仍应在安装时校验。
- AGPL 可选容器不应被重新打包为修改版；若后续修改其源码，必须重新进行许可证审查。
