# 更新系统开发验证

## 2026-10-15 固定输入的工具与启动检查

父提交 `3ab3ff1c444c80cec73e8a0eedd5338db4e45916`、源码 `10df321519820764578dc42eca40ff1e6bed0dc5` 的[仓库检查](https://github.com/lonefisher/fishgram/actions/runs/38726743337)成功：Python 316 项中 303 项通过、13 项原生集成跳过，随后生产 helper 与原生 Release 参数检查通过。本机同输入完整 Python 回归另有 5 个临时 Git 目录清理错误，未记为全套通过；跳过项和清理错误不能由云端成功抵消。

同输入本机开发程序完整链接成功，并在独立空工作目录实际启动，窗口及欢迎页显示 FishGram 与非官方 Telegram API 说明；后台关闭后确认测试进程退出。程序 SHA256 为 `3721ed5b25f27211c0b80e95b5d43215c0491fb4838d8fd24e2c421f816a8b37`。该程序关闭自动更新，尚无登录、搜索或安装结论，不是云端产品候选。

对应源码归档由 Git 对象生成：41 个仓库，15,833 个归档文件，其中 15,830 个 Git blob 逐项匹配，另有 3 个许可补充；93 个许可路径、未解决许可项为 0。ZIP SHA256 为 `343fa6fe62d16735394b16df079299af983130da5987c66a5f0863687bf45019`。此项仅证明对应源码完整性，不证明首次发行门槛通过。

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

2026-10-13，[独立仓库检查 38381995069](https://github.com/lonefisher/fishgram/actions/runs/38381995069) 在固定父提交 `a27bb08d7892adf2dd3c7896aecc594dc7a949eb`、Python 3.13.16 下完整成功。此前在 PR 和 push 两次检查中失败的并发创建输出测试本次实际通过；修复让竞态注入按父目录文件身份匹配，并断言注入一次，未放宽生产独占创建或竞争文件保留规则。297 项 Python 检查中 284 项通过、13 项原生集成明确跳过；生产搜索 helper 和原生 Release 参数对照随后通过。该运行没有发行环境或凭据，只验证仓库检查，不是完整客户端编译或产品候选；原生集成的本机零跳过结果仍与其固定提交分别记录。
