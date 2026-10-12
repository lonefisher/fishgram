# FishGram 应用身份

正式候选必须使用专属于 FishGram 的 Telegram 应用身份。现有本机凭据属于其他应用，不得复用；公开 CI 使用测试身份，产物不可发行。

在 [Telegram 应用管理页](https://my.telegram.org/apps) 建立或调整自己的应用，名称为 FishGram，并在描述和网站中明确基于 Telegram API 的非官方客户端身份。不要把 api_hash、验证码或账户信息发到聊天、日志或公开问题。具体流程以 [Telegram 官方申请说明](https://core.telegram.org/api/obtaining_api_id) 为准。

本机配置通过自己的终端执行，输入不会打印 API hash：

```powershell
./tools/set-fishgram-api.ps1 -ConfirmOwnApplication
```

凭据保存到被忽略的 `.private/fishgram-api.clixml`，以当前 Windows 用户 DPAPI 加密。换机器重新配置。旧的 `telegram-api.clixml` 不会被 FishGram 构建工具自动读取。

云端在 `candidate` 受保护环境人工配置 `FISHGRAM_API_ID` 和 `FISHGRAM_API_HASH`；仅审批后的候选构建任务可访问，PR 检查不可引用该环境。不要把含身份的客户端编译目录或 CMakeCache 保存到公共缓存或作为日志附件上传。

官方客户端的 `config.h` 使用编译宏提供 API ID/hash，FishGram 沿用此方式，因此程序二进制包含运行所需的应用身份，不能声称该身份无法从程序提取。这里的隔离要求是身份配置不进入 Git、源码归档、清单、缓存或原始日志；客户端运行所需的应用身份与必须保密的更新签名私钥分开管理。签名私钥及账户数据不得进入程序包。
