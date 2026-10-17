# 账户快照与手动恢复

工具是 `tools/data_recovery.py`，只依赖 Python 标准库。候选 Updater 已接入跨官方基线替换前的原生快照回调；该接入本身不代表 Comet A7 或首次发行门槛通过。只在 Windows 上进行实际账户操作；开发测试使用合成数据。

命令行必须同时指定 `--installation-dir` 和该目录中的 `--installed-executable Telegram.exe`。三个入口共用程序名、普通文件、父目录文件身份及无链接校验，在取得会话锁或探测进程前拒绝错误目标。独立数据目录仍由 `--work-dir` 指定；工具不能猜测任意数据目录属于哪个安装，维护者须核对这两个目录的对应关系。Python 内部调用未指定 installation_dir 时使用已指定客户端的父目录，新增调用应显式传入安装目录以避免误选另一个客户端。

## 建立快照

先正常退出目标安装的全部客户端实例，并在工具完成前保持关闭。工具按实际安装 exe 的文件身份检查进程，拒绝正在运行或无法安全检查的匹配进程；复制后再次检查，并比较源和快照的完整内容清单。它不会退出或终止客户端，也不能从进程列表推断之前是正常退出还是崩溃。

快照、恢复和中断恢复会在安装目录的 `client-session.lock` 上取得与生产客户端/Updater 相同的独占 lease，并一直持有到操作结束。gate-aware 客户端使用共享 lease，因此会阻止快照或数据替换与客户端会话并行；进程检查仍保留，用于发现未接入 gate 的旧客户端。lease 不可用、ACL 不可信或锁忙时工具停止。

Python 首次初始化快照目录时，先在同目录的 `.partial-<32位随机ID>` 普通文件中写入 marker、flush 并 fsync，再以不覆盖目标的原子硬链接发布 `.fishgram-snapshots`；不支持硬链接时安全失败，不退回直接写最终 marker。目录链用无 share-delete 的 Windows 句柄持有，包含 LIST_DIRECTORY、READ_ATTRIBUTES 与 READ_CONTROL 权限；读取初始化文件也使用不跟随 reparse 的句柄。成功后只清理本次创建且文件身份读回一致的暂存，Windows 通过持有句柄删除该对象；异常残留不自动删除，其名字符合 native 快照根目录的 `.partial-<ID>` 白名单，且文件 ACL 与 marker 相同。无最终 marker 时，只接受私有 ACL 校验通过、严格命名的普通暂存文件，内容必须是完整 marker 的前缀（含写入前的空文件），并重新从新暂存文件发布。已有空白或损坏的最终 marker、未知内容、目录或链接继续拒绝并保留现场，不猜测其来源或自动覆盖。此规则仅覆盖 Python 初始化，不代表断电耐久性或真实账户恢复验收。

明确指定包含 `tdata` 的工作目录，不能只猜 exe 旁的路径。快照目录必须与工作目录互不包含，父目录须已存在。首次使用建议指定尚不存在的快照目录，让工具创建并设置私有 ACL；不要预先创建一个继承工作区权限的空目录。已有空目录只有通过私有 ACL 校验才可使用，工具不会自动改写其权限；之后只接受工具自身管理的目录。目录链接、junction、reparse point、路径穿越、缺少空间或源数据变化都会停止操作。普通 Windows 8.3 路径会规范化到实际目录。

```powershell
python tools/data_recovery.py snapshot --work-dir D:\FishGram\FishGramData --installation-dir D:\FishGram --installed-executable D:\FishGram\Telegram.exe --snapshot-root D:\FishGram-private-snapshots --version 7.2.9-r8
```

返回快照 ID。文件哈希、大小、完整版本、时间及目录结构记录在私有清单中；空目录也保留，文件修改时间复制到新文件。复制到全新暂存目录并验证后才完成快照，默认保留最近两份。只有工具管理且内容校验通过的旧快照才能清理。失败保留已有快照；清理失败可能暂时多于两份，需要排查后再操作。

