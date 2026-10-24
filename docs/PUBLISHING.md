# FishGram 公开发行

`tools/publish_release.py prepare` 完全离线：复用 `sign_candidate.py` 与 `release_gate.py` 验证批准候选 record、QA、固定父/源码提交、候选 ZIP、对应源码、生产 trust、signing record 及 TDUP v2 生产签名；生成 `release-assets/`、`pages/keys/`、单渠道索引和逐项 SHA256 promotion manifest。已有输出目录会拒绝覆盖。

`tools/publish_release.py publish` 是单独的外部写入入口。它从本地 promotion manifest 重验每个文件的大小和 SHA256，只接受明确的 `--enable-publication`、`workflow_dispatch`、`refs/heads/main`、`release` environment 和审批 marker，并从环境读取两个不同用途的 GitHub App 安装令牌。命令行不接受 token。产品 API 固定到 `lonefisher/fishgram`，源码 ref API 固定到 `lonefisher/tdesktop`；没有 App 凭据时安全停止。源码 tag 必须由单独的源码仓 token 创建，父仓 `GITHUB_TOKEN` 不用于冒充跨仓权限。

进入 `promote()` 后、首次调用发布 transport 前，发布器从自身受审查 checkout 的 `config/update-trust` 重新验证当前根签名清单，要求 promotion 的四个 trust 文件逐字节一致，再复用 `sign_candidate.py` 解析 TDUP v2、验证根签名与发行键签名及当前授权。包的平台、渠道、完整版本必须匹配候选；公开 signing record 同时绑定候选版本、两仓提交、候选及源码归档、发行键和更新包的名称、大小及 SHA256。篡改离线载荷后重算所有 handoff 哈希仍会被签名验证拒绝；替换根或换回旧的根签名清单也无法恢复已吊销发行键。

生产顺序为：核对两仓标签目标及 release 状态；读取并核对 Pages 配置；创建尚不存在的源码仓版本 tag；创建父仓 draft release；逐件上传所有附件并核对 API 返回的名称、大小和 uploaded 状态；发布 draft；从公开 release URL 逐件下载并校验 SHA256；经 Git blobs/tree/commit API 将 `keys/` 中四个 trust 文件放入同一提交，以非强制更新推进 Pages 分支，随后从公开 Pages URL 逐件读回并校验 SHA256；最后更新 `stable.json` 或 `beta.json`，再从公开 Pages URL 读回并校验索引哈希。任何失败均中止后续步骤，不回滚或覆盖已公开对象。错误标签目标、普通发布下已存在的 release、旧/相同完整十进制 update version、Pages 缺失或 Pages 配置不符合 FishGram URL 都会拒绝。

信任授权以 Pages 源分支的固定父提交为准，每次推广都检查完整的四文件 bundle；公开 CDN 字节相同也不能跳过该检查。权威 bundle 完全相同则不创建新提交；不同内容必须保持根 PEM 不变且 `manifest_version` 严格递增，权威文件不完整时停止。Git ref 非强制更新拒绝并发分支推进。索引也从权威分支读回完整版本，写入前按同一 Contents 读取重新比较并使用 SHA 条件更新；公开 CDN 仅用于部署后哈希读回，不决定是否允许版本回退。

公开证明中的 QA 批准时间必须与已验证 QA 一致，批准时间及签名时间只接受实际有效的 UTC 字符串。对象或其他嵌套值不会被透传到公开附件；这些时间记录不替代人工环境审批或候选哈希绑定。

若源码标签创建成功后 GitHub API 暂时失败，且产品 release 尚未创建，可用相同候选续跑。已有源码标签必须解析到该候选 `sourceCommit`；已有父仓标签也必须解析到候选 `parentCommit`。发布器保留这些精确标签，继续创建缺少的对象，不删除或覆盖标签。已有 release 须显式选择 `reuse_release`，按下述完整附件和候选校验续跑。

索引顶层 `channel` 表示 index channel；entry `channel` 表示签名 package channel，省略时生产 `ParseFeed` 默认到索引渠道。beta index 可推广新的 stable 签名候选；stable index 不接受 beta 包。更新 URL 使用生产约束 `https://github.com/lonefisher/fishgram/releases/download/<tag>/fishgram-update-win-x64-<base>-r<revision>[-beta]`。版本和大小保留十进制字符串。

## 人工 workflow

