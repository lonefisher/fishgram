# GitHub 配置与接续

两个仓库保持 Public。以下名称来自当前 workflow；配置完成后仍须读回权限并执行对应验收。不要把私钥或 API hash 写进本文件、issue 或聊天。

## 受保护环境

`candidate`、`release` 和 `upgrade-automation` 均使用 Selected branches and tags，仅添加 branch `main`，不添加 tag。`candidate` 和 `release` 保留维护者人工审批；三个环境均禁止管理员绕过。新增其他受保护分支不会因此得到这些环境凭据。分支检查也适用于管理员；App 不加入主分支绕过名单。PR workflow 不绑定这些环境。

| 所在位置 | 类型与名称 | 用途 |
| --- | --- | --- |
| candidate environment | secret `FISHGRAM_API_ID`、`FISHGRAM_API_HASH` | FishGram 自有 Telegram 应用身份 |
| release environment | variable `ISSUER_KEY_ID` | 已由根签名清单授权的发行密钥 ID |
| release environment | secret `ISSUER_PRIVATE_KEY_ENCRYPTED`、`ISSUER_PASSPHRASE` | 人工审批后的发行包签名 |
| release environment | variable `RELEASE_APP_CLIENT_ID`、secret `RELEASE_APP_PRIVATE_KEY` | 人工发布的 GitHub App |
| upgrade-automation environment | variable `UPGRADE_APP_ID`、secret `UPGRADE_APP_PRIVATE_KEY` | 每日官方升级候选 App |
| repository | variable `UPGRADE_APP_ENABLED=true` | 验证升级 App 后才开启每日候选 |
| repository | variable `UPDATE_TRUST_MAINTENANCE_ENABLED=true` | 验证生产公钥信任包后才开启到期维护 |

根私钥和根口令由维护者通过 KeePass 管理，不作为 Actions secret。应用身份与密钥建立分别见[应用身份](APPLICATION-IDENTITY.md)和[密钥维护](KEY-MANAGEMENT.md)。不要把旧应用身份或一次性测试根当成生产配置。

## Windows 依赖缓存

