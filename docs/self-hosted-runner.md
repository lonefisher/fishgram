# 自托管 Windows 构建机

`product-candidate.yml` 在合格构建上使用这台电脑，让 `Libraries`、`ThirdParty` 和 `build-modified` 留在本地，后面的编译可以增量进行。不探测电脑是否在线，也不使用 token。电脑关机时作业一直排队。

## 用哪台机器

标签是 `self-hosted`、`Windows`、`X64`、`fishgram-builder`。`lonefisher/tdesktop` 的源码检查使用同一组标签和同一条选择规则，但那是另一个仓库级 runner，目录分开。

| 事件 | Runner |
| --- | --- |
| 本仓库的 `push`，以及未勾选的 `workflow_dispatch` | 构建机。离线就排队 |
| `workflow_dispatch` 勾选 `use_github_hosted` | `windows-2022` |
| head 仓库不是本仓库（fork 的 pull request） | `windows-2022` |

这个 workflow 没有 `pull_request` 触发，job 也只在 `refs/heads/main` 上跑。fork 的 pull request 不会进入这里。`runs-on` 仍把 head 仓库不同的事件送到 `windows-2022`，避免以后加上 pull request 触发时落到构建机。

`pull_request` 使用的是 PR 头上的 workflow 文件。fork 可以改 `runs-on`，本文件里的表达式约束不了那份被改过的文件。请到仓库 Settings → Actions → General，把 “Fork pull request workflows from outside collaborators” 设为 **Require approval for all outside collaborators**。不要批准把作业指到 `fishgram-builder` 的外部 workflow 改动。`cloud-build.yml` 仍固定 `windows-2022`，不使用构建机。

电脑关机时作业保持排队。GitHub 会让排队超过 24 小时的作业失败。重新运行即可。若要立刻在 GitHub 托管 runner 上构建，手动运行并勾选 `use_github_hosted`。

产物与现在相同：不构建 Updater，版本来自 `fishgram.json`，只上传内部候选 artifact。不发布，不改 Scoop，不改分支保护。

## 目录

每个仓库一个 runner，不要共用工作目录，也不要加 `--ephemeral`。

| 用途 | fishgram | tdesktop |
| --- | --- | --- |
| runner 程序 | `C:\fishgram-ci\fishgram\runner` | `C:\fishgram-ci\tdesktop\runner` |
| `--work`，checkout 会清空 | `C:\fishgram-ci\fishgram\work` | `C:\fishgram-ci\tdesktop\work` |
| 持久的依赖和编译树 | `C:\fishgram-ci\fishgram\persistent` | `C:\fishgram-ci\tdesktop\persistent` |

持久目录必须和 `--work` 在同一个盘（现在是 `C:`），这样移动是重命名，不是复制。它在 `--work` 外面，`actions/checkout` 的 `git clean` 和 runner 的 `_temp` 都碰不到它。

每次自托管作业开始前，若上次运行没能把树移回去，先把工作区里的 `Libraries`、`ThirdParty`、`build-modified` 移进持久目录，再让 checkout 清空工作区。凭据检查通过之后再移回来。作业结束（包括失败）后再移回持久目录。GitHub 托管的 `windows-2022` 不走这几步，依赖缓存的键、体积和保存条件保持原样，自托管运行不读也不写那份缓存。

第一次在构建机上构建会完整准备依赖。之后 `prepare.py` 按自己的 cache key 跳过未变的阶段，Ninja 复用 `build-modified`。不要改 `--work` 的路径，CMake 缓存里是绝对路径。`build-modified` 留在这台电脑上，不上传；其中的 CMake 缓存可能含有这次构建写入的 API 配置。

## 注册

用管理员 PowerShell。注册 token 从仓库 Settings → Actions → Runners → New self-hosted runner 复制，大约一小时过期，不要写进仓库。

1. 按上面的表创建三个目录。
2. 打开该页面，下载它给出的 Windows x64 runner 包，解压到 `C:\fishgram-ci\fishgram\runner`。
3. 在该目录执行（把 `TOKEN` 换成页面上的 token）：

```bat
.\config.cmd --url https://github.com/lonefisher/fishgram --token TOKEN --name fishgram --labels self-hosted,Windows,X64,fishgram-builder --work C:\fishgram-ci\fishgram\work --runasservice --unattended
```

4. 服务默认用 `NETWORK SERVICE`。Build Tools 在 `Program Files` 里，这个账户可以读。把 `C:\fishgram-ci\fishgram` 的修改权限给运行服务的账户，或者在服务里把 `actions.runner.lonefisher-fishgram.fishgram` 的登录账户改成这台电脑的用户。
5. `tdesktop` 再做一遍：URL 换成 `https://github.com/lonefisher/tdesktop`，`--name tdesktop`，目录换成 `C:\fishgram-ci\tdesktop\...`。标签相同。两个 runner 只各自监听自己的仓库。

系统 Git 打开长路径：`git config --system core.longpaths true`。

## Visual Studio

沿用已经装好的 Visual Studio 2022 Build Tools 17.14，路径 `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`。需要这些组件：

- MSVC v143，工具集 **14.44**（本机是 14.44.35207）。`fishgram.json` 的 `msvc` 是 `14.44`，与 [tdesktop 的 Windows 构建说明](https://github.com/telegramdesktop/tdesktop/blob/dev/docs/building-win.md) 里的 `-vcvars_ver=14.44` 一致
- Windows 11 SDK **10.0.26100.0**
- 用于 CMake 的 C++ 组件；CMake 和 Ninja 也可以在 PATH 上

以后如果再装 Visual Studio 2026，构建不会改用“最新的 VS”。`tools/common.ps1` 用 `vswhere` 列出全部带 C++ 工具的安装，选用里面存在 `VC\Tools\MSVC\14.44.*` 的那一个，再用 `vcvars64.bat 10.0.26100.0 -vcvars_ver=14.44`。没有 14.44 的较新 VS 会被跳过。打包脚本走同一个 `Get-FishGramToolchain`。

## 校验

1. 服务在运行：`Get-Service actions.runner.*`。GitHub 的 Runners 页面显示 Idle。
2. 工具集还在：

```bat
"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
```

列出的某个安装下要有 `VC\Tools\MSVC\14.44.*`。

3. 在 `main` 上手动运行 FishGram protected product candidate，不要勾选 `use_github_hosted`。电脑开着时作业应落到这台机器；关着时作业停在排队，而不是马上改去 `windows-2022`。
4. 再运行一次并勾选 `use_github_hosted`。这次应在 `windows-2022` 上执行，依赖缓存步骤与原来相同。
5. 一次自托管构建结束后，`C:\fishgram-ci\fishgram\persistent` 里应有 `Libraries`、`ThirdParty` 和 `build-modified`。下一次未改源码的构建应跳过已准备好的依赖阶段，并增量编译。