进程被强制终止时不会执行 Python 的 finally，断电也可能留下含账户副本的 `.partial-<ID>` 目录。Python 的 snapshot 与 restore 在取得共用的快照根锁和工作目录锁后，若仍发现这种目录就拒绝继续，提示需要人工恢复；不新增副本、不清理旧快照，也不删除或恢复遗留内容。无论目录来自 Python、native、竞争操作或未知来源，都不凭名称判定所有权。该检查保留 native 的 partial 命名与白名单，正常已发布的 native 快照仍可读取、恢复；不改 native 的行为或宣称 native 已具备相同的拒绝检查。marker 初始化遗留的普通 partial 文件仍按前述内容前缀与 ACL 规则处理，不作为账户复制目录。

遇到此错误须保持目标客户端关闭，由维护者明确核对对应工作目录、已完成快照与遗留内容，再单独决定如何保全或处理；不要批量删除 partial 或将其作为完整快照恢复。`recover --confirm-restore` 只处理工作目录的恢复 journal，不会清理快照根的 partial，也不能解除此拒绝。工具未增加自动 partial 恢复或删除入口；仅在遗留目录已由明确的人工处理移出所选快照根后，才可重新运行。

Windows 最终发布目录改名遇到错误 5、32、33 时，最多尝试 11 次，等待总预算为 1 秒。仅这一处改名重试，不重新复制、写清单或执行恢复事务。每次尝试都重新校验原 canonical root/partial 的文件身份及无链接目录链，目标须不存在，并使用不覆盖目标的 Windows rename；路径被替换、目标出现或其他错误立即停止。持久锁最终失败时不清理旧快照；工具安全清理本次 partial，若锁仍阻止清理，会保留原失败及附加提示，不掩盖原错误。释放锁并排查后才可处理残留 partial，不应把残留目录当成完整快照。

快照包含可恢复登录的数据，内容没有额外加密。Windows ACL 按 native `TrustedPrincipals` 契约设置并逐 ACE、所有者及 DACL 保护位读回：普通令牌的当前用户可完全控制；提升令牌的当前用户仅有读取/执行权限；SYSTEM 与 Administrators 完全控制。当前用户完全控制时，所有者限当前用户、SYSTEM 或 Administrators；当前用户只读时，所有者限 SYSTEM 或 Administrators，避免当前用户通过 owner 隐含的 WRITE_DAC 改写 ACL。DACL 只能包含这三类允许 ACE，并校验目录继承标志和继承 ACE 标记；拒绝额外、拒绝型、未知类型或权限不匹配的 ACE。读取 native 提升快照时保留其只读 ACL，不尝试改写快照树；显式恢复只读取快照，并把恢复暂存与回滚数据写到调用者拥有的工作目录。清单 JSON 写入和读取均限制为 8 MiB，超限清单不会发布快照或触发旧快照清理。快照、清单及事务记录只放在本机私有目录，不提交 Git、贴到问题单或上传云端。控制台只显示状态和随机 ID，不输出账户内容或文件清单。

