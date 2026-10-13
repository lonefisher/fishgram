# FishGram 更新与维护指南

所有定制维护从产品父仓库开始。源码 README 只链接本指南，不维护副本。先核对现场，再按 Comet 入口恢复对应 change。

## 开始与换机器

```powershell
git -c core.longpaths=true clone --recurse-submodules https://github.com/lonefisher/fishgram.git
cd fishgram
git status --short
git -C tdesktop status --short
git submodule status --recursive
comet workflow resolve . --activate --json
comet native status --json
```

安装配方所需 C++ 工具链及 Python；工具自动发现安装位置。需要覆盖路径时使用 `tools.local.json`，可选键为 `visualStudio`、`cmake`、`ninja`、`python`。凭据使用本机 DPAPI 设置工具保存，新机器须重新配置；不复制明文或将构建日志贴进公开问题。

Comet 正式规格与可携带状态纳入版本控制，Runtime、锁、完整输出留本机；状态更新只通过 Runtime。迁移原有 change 的验收标准不可改变。恢复不完整时先运行只读 doctor，不伪造状态。

## 新增修复与两层提交

先备份未提交、未跟踪和被忽略的测试；保存 Git bundle、工作树及索引 patch。不要 reset 或重克隆原目录。开独立临时分支及 Comet change，修改生产源码与实际生产 helper 测试。定制指令只改父 AGENTS，子模块 AGENTS 跟随官方。

```powershell
# 先审查源码，逐项添加，不全量提交本机目录。
git -C tdesktop diff --check
git -C tdesktop push origin HEAD
if ($LASTEXITCODE -ne 0) { throw 'Source push failed' }
git diff --submodule=log -- tdesktop
git add -- tdesktop
# 审查并提交本轮父仓库文件后：
git push --recurse-submodules=check
```

父仓库保存 gitlink，子模块保存源码历史，分别提交和推送。源码测试目录被上游忽略时，明确 force-add 审查后的生产 helper、搜索核心及更新测试，不加入旧的复制 helper。公开历史不包含本机维护文档分支、API 身份、账户信息、日志、截图、查询词和个人频道名。

## 官方升级

每天的机器人只创建候选。先核实官方稳定 tag，再审查源码 PR 及工具链变化。冲突停止并保留冲突文件记录，人工解决；不强制覆盖搜索、品牌或更新实现。重复检测复用既有记录。

```powershell
git -C tdesktop fetch upstream --tags
# 从 custom/main 的临时分支合入已核实的官方 tag。
git -C tdesktop submodule sync --recursive
git -C tdesktop submodule update --init --recursive
```

变更工具链须修改配方及缓存键并验证。源码 PR 先合并，再更新父指针、重新验证父 PR，最后合入。已有父仓库同步前保存两层本地修改；然后执行递归 sync/update。源码及父仓库标签对应同一构建输入，发布后不可覆盖。

## 测试、构建与候选打包

```powershell
& .\tools\test-restricted-search.ps1
& .\tools\prepare-dependencies.ps1 -Silent
& .\tools\build-telegram.ps1 -Parallel 2
```

PR 编译使用显式 `-TestIdentity`，绝不能发行该产物。产品身份只在受保护候选构建中开放。所有可能含 API 配置的编译目录、缓存和日志均为私有；CI 不上传它们。

Windows 本机构建宜使用较短的工作区与构建目录路径。`git core.longpaths=true` 只解决 Git 文件操作，不能保证 MSVC 可写入超过 260 字符的对象路径。若出现 `C1083`、编译器生成文件名为空及 `Invalid argument`，先核对实际 `/Fo` 输出路径；本机已用同一编译命令仅缩短 `/Fo` 输出的对照确认过这一原因。`CMAKE_OBJECT_PATH_MAX` 无法缩短固定的目标目录层级，不能单凭设置它判定问题解决。已有构建应先核对 `CMAKE_HOME_DIRECTORY`、对象与链接记录及程序哈希，避免混淆不同构建目录；不要删除原构建、运行目录或账户数据来尝试修复。短路径目录链接只作本机临时诊断，须核对目标且不进入公开仓库；这类本机结果不替代云端候选验收。

Windows Release 配方固定 `releaseDebugInformation=None`，客户端编译显式设置空的 `CMAKE_MSVC_DEBUG_INFORMATION_FORMAT`，避免上游默认 Embedded 的调试信息及 PDB 链接开销。构建清单记录该策略；不改变 MSVC、SDK、Qt 或 Release 优化。`tools/test-release-debug-policy.ps1` 用同一份上游 CMake 规则实际编译、链接和运行 Embedded/None 对照；它验证参数传播，不能替代完整客户端云端构建。云端运行 37821646662 的 x64 链接失败时系统峰值提交内存约 34.7 GiB，与失败后提交上限接近；系统峰值是开机以来的峰值，并非链接进程独立峰值；新策略的实际资源效果须由新运行测量确认。

