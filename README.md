# FishGram

基于 Telegram Desktop 的非官方客户端定制。首版支持 **Windows 11 x64 便携版**。

[下载 7.3.3-r11 Beta](https://github.com/lonefisher/fishgram/releases/tag/v7.3.3-r11-beta)，或通过 Scoop 安装：

```powershell
scoop bucket add lonefisher https://github.com/lonefisher/scoop-bucket
scoop install lonefisher/fishgram-beta
```

Scoop 保留 `FishGramData` 中的账号与设置，使用 `scoop update fishgram-beta` 更新。便携版解压后运行 `Start-FishGram.cmd`。首版按用户要求先行发布；大范围搜索性能、原生遗漏对照样本、完整搜索范围、恢复后重试和自动更新安装恢复仍有待验项目，见 [Beta 说明](docs/BETA-RELEASE.md)。

本仓库负责配方、构建工具、维护指南、规格、CI 和发行记录；[`lonefisher/tdesktop`](https://github.com/lonefisher/tdesktop) 保存源码，并通过 `tdesktop` 子模块固定具体提交。

```powershell
git -c core.longpaths=true clone --recurse-submodules https://github.com/lonefisher/fishgram.git
cd fishgram
```

开始开发前阅读 [AGENTS.md](AGENTS.md)、[维护指南](docs/UPDATING.md)、[架构](docs/ARCHITECTURE.md) 和 [验收状态](docs/STATUS.md)。工具链版本固定在 `fishgram.json`，本机路径覆盖可放在被忽略的 `tools.local.json`。

源码沿用 GPLv3 与 OpenSSL 例外；见子模块的 [LICENSE](tdesktop/LICENSE)、[LEGAL](tdesktop/LEGAL) 和依赖许可。FishGram 基于 Telegram API，与 Telegram 官方无隶属关系，保留官方基本功能及广告支持。首版不新增遥测或向官方发送 FishGram 崩溃报告。

自己的更新包签名用于客户端验证更新来源；Windows Authenticode 是另一种用途的签名。免费优先阶段暂不采购 Authenticode 证书。
