---
generated_from_state_version: 13
---

# 验证

## 当前结果

- 结果: **已阻塞**
- 验证情况: **解决报告中的阻塞项后恢复验证**
- 目标周期: 1
- 迭代: 2
- 验证器尝试次数: 1
- 完成时间: 2026-10-08T16:39:37.261Z
- 摘要: 已逐项核验当前候选 A1-A8。A2 通过；A1、A3-A8 因自有 API/真实云端产品构建、缓存构建、GitHub App 或实际 Windows/签名发布证据缺失而阻塞。A7 源码与许可部分已真实验证，完整产品清单和 QA 仍缺。保留先前报告中的外部阻塞；不以合成程序、旧 CI 输入或回归夹具替代产品验收。

## 验收

| 编号 | 结果 | 来源 | 验收项 | 原因 |
| --- | --- | --- | --- | --- |
| A1 | blocked | brief.md | A1：产品名称、独立图标、应用ID、默认数据目录统一FishGram，自己的API身份，非官方说明、基本功能与广告保留，无新增遥测/官方崩溃上报。 | 尚无 FishGram 自有 API 身份和产品候选，因此无法确认产品身份及真实产品行为的完整验收。 |
| A2 | passed | brief.md | A2：源码和子模块一致性、五组生产helper、脚本及测试身份编译对全部PR运行；外部PR无法访问发行API或签名密钥。 | 当前 PR 检查工作流覆盖源码/递归依赖、脚本、生产 helper 和测试身份编译；发行凭据仅注入受保护候选流程，且外部 PR 无发行权限。 |
| A3 | blocked | brief.md | A3：标准Windows runner从固定父/源/递归依赖提交无缓存实际完成准备与客户端构建，记录空间、耗时、工具链和程序哈希；如失败报告实际瓶颈，不算本机成功。 | 没有绑定当前父提交 70af27400ce9e1dd40c563f8f36f2585fde897f9 的标准 Windows runner 无缓存产品构建及完整产物记录；现有 37785578154/37785610848 对应 e222737/7ba2169，不能替代。 |
| A4 | blocked | brief.md | A4：可信依赖缓存按平台/工具链/准备内容精确绑定，实际缓存构建通过且不缓存或上传API配置/客户端编译目录。 | 没有当前候选的可信依赖缓存命中构建及产物证据。 |
| A5 | blocked | brief.md | A5：受保护环境和分支检查有效，产品候选从已审查输入使用FishGram身份；所有发行前置门槛未通过时没有公开二进制或更新索引。 | 分支严格保护、管理员约束和候选/发行 reviewer 规则已读回，但 candidate/release 环境 secret 名称为空，未生成真实产品候选；公开 Pages 当前无稳定/Beta索引、签名清单或程序发布。upgrade-automation 无 reviewer 符合每日自动候选设计，不作为缺陷。 |
| A6 | blocked | brief.md | A6：首次和重复官方检查、冲突、CI失败均正确；机器人使用仅两仓库GitHub App创建关联PR/去重记录，不能绕过检查、自动发布或更新客户端。 | 升级 workflow 代码和 fixture 回归存在，但 GitHub App 尚未配置，缺少真实安装授权及首次/重复/冲突/CI失败行为证据。 |
| A7 | blocked | brief.md | A7：清单包含父/源/递归提交、工具链、平台渠道完整版本及产物SHA256；对应源码与构建脚本、GPLv3/OpenSSL例外/依赖许可完整。 | 对应源码归档已绑定父 70af274/源码 e222737：41 个仓库节点、15,813 个文件哈希、93 个许可路径、未解析许可 0；但没有真实产品候选清单及其产物哈希/完整版本与渠道绑定，A7 全项未满足。 |
| A8 | blocked | brief.md | A8：实测同一云端候选后不重编译，人工签名对应程序哈希，发布附件并公开下载核验后最后更新索引；已发布标签附件不覆盖。 | 尚无真实云端候选实测、同一候选人工签名、附件公开下载核验及最后晋升索引的完整链路。 |

## 检查

