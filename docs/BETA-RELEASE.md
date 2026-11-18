# 公开发行

## 当前规则

2026-11-18 起，`publish-beta-attachments.yml` 发布普通 GitHub Release。标签是 `v` 加候选版本，例如 `v7.4.4-r14`，不附加 `-beta`。标题是 `FishGram <version>`。`prerelease` 为 false，并设为 Latest。Scoop 清单名是 `fishgram`，更新命令是 `scoop update fishgram`。

该入口仍只上传同一候选的便携 ZIP、对应源码、候选记录、分发记录和 SHA256。不签名，不改 Pages，不推广客户端自动更新。`autoUpdate` 保持 false，程序包不含 `Updater.exe`。已发布的 r11–r13 prerelease 及其附件不修改、不覆盖。

候选记录里的 `channel` 必须与构建该候选时父提交上的配方一致。r14 候选仍是当时的 `beta` channel。这个字段不再决定 GitHub 标签后缀或 prerelease 标志。

## 2026-10-31 Scoop 优先发行决定

下面是当时的决定，不是当前规则。用户当时要求跳过剩余测试先发布。首版使用已成功构建的 `7.3.3-r11` 产品候选，发布为 GitHub prerelease，并提供当时的 `fishgram-beta` 清单。这些历史 release 保持原样。

本次发行公开列出尚未完成的完整搜索范围覆盖、网络恢复后的整轮重试、更新安装及恢复矩阵，以及下文的性能和原生遗漏对照样本。原验收记录保留实际结论，后续按用户反馈继续修复。

程序 ZIP、对应源码、构建记录和 SHA256 同时发布。Scoop 安装写入 `externalupdater.d`，持久化 `FishGramData`。当时的更新命令是 `scoop update fishgram-beta`；当前命令是 `scoop update fishgram`。便携用户手动下载更新。客户端自动更新通道以后再推广，沿用签名和完整恢复验收流程。当时可直接发布这些下载附件，无需等待原完整签名发布工作流。

## 原验收范围

2026-10-19 用户明确允许首版先以 Beta 发布，并公开以下搜索限制：

- 大范围受限会话搜索尚未达到原先约 2 秒首屏的性能目标。既有约 99 个来源的实测需约 35–37 秒；性能优化继续保留在原搜索 change。
- 暂无“原生全局搜索遗漏、会话内搜索可找到”的明确消息和媒体对照样本，此项保持待验。

这两项作为 Beta 已知限制，不能改记为通过。其余搜索验收继续执行。签名、更新安全、安装、恢复、本机同一云端候选及公开下载核验仍须完成。

## 渠道

下面记录 2026-10-19 的当时规则。2026-11-18 起，附件发布改为普通 Release，见上文当前规则。当时要求 GitHub Release 标为 prerelease，更新索引只推广 `beta.json`。稳定索引不推广这个 Beta。

当时的 Qt6 配方构建 Beta channel。程序、Updater 和签名包使用同一 channel。客户端也支持稳定签名包进入 Beta 索引；早期候选 39128598025 保留为 Qt5 历史实测。正式发行使用完成验收的同一新版云端候选，签名 channel 保持其实际值。Beta 转稳定版时分配新修订号。

当时的 Release 说明和便携 ZIP README 写明 Beta 及上述两项限制。下载入口优先指向便携 ZIP，源码单独提供。公开 QA 使用明确的 Beta 验收类型，列出这两项暂缓；其他检查仍须通过。该类型不能进入稳定索引。

Scoop 首版使用当时的 `fishgram-beta` 清单。当前清单名是 `fishgram`。新的普通 Release 设为 Latest，因此可以用 `/releases/latest`。历史 prerelease 仍被该入口排除，因为它们自己标成了 prerelease。

## 2026-11-06 受保护云端附件发布入口

本次增加 `publish-beta-attachments.yml`，手动从 `main` 运行并通过原 `release` 环境人工审批。输入成功的产品候选 run ID；默认 `publish_externally=false` 只生成可审查附件。核验直接读取 GitHub run、job、artifact 元数据并比对下载的外层 ZIP SHA256，再核验候选父提交、源码、递归依赖、程序与许可。不能用本地编造的成功记录代替来源核验。

该入口只发布同一候选的确定性便携 ZIP、对应源码、候选/分发记录及 SHA256。2026-11-18 起它创建普通 Release：标签不带 `-beta`，`prerelease` 为 false，并设为 Latest。标签仍指向候选父提交及源码提交，而非后来的工作流提交。既有 Release（含草稿、部分上传，以及 r11–r13 prerelease）一律拒绝，不修补、删除或覆盖；既有标签只允许核实目标完全一致后复用。中途失败需人工核对报告中的可能已创建对象。

发行说明与 ZIP README 披露 Windows 程序未做 Authenticode 签名、本候选尚未完成 Windows 界面及真实账号测试，以及延期的搜索、更新安装及恢复测试。上述待验项目不能记为通过。每个公开附件匿名重新下载并核对哈希后，才生成供后续 Scoop 更新使用的公开程序 URL/哈希结果。Scoop bucket 由独立步骤更新，安装/数据保留约定不变。

本入口不读取发行签名私钥、不修改 Pages 或 stable/beta 更新索引，不推广客户端内置自动更新。便携用户手动更新，Scoop 用户通过 `scoop update fishgram` 更新。原完整签名、真实验收和客户端更新通道流程保持不变；上文历史渠道/QA 要求适用于该完整流程，不代表本附件入口已完成它们。
