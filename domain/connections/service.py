"""domain/connections 的业务逻辑：四类对外连接的统一 CRUD + 加密凭据的存取
+ 测试结果落库。

本模块不认识 `domain.accounts.Account`（跨 domain，`account_id` 是裸整数，
见 models.py 的说明），也不认识任何具体的"怎么测连接"的知识——测试连接的
实际动作是各 `adapters/*` 自己的 `check()`，经由 `caps/probe.run_check`
执行，本模块只提供落库测试结果的 `record_check_result`。

加解密复用 `caps/secrets`（domain → caps 是架构允许的依赖方向，和
`domain/works` 复用 `caps/bibformats.Person` 是同一类复用）。密钥本身从不
由本模块生成或保管——`secret_key` 永远由调用方传入，调用方从哪拿到这个
密钥（`.env` 口令派生还是别的）不是本模块的知识。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from caps.secrets import decrypt, encrypt
from domain.connections.models import Connection, _utcnow
from infra.errors import NotFound

KINDS = frozenset({"ai_profile", "telegram_destination", "source_credential", "download_proxy"})
_UNSET = object()


class ConnectionNotFound(NotFound):
    code = "connection_not_found"


@dataclass(frozen=True)
class ConnectionDTO:
    id: int
    account_id: int
    kind: str
    name: str
    enabled: bool
    is_default: bool
    config: dict
    has_secret: bool
    last_checked_at: datetime | None
    last_check_ok: bool | None
    last_check_message: str | None
    created_at: datetime
    updated_at: datetime


def _connection_dto(row: Connection) -> ConnectionDTO:
    return ConnectionDTO(
        id=row.id,
        account_id=row.account_id,
        kind=row.kind,
        name=row.name,
        enabled=row.enabled,
        is_default=row.is_default,
        config=row.config_json,
        has_secret=row.secret_ciphertext is not None,
        last_checked_at=row.last_checked_at,
        last_check_ok=row.last_check_ok,
        last_check_message=row.last_check_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_connection_row(db: Session, connection_id: int) -> Connection:
    row = db.get(Connection, connection_id)
    if row is None:
        raise ConnectionNotFound(f"连接不存在：{connection_id}")
    return row


def _demote_existing_default(db: Session, *, account_id: int, kind: str) -> None:
    current = db.scalar(
        select(Connection).where(
            Connection.account_id == account_id, Connection.kind == kind, Connection.is_default == True  # noqa: E712
        )
    )
    if current is not None:
        current.is_default = False


def create_connection(
    db: Session,
    *,
    account_id: int,
    kind: str,
    name: str,
    config: dict | None = None,
    secret_plain: str | None = None,
    secret_key: str | list[str] | None = None,
    enabled: bool = True,
    is_default: bool = False,
) -> ConnectionDTO:
    """新建一个连接。`secret_plain` 给了就必须一起给 `secret_key`（本模块
    不会凑合着不加密存明文）；`is_default=True` 时自动把这个账号在同一个
    `kind` 下原来的默认连接降级——和 `domain/attachments` 的"每篇文献只有
    一个 main"是同一个处理方式：有一个新的默认出现，旧的自动让位，不报冲突
    错误。
    """
    if kind not in KINDS:
        raise ValueError(f"kind 必须是 {sorted(KINDS)} 之一，收到 {kind!r}")
    name = name.strip()
    if not name:
        raise ValueError("name 不能为空")
    if secret_plain is not None and not secret_key:
        raise ValueError("给了 secret_plain 就必须一起给 secret_key")

    ciphertext = encrypt(secret_plain, key=secret_key) if secret_plain is not None else None

    if is_default:
        _demote_existing_default(db, account_id=account_id, kind=kind)

    row = Connection(
        account_id=account_id,
        kind=kind,
        name=name,
        enabled=enabled,
        is_default=is_default,
        config_json=config or {},
        secret_ciphertext=ciphertext,
    )
    db.add(row)
    db.flush()
    return _connection_dto(row)


def get_connection(db: Session, connection_id: int) -> ConnectionDTO:
    return _connection_dto(_get_connection_row(db, connection_id))


def list_connections(
    db: Session, *, account_id: int, kind: str | None = _UNSET, enabled_only: bool = False
) -> list[ConnectionDTO]:
    stmt = select(Connection).where(Connection.account_id == account_id)
    if kind is not _UNSET:
        stmt = stmt.where(Connection.kind == kind)
    if enabled_only:
        stmt = stmt.where(Connection.enabled == True)  # noqa: E712
    stmt = stmt.order_by(Connection.created_at)
    return [_connection_dto(row) for row in db.scalars(stmt)]


def update_connection(
    db: Session,
    connection_id: int,
    *,
    name: str = _UNSET,
    config: dict = _UNSET,
    enabled: bool = _UNSET,
) -> ConnectionDTO:
    """只更新显式传入的字段——哨兵用法和 `domain/works.update_work` 一致。
    改默认态走专门的 `set_default_connection`，不在这里（涉及同 kind 下别
    的行要联动降级，混进通用部分更新函数里容易漏判）。
    """
    row = _get_connection_row(db, connection_id)

    if name is not _UNSET:
        name = name.strip()
        if not name:
            raise ValueError("name 不能为空")
        row.name = name
    if config is not _UNSET:
        row.config_json = config
    if enabled is not _UNSET:
        row.enabled = enabled

    db.flush()
    return _connection_dto(row)


def set_default_connection(db: Session, connection_id: int) -> ConnectionDTO:
    row = _get_connection_row(db, connection_id)
    if not row.is_default:
        _demote_existing_default(db, account_id=row.account_id, kind=row.kind)
        row.is_default = True
        db.flush()
    return _connection_dto(row)


def delete_connection(db: Session, connection_id: int) -> None:
    row = _get_connection_row(db, connection_id)
    db.delete(row)
    db.flush()


def rotate_secret(
    db: Session, connection_id: int, *, secret_plain: str, secret_key: str | list[str]
) -> ConnectionDTO:
    """用户改了凭据（比如换了一个新的 API key）——整条重新加密，不是增量
    修改。"""
    row = _get_connection_row(db, connection_id)
    row.secret_ciphertext = encrypt(secret_plain, key=secret_key)
    db.flush()
    return _connection_dto(row)


def clear_secret(db: Session, connection_id: int) -> ConnectionDTO:
    """有些 kind（比如 download_proxy 的匿名代理）本来就可能没有凭据——
    显式清空而不是靠"忘了填"来表达"这个连接不需要密钥"。
    """
    row = _get_connection_row(db, connection_id)
    row.secret_ciphertext = None
    db.flush()
    return _connection_dto(row)


def get_decrypted_secret(db: Session, connection_id: int, *, secret_key: str | list[str]) -> str | None:
    """取出明文凭据，给真正要去登录第三方服务的 adapter 用。返回 `None`
    表示这个连接本来就没存密钥（不是解密失败——解密失败会是
    `caps.secrets.DecryptFailed` 异常，本模块不吞掉它）。
    """
    row = _get_connection_row(db, connection_id)
    if row.secret_ciphertext is None:
        return None
    return decrypt(row.secret_ciphertext, key=secret_key)


def record_check_result(db: Session, connection_id: int, *, ok: bool, message: str) -> ConnectionDTO:
    """落一次"测试连接"的结果。实际怎么测是 `adapters/*.check()` 的知识，
    经 `caps/probe.run_check` 执行后，调用方把 `ProbeResult` 的
    `ok`/`message` 转述给这个函数——本模块只负责存，不负责测。
    """
    row = _get_connection_row(db, connection_id)
    row.last_checked_at = _utcnow()
    row.last_check_ok = ok
    row.last_check_message = message
    db.flush()
    return _connection_dto(row)
