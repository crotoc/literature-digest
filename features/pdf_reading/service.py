"""features/pdf_reading：附件在线查看（内嵌 PDF 阅读器）。

组合 `domain/attachments`。本模块不开表，也不 import `caps/blobstore`——
`blob_store` 作为一个鸭子类型参数由调用方（`app/pages`）注入，和
`features/uploading` 的 `download_attachment` 同一个约定。

和 `features/uploading.download_attachment` 的区别：那边面向"原样取回
文件"（下载，`Content-Disposition: attachment`），不关心类型；这里要进
浏览器内嵌渲染（`Content-Disposition: inline`），必须先过一道可查看类型
的白名单——把一个 `.docx` 塞进 `<iframe>` 没有意义。
"""

from __future__ import annotations

from typing import BinaryIO

from domain.attachments import AttachmentDTO, get_attachment, list_attachments_for_work

VIEWABLE_CONTENT_TYPES = frozenset({"application/pdf"})


class NotViewable(ValueError):
    """附件存在，但类型不在内嵌阅读器的白名单里。"""

    def __init__(self, attachment_id: int, content_type: str | None) -> None:
        super().__init__(f"附件 {attachment_id} 的类型 {content_type!r} 不支持在线查看")
        self.attachment_id = attachment_id
        self.content_type = content_type


def get_main_attachment_for_view(db, work_id: int) -> AttachmentDTO | None:
    """这篇文献的 main 附件——内嵌阅读器默认打开的那个。没有就返回 `None`，
    不抛异常：一篇文献没有主附件是正常状态，不是错误。"""
    for attachment in list_attachments_for_work(db, work_id):
        if attachment.role == "main":
            return attachment
    return None


def open_for_view(db, attachment_id: int, *, blob_store) -> tuple[AttachmentDTO, BinaryIO]:
    """取附件元数据 + 打开它对应的字节流，供内嵌阅读器直接显示。

    本模块不把内容读进内存，流交给调用方自己决定怎么往 HTTP 响应里写
    （和 `uploading.download_attachment` 同一个约定）。

    Raises:
        NotViewable: `content_type` 不在 `VIEWABLE_CONTENT_TYPES` 里。
    """
    attachment = get_attachment(db, attachment_id)
    if attachment.content_type not in VIEWABLE_CONTENT_TYPES:
        raise NotViewable(attachment_id, attachment.content_type)
    return attachment, blob_store.open(attachment.digest)
