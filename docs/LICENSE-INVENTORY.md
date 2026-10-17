# 对应源码的许可证库存

导出、签名与发布共用 `tools/license_inventory.py`。库存按 `source-manifest.json` 的每个固定 Git 节点检查实际归档正文；完整结果留在本机私有记录。它帮助定位材料，不判断许可兼容性或代替人工审查。

## 官方正文与归档绑定

`config/license-sources.json` 保存固定官方提交、HTTPS raw 地址、UTF-8 正文、SHA256、适用外链与归档路径。导出器只读取已提交的配置，将必要正文写入 `LICENSES/official/`，并生成 `licenseSupplements` 及每个成员的字节摘要。许可库存验证这些绑定，发行 gate 再复核全部内容；不能只把外链或 SPDX 标签当作完整许可正文。

当前配置固定三份官方材料：

| 材料 | 官方提交 | SHA256 |
| --- | --- | --- |
| Desktop App Toolkit `LEGAL` | `81c3a0ebf04dca9ffc49c9a06a922fea34b01892` | `dc56f7e72b7a0306e68273008ff81c68c8fc2bb04bfb7833452139ded9af9b21` |
| Desktop App Toolkit `LICENSE` | 同上 | `41046d5acf6e70c370f0ba5e5af400f03d6f1ee639b81b90ae3d2770c4b718f7` |
| jQuery PowerTip `LICENSE.txt` | `0f7cd704c56d4618ea37c9a0166191e4700a5dde` | 以已提交配置的完整摘要为准 |

官方来源：[Toolkit LEGAL](https://github.com/desktop-app/legal/blob/81c3a0ebf04dca9ffc49c9a06a922fea34b01892/LEGAL)、[Toolkit LICENSE](https://github.com/desktop-app/legal/blob/81c3a0ebf04dca9ffc49c9a06a922fea34b01892/LICENSE)、[PowerTip LICENSE](https://github.com/stevenbenner/jquery-powertip/blob/0f7cd704c56d4618ea37c9a0166191e4700a5dde/LICENSE.txt)。

## 操作

先按[源码导出](SOURCE-EXPORT.md)生成固定提交 ZIP，然后检查：

```powershell
python tools/license_inventory.py --archive C:\path\to\staging\fishgram-source.zip --config config/license-sources.json --pre-export
```

未解决节点、缺正文、摘要不符、重复/不安全条目、未绑定的官方补充材料均停止发行。完整源码头中的 MIT 等正文可以作为已归档材料；只有版权行、简短声明或链接仍会阻止通过。新增或升级依赖后重新导出、核对结果；不得沿用旧归档的结论。

## 历史调查

父 `b7efe4e6ff4db6096fd4687c370963bf4a5e4f88` / 源 `04a73a1464d3ef31bdd4f98208ad377eabc115f5` 的私有固定 ZIP 含 41 个 Git 节点、15,761 个文件，SHA256 `3dd49d0b10efa6e39164375db32c02bb143f3bc4b412573949c1494d9229da37`。该归档尚无新许可配置和补充正文；当时发现 16 个未解决节点，不能通过当前发行 gate。历史 ZIP 保留原字节，不覆盖为新候选。

库存、导出与 gate 的合成测试通过不代表产品固定源码归档或完整发行验收通过。新候选的实际提交、归档摘要与库存结果须单独记录。
