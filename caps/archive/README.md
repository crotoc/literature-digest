# caps/archive

流式 ZIP 写出。无表、无网络，单测不碰磁盘（全部在内存 `BytesIO` 里拼）。

## 为什么是 cap：两个用的场景

- `features/exporting`：RIS/BibTeX + PDF 打成一个 ZIP 发给用户
- `features/extension_dist`：动态打包浏览器扩展成 Chrome 可加载的 ZIP

两者都要"把若干条目写进一个 ZIP，边写边流出去，不在内存里攒出整份结果"，
所以下沉成一个薄 cap，不各写一遍。

## "流式"具体指什么

- **写入方**：调用方给一个可迭代的条目序列（可以是生成器），本模块边读边写。
  附件 ZIP 可能几百 MB，不要求先把所有内容读进内存再传进来。
- **输出方**：`write_zip_to` 写到调用方给的任意可写流（文件、`BytesIO`、
  HTTP 响应体），本模块不关心那是什么、最终去哪，也不会 close 它。

## 对外承诺

```python
from caps.archive import write_zip_to, entry, sanitize_entry_name, iter_zip_entries

count = write_zip_to(destination, [
    entry("2019/smith_paper.pdf", pdf_bytes),
    entry("export.ris", ris_text.encode(), mtime=some_datetime),
    entry("big.bin", blobstore.open(digest)),   # 可读流，边读边写
])

sanitize_entry_name(raw_name)       # 规范化并校验一个条目名；不安全就抛异常
iter_zip_entries(zip_bytes)         # 列出 (名字, 大小)，不解压内容——给核对打出来的包用
```

异常：`UnsafeEntryName` / `DuplicateEntryName`，共同基类 `ArchiveError`。

### ⚠️ 路径安全是这里真实的风险点

ZIP 里的条目名**完全由调用方决定**——文件名来自文献标题、作者名，这些是
用户可控的输入。不做任何约束的话，`../../../etc/passwd` 这种名字会在
解压时造成路径穿越。

本模块的职责边界很窄：**只保证写进去的条目名干净**，不负责解压那一侧的防护
（那可能是另一个系统）。但"写的时候就把毒丢掉"好过"指望以后所有读的人都记得防"。
单测 `TestWriteZipUnsafeNames` 和 `TestSanitizeEntryName` 的 `..` 系列用例盯着这条。

`sanitize_entry_name` 的具体规则：统一用 `/` 分隔（`\` 转换）、去掉开头的 `/`、
拒绝 `..` 路径段、拒绝控制字符和 Windows 保留字符（`<>:"|?*`）。

### 重名策略

`on_duplicate`：
- `"raise"`（默认）：撞名抛 `DuplicateEntryName`
- `"rename"`：自动加 `(2)` `(3)` 后缀（保留扩展名：`a.pdf` → `a (2).pdf`）

重复检测发生在**规范化之后**——`a.pdf` 和 `/a.pdf` 规范化后是同一个名字，
必须被当成重复，不能靠字符串原文比较漏过去。

**这不是 `features/uploading` 的重名策略。** 那边的 ask/overwrite/rename
是对着数据库记录做的、要回 UI 问用户；这里是对着**一次性生成的 ZIP**内部，
纯粹是"同一个包里不能有两个同名条目"这条格式约束。

## 依赖方向

标准库（`zipfile` `io` `re` `datetime`）。
**不依赖任何 infra / domain / adapters / features，也不依赖别的 cap。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 读 ZIP 内容（解压) | v1 不需要；`iter_zip_entries` 只列名字和大小，不读内容 |
| 决定 ZIP 里该放哪些文件 | `features/exporting` / `features/extension_dist` |
| 加密 ZIP | v1 不做 |
| 分卷压缩 | v1 不做 |
| 对已存在的同名实体文件做 ask/overwrite/rename | `features/uploading`（对数据库记录，要回 UI；这里只是 ZIP 内部格式约束） |
| tar / 7z 等其他格式 | 用不到 |