新对象的默认 owner 来自 TOKEN_OWNER，不能仅凭 TokenElevation 假定为 Administrators。Python `_private_acl` 在提升模式下显式设置 O:BA 与 OWNER_SECURITY_INFORMATION，并读回确认 Administrators owner；设置或读回失败即停止，不弹出 UAC。native 测试夹具采用相同的提升判断与显式 owner，并在创建子目录前读回 ACL。参见 [Windows 新对象所有者规则](https://learn.microsoft.com/en-us/windows/win32/secauthz/owner-of-a-new-object)。

数据操作锁始终锁定偏移 0 的 1 字节范围。已有锁文件不可写时以只读句柄打开；Python 不向空锁文件写入填充字节。Windows `_locking` 支持只读描述符以及超出 EOF 的锁范围，因此 0 字节文件也能互斥；native 自己建立的锁文件会初始化 1 字节。参见 [MSVC _locking](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170) 与 [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)。

## 明确选择数据恢复

先保存当前数据快照，并明确选择对应程序版本和数据快照。新版开始数据迁移后，旧 exe 不保证能读取当前数据；工具也不会证明选中的快照与程序版本兼容。恢复会丢弃目标账户目录中快照之后的状态，因此必须显式使用 `--confirm-restore`。

```powershell
python tools/data_recovery.py restore --work-dir D:\FishGram\FishGramData --installation-dir D:\FishGram --installed-executable D:\FishGram\Telegram.exe --snapshot-root D:\FishGram-private-snapshots --snapshot <快照ID> --confirm-restore
```

工具验证清单和全部内容，在建立恢复暂存区前先保存 schema 2 的 `allocating` 事务记录：同目录临时 JSON 写入、flush、fsync 后，以不覆盖已有 journal 的原子硬链接发布。建立私有暂存目录后，读回卷与目录文件身份并持久化 `copying` 状态，之后才复制账户字节；完整复制校验通过后进入 `prepared`。当前数据通过同工作目录中的重命名保留为 `.fishgram-data-rollback-<随机ID>`，完整恢复后仍保留，避免丢失恢复前的状态。该目录不是自动保留两份的快照，暂不自动清理。普通替换失败恢复原数据；不会修改 exe、客户端配置文件或其他工作目录内容。

普通清单哈希用于校验内容一致性，并不是发行签名或独立信任证明。能够以当前用户身份同时改写快照和清单的程序仍可能伪造一致内容；只从自己保管的私有快照恢复。

## 中断后的恢复

发生进程中断时，不要删除 `.fishgram-data-restore.json` 或手动启动客户端。正常关闭全部目标客户端后，明确选择处理该事务：

```powershell
python tools/data_recovery.py recover --work-dir D:\FishGram\FishGramData --installation-dir D:\FishGram --installed-executable D:\FishGram\Telegram.exe --confirm-restore
```

未完成事务恢复原数据；已提交事务核实新数据并完成清理。未知状态、损坏记录、缺少或不匹配的备份停止自动处理并保留现场。如果未完成替换时已有新 `tdata`，恢复会先把它保留为 `.fishgram-data-rejected-<随机ID>`，不会无声删除。

复制中或复制后退出时，journal 已包含本次 stage 的目录身份。显式 `recover` 先确认原 `tdata` 未改变且未出现 rollback，再处理初始化事务；不会把部分副本恢复成账户数据。仅清理身份匹配、ACL 可信、无 reparse 且内容属于所选清单的 stage：允许缺少未复制条目与尚未写完的短文件，完整长度的文件仍须匹配哈希。删除前持有并验证全部现有条目，Windows 通过 no-share-delete 句柄删除已验证对象，避免目录身份替换后误删。未知条目、错误类型、超长或完整内容不匹配、原数据变化均停止并保留 journal 与现场。恢复仍兼容旧 schema 1 事务。

若中断发生在 `allocating` 已持久化、stage 尚未建立时，显式恢复可核实原数据后清除空事务；若目录已建立但身份尚未持久化，则此时尚未复制账户字节，工具保留目录和 journal 并要求人工调查。预占同名目录也采用该拒绝策略，不猜测或删除其内容。没有 journal 的历史遗留 stage 不会被扫描或自动删除。

## 当前验证边界

本轮 snapshot 合成测试用独立子进程 `os._exit` 在文件复制中与完整复制返回后退出，分别读回短副本与完整副本，确认后续 snapshot/restore 拒绝且保留当前账户、全部原快照和 partial。另覆盖未知 partial 内容保全、根校验之后锁取得之前出现的竞争目录，以及 marker partial 普通文件仍兼容正常 snapshot/restore。这验证本机进程终止后的 fail-closed 行为，不模拟断电或证明自动恢复；native 本轮仅使用已有合成测试程序做互操作，没有重编译或改变其实现。

本轮合成回归以独立子进程 `os._exit` 分别中断实际 restore 的文件复制和完整复制返回，验证 journal 先于账户字节存在、原数据保全、再次 restore 拒绝绕过恢复、明确确认后仅清理本次 stage。另覆盖初始化发布失败与竞争 journal、mkdir 前中断、目录身份尚未持久化、同名目录竞争、未知内容、目录身份替换、reparse、原数据变化、普通 copy 失败、独立进程工作锁互斥、实际 Windows stage rename 返回错误 32，以及旧事务兼容。这些仅是合成数据与本机文件系统证据，不代表真实账户恢复或断电耐久性。

已用合成文件树验证两份保留、内容与空目录、实际 Windows 进程识别、复制期间重新启动、变化检测、空间不足、链接、重叠、篡改、路径穿越、显式恢复、替换失败和模拟中断恢复；ACL 用例覆盖普通完整权限、native 提升时用户只读、可信所有者、继承 ACE 与拒绝型/额外/错误权限/不可信所有者。新增真实文件测试用合成 ACL 阻止数据写入，即使测试令牌启用了管理员组也需以只读句柄打开；在空文件上取得 Python 锁后，另一进程的 Python 锁被阻止，直接 LockFileEx 返回 ERROR_LOCK_VIOLATION（33），释放后两者均能取得锁，文件仍为 0 字节。该 ACL 含测试专用 deny ACE，不是生产快照信任夹具。另以模拟 TokenElevation 加真实 Win32 安全描述符解析验证 Python 请求 Administrators owner，没有执行实际提升的 owner 修改。

子任务定向运行时，36 项数据测试中 native 互操作因未配置程序而跳过；主代理随后重新严格编译原生测试与 Updater，原生快照 DACL 读回、Python 显式恢复及全部 36 项数据测试通过，无 native 跳过。Windows gate 测试还确认共享客户端 lease 会阻止数据工具，并且独占 lease 覆盖操作期间的进程检查。账户数据从未用于这些测试。未接入 gate 的旧客户端仍由重复进程检查保护，无法获得 gate-aware 客户端提供的启动互斥。

[云端回归](https://github.com/lonefisher/tdesktop/actions/runs/37784975830)已实际读回 `elevated=1`，事务与 36 项 Python 数据测试通过，其中 native 互操作 1 项明确跳过。只读空锁也用真实受限 ACL 和两个独立进程验证互斥。此证据覆盖真实管理员 runner；仍没有真实 UAC 提示、提升快照与普通令牌跨权限恢复或真实账户验收。

真实账户跨官方基线迁移、Windows 11 实机运行与恢复、独立程序回退工具及断电耐久性仍待验证。此工具不替代更新器的程序事务，也不承诺只换旧 exe 就能安全降级。

2026-10-13 使用独立真实测试账号，在客户端正常退出且进程消失后建立私有快照，重新读取并校验快照内容与原 `tdata` 一致。快照再经生产恢复工具恢复到全新空私有工作目录：恢复后内容与快照一致、原账号目录未改变、事务记录已清理。用同一源码 `112a5e5585e9759ab358573bf8e0b7a34bfb6c02`、程序 SHA256 `C5EBD4582C88A06D4BDCE04915B54F87AC953D97CA8BED511723B65760EE7005` 启动恢复目录，实际进入已登录会话列表，无需重新登录；随后正常退出并核实进程消失。这仅证明本机同版本的真实账号快照与隔离恢复，不覆盖第二账号逐一检查、跨版本数据迁移、更新安装失败恢复、断电或干净 Windows 11 环境。

隔离 Windows 测试用真实 no-share-delete 目录句柄复现最终改名错误 32，并由另一线程在失败后延迟释放，验证发布重试成功、复制仅一次、旧快照保留。另覆盖持久锁预算与清理错误、目标竞争注入、root/partial 身份交换、reparse 替换和非相关错误不重试；5、33 的分类使用显式错误注入验证。此前完整 Python 测试两次随机错误 5 的占用来源仍未确定，现有证据不证明 Defender、DrivePool 或其他过滤器造成该失败。