首版 Beta 按 [Beta 发行](BETA-RELEASE.md)使用 schema 2 `fishgram-beta-qa`：`deferredSearch` 只能是 `largeScalePerformance` 与 `nativeOmissionControlSamples`，`searchEightAcceptance` 保留 false，新增 `remainingSearchAcceptance` 必须 true，其余检查（包括 `updaterFullMatrix`）全部 true。只有最终解析出的索引渠道为 beta 时接受该 QA；省略渠道时使用候选包渠道，stable 包的首版 Beta 仍须显式选择 beta 索引。离线 promotion 的公开 proof 也记录 Beta 渠道，不能推广稳定索引。GitHub prerelease 标志按索引渠道设置，签名包渠道仍保留真实值。包内 README 与 Release 说明公开相同限制；完整搜索验收通过的 Beta 不再声称这两项待验。发布前必须有唯一 qa-proof.json，核对其候选、检查结果、渠道、时间及公开 signing-record.json 中的证明哈希。

真实安装恢复验收使用先前已签名的内部候选包。签名前的 `fishgram-signing-qa` 与完成后的 `fishgram-candidate-qa` 或上述首版 `fishgram-beta-qa` 保存在同一 QA 路径；最终文档须满足所选发行渠道的全部必需验收。签名记录保存原文档的 base64、哈希和批准时间，发布前核对原文档、Git 历史和最终文档都绑定同一候选，仅允许申请类型、批准时间及待验检查完成。发布默认门槛不接受内部签名申请。公开 signing record 只保留原申请哈希和时间，完整最终验收另见 qa-proof.json。

`.github/workflows/publish-release.yml` 仅响应 `workflow_dispatch` 且 job 仅允许 `main`，绑定 `release` environment。默认 `publish_externally=false`，只在已签名 artifact provenance 和完整本地 gate 成功后上传可审查的离线 promotion artifact；该 artifact 明确标记 `prepared-only`，不代表已发行。只有人工明确选中 `publish_externally=true`，环境审批完成且 `RELEASE_APP_CLIENT_ID` / `RELEASE_APP_PRIVATE_KEY` 配置齐全，workflow 才请求两个各自限定到单一仓库的 App token 并调用外部写入入口。发布和准备两条路径都从 run API 重验候选 workflow 的成功 run/job/唯一 artifact，并对签名 workflow 的成功 run/job/`fishgram-signed-candidate-<candidate_run_id>` artifact 做同样 provenance 校验；下载后还会重复 QA、candidate、source、trust 和签名 gate。

发布 gate 除复用 signer pins 外，还单独要求受保护 main 中提交发布脚本、GitHub REST transport、许可 inventory/config、发布测试、发布文档、workflow 及所选 QA 文件；这样发布专用输入也绑定到候选批准的精确父仓 commit。

2026-10-11 配置读回：`candidate` 与 `release` 均保留维护者人工审批，Selected branches and tags 只允许 branch `main`，禁止管理员绕过。Pages API 为公开 `https://lonefisher.github.io/fishgram/`，来源 `gh-pages` 根目录；独立开发说明页已有公开读回证据，更新索引尚未推广。两个 App 已安装、认证及权限读回，发布 App 仅在受保护 release 环境使用。配置接续及 refs 保护见 [GitHub 配置](GITHUB-CONFIGURATION.md)。GitHub Git/Contents API 接受变更不证明 Pages 部署完成；只以公开 URL 的逐文件 HTTP 200 和 SHA256 读回为成功。索引 write 成功但读回失败会报告失败，不能据此宣称客户端已收到新索引。真实生产候选/签名 workflow provenance 及端到端公开发布仍未验收。

发布附件至少包括签名更新包、便携 ZIP、对应源码 ZIP、candidate record、`qa-proof.json` 和脱敏的 `signing-record.json`。便携 ZIP 命名为 `FishGram-<version>-windows-x64.zip`，由已验证的候选 ZIP 确定性转换，仅替换内部候选 README；其余文件逐字节保留。ZIP 使用 stored 条目，排除宿主压缩库差异。Release 同时发布 distribution-record.json，绑定最终便携 ZIP 名称、大小、SHA256、原候选归档、程序哈希及审核发布提交。候选 record 继续绑定原云端 ZIP，promotion manifest 单独记录最终便携 ZIP 的大小和 SHA256。QA proof 仅包含批准时间、候选绑定、检查结论和原始 QA SHA256，不包含 approver、环境描述、原始备注或 QA 文件名；公开 signing record 使用白名单字段，QA 部分只保留原始哈希、批准时间及 proof 哈希。原始签名记录仍用于发布前的完整绑定校验，不能拿公开投影替代原始输入。公开包不包含密钥、原始日志、账户数据或构建缓存。

