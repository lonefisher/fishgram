# 云端配置与待完成条件

标准Windows2022 runner，固定Actions提交。`Cloud build`手动`cold=true`完成实际无缓存依赖准备及测试身份编译；随后`cold=false`验证缓存。PR没有产品API或签名身份。仅main的人工触发保存精确键依赖缓存，不消费PR保存的依赖。构建目录/API配置/原始日志/程序包均不上传；资源测量记录实际磁盘、目录规模和阶段时间。免费失败仍算失败。

`config/github-app-manifest.json`是升级App注册输入。注册后只安装到fishgram和tdesktop，将App ID保存为upgrade-automation环境变量`UPGRADE_APP_ID`，私钥作为环境secret `UPGRADE_APP_PRIVATE_KEY`，不写入Git。工作流每日运行，获取仅两仓库的短期安装token。脚本对写入endpoint和候选分支做白名单限制，不调用merge、Releases、Pages或更新索引。GitHub的contents写权限本身也涵盖release相关API；因此不能把脚本禁止发布误称为GitHub权限完全不具备发布能力。保护主分支和渠道分支、限制发行标签，并隔离人工发布环境仍是必要约束。

candidate与release环境要求人工批准，只有被审查固定提交的构建/签名任务可使用FishGram API及发行键。单维护者允许本人批准自己的执行；不允许机器人绕过批准。产品API身份、根/发行密钥及离线介质未完成前不标记受保护发行可用。

源码fork的继承工作流已替换为自有源码检查，Actions已启用。`e222737` 的完整源码测试身份 CI 已成功，当前父候选 `7ba2169` 的完整 CI 返回 `LNK1102`，两次结果分别记录。受保护分支要求对应CI通过；不能以禁用CI来绕过检查。产品候选尚未实测，详见[状态记录](STATUS.md)、[源码编译](CLOUD-SOURCE-BUILD.md)与[父仓库构建](CLOUD-PARENT-BUILD.md)。

首次与重复候选、合并冲突、CI失败分别需要实际验证。单元策略检查不等于App注册/安装或真实PR成功。搜索完整验收、云端候选实测及签名恢复全部通过前，没有公开客户端与频道索引。

2026-10-08历史开发验证：旧固定提交的无缓存和修复后缓存编译均成功，更新功能关闭，不能替代当前产品候选。此前缓存配置失败已定位到Python虚拟环境保存旧runner绝对路径。`refresh-build-python.ps1`在缓存恢复后调用官方prepare的`python`阶段重建虚拟环境，不重做C++依赖；详见[状态记录](STATUS.md)。

成功的更新源码草稿CI固定父工具提交 `7dbc44234dbe1a1426afa1e412a1a52ca9ca710b`，绑定实际源码SHA后以`-TestIdentity -TestUpdateSystem`完整编译，覆盖Updater及客户端自动更新代码。临时测试信任不得用于发行，脚本在读取凭据前拒绝与生产身份混用；公开只上传脱敏诊断，主分支合并及真实产品候选仍需完整门槛。

新的父候选在构建前后记录物理内存、当前提交量/上限及系统启动以来的提交峰值；构建上下文记录通过 PE 头读取的选定链接器宿主架构。失败诊断仅识别 `Telegram.exe`、`Updater.exe`、`Packer.exe`，不复制任意命令、绝对路径或错误正文。系统峰值不是构建进程峰值，也不证明故障发生时的内存量；未成功的新云端运行仍算失败。诊断不会更改本机或云端分页文件，也不会选择付费 runner。
