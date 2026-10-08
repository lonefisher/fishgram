# FishGram 签名密钥维护

本工具只管理 FishGram 自己的 v2 更新信任链，不能使用 Telegram 官方清单、根公钥或密钥。它使用系统 OpenSSL 命令行和 Python 标准库；不依赖 `cryptography`。执行前应在独立的离线维护环境中确认 `openssl version`，建议使用 OpenSSL 3 或更新版本。可通过 `PATH` 或 `FISHGRAM_OPENSSL` 选择 OpenSSL，不要把私钥、口令或生产密钥放入命令行参数、环境变量、日志或 Git。

口令通过 `getpass` 隐藏输入，再经标准输入传给 OpenSSL；拒绝 CR、LF 和 NUL。所有输出父目录必须预先存在，并拒绝符号链接或 Windows reparse point 父目录。Windows 私钥 ACL 设置失败会中止操作并删除残留私钥；新文件不会覆盖已有文件。

## 初次建立根密钥

先人工准备并确认根密钥目录和离线介质已经存在。工具不会创建用户指定的外部备份目录，也不会覆盖已存在文件。

```powershell
python tools/key_management.py init-root `
  --private D:\FishGram-Offline\root-private.pem `
  --public D:\FishGram-Offline\root-public.pem
```

工具通过隐藏输入提示两次输入至少 12 字符的口令。根私钥以 OpenSSL 加密 PKCS#8 PEM 写入；不会落盘明文根私钥。根公钥 PEM 可公开，并应与 FishGram 客户端固定的 FishGram 专属根公钥进行独立比对。可用 `--backup` 同时复制一份加密根私钥到已存在目录。

必须保有至少一份与联网电脑分离的离线加密根私钥副本。可再将同一加密副本放入 `<云端加密备份目录>`，但云端副本不能取代离线副本。口令应保存在单独管理的密码库中，不能和密文一起同步；注意云盘删除/同步传播风险，确认保留策略并定期检查离线副本可读。工具不会自动创建这个目录或写入正式密钥。

## 发行密钥

```powershell
python tools/key_management.py new-issuer --id release-2026a `
  --private D:\FishGram-Protected\release-2026a-private.pem `
  --public D:\FishGram-Protected\release-2026a-public.pem
```

发行私钥同样加密保存、口令交互输入，并尽量只在受保护的发行环境使用；在 Windows 上工具将私钥文件 ACL 限定为当前用户，在 POSIX 上设为 `0600`。公钥与创建时间后的一年有效期需加入根签名清单。交付工具输出的公钥只用于发布流程，私钥绝不能打印、上传到普通 CI、提交或复制到共享目录。建立根/发行密钥及备份时不要使用正式密钥做测试。

## 创建第一份签名清单

首次部署没有上一份清单，使用 `init-manifest` 从现有根密钥和发行公钥引导版本 1：

```powershell
python tools/key_management.py init-manifest `
  --root-private D:\FishGram-Offline\root-private.pem `
  --root-public D:\FishGram-Offline\root-public.pem `
  --issuer-public D:\FishGram-Protected\release-2026a-public.pem `
  --issuer-id release-2026a `
  --output .\keys\manifest.min.json `
  --signature-output .\keys\manifest.sig `
  --public-fixture-dir .\keys\public-fixture
```

工具交互读取根口令，先确认根私钥解出的公钥与传入根公钥一致，再验证发行公钥确为 Ed25519；生成 `manifest_version: 1`，把同一发行键授权给 `stable` 和 `beta`，并设置发行键及清单一年有效期。生成后用根公钥实际验证 detached 签名。输出目录和可选 fixture 目录必须事先存在且不能含同名文件。`--public-fixture-dir` 只导出根公钥、发行公钥、清单及签名，不复制私钥，可供 `Core::Updates::ParseVerifiedManifest` 验证测试使用。后续所有变更再通过 `update-manifest` 递增版本。

## 续期、轮换与吊销

`update-manifest` 必须同时提供上一份清单和签名；工具先用传入的 FishGram 根公钥验证旧签名，并要求新 `manifest_version` 严格递增。它还会检查加密根私钥是否与受信根公钥匹配，再以紧凑 UTF-8 JSON 的原始字节生成 detached Ed25519 签名。不要重排或改写签名后的 JSON。

增加新发行公钥：

```powershell
python tools/key_management.py update-manifest `
  --previous .\manifest.min.json --previous-signature .\manifest.sig `
  --root-public D:\FishGram-Offline\root-public.pem `
  --root-private D:\FishGram-Offline\root-private.pem --version 3 `
  --add-key release-2027a=D:\FishGram-Protected\release-2027a-public.pem `
  --channel-group stable=release-2026a,release-2027a `
  --channel-group beta=release-2026a,release-2027a `
  --output .\next\manifest.min.json `
  --signature-output .\next\manifest.sig
```

每个 `--channel-group CHANNEL=ID[,ID]` 表示一个 OR 组；同一渠道传入多次表示这些组都必须分别满足。未指定的渠道保留旧授权。续期已存在且未吊销的密钥时传 `--renew-key ID`，有效期从本次签发时间延长一年。吊销用 `--revoke-key ID`；工具会把 ID 加入 `revoked` 并从渠道组删除。若吊销会留下空的必需组，操作失败，调用者须在同次更新中提供新的 `--channel-group` 授权。根签名清单只负责授权发行密钥，不签客户端更新包。

清单兼容 `tdesktop/Telegram/SourceFiles/core/update_verify.h/.cpp` 的 `Core::Updates::ParseVerifiedManifest`：顶层字段 `format=1`、uint32 `manifest_version`、秒级整数 `issued`/`expires`、`keys`、`channels`、`revoked`；Ed25519 公钥字段是 `alg="Ed25519"` 和 32 字节 `x` 的无填充 Base64URL。渠道组遵循 AND-of-ORs 规则。根签名覆盖清单确切字节。该客户端解析器最多允许 256 KiB、64 keys、16 channels、每渠道 8 groups、每组 16 IDs 和 64 revoked IDs。正式操作仍须由发行集成和客户端验收核对。

## 45 天到期检查

```powershell
python tools/key_management.py check `
  --manifest .\manifest.min.json --signature .\manifest.sig `
  --root-public D:\FishGram-Offline\root-public.pem
```

工具先验证所给根签名，再列出 45 天内到期及已过期的发行密钥 ID 和剩余整天数，不读取或输出私钥。该命令是本地检查器；需由维护排程定期运行。到期前完成新发行密钥、递增根签名清单以及发行系统切换验证。工具不会上传密钥或修改云端索引。

## 限制和恢复

此工具只准备本地可审查文件，不创建云端备份目录、不写生产根密钥、不上传 secret、不发布清单，也不替代实际客户端验签验证。根私钥丢失或疑似泄漏时，不可声称可通过旧在线信任链安全换根；停止索引推广，调查并通过单独批准的手动安装恢复流程处理。成功生成测试签名不等于 Windows 客户端、Packer、Pages 发布或密钥恢复链路已验收。
