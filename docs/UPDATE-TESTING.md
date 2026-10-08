# 更新系统开发验证

从父工作区运行，使用配方指定的MSVC、SDK和Qt。源码子模块先完成递归初始化；`DependencyRoot`可指定同配方的本机依赖目录，不能把本机构建记为云端成功。

```powershell
./tools/test-update-policy.ps1
./tools/test-update-transaction.ps1
./tools/test-update-verify.ps1 -DependencyRoot <同配方依赖目录>
python -m unittest discover -s tests -v
```

`test-update-verify.ps1`编译并运行真实feed/v2验签、签名载荷解码和重启权限策略，分别检查无信任配置和注入FishGram稳定/Beta清单两种情况；编译链接真实Windows Updater但不启动GUI；再运行Packer→外部签名→生产验签/解压的10项集成测试。测试的根/发行私钥临时生成、加密、限制ACL并随临时目录删除；仅公开测试公钥和签名清单放入忽略的`.private`，不使用生产密钥。普通Python discover因未提供已编译的Packer路径而跳过这10项，须另跑该脚本；跳过不能当通过。

16组事务测试在独立临时目录使用合成程序及合成账户内容，覆盖16字节FishGram就绪标记、版本/渠道、嵌套便携工作目录、空间/权限/进程/占用、替换失败、进程强制中断后恢复、提交后中断的历史保留、同安装目录互斥、程序保留与账户内容未被改写。不退出真实账户、不替换当前客户端。可通过`-SourceRoot`验证指定源码树。

截至2026-10-08，这些开发检查已通过，候选源码为04a73a1。正式Comet仍为Build，A1-A8待完整独立验收。剩余包括真实云端更新候选、真实Windows11安装重启与错误提示、关闭更新/多实例实际行为、跨基线账户数据快照和显式恢复、签名发布与索引顺序、生产密钥/应用身份、原搜索完整验收。当前r7继续保留，不发布测试身份产物。

云端更新开发候选使用 `build-telegram.ps1 -TestIdentity -TestUpdateSystem`，临时根授权公钥只用于测试身份完整编译，含此信任的程序不能发行。脚本拒绝将该开关与生产身份组合。正式配方仍关闭更新，直到生产身份与根信任配置及完整验收通过。

手动账户工具的17项合成数据回归随Python discover运行，详见[数据恢复](DATA-RECOVERY.md)。真实账户与客户端自动快照集成尚未验收；不可替代A7。
