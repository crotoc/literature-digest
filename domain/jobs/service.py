"""domain/jobs 的业务逻辑：所有后台/批量操作的通用追踪。

本模块刻意不认识任何具体业务含义——不知道 "import_ris" 和 "fulltext_download"
有什么区别，`kind` 只是一个调用方自定义的字符串标签用来过滤/展示。状态转移的
合法性也不由本模块硬编码：`transition()` 接收调用方传入的转移表 `allowed`，
各 feature 在自己的 contract.py 里声明"我这种 job 允许从哪个状态走到哪个状态"，
本模块只负责照着这张表校验、不负责知道表里有什么。这是吸取旧 `fulltext/states.py`
教训后的设计——旧代码把 `status` 字段同时当状态机状态和错误消息用（比如
`"waiting_browser"` 这个值既表示"卡在这一步"又被直接显示给用户当错误文案），
这里拆成 `status`（粗粒度、有限集合、本模块定义）+ `reason`（细粒度、调用方
定义的字符串，本模块只存不解释）两个独立字段。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.jobs.models import Job, _utcnow
from infra.errors import Conflict, NotFound

_UNSET = object()

STATUSES = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
DEFAULT_STATUS = "queued"


# ── 异常 ─────────────────────────────────────────────────────────────────


class JobNotFound(NotFound):
    code = "job_not_found"


class InvalidTransition(Conflict):
    """按调用方传入的转移表，当前状态不允许走到目标状态。"""

    code = "invalid_job_transition"

    def __init__(self, message: str, *, from_status: str, to_status: str) -> None:
        super().__init__(message, code=self.code)
        self.from_status = from_status
        self.to_status = to_status


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JobDTO:
    id: int
    parent_job_id: int | None
    account_id: int
    library_id: int | None
    kind: str
    status: str
    reason: str | None
    counts: dict
    cursor: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _job_dto(row: Job) -> JobDTO:
    return JobDTO(
        id=row.id,
        parent_job_id=row.parent_job_id,
        account_id=row.account_id,
        library_id=row.library_id,
        kind=row.kind,
        status=row.status,
        reason=row.reason,
        counts=row.counts_json,
        cursor=row.cursor,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _get_job_row(db: Session, job_id: int) -> Job:
    row = db.get(Job, job_id)
    if row is None:
        raise JobNotFound(f"job {job_id} 不存在")
    return row


# ── 创建/查询 ────────────────────────────────────────────────────────────


def create_job(
    db: Session,
    *,
    account_id: int,
    kind: str,
    library_id: int | None = None,
    parent_job_id: int | None = None,
    counts: dict | None = None,
) -> JobDTO:
    row = Job(
        account_id=account_id,
        kind=kind,
        library_id=library_id,
        parent_job_id=parent_job_id,
        status=DEFAULT_STATUS,
        counts_json=dict(counts) if counts else {},
    )
    db.add(row)
    db.flush()
    return _job_dto(row)


def get_job(db: Session, job_id: int) -> JobDTO:
    return _job_dto(_get_job_row(db, job_id))


def list_jobs(
    db: Session,
    *,
    account_id: int,
    kind: str | None = _UNSET,
    status: str | None = _UNSET,
    parent_job_id: int | None = _UNSET,
) -> list[JobDTO]:
    stmt = select(Job).where(Job.account_id == account_id)
    if kind is not _UNSET:
        stmt = stmt.where(Job.kind == kind)
    if status is not _UNSET:
        stmt = stmt.where(Job.status == status)
    if parent_job_id is not _UNSET:
        stmt = stmt.where(Job.parent_job_id == parent_job_id)
    stmt = stmt.order_by(Job.created_at.desc())
    return [_job_dto(row) for row in db.scalars(stmt)]


def list_child_jobs(db: Session, parent_job_id: int) -> list[JobDTO]:
    """按创建顺序（而非默认的 created_at desc）返回子 job——子 job 通常代表
    批量操作里的一串待处理项，调用方更关心处理顺序而不是最新的在前面。"""
    stmt = (
        select(Job).where(Job.parent_job_id == parent_job_id).order_by(Job.created_at.asc())
    )
    return [_job_dto(row) for row in db.scalars(stmt)]


# ── 状态机 ───────────────────────────────────────────────────────────────


def transition(
    db: Session,
    job_id: int,
    to_status: str,
    *,
    reason: str | None = None,
    allowed: dict[str, set[str]],
) -> JobDTO:
    """把 job 从当前状态切到 `to_status`。

    `allowed` 是调用方（某个 feature）自己声明的转移表：
    `{"queued": {"running", "cancelled"}, "running": {"succeeded", "failed"}}`。
    本模块不内置任何具体 kind 的转移规则，只负责照着调用方给的表校验这一次
    切换是否合法——这样 `domain/jobs` 永远不需要认识任何具体 kind。
    """
    if to_status not in STATUSES:
        raise ValueError(f"未知状态：{to_status!r}，必须是 {sorted(STATUSES)} 之一")

    row = _get_job_row(db, job_id)
    current = row.status
    if to_status not in allowed.get(current, set()):
        raise InvalidTransition(
            f"job {job_id} 不能从 {current!r} 切到 {to_status!r}",
            from_status=current,
            to_status=to_status,
        )

    row.status = to_status
    row.reason = reason
    if to_status == "running" and row.started_at is None:
        row.started_at = _utcnow()
    if to_status in TERMINAL_STATUSES:
        row.finished_at = _utcnow()
    db.flush()
    return _job_dto(row)


def update_counts(db: Session, job_id: int, counts: dict) -> JobDTO:
    """整体替换 `counts_json`（不是合并）——调用方每次上报完整的计数快照，
    比如 `{"total": 100, "done": 80, "skipped": 5, "failed": 15}`。"""
    row = _get_job_row(db, job_id)
    row.counts_json = dict(counts)
    db.flush()
    return _job_dto(row)


def update_cursor(db: Session, job_id: int, cursor: str | None) -> JobDTO:
    row = _get_job_row(db, job_id)
    row.cursor = cursor
    db.flush()
    return _job_dto(row)


def retry_job(db: Session, job_id: int) -> JobDTO:
    """重试 = 新建一个从 `queued` 起的 job，不倒退已有 job 的状态。旧 job 原样
    保留在它最终落到的终态上，作为这一次尝试失败的历史记录。"""
    old = _get_job_row(db, job_id)
    new_row = Job(
        account_id=old.account_id,
        kind=old.kind,
        library_id=old.library_id,
        parent_job_id=old.parent_job_id,
        status=DEFAULT_STATUS,
        counts_json={},
    )
    db.add(new_row)
    db.flush()
    return _job_dto(new_row)
