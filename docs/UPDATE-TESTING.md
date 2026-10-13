# 更新系统开发验证

从父工作区运行，使用配方指定的 MSVC、SDK 和 Qt。先递归初始化源码；`DependencyRoot` 可指定同配方的本机依赖。不能把本机构建记为云端成功。

```powershell
./tools/test-restricted-search.ps1
./tools/test-restricted-search-integration.ps1
./tools/test-update-policy.ps1
./tools/test-update-transaction.ps1
./tools/test-update-verify.ps1 -DependencyRoot <同配方依赖目录>
python -m unittest discover -s tests -v
```

`test-update-verify.ps1` 编译并运行真实 feed/v2 验签、双渠道信任、已验签载荷解码、重启权限、应用身份、失败分类和原生数据快照测试；链接真实 Windows Updater但不启动 GUI。再运行 11 项 Packer → 外部签名 → 生产验签/解压集成测试，以及原生快照与 Python 显式恢复互操作。根/发行密钥仅为临时加密 fixture，程序内测试公钥不能发行。

普通 Python discover 缺少已编译工具时会跳过原生互操作、Packer 和签名工具场景，跳过不能算通过。Windows 验证入口会设置对应可执行文件环境变量，完整结果应同时记载通过、失败和跳过。

24 组 Windows 事务场景使用独立临时目录和合成数据，涵盖就绪标记、版本/渠道、便携目录、空间/权限/进程/占用、替换失败、进程强制中断恢复、历史保留、安装互斥、签名后内存载荷、目录改名阻止和快照预检查失败。不退出真实账户，不替换当前客户端。

13 组搜索集成场景直接运行客户端使用的同一 `RestrictedSearchCore::Coordinator`，注入 transport、clock 和候选事实，覆盖范围准备、community/full-info、代际取消、迟到响应、重试、分页缓存、共享限流及 106 个候选恢复。生产 Session/MTP 适配对象另行严格编译。真实媒体/UI、设置持久化和性能仍须按原八项标准验收。

账户快照/恢复的合成测试见[数据恢复](DATA-RECOVERY.md)，程序恢复见[程序恢复](PROGRAM-RECOVERY.md)。出现间歇失败须保存精确错误和当前候选，不以重跑成功替代原因调查；本机曾出现一次原生快照 fixture 失败，目前原因未确认。

`test_locked_restore_journal_preserves_data_and_explicit_recovery` 用真实 Windows 无共享删除句柄占用恢复日志，在 prepared→moving-old 阶段使原子替换失败，核对旧账户数据未变、日志保留、临时文件清理；释放句柄后必须显式恢复，核对日志和暂存目录清除。该场景已本机通过，但未解释之前全套回归中一次 WinError 5 的占用来源，不能将它描述为该间歇问题已修复。

云端开发候选使用 `build-telegram.ps1 -TestIdentity -TestUpdateSystem`；该开关只为测试身份构建生成一次性根信任，不能与生产身份组合。正式产品仅走受保护候选 workflow，需自有 API 身份、生产根授权信任和实际云端验收。

Comet 仍处于实现阶段。完整更新矩阵、干净 Windows 11、真实 UAC/账户迁移、全部搜索验收及正式签名/公开下载链路尚未完成，r7 不发布。

## 2026-10-13 当前工具回归

父提交 `47d8d7c`、工作树源码 `7b355486ade41e14858ac3027c14a43719987a30`：现有打包专项 16 项通过；完整 Python 回归使用已有生产快照、Packer 和包验证器夹具，270 项通过、零跳过，进程退出码 0。夹具与临时 Git 仓库只证明本机工具回归，不能替代固定提交的真实云端产品候选、Windows 11 实测、签名及公开发行链路。
