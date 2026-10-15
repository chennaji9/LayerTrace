# 第三方声明（THIRD_PARTY NOTICES）

本文件登记项目再分发的第三方组件。引入前必须通过 `docs/license-policy.md` 的 allowlist。

## 前端离线资产（apps/control_plane/web/static/vendor/）

### htmx 1.9.12

- 来源：https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
- 本分发采用官方v1.9.12标签的Zero-Clause BSD（0BSD）许可。
- 上游原文：`apps/control_plane/web/static/vendor/licenses/htmx-LICENSE.txt`
- 再分发结论：允许（随源码保留上游许可原文）

### cytoscape.js 3.28.1

- 来源：https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js
- 许可证：MIT License
- 版权：Copyright (c) 2016-2023, The Cytoscape Consortium.
- 上游原文：`apps/control_plane/web/static/vendor/licenses/cytoscape-LICENSE.txt`（min.js文件头亦保留完整MIT正文）
- 再分发结论：允许（保留本声明与许可文本）

### echarts 5.5.0

- 来源：https://unpkg.com/echarts@5.5.0/dist/echarts.min.js
- 许可证：Apache License 2.0
- 版权：Copyright 2017-2024 The Apache Software Foundation
- 上游原文：`apps/control_plane/web/static/vendor/licenses/echarts-LICENSE.txt` 与 `echarts-NOTICE.txt`
- 再分发结论：允许（保留上游LICENSE/NOTICE及源码内第三方通知）

上述文件均来自对应官方版本tag，通过GitHub contents API获取，保留原始字节并核验Git blob SHA-1；每份文件的来源URL、版本、大小与SHA256记录于 `apps/control_plane/web/static/vendor/licenses/manifest.json`。

## Python 依赖（版本以 pyproject.toml 为准）

FastAPI (MIT) · Pydantic (MIT) · Starlette (BSD-3) · uvicorn (BSD-3) ·
SQLAlchemy (MIT) · asyncpg (Apache-2.0) · Alembic (MIT) · Jinja2 (BSD-3) ·
httpx (BSD-3) · click (BSD-3) · PyYAML (MIT) · jsonschema (MIT) ·
pydantic-settings (MIT) · pytest (MIT，仅开发) ·
Playwright (Apache-2.0，仅开发/验收)

开发测试环境另使用 psycopg 3.2.1（LGPL-3.0）提供同步数据库夹具；它不属于运行时依赖，
不进入发布镜像。Alembic 与应用运行时统一通过 Apache-2.0 的 asyncpg 连接 PostgreSQL。

## 容器镜像

python:3.12.6-slim（PSF License / 系统库各自许可证）·
postgres:16.4-alpine3.20（PostgreSQL Licence）·
prom/prometheus:v3.5.0（Apache-2.0）·
grafana/grafana:11.2.0（AGPL-3.0，作为独立容器运行、不再分发修改版，仅调用其
HTTP API 与页面，不与本项目代码组合为派生作品）·
caddy:2.8.4-alpine（Apache-2.0）·
grafana/tempo:2.6.0（AGPL-3.0，独立运行，同上）·
otel/opentelemetry-collector-contrib:0.109.0（Apache-2.0）

本次 VM 使用源码内的 `agents/vm_probe/socket_proxy.py` 受限只读代理，打包在 LayerTrace 探针镜像。可选部署目录保留的第三方 exporter 镜像见依赖清单。

> Grafana、Tempo 属于可选独立组件；项目不修改其源码。部署者应遵守其上游许可证，源码获取入口分别为 https://github.com/grafana/grafana 与 https://github.com/grafana/tempo。

## 模型与推理

- llama.cpp build11610 / git091b6a7fc561b78da90a0ed2d524ec995458c657（MIT），固定CPU镜像通过独立离线镜像包交付。
- Qwen2.5-0.5B-Instruct Q4_K_M GGUF（Apache-2.0），权重通过独立模型包交付。
- 原始上游许可文本随模型与镜像资产索引保留，版本、来源、SHA256见 `deploy/models/manifest.yaml` 和交付资产清单。
