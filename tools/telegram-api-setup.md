# 本机 API 身份

通过 Telegram 开发者页面为 FishGram 创建自己的应用身份。只在本机运行 `setup-telegram-api.ps1` 并输入，不在聊天或公开问题提供 api_hash。设置程序会显示窗口，用户正在使用电脑时不要自动运行。

凭据以当前 Windows 用户的 DPAPI 加密保存到 `.private/telegram-api.clixml`，不能跨用户或机器直接使用。新机器重新配置。

CI 产品候选从受保护环境读取 `FISHGRAM_API_ID` 与 `FISHGRAM_API_HASH`；PR 使用显式测试身份且不可发行。编译目录和日志可能包含身份，不上传、不缓存、不提交。

DPAPI 保护本机保存文件，不意味着发布后的 exe 无法提取 API 身份。
