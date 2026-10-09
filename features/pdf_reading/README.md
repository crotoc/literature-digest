# features/pdf_reading

附件在线查看（内嵌 PDF 阅读器）。

## 对外表面

- `VIEWABLE_CONTENT_TYPES`：当前只有 `application/pdf`
- `NotViewable(ValueError)`：附件存在但类型不在白名单里
- `get_main_attachment_for_view(db, work_id) -> AttachmentDTO | None`
- `open_for_view(db, attachment_id, *, blob_store) -> tuple[AttachmentDTO, BinaryIO]`

## 依赖方向

`domain/attachments`。**不 import `caps/blobstore`**——`blob_store` 作为
一个鸭子类型参数由调用方（`app/pages`）注入，和 `features/uploading` 的
`download_attachment` 同一个约定：本模块只调 `blob_store.open(digest)`，
不关心它具体是什么类，不需要为了一个类型注解去依赖 `caps/blobstore`。

## 和 `features/uploading.download_attachment` 的分工

两者都是"取附件元数据 + 打开字节流"，形状几乎一样，但目的不同：

| | `uploading.download_attachment` | `pdf_reading.open_for_view` |
|---|---|---|
| 场景 | 下载文件（`Content-Disposition: attachment`） | 内嵌阅读器渲染（`Content-Disposition: inline`） |
| 关心类型吗 | 不关心——下载什么都能下 | 关心——必须在 `VIEWABLE_CONTENT_TYPES` 白名单里 |

这是刻意的重复，不是漏看：features 之间禁止互相 import（lint 规则 4），
两边各自维护"打开附件流"这几行逻辑，但各自附加的业务规则完全不同，合并
成一个共享函数反而会把"要不要校验类型"这种调用方语境耦合进一个本该通用
的操作里。

## 为什么要校验 `content_type`

把一个 `.docx` 塞进浏览器的 `<iframe>` 里不会渲染出任何有意义的东西。
`open_for_view` 在打开字节流之前先检查 `attachment.content_type` 是否在
白名单里，不在（包括 `content_type is None`，比如附件创建时没传这个字段
的情况）就直接抛 `NotViewable`，让调用方决定展示什么提示（比如"这个文件
类型不支持在线预览，请下载查看"），而不是把一堆不可渲染的字节流发给
浏览器。

## `get_main_attachment_for_view`：没有主附件不是异常

一篇文献完全可以还没有上传任何 PDF，这是正常状态，所以这个函数在找不到
`role == "main"` 的附件时返回 `None`，不抛异常——调用方（通常是文献库
卡片页）据此决定是显示"打开 PDF"按钮还是"尚无附件"的占位提示。

## 刻意裁剪的范围

- **不负责分页/高亮/标注**：PDF 渲染本身交给前端的 PDF.js 之类的库，
  本模块只负责"把正确的字节流交出去"。
- **不做权限校验**：附件是否属于当前用户可见的库，是 `app/pages` 路由层
  的职责（和 `features/exporting`/`features/metadata_lookup` 同一个套路），
  本模块只信任传进来的 `attachment_id` 已经经过了那一层。
- **`caps/pdftext`（E6 阶段的 PDF 抽文本）不在这里**：那是给 AI 功能用的
  离线处理，和"把文件原样显示给人看"是两件事。
