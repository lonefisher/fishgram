# 父仓库更新候选的云端构建记录

[父仓库 CI 37785610848](https://github.com/lonefisher/fishgram/actions/runs/37785610848) 于 2026-10-08 完成并返回失败。依赖准备成功，客户端构建阶段返回 `LNK1102`，后续更新与恢复 helper 检查跳过。本次不能记为完整构建通过。

| 固定输入 | 实际记录 |
| --- | --- |
| PR 分支提交 | `7ba2169b5d95e2c8406d919e87ed82aef8cd6eeb` |
| 实际 checkout | `8b77f624d7f3eb722644cf427cf3a61b57349ab7` |
| 两者相同的父仓库树 | `27270c1521e2960583fadec5387637ad6f67c5c9` |
| 源码 gitlink | `e222737896c041be450eaac251208d5e1eb76f8e` |
| 平台及身份 | 标准 `windows-2022`；公共测试 API 和一次性更新信任 |

实际日志包含上述 checkout，提交 API 核对父树一致，并从该树核对源码 gitlink。源码树与[独立成功的源码 CI](CLOUD-SOURCE-BUILD.md)一致；两个任务的构建结论分别保留。

依赖缓存未命中，准备步骤 13:40:18–15:36:35 UTC，约 116 分钟。客户端构建为 15:36:41–17:43:56 UTC，约 127 分钟，返回失败。更新 helper 未执行，没有把跳过项算作通过。

公开附件仅含脱敏诊断和资源测量。编译后的可用磁盘为 82,854,162,432 字节，构建目录约 35 GB，runner 为 4 逻辑处理器。诊断只保留 `LNK1102`，未记录该次内存提交量或实际链接器宿主架构；这些数据不能确认物理内存、分页文件或宿主地址空间中的具体原因。

后续云端 change 补充数值内存指标、选定链接器 PE 架构，以及固定词表内的失败程序名。系统提交峰值是本次系统启动以来的峰值，可能高于后来缩小的当前提交上限，不能直接当作构建进程的峰值。API 字段语义见 [Microsoft PERFORMANCE_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-performance_information)。新诊断的本机测试通过不代表本次云端失败已修复；下一次云端结果须按自己的固定提交另行验证。

没有程序下载、该次程序 SHA256 或 Windows 11 实测。本次不含产品身份或正式信任，不构成产品候选、主分支合入或发行批准。未更换工具链、启用付费 runner 或变更本机分页配置。

## 2026-10-11 Release 调试策略后的完整构建

[运行 38145620283](https://github.com/lonefisher/fishgram/actions/runs/38145620283) 整体 success。父分支固定 `825a33f12f65e461bbab394fe2cd1e8f5e33761e`，实际 checkout `35404c7ea4d5ae380be2e86d36ee13b81b65eb4b`；REST 读回两者父树均为 `5401cc20c875abe37e445f288f944cf357b0656d`，源码 gitlink `e222737896c041be450eaac251208d5e1eb76f8e`。

可信依赖缓存未命中，客户端编译于 15:19:52–16:39:59 UTC 完成，约 80 分钟。生产版本/路径、Windows 事务、便携事务、重启权限和原生快照检查成功；Python 数据恢复 36/36、生产 Packer 往返 11/11、临时信任验签 60/60 与 63/63 均通过，无测试跳过。

结束后可用磁盘 195,486,994,432 bytes，系统启动以来峰值提交内存 24,258,383,872 bytes，结束时提交上限 32,434,282,496 bytes，链接器宿主 x64。该峰值属于整个系统，不能当作单个链接进程峰值。此次固定 None 调试策略成功经过完整客户端链接；旧 LNK1102 失败记录继续保留。

本次使用公共测试身份和一次性信任，仅上传脱敏资源测量。没有上传程序，不是生产产品候选；不能替代本地后续提交的云端结果、受保护自有 API 构建、Windows 11 实测、签名或公开发行。

## 2026-10-11 d8bad415 的独立云端证据（2026-10-12 核对）

[运行 38156803064](https://github.com/lonefisher/fishgram/actions/runs/38156803064) 为 `pull_request` 触发的第 1 次尝试，REST 读回 `completed / success`。本节仅记录该运行：依据本机私有日志和五份资源测量，并用 `gh api` 核对运行、jobs/steps、Git commit/tree 和附件元数据；原始日志不纳入文档。上节运行 38145620283 的通过结果不计入本次，也不证明后续提交通过。

| 固定输入 | 本次核对结果 |
| --- | --- |
| 运行 `head_sha` / PR 父提交 | `d8bad415c36d8cb5916f11d4a162d0e34daa264a` |
| 两个 job 实际 checkout | `83c5bb6a827eed53b91183b68b03885b758733bf`（PR 合并提交） |
| checkout 的两个父提交 | `496910e2254f49ce51369b61f740044e7715b068`、`d8bad415c36d8cb5916f11d4a162d0e34daa264a` |
| PR 父提交及实际 checkout 的相同树 | `8e330bd63b579500eb7c1316282a2efca29a3317` |
| 上述树的 `tdesktop` gitlink | `e222737896c041be450eaac251208d5e1eb76f8e`（mode `160000`）；两个 job 的日志均确认实际 checkout 此源码提交 |
| 平台及构建参数 | 标准 `windows-2022`，4 逻辑处理器；`-TestIdentity -TestUpdateSystem -Parallel 4` |

这里绑定的是实际 checkout 的树，不能将运行的 `head_sha` 直接当作实际 checkout。REST commit/tree 与日志交叉核对后，才将本次结果归于上述父树和源码输入。

缓存键为 `deps-windows2022-x64-0323f34876bbc474821bccd85e6238a52210ad94877f356688f4d7bd00fa9ce6`，编译 job 日志明确未命中，随后依赖准备成功；本次没有缓存命中构建或缓存写入证据。`Prepare trusted dependencies` job 虽为 success，但 PR 路径只计算键：恢复可信缓存、两次资源测量、无应用身份依赖准备、缓存保存及资源附件上传共六步均为 skipped，不能记为执行成功。编译 job 的 `Recreate runner-local Python after cache restore` 也因缓存未命中而 skipped。

| 阶段（2026-10-11 UTC；按 REST 时间） | 起止 | 耗时及结果 |
| --- | --- | --- |
| 整体运行（开始至最终更新时间） | 16:45:43–20:35:12 | 3 小时 49 分 29 秒；success |
| 编译 job | 16:47:35–20:35:11 | 3 小时 47 分 36 秒；success |
| 缓存未命中后的依赖准备 | 16:48:43–18:42:43 | 1 小时 54 分；success |
| 不可发行测试身份客户端构建 | 18:42:49–20:33:43 | 1 小时 50 分 54 秒；success，包含配置及 `Telegram`、`Updater` 构建 |
| 生产更新与恢复 helper 测试 | 20:33:43–20:34:51 | 1 分 8 秒；success |

| 本次测试 | 已验证结果 |
| --- | --- |
| 生产更新版本及 payload 路径策略 | 日志明确通过 |
| Windows 更新事务、便携更新事务 | 日志明确通过；事务测试记录 `elevated=1` |
| 更新客户端重启权限、合成原生数据快照、Windows payload 验证 | 日志明确通过 |
| Python 数据恢复及原生快照互操作 | 37/37，3.672 秒，`OK`，无测试跳过 |
| 生产 v2 验签及配置的一次性信任 | 分别 60/60、63/63 通过 |
| 生产 Packer 签名包往返 | 11/11，3.602 秒，`OK`，无测试跳过 |
| 失败分类、Windows 应用身份、生产 feed helper | 本次固定父树的测试脚本逐项调用并检查退出码；整个测试步骤成功，日志没有独立计数，不另造测试数量 |

上述 helper 测试步骤未跳过；日志中的 CMake 编译器检查 `skipped` 属于配置探测，不是测试跳过。本次工作流没有运行完整搜索验收、产品候选打包/发布验收或真实账户迁移验收，不将它们记为通过。

| 资源（bytes，资源测量采样值） | 编译前 18:42:45 UTC | helper 测试后 20:34:52 UTC |
| --- | ---: | ---: |
| 可用磁盘 | 135,118,315,520 | 114,013,904,896 |
| 物理内存总量 | 17,174,360,064 | 17,174,360,064 |
| 可用物理内存 | 14,538,547,200 | 14,740,480,000 |
| 系统当前提交内存 | 3,010,732,032 | 2,773,553,152 |
| 系统提交上限 | 20,261,367,808 | 23,513,911,296 |
| 系统启动以来峰值提交内存 | 5,625,851,904 | 21,748,723,712 |

结束时 `Libraries` 为 17,676,594,900 bytes，`ThirdParty` 为 1,237,512,805 bytes，`build-modified` 为 17,773,239,863 bytes。构建开始及完成的诊断均确认链接器宿主 `x64`；另一次资源诊断未提供架构，不能将其 `null` 当作架构变化。峰值属于整个系统启动以来的提交量，不是客户端或链接进程的内存峰值，也不能由这两个采样推定分页文件的具体调整原因。

REST 附件列表仅有 [compile-measurements](https://github.com/lonefisher/fishgram/actions/runs/38156803064/artifacts/11714497255)（1,929 bytes）。日志确认上传五份数值资源/内存诊断 JSON，与本机测量文件吻合；未上传程序或原始编译日志。本次使用公共测试 API 身份及一次性更新信任，构建成功仅证明固定输入的测试身份云端构建和上述 helper 检查通过，**不是产品候选**。没有本次程序下载与 SHA256、受保护自有 API 构建、Windows 11 实机验证、正式签名或发行批准。

## 2026-10-12 当前 PR 输入的无缓存结果

[run 38236613947](https://github.com/lonefisher/fishgram/actions/runs/38236613947) 已成功。PR head 为 `cfacb8cef84d53338170c4faf8536216ef9df3a7`，实际 checkout 为合并提交 `45a035f276668c8d244bfc30502a512aca32c8f8`；GitHub commit API 验证两者 tree 均为 `2c45be3db07b0c7388e0f987fdfaaa0850193ab1`。源码仍固定 `e222737896c041be450eaac251208d5e1eb76f8e`。

精确依赖缓存未命中。实际准备 10:32:49–12:26:40 UTC，测试身份编译 12:26:45–14:16:25 UTC。生产 helper 步骤成功：数据恢复 37 项、Packer 包往返 11 项无跳过；Windows payload 验证通过，原生验签与一次性信任分别 60/60、63/63。结束采样可用磁盘 113,366,028,288 bytes，系统启动以来峰值提交内存 21,784,821,760 bytes；链接器宿主 x64。资源 artifact 只包含数值 JSON。

这些证据仅属于上述固定树和不可发行测试身份。之后的发布审查修复、受保护产品候选、程序哈希、真实账号及 Windows Sandbox 验收均不由此证明。

## 2026-10-13 父 PR625 的完整无缓存结果

run 38300802404 已整体成功。PR head 625165775734157474bd6149feedabddc3e93d81；原始 checkout 日志为 f65d44dc8967c1f182649eb7c007b4e0682fb244，提交 API 读回两者 tree 均为 fcdcec5800a702d699c428dbbd3d35e34e48f6dc。源码固定 ef2e1996a9f2ba639e5cdbb099a0f4188fddcf61。

依赖缓存未命中，实际准备 19:59:42–21:45:30 UTC；测试身份编译 21:45:34–23:28:27 UTC（102 分 53 秒）。更新及恢复 helper 步骤成功，日志确认 Windows 事务 elevated=1、数据恢复 37 项和 Packer 往返 11 项均为 OK，无跳过；v2 验签 60/60、一次性信任 63/63。实际编译与 helper 均成功，不将依赖 Git 下载中的同数字计数当成测试。

脱敏附件 compile-measurements（11779760539，1922 bytes）含五份数值 JSON。结束采样磁盘可用 114837569536 bytes，系统启动以来峰值提交内存 21762220032 bytes，提交上限 22684606464 bytes；构建结束诊断链接器宿主 x64。峰值属于整个系统，不是编译或链接进程独立峰值。

原日志仅留本机私有区，未上传程序。本次为公共测试身份，不是生产产品候选；不证明之后本地提交、新源码 CI、缓存构建、真实搜索或安装恢复通过。

## 2026-10-14 既有完整构建终态

REST 读回 run 38380825820 为 completed/success，head 为 edf4d759e0a4fb89cc1cc35b50ae9d9fc1b4fe87。依赖 job 于 2026-10-13 10:04:19–10:06:14 UTC 成功；Test identity compile job 于 10:06:16–13:08:28 UTC 成功。以上是 job 时间，不能当作编译步骤净耗时或缓存命中证据。仅证明该固定输入的测试身份 CI 成功，不覆盖后续修复或产品候选。

## 2026-10-15 固定 9321091 的完整无缓存结果

run 38582455230 整体 completed/success。head 为 9321091a994ca3f37c86cb5545f3d017cfe9c7c7，源码固定 77cfa9e7ccce95fbc04e1c7be917d67893af1d67；实际 PR merge checkout dae375a 与 head 的 tree 均为 8f30aa4c2ecbb0510c79df457b2da6a1977d31a6。依赖 cache 明确未命中。Test identity compile job 为 2026-10-14 14:41:55–18:33:50 UTC，此为整个 job 时间，不是编译净耗时。

日志实际读回恢复测试 38 项、Packer 11 项及验签 60/60、63/63 成功。数值附件显示 4 逻辑核、物理内存 17174360064 bytes；结束可用磁盘 113367715840 bytes，build-modified 17772951704 bytes。系统 peakCommit 21765869568、commitLimit 24150757376 bytes，结束链接器宿主 x64；系统峰值不能算单个编译进程峰值。

该运行只有不可发行测试身份，没有产品程序下载或同候选实机结论。随后新增发行续跑、程序恢复、QA 与密钥策略审查修复仍需自己的验证，不套用本次成功。

## 2026-10-16 读回 3ab3ff1 完整构建

run 38726743268 completed/success，head 3ab3ff1c444c80cec73e8a0eedd5338db4e45916，源码10df321519820764578dc42eca40ff1e6bed0dc5。实际PR合并checkout 6bd3abb311aed39f440c15828f694728b6c4ef1b与head的tree均为288ba055ae43ca2aeea74d3a2e0ff0c6045df1d8，经提交API确认。compile job 2026-10-15 11:22:53–15:12:50 UTC，包括依赖准备，不是纯编译时间。依赖cache明确未命中。

实际日志读回恢复38项、Packer11项、验签60/60与63/63成功。数值附件11984192406（compile-measurements，1929bytes）包含5份JSON：4逻辑核，物理内存17174360064bytes；结束磁盘可用113602764800bytes，build-modified17773105653bytes。系统peakCommit21832478720、commitLimit23915298816bytes，链接器宿主x64。峰值属于整个系统，不是单个编译或链接进程。

本次是不可发行测试身份，不上传客户端；不能提供该次程序SHA256、产品候选或Windows11实测结论。后续abce384进程退出修复及新增审查修复均需自己的检查。缓存复测、真实产品、搜索完整验收、签名更新与恢复和首发门槛继续保留。
# 2026-10-17: completed abce384 build

[Run 38891184021](https://github.com/lonefisher/fishgram/actions/runs/38891184021) completed successfully for parent `abce3841b73848664cc260510bad1df023f6408a` and source `10df321519820764578dc42eca40ff1e6bed0dc5`. The compile job ran from 2026-10-16 11:04:41 to 14:54:14 UTC, including dependency preparation. Its exact dependency cache was absent. Logs confirm 38 recovery tests, 11 Packer tests and 60/60 plus 63/63 verification checks. System peak committed memory was 21,737,754,624 bytes, not a per-process peak.

This was a non-releasable test identity build without client uploads. It does not establish product-candidate or Windows 11 acceptance, nor cloud success for later review fixes. Run 38920034465 for `03ddecb` remains separate.

## 2026-10-17 父 PR 最终构建

[运行 39016888761](https://github.com/lonefisher/fishgram/actions/runs/39016888761) 成功，父 head `4a5c33fefa4b87809adaafb66569d427684fbaa6`，源码 `3b392e8bb201461d5c5d0729d42896891d909404`。依赖缓存明确未命中；客户端编译步骤09:28:17–10:43:36 UTC，约75分钟。随后恢复68项、Packer11项、验签60/60和63/63通过，Windows事务、重启权限及原生账户快照测试均成功。

资源附件读回：4逻辑核，物理内存17,174,360,064 bytes；编译前后可用磁盘213,337,833,472 / 195,486,273,536 bytes；客户端构建目录17,773,105,663 bytes。系统累计峰值提交内存22,683,623,424 bytes，结束提交上限32,263,475,200 bytes，链接器宿主x64。峰值为系统累计统计。

父 PR1 全部必需检查通过后正常合入 main `68bc957d7bf2c94aa4c4be2ebb2b3da02ef813e6`。受保护产品候选运行39026170297和main冷构建39026221191使用该main提交，结果分别跟踪。
