---
generated_from_state_version: 10
---

# 验证

## 当前结果

- 结果: **已阻塞**
- 验证情况: **解决报告中的阻塞项后恢复验证**
- 目标周期: 1
- 迭代: 2
- 验证器尝试次数: 1
- 完成时间: 2026-10-08T13:50:32.284Z
- 摘要: 独立核对候选 parent 7ba2169b5d95e2c8406d919e87ed82aef8cd6eeb 与 source e222737896c041be450eaac251208d5e1eb76f8e、brief/Spec、A1-A8、候选绑定的六组 Runtime 检查日志及 Git 提交身份。A1、A2、A4、A5、A6 passed；A3、A7、A8 blocked。六组 Runtime 检查均为 PASS 并与当前候选对应；没有重跑全套。结论保留生产密钥/备份、精确候选 CI、Windows 11、真实账户迁移恢复和发布门槛未完成状态。未修改源码、Git 或正式规格/状态。

## 验收

| 编号 | 结果 | 来源 | 验收项 | 原因 |
| --- | --- | --- | --- | --- |
| A1 | passed | brief.md | A1：FishGram发现源与v2-only实际有效，官方发现/RSA包不可作为回退，平台/渠道/完整版本绑定。 | 独立核对固定 FishGram feed 和 v2-only 验签/客户端发现路径；没有官方 HTTP/MTP 或 RSA 安装回退。候选绑定的 source-and-scripts、update-production 和 all-tool-tests Runtime 检查通过，生产 Windows 载荷/v2 验签及 feed 定向测试通过。 |
| A2 | passed | brief.md | A2：同基线r8→r9、跨基线、Beta主动选择/转稳定新修订、旧版本及JSON大整数均正确处理，客户端/Packer/ready/Updater一致。 | 核对完整 64 位版本在客户端、Packer、ready 标记和 Updater 的路径，以及十进制字符串/大整数解析；同基线修订、跨基线、Beta opt-in 与从 Beta feed 接受稳定新修订均有生产实现测试。update-production、all-tool-tests 检查通过。 |
| A3 | blocked | brief.md | A3：根签名清单验证、单调版本、年度发行键过期/续期/吊销/回退拒绝、45天提醒、根离线加密备份及手动重装文档通过。 | 签名 manifest 单调版本、续期/吊销/回退和 45 天提醒有实现及定向测试，密钥维护和手动重装文档存在；但当前无生产 root/issuer trust 与签名环境，也无可核实的独立离线加密 root 备份。测试 fixture 不能证明生产密钥保管和恢复已经完成。 |
| A4 | passed | brief.md | A4：自动下载可禁用；完成仅提示下次重启，不退出/抢窗口，多个workdir和同安装单事务正确，启动器不默认-many。 | 核对稳定默认自动检查、Beta 显式选择、关闭自动更新、ready 后提示下次重启、按 workdir 隔离及安装事务互斥、启动器参数；候选绑定的 update-transactions、all-tool-tests 通过。 |
| A5 | passed | brief.md | A5：损坏/错误签名/官方/旧包、大小/哈希/目标/渠道错误、路径穿越及账户目录载荷被实际拒绝且当前客户端可继续。 | 实际 Windows 生产验签与事务测试和 Python/Packer 测试覆盖坏包、错误签名、官方/旧包、目标/渠道/版本/大小/哈希不匹配、路径穿越及账户目录载荷拒绝；Runtime 的 update-production、update-transactions、all-tool-tests 均通过，客户端失败后保留当前程序的路径已核对。 |
| A6 | passed | brief.md | A6：中断/断网/服务不可达重试和理解性错误；空间不足、权限失败、进程未退、文件占用均不破坏现有程序。 | 核对下载失败/重试及可理解错误、空间/权限/运行中进程/文件占用预检和事务恢复；本轮 Windows update-transactions 与 all-tool-tests 通过。该结论是当前候选的定向自动化验证，不代表未做的完整 Win11/UAC 实机矩阵。 |
| A7 | blocked | brief.md | A7：事务备份、替换失败/中断恢复、最近三程序版本、跨基线退出后两份数据快照、显式数据恢复和独立手动回退实测。 | 候选原生 Windows 事务、替换失败/中断恢复、程序备份和 synthetic account snapshot/recovery 检查通过；但验收要求的跨官方基线真实账户退出后快照、显式数据恢复及独立手动程序回退未被接受。当前没有真实账户迁移/恢复验证，不能用合成测试替代。 |
| A8 | blocked | brief.md | A8：干净Windows11首次运行，实际云端同一候选签名/更新安装/恢复链路通过；搜索与云端门槛全过才首发，未通过不发布索引。 | 没有干净 Windows 11 首次运行及同一候选实际云端签名、更新安装、故障恢复验收；当前精确候选完整 CI 仍待完成，FishGram API identity、生产 trust/signing keys、发行/Pages 环境未配置。当前候选未公开发布索引，公开门槛未满足。 |

