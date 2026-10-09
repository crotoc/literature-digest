"""domain/connections 的对外表面。外部只许 import 这里的东西，不碰 models.py。"""

from domain.connections.service import (
    KINDS,
    ConnectionDTO,
    ConnectionNotFound,
    clear_secret,
    create_connection,
    delete_connection,
    get_connection,
    get_decrypted_secret,
    list_connections,
    record_check_result,
    rotate_secret,
    set_default_connection,
    update_connection,
)

__all__ = [
    "KINDS",
    "ConnectionDTO",
    "ConnectionNotFound",
    "clear_secret",
    "create_connection",
    "delete_connection",
    "get_connection",
    "get_decrypted_secret",
    "list_connections",
    "record_check_result",
    "rotate_secret",
    "set_default_connection",
    "update_connection",
]