| 检查 | 命令 | 工作目录 | 状态 | 退出码 | 耗时 |
| --- | --- | --- | --- | ---: | ---: |
| Pinned source, official instructions and build scripts | -NoProfile -Command . ./tools/common.ps1; $r=Get-FishGramRecipe; $null=Assert-FishGramSource $PWD.Path $r; git -c core.longpaths=true -C tdesktop diff --exit-code $r.upstreamCommit -- AGENTS.md; if($LASTEXITCODE){exit $LASTEXITCODE}; $pins=@(git -c core.longpaths=true -C tdesktop submodule status --recursive); if($LASTEXITCODE -or $pins.Count -ne 39 -or @($pins \| Where-Object {$_ -match '^[-+U]'}).Count){throw 'Recursive pins mismatch'}; foreach($f in Get-ChildItem tools -Filter '*.ps1'){$t=$null;$e=$null;$null=[Management.Automation.Language.Parser]::ParseFile($f.FullName,[ref]$t,[ref]$e);if($e.Count){throw $f.Name}};git -c submodule.recurse=false diff --check --ignore-submodules;exit $LASTEXITCODE | . | passed | 0 | 11896 ms |
| Reviewed baseline, actual product ZIP and release/publication regressions | -c import unittest; patterns=['test_candidate_packaging.py','test_release_gate.py','test_release_tools.py','test_publish_release.py']; s=unittest.TestSuite(unittest.defaultTestLoader.discover('tests',pattern=p) for p in patterns); r=unittest.TextTestRunner(verbosity=2).run(s); raise SystemExit(0 if r.wasSuccessful() and not r.skipped else 1) | . | passed | 0 | 86751 ms |
| Exact current recursive committed source, all file hashes and licenses | .private/check-current-source.py | . | passed | 0 | 58887 ms |
| Actual public Pages bytes with no update promotion | .private/check-pages-bootstrap.py | . | passed | 0 | 2128 ms |

### Builder 报告的证据

以下为 Builder 报告，不等同于 Runtime 检查凭据或独立验收结果。

- Product candidate actual ZIP and Git bindings: passed — .private/candidate-packaging-green.log6/6no skips incl recipefalse/effectivetrue,identity/trust negatives, changedbytes and3real link boundaries; all-red log3fail beforefix.
- Existing release gate regression: passed — .private/release-gate-link-regression.log28/28 includingQA/sign/manifest/pins/identity.
- Current full source/parent CI: not-run — 37785578154/e222737 fullsource compile live;37785610848/7ba2169 parentdependencies live. Those runs precede b606 packaging-only fix. Historical cold37031108321/warm37743424883 success testidentity/updatesOFF are not current product acceptance.
- Production credentials and real Windows product QA: not-run — OwnFishGram API/offlineroot/App absent, no production product or Win11 signed/recovery/searchmatrix evidence. Synthetic inputs remain test fixtures only.
- Official baseline binding RED/GREEN: passed — cloud-baseline-manifest-red/green.log:6 missing or forged values reproduced, now29/29; actual product ZIP6/6 verifies all3baseline fields in embedded/external manifests.
- Actual current corresponding source: passed — cloud-source-audit.json for70af274/e222737:41nodes/15813hashedfiles/archiveSHAeef37725bb0086119ffee76e973c50622055a6daac660d7fb8c4075c6f8d8eb1;license93/unresolved0;not actualproductcandidate.
- Public Pages bootstrap: passed — pages-bootstrap-readback.json:239c8a9 HTTP200 exact committedHTML;stable.json/beta.json/keys manifest+sig404. POSTPages409 alreadyenabled, actualGET/build/publicreadback confirms correctpublicsourcegh-pages/root. No updateindex/release.
- 已知限制: OwnAPI/root/issuer/App remainunconfigured; environmentsecret names all empty at authenticated16:06UTCpreflight. Reviewer/adminbranchcontrolsverified. Pages now configured, original earlierblockedreport preserved.
- 已知限制: Newparent70af274 packages-onlyfix is local; runningfulltestidentityinputs7ba/e222 not thisparent/product. Historical cold/warmsuccess olderupdatesOFF.
- 已知限制: Runtime source-check priorpermission failure resolved through main authorizedGitmetadata route; originalfailedreceipt preserved. Firstpermissionreview timedout; returnedinstructionsallowedonceretry, successfulactual41nodeexport.
- 已知限制: No productioncloud cold/cached package, cleanWin11/UAC/realaccounts/search6/signedrestart/recovery/publishedchainyet. No public release orindexuntilallpass.
- 已知限制: Originalsearch source unchanged: earlierbound5helpers+13sharedcore remainsvalid forsourcee222; targetedmanifestchanges noC++edit.
- 已知限制: Synthetic program/keys and realPacker testfixtures cannot substitute ownAPI/trust/manualproductQA.

