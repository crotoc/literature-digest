"""domain/usage 的业务逻辑：账号级配额计数器。

本模块只负责"记账"——累加计数、读计数、按周期/kind 列计数。它不知道任何一个
kind 的配额上限是多少，也不负责在计数超限时拒绝任何操作；"这个账号这个月
AI 调用是不是已经超过它套餐允许的次数"是调用方（某个 `features/*`）自己读出
计数、和自己从 `domain/settings` 读出的上限比较之后做的判断，本模块完全不
参与这个判断。

`period` 是一个任意字符串，格式由调用方自己约定（比如按月 `"2026-10"`、
按周 `"2026-W41"`），本模块不解析、不校验、不负责"周期到了自动翻页"——
这些都是调用方的职责。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.usage.models import UsageCounter

_UNSET = object()


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UsageCounterDTO:
    id: int
    account_id: int
    period: str
    kind: str
    count: int
    created_at: datetime
    updated_at: datetime


def _dto(row: UsageCounter) -> UsageCounterDTO:
    return UsageCounterDTO(
        id=row.id,
        account_id=row.account_id,
        period=row.period,
        kind=row.kind,
        count=row.count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _find_row(db: Session, account_id: int, period: str, kind: str) -> UsageCounter | None:
    return db.scalars(
        select(UsageCounter).where(
            UsageCounter.account_id == account_id,
            UsageCounter.period == period,
            UsageCounter.kind == kind,
        )
    ).first()


# ── 读写 ─────────────────────────────────────────────────────────────────


def increment_usage(
    db: Session, *, account_id: int, period: str, kind: str, amount: int = 1
) -> UsageCounterDTO:
    """给某个账号在某个周期的某类用量计数加上 `amount`（可以是负数，用于
    纠错）。不存在对应的行就以 `amount` 为初值新建一行。"""
    row = _find_row(db, account_id, period, kind)
    if row is None:
        row = UsageCounter(account_id=account_id, period=period, kind=kind, count=amount)
        db.add(row)
    else:
        row.count += amount
    db.flush()
    return _dto(row)


def get_usage(db: Session, *, account_id: int, period: str, kind: str) -> int:
    """没有计数过就是 0——"这个账号这个月还没调用过这个 kind"是最常见的正常
    状态，不是异常，所以这里返回 0 而不是抛异常，和 `domain/notes.get_note`
    对"还没写过笔记"的处理是同一种设计倾向。"""
    row = _find_row(db, account_id, period, kind)
    return row.count if row is not None else 0


def list_usage(
    db: Session, *, account_id: int, period: str | None = _UNSET, kind: str | None = _UNSET
) -> list[UsageCounterDTO]:
    stmt = select(UsageCounter).where(UsageCounter.account_id == account_id)
    if period is not _UNSET:
        stmt = stmt.where(UsageCounter.period == period)
    if kind is not _UNSET:
        stmt = stmt.where(UsageCounter.kind == kind)
    stmt = stmt.order_by(UsageCounter.period.desc(), UsageCounter.kind)
    return [_dto(row) for row in db.scalars(stmt)]


def reset_usage(db: Session, *, account_id: int, period: str, kind: str) -> None:
    """清掉这一行计数（而不是把 count 设成 0 留着那一行）——之后再
    `get_usage` 同样会看到 0。本来就不存在也不报错，理由同
    `domain/connections.clear_secret`：确保它"没有计数"这件事本身是幂等的。"""
    row = _find_row(db, account_id, period, kind)
    if row is not None:
        db.delete(row)
        db.flush()
