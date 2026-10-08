# FishGram 对应源码导出

`tools/export_source.py` 从给定父仓库提交和每个固定 gitlink 读取 Git 对象，生成 ZIP 与 `source-manifest.json`。未提交、未跟踪和忽略文件不进入归档。父提交和递归子模块必须在本机可读，且各工作树 HEAD 必须等于对应 pin；不匹配时工具停止。

在已有的私有暂存目录中运行：

```powershell
$root = (Resolve-Path .).Path
$commit = (git -C $root rev-parse HEAD).Trim()
python tools/export_source.py --root $root --parent-commit $commit --output C:\path\to\staging\fishgram-source.zip
```

`--parent-commit` 明确锁定父仓库输入，命令不会把当前未提交修改打包。输出文件必须尚不存在，且其父目录必须已存在。运行前应确认所有递归子模块已检出到固定提交；不要通过更改 gitlink 来规避缺失或不匹配。

归档包含递归固定源码、构建脚本及提交中的许可证文件；不得删减 GPLv3、OpenSSL 例外或依赖许可证。symlink 以 ZIP Unix symlink 条目保存，内容仅是经过包内相对路径检查的链接文本，不读取目标文件。工具拒绝凭据、`.private`、`tdata` 和父维护仓库的私有运行目录；递归依赖中的 `dist`、`logs`、`packages`、`runtime` 等同名源码目录仍从 Git 对象导出。

导出器从父提交读取 `config/license-sources.json` 的 Git blob，验证固定官方提交、下载地址、正文和 SHA256，将必要的外链许可正文写到 `LICENSES/official/`。工作区未提交的配置不参与导出。`source-manifest.json` 的 `licenseSupplements` 记录这些补充正文的来源和摘要，`files` 同时记录其实际字节。

发行门槛 `release_gate.verify_source_archive` 核对所有成员、提交和补充许可正文；只剩外链、缺正文、配置缺失或来源与 manifest 不一致均拒绝签名。库存工具提供逐 Git 节点的证据定位，不能替代依赖适用范围和许可兼容性的人工审查。详见[许可证库存](LICENSE-INVENTORY.md)。

导出后应检查 ZIP 文件清单、`source-manifest.json` 的提交与文件哈希，并确认 GPLv3、OpenSSL 例外及每项递归依赖所需许可证均在归档中。合成 fixture 测试通过只证明工具路径，不代表当前 FishGram 固定候选 ZIP 已成功生成或许可证审查、实机发行验收已完成。