## 阻塞项

- **user**: 已逐项核验当前候选 A1-A8。A2 通过；A1、A3-A8 因自有 API/真实云端产品构建、缓存构建、GitHub App 或实际 Windows/签名发布证据缺失而阻塞。A7 源码与许可部分已真实验证，完整产品清单和 QA 仍缺。保留先前报告中的外部阻塞；不以合成程序、旧 CI 输入或回归夹具替代产品验收。 (acceptance: A1, A3, A4, A5, A6, A7, A8) — next: `resolve-verifier-blocker`

## 风险与跳过的工作

- Runtime 四项绑定检查均 PASS：固定源/脚本；包装、清单、发布回归 59/59 且无跳过；精确递归源码与许可审计；实际 Pages HTML 与提交 239c8a9 一致且更新资产返回 404。
- 候选 ZIP/清单基线绑定、真实 Packer 签名测试 8/8 为本机回归证据；不等同生产云端构建或人工签名产品。
- 历史 cold/warm 构建使用测试身份且关闭更新，不能证明当前产品验收。
- 当前 verifier 首次 Git 状态读回受 worktree 元数据路径权限限制；采用 Runtime 已授权 Git 元数据路径的成功 41 节点导出与 SHA 审计，不据此判定 A7 归档失败。

## 之前的迭代

| 目标周期 | 迭代 | 尝试 | 结果 | 未解决项 | 摘要 | 完成时间 |
| ---: | ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | 1 | blocked | A1, A3, A4, A5, A6, A7, A8 | Independent read-only review of A1-A8 for parent b606bca/source e222737. A2 passes from checked-in PR workflow configuration and the three bound Runtime checks; A1 and A3-A8 remain blocked by missing production identity/configuration, current-candidate cloud and cache runs, authenticated GitHub metadata, exact product archive, and real Windows/release evidence. Synthetic tests are reported only as synthetic evidence. No release approval. | 2026-10-08T15:49:36.229Z |
| 1 | 1 | 1 | recovery | — | 用户明确要求继续独立核查，并补充15:52 UTC GitHub API状态：父run 37785610848 (head 7ba2169) 与源码run 37785578154 (head e222737) 均进入client compile；这两个run不是冻结的本地候选b606bca。继续只读核查当前scope并以实际权威读回更新结论。 | 2026-10-08T15:50:29.691Z |
| 1 | 1 | 2 | blocked | A1, A3, A4, A5, A6, A7, A8 | A1、A3、A4、A5、A6、A7、A8 均缺少其完整验收所需的真实证据，全部保持 blocked。A2 已由既有 Runtime 状态通过，不在本轮 scope。当前没有首发或公开发布依据。 | 2026-10-08T16:08:52.951Z |
| 1 | 1 | 2 | recovery | — | User authorized completing the full plan. Preserve external blockers and original independent reports; fix the same missing official-baseline traceability in protected and internal package manifests, and restore exact-source archive evidence using the authorized Git metadata path. | 2026-10-08T16:15:49.599Z |
| 1 | 2 | 1 | blocked | A1, A3, A4, A5, A6, A7, A8 | 已逐项核验当前候选 A1-A8。A2 通过；A1、A3-A8 因自有 API/真实云端产品构建、缓存构建、GitHub App 或实际 Windows/签名发布证据缺失而阻塞。A7 源码与许可部分已真实验证，完整产品清单和 QA 仍缺。保留先前报告中的外部阻塞；不以合成程序、旧 CI 输入或回归夹具替代产品验收。 | 2026-10-08T16:39:37.261Z |



## 结论

已逐项核验当前候选 A1-A8。A2 通过；A1、A3-A8 因自有 API/真实云端产品构建、缓存构建、GitHub App 或实际 Windows/签名发布证据缺失而阻塞。A7 源码与许可部分已真实验证，完整产品清单和 QA 仍缺。保留先前报告中的外部阻塞；不以合成程序、旧 CI 输入或回归夹具替代产品验收。
