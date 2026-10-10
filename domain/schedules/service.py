"""domain/schedules 的业务逻辑：按 `(target_kind, target_id)` 唯一的一条
cron 调度记录——"该不该跑"的数学交给 `caps/schedule`，这里只管存取。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定同 `domain/accounts/service.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from caps.schedule import is_due, validate_cron
from domain.schedules.models import Schedule
from infra.errors import NotFound


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ScheduleNotFound(NotFound):
    code = "schedule_not_found"


@dataclass(frozen=True)
class ScheduleDTO:
    id: int
    account_id: int
    target_kind: str
    target_id: int
    cron: str
    enabled: bool
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _dto(row: Schedule) -> ScheduleDTO:
    return ScheduleDTO(
        id=row.id,
        account_id=row.account_id,
        target_kind=row.target_kind,
        target_id=row.target_id,
        cron=row.cron,
        enabled=row.enabled,
        last_run_at=row.last_run_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_row(db: Session, *, target_kind: str, target_id: int) -> Schedule | None:
    return db.scalar(
        select(Schedule).where(Schedule.target_kind == target_kind, Schedule.target_id == target_id)
    )


def upsert_schedule(
    db: Session, *, account_id: int, target_kind: str, target_id: int, cron: str, enabled: bool = True
) -> ScheduleDTO:
    """创建或更新这个目标的调度配置。`(target_kind, target_id)` 全库唯一——
    重复调用是"编辑已有调度"，不会产生第二条记录。`cron` 不合法时
    `caps.schedule.validate_cron` 抛的 `InvalidCronExpression`（是
    `ValueError` 的子类）原样往上冒，不在这里额外包一层。
    """
    validate_cron(cron)

    row = _get_row(db, target_kind=target_kind, target_id=target_id)
    if row is None:
        row = Schedule(
            account_id=account_id,
            target_kind=target_kind,
            target_id=target_id,
            cron=cron,
            enabled=enabled,
        )
        db.add(row)
    else:
        row.account_id = account_id
        row.cron = cron
        row.enabled = enabled
    db.flush()
    return _dto(row)


def get_schedule(db: Session, *, target_kind: str, target_id: int) -> ScheduleDTO | None:
    row = _get_row(db, target_kind=target_kind, target_id=target_id)
    return _dto(row) if row is not None else None


def delete_schedule(db: Session, *, target_kind: str, target_id: int) -> None:
    """删掉这个目标的调度配置。目标本来就没配过调度时是空操作——这是"删
    掉目标顺手级联删它的调度"这类清理路径需要的幂等行为，不是"调度一定
    存在才能删"的强断言。
    """
    row = _get_row(db, target_kind=target_kind, target_id=target_id)
    if row is None:
        return
    db.delete(row)
    db.flush()


def mark_run(db: Session, *, target_kind: str, target_id: int, when: datetime | None = None) -> ScheduleDTO:
    row = _get_row(db, target_kind=target_kind, target_id=target_id)
    if row is None:
        raise ScheduleNotFound(f"没有找到调度：{target_kind}#{target_id}")
    row.last_run_at = when or _utcnow()
    db.flush()
    return _dto(row)


def list_schedules(db: Session, *, account_id: int) -> list[ScheduleDTO]:
    rows = db.scalars(select(Schedule).where(Schedule.account_id == account_id).order_by(Schedule.id))
    return [_dto(row) for row in rows]


def list_due_schedules(db: Session, *, as_of: datetime | None = None) -> list[ScheduleDTO]:
    """跨全部账号，找出"现在该跑"的启用中调度。`caps.schedule.is_due()`
    的数学没法翻成 SQL WHERE 条件（cron 表达式解析），所以这里先按
    `enabled=True` 筛一遍缩小范围，再在 Python 里逐条判断——v1 规模下
    调度条数不会多到这样做有性能问题。
    """
    moment = as_of or _utcnow()
    rows = db.scalars(select(Schedule).where(Schedule.enabled.is_(True)))
    return [_dto(row) for row in rows if is_due(row.cron, last_run_at=row.last_run_at, now=moment)]