## 检查

| 检查 | 命令 | 工作目录 | 状态 | 退出码 | 耗时 |
| --- | --- | --- | --- | ---: | ---: |
| Fixed source, official instructions and script syntax | -NoProfile -Command . ./tools/common.ps1; $r=Get-FishGramRecipe; $null=Assert-FishGramSource $PWD.Path $r; git -C tdesktop diff --exit-code $r.upstreamCommit -- AGENTS.md; if ($LASTEXITCODE) { exit $LASTEXITCODE }; foreach ($f in Get-ChildItem tools -Filter '*.ps1') { $t=$null; $e=$null; $null=[Management.Automation.Language.Parser]::ParseFile($f.FullName,[ref]$t,[ref]$e); if ($e.Count) { throw $f.Name } }; git -c submodule.recurse=false diff --check --ignore-submodules; exit $LASTEXITCODE | . | passed | 0 | 4166 ms |
| Production search helpers and shared coordinator | -NoProfile -Command ./tools/test-restricted-search.ps1; ./tools/test-restricted-search-integration.ps1 | . | passed | 0 | 16749 ms |
| Actual verifier, Packer, Updater and native data interoperability | -NoProfile -Command ./tools/test-update-verify.ps1 -DependencyRoot ../.. | . | passed | 0 | 28548 ms |
| Windows update policy and installation transactions | -NoProfile -Command ./tools/test-update-policy.ps1; ./tools/test-update-transaction.ps1 | . | passed | 0 | 14436 ms |
| All candidate tooling tests with mandatory compiled production fixtures | -NoProfile -Command $env:TEMP=Join-Path $PWD '.private/temp'; $env:TMP=$env:TEMP; $env:FISHGRAM_PACKER=Join-Path $PWD 'build-update-verify-tests/packer_tests.exe'; $env:FISHGRAM_PACKAGE_VERIFY=Join-Path $PWD 'build-update-verify-tests/package_verify.exe'; $env:FISHGRAM_NATIVE_SNAPSHOT_TEST=Join-Path $PWD 'build-update-verify-tests/data_snapshot_tests.exe'; foreach ($f in @($env:FISHGRAM_PACKER,$env:FISHGRAM_PACKAGE_VERIFY,$env:FISHGRAM_NATIVE_SNAPSHOT_TEST)) { if (-not (Test-Path -LiteralPath $f)) { throw 'Required production fixture is absent' } }; python -m unittest discover -s tests -v; exit $LASTEXITCODE | . | passed | 0 | 82204 ms |
| Actual fixed source ZIP and pinned complete license evidence | -NoProfile -Command python tools/license_inventory.py --archive .private/source-pin-check-7ba2169.zip --config config/license-sources.json --pre-export --output .private/source-license-inventory-7ba2169.json; exit $LASTEXITCODE | . | passed | 0 | 23199 ms |

### Builder 报告的证据

以下为 Builder 报告，不等同于 Runtime 检查凭据或独立验收结果。

- Actual Windows native and Python interoperability: passed — .private/native-acl-integration-final.log: strict native and Updater compilation, 36 data tests no skips, production v2 60/63 checks and Packer11.
- All tooling with mandatory compiled fixtures: passed — .private/python-acl-candidate-final.log:198/198 no skips, actual compiled snapshot/Packer/verifier configured.
- Actual elevated cloud runner regression: passed — GitHub source37784975830 at e222737 success; actual elevated=1; helper/search/transactions and data35pass+1nativeinteropskip. This is not full client compilation or actual UAC.
- Clean public recursive clone: passed — .private/clone-check-74e3281: exact74e3281/6fed833 and40submodules matched; scoped to that historical candidate, not newmain.
- Exact current corresponding source and license inventory: passed — 7ba2169 archive41repos15811files SHA256739bf8766465ee00ec40a850b3b9ce3eb8fafa645bdfb3186191da14569c8f92; private source-pin-check-7ba2169.zip and source-license-inventory-7ba2169.json.
- Production cloud and real account acceptance: not-run — New exact source/parent full test-identity CI dispatched; productAPI, productiontrust/keys, offline rootbackup/App and Windows11/account/searchmatrix remain absent.
- 已知限制: Own FishGram API identity absent locally and in candidate environment; never reuse old other-app credentials.
- 已知限制: Independent offline root backup and production root/issuer trust/signing secrets absent; encrypted OneDrive supplemental only.
- 已知限制: Upgrade/release Apps and Pages unconfigured; upgrade environment branch restriction fixed. No real external release.
- 已知限制: Current full exact-commit CI still pending; earlier cold/warm successes use older test identity and autoUpdateOFF.
- 已知限制: Real clean Windows11, UAC prompts, cross-token recovery, actual account migration/recovery and installation matrix not accepted.
- 已知限制: Original restricted-global-search remains2/8; no media/UI/settings/performance target changes.
- 已知限制: One historical native snapshot fixture exit3 cause remains unknown; diagnostics improved, directed current native tests successful; no unsupported antivirus/storage attribution.
- 已知限制: Original migration scene and r7 untouched; actual account migration backup not accepted.

