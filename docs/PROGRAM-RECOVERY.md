# FishGram 手动程序回退

`tools/program_recovery.py` 是独立的 Windows 手动程序恢复入口。它只读取或替换安装目录顶层的 `Telegram.exe`、`Updater.exe` 和 DLL，不读取、复制或修改 `tdata`、工作目录或更新索引。

先列出备份：

```powershell
python .\tools\program_recovery.py --install "C:\Apps\FishGram"
```

恢复前先核对备份 ID，再显式确认同一个 ID：

```powershell
python .\tools\program_recovery.py --install "C:\Apps\FishGram" `
  --confirm-program-restore "0123456789abcdef-00000001-0123456789abcdef"
```

列表操作只读。恢复要求 FishGram 正常退出；工具按目标 exe 的完整路径检查进程，并按生产顺序持有 `.fishgram-update\install.lock` 与 `install\client-session.lock` 的独占 lease。Client gate lease 使用 `LockFileEx(LOCKFILE_FAIL_IMMEDIATELY | LOCKFILE_EXCLUSIVE_LOCK, offset=0, length=1)`；整个恢复期间同时保留从盘符根到安装目录的目录句柄，权限为 `FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY | READ_CONTROL`，共享读写但不共享删除。`FILE_LIST_DIRECTORY` 使 Windows 对目录改名执行 share-delete 冲突检查；目录内新建子项和写入仍被允许，而且多个进程可同时持有这样的 guard。session lock 使用 gate 同款当前用户、SYSTEM、Administrators 受保护 ACL，并验证 owner、全部允许 ACE、普通文件属性及 reparse 标志。共享 client lease 存在时立即拒绝恢复。非 Windows、任一锁忙/无法验证、目录交换或 reparse point、路径/文件清单异常、空间不足或程序仍运行时都会拒绝继续。

每次恢复先把当前所有程序 exe/DLL 完整复制到 `.fishgram-update\manual\latestmanualbackup`，并以持久事务保存原件。替换中断时会从事务原件回滚；下次运行会先处理遗留事务。自动回滚也失败时，保留事务现场并停止，不能清理该目录后重试。

生产事务的 `versions/<backupid>` 是原程序文件目录。生产代码将 `pending/backup` 改名为该目录，然后删除 `pending`，因此已发布 archive 本身没有 `journal.bin`，也没有 `oldbaseversion`。列表只能给出 backup ID 和文件名；“记录目标版本”与“前一程序版本”都显示未知，不能把新安装事务的 `journal.version` 当成旧程序版本。即使某个外部备份附带 journal，也不会用它把记录目标版本冒充前一程序版本。

这个工具从不自动恢复账户数据。需要恢复 `tdata` 时，须另行明确选择数据恢复工具和快照；本工具不操作它们。程序回退也不修改签名索引或在线版本号。

## 验证边界

`tests/test_program_recovery.py` 覆盖只读列表、生产 journal 字段语义、显式确认、旧程序恢复、失败复制回滚、updater 锁互斥、共享 client lease 阻断与释放后恢复、安装路径和 session lock reparse 拒绝、空间不足、运行中拒绝、路径遍历及账号文件拒绝。归档测试对普通安装路径 ACL 检查做隔离；client-session.lock 会实际创建 gate 同款 ACL 并验证 owner/DACL。真实安装目录恢复仍需在匹配权限的 Windows 安装环境单独验收。测试不接触真实 client 或 tdata。
