"""domain/attachments 的对外表面。外部只许 import 这里的东西，不碰 models.py。"""

from domain.attachments.service import (
    DEFAULT_ROLE,
    ROLES,
    AttachmentDTO,
    AttachmentNotFound,
    count_references,
    create_attachment,
    delete_attachment,
    get_attachment,
    list_attachments_for_work,
    set_main_attachment,
)

__all__ = [
    "DEFAULT_ROLE",
    "ROLES",
    "AttachmentDTO",
    "AttachmentNotFound",
    "count_references",
    "create_attachment",
    "delete_attachment",
    "get_attachment",
    "list_attachments_for_work",
    "set_main_attachment",
]
