# FishGram

基于 Telegram Desktop 的非官方客户端定制。首版支持 **Windows 11 x64 便携版**。

当前仍为内部候选，尚无正式公开安装包。现有搜索功能仍有待验项目；云端候选、签名更新和恢复链路通过后才首次发行。

本仓库负责配方、构建工具、维护指南、规格、CI 和发行记录；[`lonefisher/tdesktop`](https://github.com/lonefisher/tdesktop) 保存源码，并通过 `tdesktop` 子模块固定具体提交。

```powershell
git -c core.longpaths=true clone --recurse-submodules https://github.com/lonefisher/fishgram.git
cd fishgram
```

开始开发前阅读 [AGENTS.md](AGENTS.md)、[维护指南](docs/UPDATING.md)、[架构](docs/ARCHITECTURE.md) 和 [验收状态](docs/STATUS.md)。工具链版本固定在 `fishgram.json`，本机路径覆盖可放在被忽略的 `tools.local.json`。

源码沿用 GPLv3 与 OpenSSL 例外；见子模块的 [LICENSE](tdesktop/LICENSE)、[LEGAL](tdesktop/LEGAL) 和依赖许可。FishGram 基于 Telegram API，与 Telegram 官方无隶属关系，保留官方基本功能及广告支持。首版不新增遥测或向官方发送 FishGram 崩溃报告。

自己的更新包签名用于客户端验证更新来源；Windows Authenticode 是另一种用途的签名。免费优先阶段暂不采购 Authenticode 证书。
