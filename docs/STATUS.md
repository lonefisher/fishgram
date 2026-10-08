# 实施与验收状态

本页记录实现和证据，不能作为发行批准。Comet 的验收结论以 Runtime 为准。

| 项目 | 当前事实 |
| --- | --- |
| 仓库 | 两个 Public 仓库和相互关联的草稿 PR 已建立；主分支检查、管理员约束及发布环境人工审批已读回确认 |
| 本轮候选 | 已推送草稿父提交 `7ba2169`、源码 `e222737`；新增品牌、原生跨基线快照、恢复工具、生产搜索核心、签名和发布器。完整云端与实机验收仍待完成 |
| 搜索 | 五组生产 helper 和同一生产核心的 13 组集成场景通过；生产 Session/MTP 适配对象严格编译通过；原 Comet 保留 2/8，真实 UI、媒体、设置和性能待验 |
| 版本与品牌 | FishGram 名称、独立 Windows 应用身份、图标和默认便携工作目录已实现；相关对象/资源编译通过，完整产品包待验 |
| Comet 更新验收 | 对 `7ba2169`/`e222737` 的新独立 Verifier 已确认 A1、A2、A4、A5、A6 共 5/8 通过；A3、A7、A8 受生产配置及真实账户、Windows 11、完整发行链路证据阻塞，本轮没有实现失败项 |
| 更新签名 | 实际 v2 验签 60/60、双渠道信任 63/63、生产 Packer 往返 11/11 通过；均使用一次性测试密钥 |
| 安装事务 | 实际 Windows Updater 链接及 22 组隔离事务场景通过；真实 UAC、干净 Windows 11 和完整安装矩阵待验 |
| 数据恢复 | 原生跨基线快照与 Python 显式恢复已实现；合成测试和互操作有成功证据，也观察到一次原生 fixture 失败，已改善诊断，间歇原因未确认 |
| 发行工具 | 对应源码导出、许可正文归档绑定、候选 QA/签名门槛、公开附件读回及索引最后推广已实现；真实公开链路待验 |
| 生产配置 | 自有 API 身份、离线根密钥备份、正式信任包、发行键及 GitHub App 仍未配置；旧 API 身份不复用 |
| 首次发行 | 阻止：搜索完整验收、实际产品云端候选及安装/恢复链路尚未通过；r7 只保留为本机历史 |

旧固定提交的[冷缓存云端构建](https://github.com/lonefisher/fishgram/actions/runs/37031108321)和[修复后的热缓存构建](https://github.com/lonefisher/fishgram/actions/runs/37743424883)均完整成功，客户端编译约 113–114 分钟，编译后可用空间约 176 GB。两次使用公共测试身份，更新功能关闭，不能计为新增产品候选的实测。新候选构建结果按实际运行另行记录。此前候选的六组 Comet Runtime 检查通过，包括生产 fixture 下 Python 194/194 及固定源码许可证库存 41 节点、未解决 0；这些记录保持原提交绑定。

干净 runner 的缺目录、短路径夹具修复已获[父仓库检查成功](https://github.com/lonefisher/fishgram/actions/runs/37779473800)。源码事务夹具匹配提升权限策略后，[单独的云端 helper 检查](https://github.com/lonefisher/tdesktop/actions/runs/37784107745)成功，完整客户端编译明确跳过，不将其计为完整构建。后续本机独立检查为 Python 198/198 无跳过、原生快照与恢复 36/36、验签 60/63 和 Packer 11/11；真实 UAC 与账户仍待验。公开候选 `74e3281`/`6fed833` 已在全新目录完成递归克隆，40 个子模块指针均匹配。App、环境变量和 Pages 接续见[GitHub 配置](GITHUB-CONFIGURATION.md)。

后续[云端 ACL 互操作回归](https://github.com/lonefisher/tdesktop/actions/runs/37784975830)成功，日志实际读回 `elevated=1`；事务、13 组搜索核心及 Python 数据测试通过，36 项数据测试中 1 项 native 互操作明确跳过。真实管理员 runner 证据不替代 UAC 提示、普通令牌跨权限恢复或真实账户验证。`upgrade-automation` 环境现已限制受保护分支，无人工 reviewer；`candidate`/`release` 的维护者审批仍保留。

当前更新候选的六组 Comet Runtime 检查已通过，包括 Python 198/198 无跳过、原生快照互操作、签名/Packer 和事务测试。对应源码按固定 `7ba2169` 输入导出，41 个仓库节点、15,811 个文件，许可库存无未解决项。已另行验证公开父 `main=496910e` 的全新递归克隆：源码 gitlink 固定 `af84bb1`，40 个递归子模块一致；该克隆是公开主分支的架构证据，与当前更新候选分开记录。

[当前父仓库完整 CI](https://github.com/lonefisher/fishgram/actions/runs/37785610848)及[当前源码完整 CI](https://github.com/lonefisher/tdesktop/actions/runs/37785578154)仍在运行，不能提前记为构建成功；不因元数据或文档更新重复启动这些长任务。

账户截图、查询、频道名称、原始日志与凭据均留在私有区。公开证据仅保存提交、版本、摘要、匿名测试结论和已知限制。完整目标及各自门槛见 Comet Spec、[更新开发验证](UPDATE-TESTING.md)、[发行指南](RELEASING.md)、[发布流程](PUBLISHING.md)和[数据恢复](DATA-RECOVERY.md)。
