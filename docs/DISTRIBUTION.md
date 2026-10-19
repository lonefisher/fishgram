# 下载与 Scoop

## 用户下载

Release 顶部说明首先链接 Windows 11 x64 便携 ZIP。解压后运行 `Start-FishGram.cmd`，账户与设置保存在同目录的 `FishGramData`。

便携 ZIP 包含 `Telegram.exe`、`Updater.exe`、启动脚本、许可和简短说明、构建清单。Packer、源码、验收记录和构建工具不放进用户程序包。正式包装可以调整文件名和说明，两个程序文件必须与已验收云端候选的 SHA256 一致；新 ZIP 另行计算哈希并实测解压启动。

对应源码 ZIP 单独作为同一 Release 的附件，包含递归依赖、必要构建脚本和许可。普通用户只需下载便携 ZIP；源码归档服务于复现和 GPLv3 对应源码分发。GitHub 自动生成的父仓库 Source code ZIP 不包含子模块内容，不能替代对应源码归档。

签名更新包用于客户端更新；校验值和构建清单用于核验。Release 说明区分“下载运行”“源码”“更新包与校验”，不要求用户下载整个 Actions Artifact。

## Scoop

目标仓库为 `lonefisher/scoop-bucket`，主分支 `master`，清单路径 `bucket/fishgram.json`。正式 Release 公开下载读回成功后提交清单，下载 URL 指向 Release 的便携 ZIP，SHA256 来自该公开附件。版本使用完整 `7.3.1-r9`，检查新版本时保留修订号。

清单仅支持 `64bit`，创建 FishGram 快捷方式和 `fishgram` 命令，`persist` 使用 `FishGramData`。安装及升级时创建该目录；升级前要求目标实例正常退出，随后验证持久化目录和登录状态保留。

Scoop 版由 `scoop update fishgram` 管理版本。客户端已有 `externalupdater.d` 机制：在程序目录内写入文件，内容为当前版本实际解析后的 `Telegram.exe` 绝对路径，即可禁用内置更新。每次 Scoop 安装和升级都重新生成该路径，不持久化旧版本的标记。`-noupdate` 只抑制启动时安装，不能单独用来禁用整个自动更新功能。实测须确认快捷方式、命令启动都读取正确数据目录，且设置中内置更新已禁用。

首次发布后执行：

```powershell
scoop bucket add lonefisher https://github.com/lonefisher/scoop-bucket
scoop install lonefisher/fishgram
```

后续更新顺序：发布并核验 FishGram Release → 更新清单版本、URL 和 SHA256 → 验证首次安装及升级 → 提交并推送 bucket。账户数据只留在本机，卸载时保留 Scoop persist 数据，用户明确要求清除时才另行处理。