## 阻塞项

- **user**: 独立核对候选 parent 7ba2169b5d95e2c8406d919e87ed82aef8cd6eeb 与 source e222737896c041be450eaac251208d5e1eb76f8e、brief/Spec、A1-A8、候选绑定的六组 Runtime 检查日志及 Git 提交身份。A1、A2、A4、A5、A6 passed；A3、A7、A8 blocked。六组 Runtime 检查均为 PASS 并与当前候选对应；没有重跑全套。结论保留生产密钥/备份、精确候选 CI、Windows 11、真实账户迁移恢复和发布门槛未完成状态。未修改源码、Git 或正式规格/状态。 (acceptance: A3, A7, A8) — next: `resolve-verifier-blocker`

## 风险与跳过的工作

- FishGram API identity、生产 root/issuer trust 与签名 secrets 缺失；不得使用其他应用凭据代替。
- 独立离线加密 root-key 备份未确认，OneDrive 加密副本仅为补充。
- 当前精确候选完整云端 CI 尚待完成；较早 CI 使用旧测试身份且 autoUpdate 关闭。
- 未验收干净 Windows 11、真实 UAC、跨权限恢复、账户迁移/恢复和完整安装矩阵。
- A7 的合成数据验证不能证明真实账户数据与程序回退链路；A3 的测试密钥不能证明生产信任/备份可用。
- 发布与 Pages/App 配置尚未完成；无实际签名发布或索引晋升。
- 已有历史原生快照 fixture exit 3 原因仍未查明；本轮定向原生测试成功，不能推断为存储过滤驱动原因。

## 之前的迭代

| 目标周期 | 迭代 | 尝试 | 结果 | 未解决项 | 摘要 | 完成时间 |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | blocked | A3, A7, A8 | 已独立核对候选 parent 067028b26a25223fdf5d63c35fdb974e5d87fd4b 与 source 8f50676ba9b7da0c9a142e914f6d3ed9358306fd、A1-A8 全部场景、候选绑定的六组 Runtime 检查、当前 CI 失败日志及 Builder 交接。结论：A1、A2、A4、A5、A6 passed；A7 failed；A3、A8 blocked。整体 blocked。未重复完整测试，未改源码、Git、Spec 或正式状态；仅提交本正式 verifier-response。 | 2026-10-08T12:38:22.444Z |
| 1 | 1 | 1 | recovery | — | User has explicitly authorized implementing the approved plan and continuing until complete in Goal mode. Repair only the CI test fixture initialization and canonical short-path handling demonstrated by the failed candidate; retain all acceptance targets and external production/Win11 blockers. No requirement adjustment or pretend resolution. | 2026-10-08T12:41:57.273Z |
| 1 | 2 | 1 | blocked | A3, A7, A8 | 独立核对候选 parent 7ba2169b5d95e2c8406d919e87ed82aef8cd6eeb 与 source e222737896c041be450eaac251208d5e1eb76f8e、brief/Spec、A1-A8、候选绑定的六组 Runtime 检查日志及 Git 提交身份。A1、A2、A4、A5、A6 passed；A3、A7、A8 blocked。六组 Runtime 检查均为 PASS 并与当前候选对应；没有重跑全套。结论保留生产密钥/备份、精确候选 CI、Windows 11、真实账户迁移恢复和发布门槛未完成状态。未修改源码、Git 或正式规格/状态。 | 2026-10-08T13:50:32.284Z |



## 结论

独立核对候选 parent 7ba2169b5d95e2c8406d919e87ed82aef8cd6eeb 与 source e222737896c041be450eaac251208d5e1eb76f8e、brief/Spec、A1-A8、候选绑定的六组 Runtime 检查日志及 Git 提交身份。A1、A2、A4、A5、A6 passed；A3、A7、A8 blocked。六组 Runtime 检查均为 PASS 并与当前候选对应；没有重跑全套。结论保留生产密钥/备份、精确候选 CI、Windows 11、真实账户迁移恢复和发布门槛未完成状态。未修改源码、Git 或正式规格/状态。
