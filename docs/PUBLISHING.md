# FishGram 公开发行

`tools/publish_release.py prepare` 完全离线：复用 `sign_candidate.py` 与 `release_gate.py` 验证批准候选 record、QA、固定父/源码提交、候选 ZIP、对应源码、生产 trust、signing record 及 TDUP v2 生产签名；生成 `release-assets/`、`pages/keys/`、单渠道索引和逐项 SHA256 promotion manifest。已有输出目录会拒绝覆盖。

`tools/publish_release.py publish` 是单独的外部写入入口。它从本地 promotion manifest 重验每个文件的大小和 SHA256，只接受明确的 `--enable-publication`、`workflow_dispatch`、`refs/heads/main`、`release` environment 和审批 marker，并从环境读取两个不同用途的 GitHub App 安装令牌。命令行不接受 token。产品 API 固定到 `lonefisher/fishgram`，源码 ref API 固定到 `lonefisher/tdesktop`；没有 App 凭据时安全停止。源码 tag 必须由单独的源码仓 token 创建，父仓 `GITHUB_TOKEN` 不用于冒充跨仓权限。

生产顺序为：拒绝已存在的父仓 tag、源码 tag 或 release；读取并核对 Pages 配置；创建源码仓版本 tag；创建父仓 draft release；逐件上传所有附件并核对 API 返回的名称、大小和 uploaded 状态；发布 draft；从公开 release URL 逐件下载并校验 SHA256；经 Contents API stage `keys/` 中四个 trust 文件，随后从公开 Pages URL 逐件读回并校验 SHA256；最后更新 `stable.json` 或 `beta.json`，再从公开 Pages URL 读回并校验索引哈希。任何失败均中止后续步骤，不回滚或覆盖已公开对象。重复 tag/release、release 中已有附件、旧/相同完整十进制 update version、Pages 缺失或 Pages 配置不符合 FishGram URL 都会拒绝。

索引顶层 `channel` 表示 index channel；entry `channel` 表示签名 package channel，省略时生产 `ParseFeed` 默认到索引渠道。beta index 可推广新的 stable 签名候选；stable index 不接受 beta 包。更新 URL 使用生产约束 `https://github.com/lonefisher/fishgram/releases/download/<tag>/fishgram-update-win-x64-<base>-r<revision>[-beta]`。版本和大小保留十进制字符串。

## 人工 workflow

`.github/workflows/publish-release.yml` 仅响应 `workflow_dispatch` 且 job 仅允许 `main`，绑定 `release` environment。默认 `publish_externally=false`，只在已签名 artifact provenance 和完整本地 gate 成功后上传可审查的离线 promotion artifact；该 artifact 明确标记 `prepared-only`，不代表已发行。只有人工明确选中 `publish_externally=true`，环境审批完成且 `RELEASE_APP_CLIENT_ID` / `RELEASE_APP_PRIVATE_KEY` 配置齐全，workflow 才请求两个各自限定到单一仓库的 App token 并调用外部写入入口。发布和准备两条路径都从 run API 重验候选 workflow 的成功 run/job/唯一 artifact，并对签名 workflow 的成功 run/job/`fishgram-signed-candidate-<candidate_run_id>` artifact 做同样 provenance 校验；下载后还会重复 QA、candidate、source、trust 和签名 gate。

发布 gate 除复用 signer pins 外，还单独要求受保护 main 中提交发布脚本、GitHub REST transport、许可 inventory/config、发布测试、发布文档、workflow 及所选 QA 文件；这样发布专用输入也绑定到候选批准的精确父仓 commit。

2026-10-11 配置读回：`candidate` 与 `release` 均保留维护者人工审批，Selected branches and tags 只允许 branch `main`，禁止管理员绕过。Pages API 为公开 `https://lonefisher.github.io/fishgram/`，来源 `gh-pages` 根目录；独立开发说明页已有公开读回证据，更新索引尚未推广。两个 App 已安装、认证及权限读回，发布 App 仅在受保护 release 环境使用。配置接续及 refs 保护见 [GitHub 配置](GITHUB-CONFIGURATION.md)。GitHub Contents API 接受变更不证明 Pages 部署完成；只以公开 URL 的逐文件 HTTP 200 和 SHA256 读回为成功。索引 write 成功但读回失败会报告失败，不能据此宣称客户端已收到新索引。真实生产候选/签名 workflow provenance 及端到端公开发布仍未验收。

发布附件至少包括签名更新包、候选 ZIP、对应源码 ZIP、candidate record、QA approval 和 signing record。公开包不包含密钥、原始日志、账户数据或构建缓存。上传中断可能留下 tag、draft/已发布 release 或部分附件；不自动删除/重试覆盖，需人工检查并用新版本继续。索引始终是最后的公开推广点。

## 定向验证与外部限制

- `python -m unittest discover -s tests -p test_publish_release.py -v` 验证 offline manifest、split-channel、版本单调性、来源候选及门控。
- `python -m unittest discover -s tests -p test_github_release_transport.py -v` 使用内存 HTTP stub 检查具体 REST method/host/path/body/auth、draft 上传地址、双仓权限分离、Pages 404 fail-closed、公开无 token 下载以及 attachment → keys → index 最后读回的请求顺序。测试不访问 GitHub，不签真实密钥、不公开产物。
- 尚未完成：生产 `config/update-trust` 已在候选分支由客户端代码验签，但仍须随受审查固定提交进入实际人工 workflow；真实已批准候选/签名 artifact、分仓发布 token、release/tag/附件及更新索引推广均待端到端验收。已完成的 App 认证、配置、环境保护和 API stub 不替代这些验收，也不表示已发行。
