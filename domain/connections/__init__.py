"""domain/connections。详见 contract.py。"""

from domain.connections.contract import (
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
