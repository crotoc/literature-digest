# caps/fileprobe

按**内容**识别文件类型，外加 PDF 专项探查。无表、无网络，单测不建库。

## 对外承诺

```python
from caps.fileprobe import sniff, probe_pdf, check_upload

sniff(data).media_type          # "application/pdf" / "image/png" / "text/plain" / ...
sniff(data).kind                # pdf | image | text | archive | document | unknown
sniff(data).extension           # ".pdf"；认不出是空串
sniff(data).size                # 字节数

sniff(data, deep=True).pdf      # PdfInfo(version, encrypted, page_count, has_text_layer, damaged)
probe_pdf(data)                 # 只做 PDF 探查；内容不是 PDF 返回 None（不抛异常）

check_upload(data, max_bytes=50 << 20,
             allowed_media_types={"application/pdf"},
             reject_damaged=True)   # 通过返回 FileProbe，否则抛下面三种异常
```

异常：`FileTooLarge` / `UnsupportedMediaType` / `CorruptFile`，共同基类 `FileProbeError`。

### 三条硬保证

1. **不看文件名、不信客户端给的 Content-Type**。那两样都是上传者可控的，
   信了就等于把类型判定权交给上传者。只认字节。
   （单测里有 `actually_a_pdf.txt` 和 `fake.pdf` 两个反向用例盯着这条。）
2. **判定与策略分离**。大小上限、类型白名单、损坏是否算失败，全部由调用方作为参数
   传进 `check_upload`——和 `jobs.transition(allowed=...)` 同一个套路，策略当数据传，
   cap 里不内置任何业务规则。
3. **三种入口行为一致**。bytes / 路径 / 可 seek 的流给出完全相同的 `FileProbe`。
   不可 seek 的流**直接报错**，让调用方自己决定怎么缓冲，而不是在这里偷偷读进内存。
   传进来的流不会被本模块关闭。

### 几个刻意的取舍

- `deep` 默认 **关**。它要真解析一遍文件，比看魔数贵得多；多数调用只想知道"这是什么"。
- 认不出来就是 `application/octet-stream` + 空扩展名 + `unknown`，**不猜、不回退到扩展名**。
- 空口令加密的 PDF（只为限制打印，内容照样能读）很常见：会 `decrypt("")` 试一下继续读，
  但 `encrypted` 仍然如实报 `True`。
- 读不出来的字段给 `None` 而不是 0 或 False——"0 页"和"数不出页数"是两件事。
- `check_upload` **先查大小**再查类型，否则一个超大文件会被白白解析一遍。

## 依赖方向

标准库（`io` `os` `zipfile` `pathlib`）+ `pypdf`。**不依赖任何 infra / domain / adapters / features。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 算 sha256 / 内容寻址 | `caps/blobstore`（内容寻址是它的本职，不在两处各算一遍） |
| 抽 PDF 全文 + 分块 | `caps/pdftext`（E6）。这里只回答"有没有文本层"这个布尔问题，只看前 3 页 |
| 识别 RIS / BibTeX / CSL-JSON 是哪一种 | `caps/bibformats`（它认识自己的格式）。这里到 `text/plain` 为止 |
| `rel_path` 路径穿越防护 | `features/uploading`（目录上传时才有 `rel_path` 这个概念） |
| 重名策略 ask / overwrite / rename | `features/uploading`（要回头调模板、要回 UI，是编排不是能力） |
| 病毒扫描 | 不在范围内 |
