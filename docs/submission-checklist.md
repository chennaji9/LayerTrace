# LayerTrace 提交核对清单

用户提供的比赛通知要求完整可运行代码仓库链接、作品介绍文档、3–5 分钟演示视频链接及材料附件。截止时间为 **2026年10月16日24:00 Asia/Shanghai，即10月17日00:00**。报名确认邮件中有关收件地址、主题、附件大小和身份信息的要求，提交前按原邮件逐项核对。

## 本地交付核对

最终目录为 `D:\ZSvirt\LayerTrace-delivery-voiced`。文件清单、内部路径、大小与 SHA256 以实际 `release-manifest.json` 和各包内 manifest 为准，目录说明见 [交付资产索引](assets-delivery-index.md)。

| 文件 | 核对内容 |
|---|---|
| `LayerTrace-source.zip` | 完整源码、部署、测试、许可、[UI 操作指南](ui-operation-guide.md)和[视频口播稿](video-narration.md) |
| `LayerTrace-source.bundle` | 从独立脱敏快照恢复 `submission` 分支，与源码 ZIP 对应 |
| `LayerTrace-materials.zip` | 介绍 PDF、演示视频、截图、真实原始证据、环境/场景汇总和报告；`evidence/ui/` 包含详细 UI 指南、口播、精确 ID 快捷表，`evidence/ui/local-launcher/` 包含本机启动器与恢复脚本 |
| `LayerTrace-models.zip` | 固定模型资产、摘要、来源及原始许可 |
| `LayerTrace-offline-images.zip` | CPU 推理、业务、探针与控制面镜像，精确清单及原始许可 |
| `release-manifest.json` | 实际生成的包、大小、SHA256、视频时长和本地材料完整状态 |
| `SHA256SUMS` | 上述实际归档及 release manifest 的外层摘要 |

1. 执行交付工具的 `verify`，再执行 `sha256sum -c SHA256SUMS`，保存实际输出。
2. 打开 PDF 逐页检查文字、图表与证据边界；介绍文档不应包含未填写实测字段。
3. 播放完整视频，核对实际时长 180–300 秒、关键文字可读、画面不含凭据。提交主片已包含普通话配音与同步字幕，逐段检查音画同步和结尾完整。
4. 对照截图和原始记录检查 CPU、进程中断、工具 503 的 task / incident / diagnosis revision。五轮时延保留原值，报告最小值、中位数、最大值及逐轮时间质量。
5. 复现入口、模型来源、镜像导入、真实平台条件和未验证能力按 [复现报告](reproduction-report.md)、[发布证据索引](release-evidence.md) 核查。qcow2 是外部平台环境资产，未放入作品归档。

材料包 `LayerTrace-materials/evidence/ui-final/voiced-media-qa.json` 是媒体完成状态及各 manifest SHA256 的核对入口。它引用冻结的最终汇总、场景、截图来源衍生记录、PDF/视频/快捷表清单，并保存实际视觉核验结果。`evidence-summary.json` 中 `media_ready` / `media_remaining` 保留实验验收事实快照字段；媒体是否完成以独立 QA 记录为准。本机演示使用 `MODE=lab`、15秒稳定恢复窗口和3600秒基线窗口；交付默认 `MODE=prod`，实验功能默认关闭。

## 外部提交状态

| 步骤 | 状态 | 完成时的核对依据 |
|---|---|---|
| 目标代码仓库发布 | 已创建公开仓库；源码提交以独立发布记录核对 | [github.com/chennaji9/LayerTrace](https://github.com/chennaji9/LayerTrace)；核对最终bundle快照提交、源码字节与未登录访问权限 |
| 演示视频 | 本地成片，已含配音字幕；按用户要求不上传 | `D:\ZSvirt\LayerTrace-handoff\LayerTrace-demo.mp4`；如比赛要求外链，由参赛者在选定平台上传 |
| 对外链接检查 | 仅核对源码仓库 | 核对仓库与固定源码提交的匿名可访问性；材料与视频仅保存在本地 |
| 比赛邮件或指定入口提交 | 未发送 / 未提交 | 按原通知与报名确认邮件提交真实链接、附件和身份信息，保留实际回执 |

本地校验结果不代表上述外部步骤已完成。`release-manifest.json` 中 `repository_published`、`video_published`、`mail_sent` 记录本地打包时的状态，后续发布以独立记录和实际外链检查为准。仓库URL已真实创建；视频未上传，不生成视频外链。

## 提交顺序

1. 从已核验 bundle 恢复源码快照，将同一提交发布到上述GitHub仓库；保留开源许可、模型/镜像来源和能力边界。
2. 直接使用本地已配音的 `LayerTrace-demo.mp4`；本人声音替换为可选操作，步骤见 `video-editing-guide.md`。如比赛要求视频链接，参赛者自行选择平台上传。
3. 核对公开源码仓库可访问；本地打开正式视频检查声音、字幕与4分30秒完整时长。
4. 对照比赛通知核对收件信息、截止时间、身份信息、链接和附件限制。若限制附件体积，模型与镜像采用通知允许的独立分发方式，并保留来源与校验摘要。
5. 发送邮件或提交指定入口，保存真实回执。发布、上传、发送及外部账户操作按用户的明确授权执行。
