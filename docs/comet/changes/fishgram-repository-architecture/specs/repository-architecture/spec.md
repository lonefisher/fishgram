# FishGram 仓库架构完整规格

## 仓库与迁移
公开父仓库 lonefisher/fishgram 的 main 保存产品维护与构建工具、正式规格、可携带Comet状态和源码gitlink；公开源码fork lonefisher/tdesktop 的 custom/main 保存官方完整历史和审查后的定制源码与测试。源码fork的upstream指向telegramdesktop/tdesktop，origin指向自己的fork。父tdesktop子模块固定具体提交，递归子模块固定上游版本。先推源码提交，再提交和推父指针。
迁移前保存并验证原源码bundle、HEAD/worktree/index差异、新源码、五组测试、父工作文件及正常退出后运行数据快照。原含本机维护描述的文档分支只在本机及私有备份中保存；公开custom/main从官方基线和审查后的定制源码提交建立，不改写官方历史、不推送私有refs。
首次父初始化只建立最小Git入口以让Runtime隔离当前active搜索change。实现阶段在独立worktree，完成后在既有用户授权下整合到父main，不删除旧源码或运行数据。

## 文件归属与保密
定制规则只保存在父AGENTS.md，子模块AGENTS.md与对应官方版本完全一致。维护指南唯一正文在父docs/UPDATING.md，源码README链接产品父仓库并说明FishGram为基于Telegram API的非官方客户端。公开维护记录不包含账户、真实频道、查询原文、消息内容、窗口截图、个人绝对路径或运行PID。
父仓库保存Comet配置、正式规格和Runtime提供的可携带状态；本机.comet/runtime、锁、完整输出、代理设置、API密钥、tdata、日志、依赖、构建缓存、运行安装目录和打包暂存区不入Git。不得手改comet-state.yaml或verification.md。遗留规格中的本机路径作为历史来源保留私有副本，公开目标仅保留可移植路径；相关正式状态更新由Runtime处理。

## 工具与交付
配方固定产品FishGram、首期Windows11/x64/便携、官方v7.2.9提交fb2e33209517e1a34637d837bfadb3783f2fd59c、MSVC14.44、SDK10.0.26100.0与Qt5.15.19，并带官方版本、FishGram修订及渠道。工具定位VS工具链和Python/CMake/Ninja，允许未提交本机配置覆盖路径；不能把用户名、工具绝对路径或包版本写死。错误信息不能输出API hash或敏感命令行。实际构建凭据继续由本机DPAPI保存；外部发行凭据属于后续change。
干净包由构建产物复制到独立暂存目录，不能从登录后的安装目录打包。版本命名来自配方，已存在包/ZIP拒绝覆盖。生成包含父源码提交、fork提交、官方基线、递归子模块、工具链与每项公开产物SHA256的清单。白名单拒绝账户数据、私有配置、原始日志、路径穿越和未知附件；便携工作目录明确且与官方实例隔离。
所有实际生产helper测试均提交，移除无消费者的旧测试副本。根许可沿用GPLv3及上游OpenSSL例外，维护指南说明依赖许可、公开对应源码和更新签名职责。

## 边界与后续
保持restricted-global-search已有Build和八项验收，仅2/8已有Runtime通过，不伪造其余六项结论。r7本机客户端不标成FishGram正式发行，程序及账户不替换。FishGram默认Windows11 x64、免费资源优先、云端完整构建人工发布、自动创建上游候选、受保护云端签名、下次重启安装；云端发行和自动更新分别建立后续普通change。
公开安装包必须在原搜索全项验收、真正云端构建候选实测及更新/恢复安全链路通过后发布。未知或失败项目保持明确未完成。桌面验证保持后台Cua Driver，不升级到前台和真实鼠标。
