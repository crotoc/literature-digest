"""domain/settings 的业务逻辑：站点级 + 账号级设置，三级回退（账号 → 站点 →
代码默认值）。

本模块不知道、也不校验任何具体设置项该是什么类型、取值范围是什么——每个
`features/<x>` 在自己的 `contract.py` 里声明设置表单（字段/类型/默认值/
归属层级/所属分区），`app/pages/settings` 把这些声明拼成设置页。本模块只提供
存取和三级回退这一个通用机制，`value_json` 可以是任意 JSON 可序列化的值。

"代码默认值"这一级根本不落库——它就是调用 `resolve_setting()` 时传入的
`default` 参数，由调用方（通常就是声明这个设置项的那个 feature）提供。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.settings.models import AccountSetting, SiteSetting

_UNSET = object()


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SiteSettingDTO:
    id: int
    module: str
    key: str
    value: object
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AccountSettingDTO:
    id: int
    account_id: int
    module: str
    key: str
    value: object
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SettingResolution:
    """`resolve_setting()` 的返回值——除了值本身，还带上这个值来自哪一级，
    方便设置页显示"当前这是账号覆盖值还是站点默认值"这类 UI 提示。"""

    value: object
    source: str  # "account" | "site" | "default"


def _site_dto(row: SiteSetting) -> SiteSettingDTO:
    return SiteSettingDTO(
        id=row.id,
        module=row.module,
        key=row.key,
        value=row.value_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _account_dto(row: AccountSetting) -> AccountSettingDTO:
    return AccountSettingDTO(
        id=row.id,
        account_id=row.account_id,
        module=row.module,
        key=row.key,
        value=row.value_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _find_site_row(db: Session, module: str, key: str) -> SiteSetting | None:
    return db.scalars(
        select(SiteSetting).where(SiteSetting.module == module, SiteSetting.key == key)
    ).first()


def _find_account_row(db: Session, account_id: int, module: str, key: str) -> AccountSetting | None:
    return db.scalars(
        select(AccountSetting).where(
            AccountSetting.account_id == account_id,
            AccountSetting.module == module,
            AccountSetting.key == key,
        )
    ).first()


# ── 三级回退读取 ─────────────────────────────────────────────────────────


def resolve_setting(
    db: Session, *, module: str, key: str, account_id: int | None = None, default: object = None
) -> SettingResolution:
    """按 账号 → 站点 → 代码默认值 的顺序解析一个设置项的当前有效值。

    `account_id` 为 None 时直接跳过账号级（比如还没登录、或这个设置项本来就
    是全站共享、不支持账号覆盖）。
    """
    if account_id is not None:
        account_row = _find_account_row(db, account_id, module, key)
        if account_row is not None:
            return SettingResolution(value=account_row.value_json, source="account")

    site_row = _find_site_row(db, module, key)
    if site_row is not None:
        return SettingResolution(value=site_row.value_json, source="site")

    return SettingResolution(value=default, source="default")


# ── 站点级 ───────────────────────────────────────────────────────────────


def get_site_setting(db: Session, *, module: str, key: str) -> object | None:
    """只看站点级这一层，不做回退——站点级本身没有更上一级可退了，不存在就是
    `None`。"""
    row = _find_site_row(db, module, key)
    return row.value_json if row is not None else None


def set_site_setting(db: Session, *, module: str, key: str, value: object) -> SiteSettingDTO:
    row = _find_site_row(db, module, key)
    if row is None:
        row = SiteSetting(module=module, key=key, value_json=value)
        db.add(row)
    else:
        row.value_json = value
    db.flush()
    return _site_dto(row)


def delete_site_setting(db: Session, *, module: str, key: str) -> None:
    """删掉即恢复成代码默认值。本来就不存在也不报错——"确保这条覆盖不存在"
    这个操作天然是幂等的，不需要调用方先查一遍再决定要不要删。"""
    row = _find_site_row(db, module, key)
    if row is not None:
        db.delete(row)
        db.flush()


def list_site_settings(db: Session, *, module: str | None = _UNSET) -> list[SiteSettingDTO]:
    stmt = select(SiteSetting)
    if module is not _UNSET:
        stmt = stmt.where(SiteSetting.module == module)
    stmt = stmt.order_by(SiteSetting.module, SiteSetting.key)
    return [_site_dto(row) for row in db.scalars(stmt)]


# ── 账号级 ───────────────────────────────────────────────────────────────


def get_account_setting(db: Session, *, account_id: int, module: str, key: str) -> object | None:
    """只看账号级这一层，不做回退——想要三级回退后的有效值用
    `resolve_setting()`。"""
    row = _find_account_row(db, account_id, module, key)
    return row.value_json if row is not None else None


def set_account_setting(
    db: Session, *, account_id: int, module: str, key: str, value: object
) -> AccountSettingDTO:
    row = _find_account_row(db, account_id, module, key)
    if row is None:
        row = AccountSetting(account_id=account_id, module=module, key=key, value_json=value)
        db.add(row)
    else:
        row.value_json = value
    db.flush()
    return _account_dto(row)


def delete_account_setting(db: Session, *, account_id: int, module: str, key: str) -> None:
    """删掉即回退到站点级/代码默认值。本来就不存在也不报错，理由同
    `delete_site_setting`。"""
    row = _find_account_row(db, account_id, module, key)
    if row is not None:
        db.delete(row)
        db.flush()


def list_account_settings(
    db: Session, *, account_id: int, module: str | None = _UNSET
) -> list[AccountSettingDTO]:
    stmt = select(AccountSetting).where(AccountSetting.account_id == account_id)
    if module is not _UNSET:
        stmt = stmt.where(AccountSetting.module == module)
    stmt = stmt.order_by(AccountSetting.module, AccountSetting.key)
    return [_account_dto(row) for row in db.scalars(stmt)]