Cloud build 与受保护产品候选共用 `deps-windows-v3-` 精确键，Actions 只缓存 runner 临时目录中的单个 `fishgram-dependencies-v3.zip`。归档工具只从 `Libraries/ThirdParty` 选取依赖，移除 Git 历史和编译/链接中间文件，保留 Qt 安装树、源码、静态库、Qt 工具及 Rust 生成绑定。上传前实际压缩并验证 ZIP 不超过 8,000,000,000 字节，为默认 10GB 仓库总额度留下余量。缓存规则、恢复和本机验证命令见[维护指南](UPDATING.md#windows-免费依赖缓存)。

可信写入入口为 main 的手动 Cloud build，或 main 手动产品候选在 `candidate` 人工审批后的依赖阶段；保存 step 位于应用 API 身份进入之前。PR 只恢复缓存并完成全部测试身份编译。两种构建都在同一 runner 上准备并继续编译，缓存写入失败时继续使用已准备依赖。需要单独准备或补充缓存时，手动 Cloud build 设置 `prepare_only=true`；`cold=true` 重新准备，`cold=false` 优先恢复，不必再编译测试身份客户端。

免费额度按所有缓存的总量计算，v2 大缓存及旧版本键可能被淘汰；不要提高付费额度或仅拆分文件来规避总量。新键首次运行后读回归档实际大小、Actions 缓存条目及下一次运行的精确命中结果。多个其他大缓存仍可能挤占额度，维护时保留当前依赖键并清理已不用的旧键。

## 两个独立 App

升级 App 以 `config/github-app-manifest.json` 为权限清单，在自己的 GitHub 账户注册，只安装到 `lonefisher/fishgram` 和 `lonefisher/tdesktop`。当前 workflow 请求 contents/issues/pull requests/workflows 写权限、checks 读权限；它只运行候选脚本。首次、重复、冲突和 CI 失败均实测后，再开启 `UPGRADE_APP_ENABLED`。`upgrade-automation` 仅允许 `main`，无人工审批，以便日常候选自动执行；它不保存发行签名密钥或发布 App 私钥。

合入已审查的工作流后，从 `main` 手动运行 Official upgrade candidates 验证真实 App；手动入口不依赖每日启用开关。定时入口仍要求仓库变量 `UPGRADE_APP_ENABLED=true`，其他事件和非 `main` 引用不执行候选 job。手动运行会创建升级候选分支、PR 或去重问题记录，仍不能合并、发布或推广客户端更新。真实首次、重复、冲突及 CI 失败证据齐全后才开启每日开关。

两个仓库均须启用 Issues；源码 fork 默认可能关闭此功能，导致冲突记录的 POST 被拒绝。首次检查读回 `has_issues`，关闭时先启用并读回。机器人从公开父 `main` 的配方及独立临时克隆创建升级父分支，不切换开发工作区；配方在查询与克隆之间变化时停止并重试。

发布 App 单独注册并生成私钥，只安装到这两个仓库。发布 workflow 请求父仓 contents 写、workflows 写和 Pages 读权限、源码仓 contents 写权限；分别生成只针对一个仓库的 token。GitHub 为包含旧版 workflow 的固定候选提交创建标签时也检查 workflows 写权限；用户于 2026-10-31 批准新增并在安装实例中接受这项权限。它的 Client ID 与私钥放在 `release` environment，不复用升级 App。客户端自动更新发布走完整 QA、签名和人工审批流程；Scoop 首版按 [已批准决定](BETA-RELEASE.md#2026-10-31-scoop-优先发行决定)发布。

私钥通过本机安全输入或 GitHub 的 secret 配置入口保存，不在命令参数或输出中打印。换机器时重新确认 App 安装范围、环境审批、分支限制和变量名称；旧私钥失效时先轮换，再撤销旧钥。

## Pages 与发行

两仓库使用 `FishGram release tag creation` 规则匹配 `refs/tags/v*-r*`，只有发布 App 可创建；独立 `FishGram immutable release tags` 无绕过者，禁止这些标签更新或删除。父仓 `FishGram channel publisher` 只允许发布 App 更新 `gh-pages`；独立 `FishGram channel retention` 无绕过者，禁止删除及非快进更新。升级 App 不在任何上述绕过名单；它也不能绕过主分支检查。不要将发布 App 加入主分支绕过名单。

这些规则于 2026-10-11 经 REST 逐项创建和读回 active。规则保护 Git refs，不能称为 GitHub Releases 附件的服务器不可变保护；现有发布器拒绝覆盖已存在标签和附件，公开发行时仍须核对实际发行行为。规则配置读回尚不代替 App 越权拒绝的实际验收。

发布器要求公开地址为 `https://lonefisher.github.io/fishgram/`，使用分支目录发布，目录为 `/` 或 `/docs`。先配置一个独立 Pages 分支并验证公开地址；不要在未验收时写入 stable/beta 更新索引。发布器会在任何 tag/release 写入前读回 Pages，缺失或地址不匹配即停止。

顺序为保护候选构建、下载同一包验收、提交匿名 QA、人工签名、离线准备推广文件、人工批准发布、公开附件读回、密钥清单读回、最后写入并读回渠道索引。具体命令和失败后接续见[发行指南](RELEASING.md)、[公开发布](PUBLISHING.md)。默认 `publish_externally=false` 只生成可审查产物。

## 配置验收

读回仅核对 secret 的名称是否存在，不能输出值。核对两个环境的审批与受保护分支、App 安装的精确仓库集合、各工作流的实际权限、Pages URL，以及 PR 无发行身份。随后验证升级候选与产品候选各自的真实运行。保存版本、提交、匿名结论；完整输出留私有区。

当前配置与实际验收结论分别记录在 [状态](STATUS.md)；secret 存在或 App 安装完成不等于候选构建、升级机器人或发行链路通过。

## 2026-10-13 升级 App 冲突与重复检查

本机使用已安装的独立升级 App，生成仅限 `fishgram`、`tdesktop` 的短期 installation token，运行当前 `tools/upstream_candidate.py` 两次。两次均退出 0 并报告冲突后停止推广；随后短期 token 已撤销。GitHub 读回官方 `v7.3.1` 的冲突记录仍只有[源码 issue #2](https://github.com/lonefisher/tdesktop/issues/2)，没有重复问题或强制覆盖。这证明当前 App 的真实认证、两仓范围检查、冲突停止与已有记录去重；首次成功创建关联 PR、非冲突重复复用及真实 CI 失败路径仍待实测，每日开关保持关闭。

2026-10-17，父 main `68bc957d7bf2c94aa4c4be2ebb2b3da02ef813e6` 上的真实工作流 [39026493635](https://github.com/lonefisher/fishgram/actions/runs/39026493635) 成功，GitHub App 认证及候选脚本实际执行。日志结果为 `Conflicts recorded; candidate promotion stopped.`；读回 `v7.3.1` 冲突问题仍只有源码 issue #2。该运行补齐已合入 main 的真实工作流执行与重复冲突去重证据；成功关联 PR、非冲突复用及 CI 失败路径继续验收。

2026-10-29，main `4c60b781fa6d455ea0045acd3bc6a30e6e9674e5` 上的真实工作流 [40809427693](https://github.com/lonefisher/fishgram/actions/runs/40809427693)和重复运行 [40809869474](https://github.com/lonefisher/fishgram/actions/runs/40809869474)均成功。App 取得仅限两个仓库的 token，脚本确认官方最新稳定版与配方均为 `v7.3.3`，返回 `No new official stable release.`，任务结束后撤销 token。没有新版本时无需准备依赖或编译客户端；新版本关联 PR、非冲突复用及 CI 失败处理仍按上面的待验项目执行。