可提交的 QA 使用 schema 2，完整验收和内部签名申请的字段为 `schema`、`kind`、`approvedAt`、`candidate` 和 `checks`；首版 Beta 另有上述固定 `deferredSearch` 字段。不接受 approver、environment、notes 或其他自由文本字段，旧 schema 1 在签名前拒绝。人工身份及批准由受审查提交、受保护 release 环境和 GitHub 审批记录证明，匿名 JSON 本身不是身份认证。候选哈希绑定及所有必需检查仍必须通过；完整实测环境、原始 QA 备注、日志和截图留在本机私有区，不能提交到公开 Git。匿名 QA 与 Release proof 都仍须提交前内容审查。

上传中断可能留下 tag、draft 或部分附件；不自动删除、追加或覆盖，需人工检查。release 已发布且附件完整，仅 Pages 推广失败时，可显式复用同一候选续跑；缺失或不匹配的附件不能通过此路径修补。索引始终是最后的公开推广点。

## 定向验证与外部限制

签名提交可晚于候选提交：发布器验证 candidate → signer → 当前受保护 main 的祖先关系，以及 signer 和当前 main 中 QA 文件的 Git blob 一致。签名 run 的来源仍要求 main、人工触发、成功 job 与唯一候选 artifact；候选本身继续绑定原构建提交。

产品发行标签固定到候选记录的 `parentCommit`，不指向后续 QA 或发布审核提交；后者单独保存在 promotion 的 `repositoryInputs` 中 `lonefisher/fishgram` 项的 `reviewedCommit`。stable 与 beta 共用一个发布并发组，串行写入共享 Pages 信任文件。受保护 workflow 的 `reuse_release` 默认关闭，只有显式开启时传递复用选项；支持 beta 包进入 beta 索引、stable 包进入 beta 索引及同渠道发行的中断续跑。复用时已有 Release 的 prerelease 状态必须与索引渠道一致，拒绝跨渠道复用；Beta 转稳定须使用新修订号。每次均核对两仓标签、完整附件集合及逐件公开下载 SHA256，候选 record 也必须一致，不创建或覆盖 release、标签和附件；stable 索引仍拒绝 beta 包。

Pages 每次写入后按公开字节进行有界轮询，轮询预算为 180 秒，每 5 秒读回一次，单次 HTTP 请求还受 transport 的 30 秒超时约束；旧内容、404 和临时 HTTP 错误均继续等待，超时明确失败，不把 Contents API 写入成功当成公开推广成功。离线 promotion 显式保存并校验布尔 `reuseRelease`。源分支索引已是同版本同内容时，复用仅重新核对附件及公开索引部署，不重写索引；同版本不同内容和版本回退仍拒绝。准备成功会清理暂存副本。

签名 workflow 在接触发行密钥前限制 QA 为安全、区分大小写的 `.json` basename，拒绝通配符、目录和其他扩展名，与发布器 `verify_signing_history` 的 `release/qa/<basename>.json` 输入契约一致。

- `python -m unittest discover -s tests -p test_publish_release.py -v` 验证 offline manifest、split-channel、版本单调性、来源候选及门控。
- `python -m unittest discover -s tests -p test_github_release_transport.py -v` 使用内存 HTTP stub 和缓存的一次性 Ed25519 签名夹具，检查具体 REST method/host/path/body/auth、draft 上传地址、双仓权限分离、Pages 404 fail-closed、公开无 token 下载以及 attachment → keys → index 最后读回的请求顺序。发布器测试还覆盖重算离线哈希、替换根、旧清单及已吊销发行键；新签名边界测试执行实际密码学验签。
- 尚未完成：生产 `config/update-trust` 已在候选分支由客户端代码验签，但仍须随受审查固定提交进入实际人工 workflow；真实已批准候选/签名 artifact、分仓发布 token、release/tag/附件及更新索引推广均待端到端验收。已完成的 App 认证、配置、环境保护和 API stub 不替代这些验收，也不表示已发行。

复用既有发行前，发布器解析两个仓库的实际标签目标（普通标签或附注标签），逐一核对候选 parentCommit/sourceCommit；错误目标、非提交对象、循环或过长标签链会拒绝。标签存在和附件匹配不能代替源码目标一致。该核验在任何 Pages 写入前完成。
# Authoritative trust preflight

Before creating either repository tag or the release, the publisher reads the Pages trust bundle at a fixed branch commit. It rejects an incomplete bundle, a changed root, or a changed bundle with a nonincreasing manifest version. The atomic Pages update repeats the same checks against its current branch parent and never forces the ref. This preflight avoids known conflicts before immutable release writes; it is not a cross-service transaction and cannot prevent an unrelated writer changing Pages after the check.

便携 ZIP 使用 ZIP_STORED，Python/压缩库升级不改变包装字节。复用既有发行时，读取并保留首次发布的 distribution-record.json，校验其原候选、最终 ZIP 和程序记录与本次已验证输入一致；当前审核提交继续保存在本次 promotion 中。
