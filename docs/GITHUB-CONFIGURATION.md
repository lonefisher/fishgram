# GitHub 配置与接续

两个仓库保持 Public。以下名称来自当前 workflow；配置完成后仍须读回权限并执行对应验收。不要把私钥或 API hash 写进本文件、issue 或聊天。

## 受保护环境

`candidate` 和 `release` 仅允许父仓库受保护的 `main`，保留维护者人工审批。分支检查也适用于管理员；App 不加入绕过名单。PR workflow 不绑定这些环境。

| 所在位置 | 类型与名称 | 用途 |
| --- | --- | --- |
| candidate environment | secret `FISHGRAM_API_ID`、`FISHGRAM_API_HASH` | FishGram 自有 Telegram 应用身份 |
| release environment | variable `ISSUER_KEY_ID` | 已由根签名清单授权的发行密钥 ID |
| release environment | secret `ISSUER_PRIVATE_KEY_ENCRYPTED`、`ISSUER_PASSPHRASE` | 人工审批后的发行包签名 |
| release environment | variable `RELEASE_APP_CLIENT_ID`、secret `RELEASE_APP_PRIVATE_KEY` | 人工发布的 GitHub App |
| upgrade-automation environment | variable `UPGRADE_APP_ID`、secret `UPGRADE_APP_PRIVATE_KEY` | 每日官方升级候选 App |
| repository | variable `UPGRADE_APP_ENABLED=true` | 验证升级 App 后才开启每日候选 |
| repository | variable `UPDATE_TRUST_MAINTENANCE_ENABLED=true` | 验证生产公钥信任包后才开启到期维护 |

根私钥和根口令保持离线，不作为 Actions secret。应用身份与密钥建立分别见[应用身份](APPLICATION-IDENTITY.md)和[密钥维护](KEY-MANAGEMENT.md)。不要把旧应用身份或一次性测试根当成生产配置。

## 两个独立 App

升级 App 以 `config/github-app-manifest.json` 为权限清单，在自己的 GitHub 账户注册，只安装到 `lonefisher/fishgram` 和 `lonefisher/tdesktop`。当前 workflow 请求 contents/issues/pull requests/workflows 写权限、checks 读权限；它只运行候选脚本。首次、重复、冲突和 CI 失败均实测后，再开启 `UPGRADE_APP_ENABLED`。`upgrade-automation` 只允许受保护分支，以便日常候选自动执行；它不保存发行签名密钥或发布 App 私钥。

发布 App 单独注册并生成私钥，只安装到这两个仓库。当前发布 workflow 请求父仓 contents 写和 Pages 读权限、源码仓 contents 写权限；分别生成只针对一个仓库的 token。它的 Client ID 与私钥放在 `release` environment，不复用升级 App。设置 App 不代表允许发布；只有完整 QA、固定候选、签名和人工审批门槛满足后才执行外部写入。

私钥通过本机安全输入或 GitHub 的 secret 配置入口保存，不在命令参数或输出中打印。换机器时重新确认 App 安装范围、环境审批、分支限制和变量名称；旧私钥失效时先轮换，再撤销旧钥。

## Pages 与发行

发布器要求公开地址为 `https://lonefisher.github.io/fishgram/`，使用分支目录发布，目录为 `/` 或 `/docs`。先配置一个独立 Pages 分支并验证公开地址；不要在未验收时写入 stable/beta 更新索引。发布器会在任何 tag/release 写入前读回 Pages，缺失或地址不匹配即停止。

顺序为保护候选构建、下载同一包验收、提交匿名 QA、人工签名、离线准备推广文件、人工批准发布、公开附件读回、密钥清单读回、最后写入并读回渠道索引。具体命令和失败后接续见[发行指南](RELEASING.md)、[公开发布](PUBLISHING.md)。默认 `publish_externally=false` 只生成可审查产物。

## 配置验收

读回仅核对 secret 的名称是否存在，不能输出值。核对两个环境的审批与受保护分支、App 安装的精确仓库集合、各工作流的实际权限、Pages URL，以及 PR 无发行身份。随后验证升级候选与产品候选各自的真实运行。保存版本、提交、匿名结论；完整输出留私有区。

当前尚缺生产应用身份、独立离线根备份和真实 App 安装；本文是配置入口，不是这些外部条件已完成的证明。
