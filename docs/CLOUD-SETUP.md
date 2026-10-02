# 云端配置与待完成条件

标准Windows2022 runner，固定Actions提交。`Cloud build`手动`cold=true`完成实际无缓存依赖准备及测试身份编译；随后`cold=false`验证缓存。PR没有产品API或签名身份。仅main的人工触发保存精确键依赖缓存，不消费PR保存的依赖。构建目录/API配置/原始日志/程序包均不上传；资源测量记录实际磁盘、目录规模和阶段时间。免费失败仍算失败。

`config/github-app-manifest.json`是升级App注册输入。注册后只安装到fishgram和tdesktop，将App ID保存为upgrade-automation环境变量`UPGRADE_APP_ID`，私钥作为环境secret `UPGRADE_APP_PRIVATE_KEY`，不写入Git。工作流每日运行，获取仅两仓库的短期安装token。脚本对写入endpoint和候选分支做白名单限制，不调用merge、Releases、Pages或更新索引。GitHub的contents写权限本身也涵盖release相关API；因此不能把脚本禁止发布误称为GitHub权限完全不具备发布能力。保护主分支和渠道分支、限制发行标签，并隔离人工发布环境仍是必要约束。

candidate与release环境要求人工批准，只有被审查固定提交的构建/签名任务可使用FishGram API及发行键。单维护者允许本人批准自己的执行；不允许机器人绕过批准。产品API身份、根/发行密钥及离线介质未完成前不标记受保护发行可用。

源码fork继承工作流当前整体禁用，避免官方发布/付费runner逻辑触发。需要审查删除继承的活动工作流并提供自有源码检查后再启用；未启用时不得声称源码PR检查有效。受保护分支要求对应CI通过；不能以禁用CI来绕过检查。

首次与重复候选、合并冲突、CI失败分别需要实际验证。单元策略检查不等于App注册/安装或真实PR成功。搜索完整验收、云端候选实测及签名恢复全部通过前，没有公开客户端与频道索引。
