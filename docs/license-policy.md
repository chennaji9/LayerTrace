# 项目许可证与第三方准入政策

**项目许可证**：Apache License 2.0（见根目录 `LICENSE`）。

## 准入 Allowlist（允许引入并可再分发）

| 许可证 | 适用范围 | 再分发结论 |
| --- | --- | --- |
| MIT | 代码、前端资产 | 允许；保留版权与许可声明 |
| Apache-2.0 | 代码、模型权重、前端资产 | 允许；保留 NOTICE 与许可声明 |
| BSD-2-Clause / BSD-3-Clause | 代码 | 允许；保留声明 |
| ISC | 代码 | 允许；保留声明 |
| 0BSD | 代码 | 允许 |
| MPL-2.0 | 逐案审查 | 允许（文件级 copyleft，不修改或隔离修改文件） |

## 禁止引入

- 不允许把 GPL / LGPL / AGPL / SSPL 组件以不兼容的方式合并到本项目代码或静态资产。独立系统工具、可选服务和基础镜像按各自许可证管理，不能因为项目采用 Apache-2.0 就声称其许可义务消失。
- 任何不允许再分发的模型权重或数据集。
- 无许可证（No License / Unlicensed）的发布产物。

## 准入流程

1. 引入前在 `THIRD_PARTY_NOTICES.md` 登记：名称、版本、来源 URL、许可证、再分发结论。
2. 前端资产固定版本放入 `apps/control_plane/web/static/vendor/`，离线可用。
3. 模型权重在 `deploy/models/manifest.yaml` 登记版本、SHA-256、许可证与再分发结论。
4. 发布终审（T145）复核全部依赖、镜像与模型，并补齐版本哈希。

## 已准入前端资产（版本固定）

| 资产 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| htmx.org | 1.9.12 | 0BSD | 页面局部更新 |
| cytoscape | 3.28.1 | MIT | 拓扑图渲染 |
| echarts | 5.5.0 | Apache-2.0 | 指标图表 |

## 已准入模型与推理运行时

- llama.cpp（MIT）：CPU 推理 server，固定镜像摘要和实际 build 见 `deploy/models/manifest.yaml`；独立镜像包保留上游原始 LICENSE。
- Qwen2.5-0.5B-Instruct Q4_K_M（Apache-2.0）：本地实际推理模型，权重 SHA256、来源及再分发记录见同一 manifest；独立模型包保留上游原始 LICENSE。

前端原始许可与 ECharts NOTICE 位于 `apps/control_plane/web/static/vendor/licenses/`，来源及文件摘要随该目录 manifest 保存。实际分发文件和资产包摘要以最终交付清单为准。
