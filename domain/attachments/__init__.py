"""domain/attachments。详见 contract.py。"""

from domain.attachments.contract import (
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
