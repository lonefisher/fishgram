# 账户快照与手动恢复

工具是 `tools/data_recovery.py`，只依赖 Python 标准库。候选 Updater 已接入跨官方基线替换前的原生快照回调；该接入本身不代表 Comet A7 或首次发行门槛通过。只在 Windows 上进行实际账户操作；开发测试使用合成数据。

## 建立快照

先正常退出目标安装的全部客户端实例，并在工具完成前保持关闭。工具按实际安装 exe 的文件身份检查进程，拒绝正在运行或无法安全检查的匹配进程；复制后再次检查，并比较源和快照的完整内容清单。它不会退出或终止客户端，也不能从进程列表推断之前是正常退出还是崩溃。

快照、恢复和中断恢复会在安装目录的 `client-session.lock` 上取得与生产客户端/Updater 相同的独占 lease，并一直持有到操作结束。gate-aware 客户端使用共享 lease，因此会阻止快照或数据替换与客户端会话并行；进程检查仍保留，用于发现未接入 gate 的旧客户端。lease 不可用、ACL 不可信或锁忙时工具停止。

明确指定包含 `tdata` 的工作目录，不能只猜 exe 旁的路径。快照目录必须与工作目录互不包含，父目录须已存在，首次使用应为空；之后只接受工具自身管理的目录。目录链接、junction、reparse point、路径穿越、缺少空间或源数据变化都会停止操作。普通 Windows 8.3 路径会规范化到实际目录。

```powershell
python tools/data_recovery.py snapshot --work-dir D:\FishGram\FishGramData --installed-executable D:\FishGram\Telegram.exe --snapshot-root D:\FishGram-private-snapshots --version 7.2.9-r8
```

返回快照 ID。文件哈希、大小、完整版本、时间及目录结构记录在私有清单中；空目录也保留，文件修改时间复制到新文件。复制到全新暂存目录并验证后才完成快照，默认保留最近两份。只有工具管理且内容校验通过的旧快照才能清理。失败保留已有快照；清理失败可能暂时多于两份，需要排查后再操作。

Windows 最终发布目录改名遇到错误 5、32、33 时，最多尝试 11 次，等待总预算为 1 秒。仅这一处改名重试，不重新复制、写清单或执行恢复事务。每次尝试都重新校验原 canonical root/partial 的文件身份及无链接目录链，目标须不存在，并使用不覆盖目标的 Windows rename；路径被替换、目标出现或其他错误立即停止。持久锁最终失败时不清理旧快照；工具安全清理本次 partial，若锁仍阻止清理，会保留原失败及附加提示，不掩盖原错误。释放锁并排查后才可处理残留 partial，不应把残留目录当成完整快照。

快照包含可恢复登录的数据，内容没有额外加密。工具设置并逐 ACE 读回 Windows 保护 DACL，核对当前用户、SYSTEM 和 Administrators 的 SID、允许类型、完整权限掩码及目录继承范围，并拒绝额外或不匹配的 ACE；不保留原始 ACL。清单 JSON 写入和读取均限制为 8 MiB，超限清单不会发布快照或触发旧快照清理。快照、清单及事务记录只放在被忽略的本机私有目录，不提交 Git、贴到问题单或上传云端。控制台只显示状态和随机 ID，不输出账户内容或文件清单。

## 明确选择数据恢复

先保存当前数据快照，并明确选择对应程序版本和数据快照。新版开始数据迁移后，旧 exe 不保证能读取当前数据；工具也不会证明选中的快照与程序版本兼容。恢复会丢弃目标账户目录中快照之后的状态，因此必须显式使用 `--confirm-restore`。

```powershell
python tools/data_recovery.py restore --work-dir D:\FishGram\FishGramData --installed-executable D:\FishGram\Telegram.exe --snapshot-root D:\FishGram-private-snapshots --snapshot <快照ID> --confirm-restore
```

工具验证清单和全部内容，再建立私有恢复暂存区；在改动 `tdata` 前保存持久事务记录。当前数据通过同工作目录中的重命名保留为 `.fishgram-data-rollback-<随机ID>`，完整恢复后仍保留，避免丢失恢复前的状态。该目录不是自动保留两份的快照，暂不自动清理。普通替换失败恢复原数据；不会修改 exe、客户端配置文件或其他工作目录内容。

普通清单哈希用于校验内容一致性，并不是发行签名或独立信任证明。能够以当前用户身份同时改写快照和清单的程序仍可能伪造一致内容；只从自己保管的私有快照恢复。

## 中断后的恢复

发生进程中断时，不要删除 `.fishgram-data-restore.json` 或手动启动客户端。正常关闭全部目标客户端后，明确选择处理该事务：

```powershell
python tools/data_recovery.py recover --work-dir D:\FishGram\FishGramData --installed-executable D:\FishGram\Telegram.exe --confirm-restore
```

未完成事务恢复原数据；已提交事务核实新数据并完成清理。未知状态、损坏记录、缺少或不匹配的备份停止自动处理并保留现场。如果未完成替换时已有新 `tdata`，恢复会先把它保留为 `.fishgram-data-rejected-<随机ID>`，不会无声删除。

## 当前验证边界

已用合成文件树验证两份保留、内容与空目录、实际 Windows 进程识别、复制期间重新启动、变化检测、空间不足、链接、重叠、篡改、路径穿越、显式恢复、替换失败和模拟中断恢复；Windows gate 测试还确认共享客户端 lease 会阻止数据工具，并且独占 lease 覆盖操作期间的进程检查。账户数据从未用于这些测试。未接入 gate 的旧客户端仍由重复进程检查保护，无法获得 gate-aware 客户端提供的启动互斥；Windows 11 实际账户迁移及自动更新完整验收仍待单独完成。

真实账户跨官方基线迁移、Windows 11 实机运行与恢复、独立程序回退工具及断电耐久性仍待验证。此工具不替代更新器的程序事务，也不承诺只换旧 exe 就能安全降级。

隔离 Windows 测试用真实 no-share-delete 目录句柄复现最终改名错误 32，并由另一线程在失败后延迟释放，验证发布重试成功、复制仅一次、旧快照保留。另覆盖持久锁预算与清理错误、目标竞争注入、root/partial 身份交换、reparse 替换和非相关错误不重试；5、33 的分类使用显式错误注入验证。此前完整 Python 测试两次随机错误 5 的占用来源仍未确定，现有证据不证明 Defender、DrivePool 或其他过滤器造成该失败。