更新系统的生产验签、Packer往返与Windows事务测试见[更新开发验证](UPDATE-TESTING.md)；完整密钥维护见[密钥指南](KEY-MANAGEMENT.md)，FishGram应用身份配置见[应用身份](APPLICATION-IDENTITY.md)。局部测试不替代正式安装和账户恢复验收。

换机器、App 权限、受保护环境、secret 名称与 Pages 接续见 [GitHub 配置](GITHUB-CONFIGURATION.md)。先配置并读回，再启用每日候选；正式发布仍须完整验收。

打包只允许配方列出的 exe/DLL，运行目录与打包目录必须分开。正式生产候选使用受保护的 `product-candidate.yml`，其中 `release_gate.py package-candidate` 直接验证构建记录及程序哈希，再生成全新候选目录；本机接续命令与门槛见[发行指南](RELEASING.md)。`package-telegram.ps1` 仅为内部开发打包入口，接收明确的 `-InputDirectory`、`-BuildRecord` 和 `-OutputDirectory`，要求记录的更新开关与配方完全一致，不能打包当前配方下强制开启更新的生产候选。两种入口均拒绝已有输出、未批准文件、链接、缺少的递归依赖或不匹配的构建记录。不得从包含 tdata 的运行目录重打 ZIP。对应源码发行包含所有递归依赖和必要脚本，保留 GPLv3、OpenSSL 例外及依赖许可。

## 验收与发行

搜索原八项标准覆盖范围、排序分页、生命周期、失败重试、设置持久化与真实性能；生产 helper 测试不能替代真实账号或模拟 RPC 验收。性能不达标时记录真实规模、时间及瓶颈；调整目标须单独确认。

逐项实测步骤及证据边界见[搜索验收清单](SEARCH-ACCEPTANCE.md)。原八项标准保持不变，缺样本或未执行场景继续记录为待验。

真实云端无缓存与缓存构建分别记录。人工下载同一候选包，在干净 Windows 11 和实际工作流验收；记录版本、父/源码/递归依赖提交、工具链、程序/附件 SHA256、匿名结论和限制。截图、原始日志及个人查询只留私有区。

通过完整门槛后，人工批准签名；只对已验收候选打包签名，不能重编译。附件发布后重新下载验证哈希与签名，再最后更新索引。机器人不得自动发布。自己的更新签名保证更新来源；不等同 Windows Authenticode，下载说明须明确。

## 安装、回退与数据恢复

本机账户快照与显式恢复工具见[数据恢复指南](DATA-RECOVERY.md)，独立程序回退见[程序恢复](PROGRAM-RECOVERY.md)。候选 Updater 已接入跨官方基线替换前的原生快照回调；合成快照与 Python 恢复互操作已有开发证据，真实账户迁移、客户端退出和恢复仍须独立验收。

指定安装目录和工作目录正常退出目标实例，读回程序路径确认，不按名称批量终止。退出失败停止替换。更新安装使用程序备份和事务日志，保留最近三个程序版本。跨官方基线前正常退出，建立并核实数据快照，默认保留最近两份。

只换旧 exe 不保证与已迁移数据兼容。恢复数据须明确由用户选择，并说明快照之后状态可能丢失。在线版本只能递增；问题版本停止索引推广、保留问题说明并发行新修订。手动恢复用独立工具，不伪装成自动降级。

## 密钥与紧急撤回

根密钥使用 KeePass 加密附件和 OneDrive 数据库备份保存，不强制离线副本。发行密钥只在受保护环境，按一年有效期维护，提前45天提醒。续期/吊销由根签名的递增清单授权，客户端拒绝回退清单及吊销键。密钥不可公开、打印或进入 Git。根密钥丢失/受损使用手动重新安装恢复，不声称在线能安全更换信任根。

撤回先停止索引推广，保留附件及调查记录；必要时递增清单吊销发行键。恢复发布以更大的完整版本进行。日常支持只索取版本、系统、错误类型及脱敏日志，不请求登录数据、API 凭据或私钥。

升级候选恢复策略：推送已成功而 PR 创建失败时，下次运行验证并复用远端 upgrade 分支的原提交，不重写或强推。源码候选必须包含目标官方提交及当前 custom/main；父候选配方和源码指针须完全一致，否则停止并要求人工审查。已关闭或已合并的旧候选不会自动重建；机器人建立去重处理记录，维护者先核对源码合并结果、父指针及配方，再明确决定如何恢复。候选为 open 时才查询 CI，避免关闭后的失效检查噪声。

### 固定基线历史的准备

候选打包及签名重验必须能证明 `upstreamCommit` 是记录的 `sourceCommit` 的祖先。仅有两个对象不够，浅克隆截断中间历史时门槛会拒绝；不在门槛函数中隐式联网。受保护候选、签名及发布 workflow 使用完整历史检出。接续既有浅源码工作树时，先核对 remote、源码指针及工作树状态，再按明确固定源码 SHA 执行 `git -C tdesktop fetch --unshallow --no-tags origin <sourceCommit>`，确认源码文件没有变化并重跑门槛；缺失对象或错误基线不能通过放宽门槛处理。
