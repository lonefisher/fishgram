# 更新源码候选的云端编译记录

## 2026-10-13 旧工具输入的链接失败与修正

[run 38288376077](https://github.com/lonefisher/tdesktop/actions/runs/38288376077) 的源码 head 为 `ef2e1996a9f2ba639e5cdbb099a0f4188fddcf61`，固定产品工具为 `7dbc44234dbe1a1426afa1e412a1a52ca9ca710b`。依赖准备于 2026-10-12 18:06:43–20:00:54 UTC 成功；客户端编译于 20:00:54–22:03:07 UTC 失败，耗时 2 小时 2 分 13 秒。生产 helper job 成功，编译后的更新包验证明确跳过，不计通过。

实际下载并读回 `source-build-diagnostics` 附件：编译错误代码为 `LNK1102`，Ninja 已停止，未报告 CMake 错误或缺少依赖。该附件没有链接进程或系统内存测量，不能据此断定具体内存分配原因；原始日志只留本机私有区。

Git 对象对照确认旧工具配方没有 `releaseDebugInformation=None`，配置入口也没有当前显式空的 `CMAKE_MSVC_DEBUG_INFORMATION_FORMAT` 选项。源码工作流的两处产品工具引用已在 `35c713e6d8521e08ba045743d2066fe96340a7d7` 改为已推送、已审查的固定父提交 `625165775734157474bd6149feedabddc3e93d81`。差分仅修改两个引用值，没有修改生产 C++ 源码、权限、身份或信任路径；不自动重跑已经结束的失败运行。

新 [run 38314885581](https://github.com/lonefisher/tdesktop/actions/runs/38314885581) 已启动，最终结果待核验。这是对失效工具输入的修正，尚不证明 `LNK1102` 已解决，也不证明产品候选或发行资格。父仓库原运行仍固定 `6251657`/`ef2e1996`，其结果与新源码工作流提交分开记录。

[源码 CI 37785578154](https://github.com/lonefisher/tdesktop/actions/runs/37785578154) 于 2026-10-08 17:20:44 UTC 完成，GitHub API 读回整体与两个 job 均为 `success`。本记录描述测试身份编译，不构成产品候选或发行批准。

## 固定输入与实际执行

| 输入 | 记录 |
| --- | --- |
| PR 源码分支提交 | `e222737896c041be450eaac251208d5e1eb76f8e` |
| 实际 checkout 的 PR 测试合并提交 | `18046022f71251a602848dd45c702b748621c426` |
| 两者相同的源码树 | `5341be2c0e458863ce3abd8a0cf2881c622fb50c` |
| 固定产品工具提交 | `7dbc44234dbe1a1426afa1e412a1a52ca9ca710b` |
| 工作流 | `.github/workflows/fishgram-checks.yml`，`pull_request` |
| 平台 | 标准 GitHub Actions `windows-2022` runner |
| 身份与更新信任 | 公共测试 API 身份、一次性测试信任；启用更新实现 |

工作流递归取得实际源码 checkout，并在测试工作区将父 gitlink 绑定到该提交。测试合并提交包含源码基线分支和 PR 分支；GitHub 提交 API 读回确认其树与 PR 源码提交一致。不能只凭 run 的 `head_sha` 推断实际 checkout 提交。

本次依赖准备不恢复或写入依赖缓存，也不使用产品凭据。准备步骤为 13:39:29–15:06:49 UTC，约 87 分钟；客户端编译为 15:06:49–17:19:26 UTC，约 133 分钟。耗时来自已完成步骤的实际时间戳，不是本机构建或估算。

## 实际结果与限制

完整源码 job 通过；更新验证日志读回普通配置 60/60、测试双渠道信任 63/63，生产 Packer 集成 11 项通过。生产 helper job 也通过。测试密钥和合成载荷仅用于验证实现。

成功任务没有生成诊断错误附件；artifact API 读回为空。该工作流按约定不上传程序、构建目录、原始日志或 API 配置。因此，本记录不提供该次程序下载、程序 SHA256、资源空间测量或干净 Windows 11 实测结论。父仓库流程另行记录资源测量和后续检查。

本记录不能替代受保护环境使用 FishGram 自有 API 和正式更新信任构建的产品候选。当前本机后续清单改动、缓存复测、真实搜索完整验收、签名安装、恢复及首发链路仍需各自证据。对应 Comet 未完成项不因本次源码编译成功而改为通过。

## 2026-10-14 搜索修复源码 CI 终态

REST 读回 run 38371777173 为 completed/success，head 为 7b355486ade41e14858ac3027c14a43719987a30。Production helper tests job 成功；Source test identity compile job 于 2026-10-13 08:48:15–12:37:05 UTC 成功。这是整个 job 时间，不是编译净耗时。测试身份成功不证明实际产品、搜索完整验收或发行链路完成。
