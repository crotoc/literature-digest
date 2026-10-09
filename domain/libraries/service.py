"""domain/libraries 的业务逻辑：库的创建/改名、成员的加入/移除/改角色、
`resolve_scope()`——其它所有按库划分数据的模块（works/folders/tags/notes/
attachments）都要先过这一关才能确认"这个账号有没有权碰这个库"。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 domain/accounts/service.py 的同一段说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.libraries.models import Library, LibraryMember
from infra.errors import AppError, Conflict, NotFound

ROLES = frozenset({"owner", "member"})
DEFAULT_ROLE = "member"

# ── 异常 ─────────────────────────────────────────────────────────────────


class LibraryNotFound(NotFound):
    """库不存在，**或者**库存在但调用方不是成员。

    两种情形故意报同一个异常——不然等于向非成员泄露"这个库 id 其实存在，
    只是你进不去"。见 `resolve_scope` 的 docstring。
    """

    code = "library_not_found"


class MembershipNotFound(NotFound):
    """在一次已经通过 owner 校验的成员管理操作里，目标账号根本不是成员。

    这里不存在上面那种泄露问题——调用方已经证明了自己是该库的 owner，
    对自己库里"到底有没有这个成员"本来就有可见权。
    """

    code = "membership_not_found"


class DuplicateMembership(Conflict):
    code = "duplicate_membership"


class NotLibraryOwner(AppError):
    status_code = 403
    code = "not_library_owner"


class LastOwnerRequired(AppError):
    """移除或降级最后一个 owner——库不能没有 owner，否则没人能再管理它的成员。"""

    status_code = 409
    code = "last_owner_required"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LibraryDTO:
    id: int
    name: str
    created_at: datetime


@dataclass(frozen=True)
class MembershipDTO:
    library_id: int
    account_id: int
    role: str
    created_at: datetime


def _library_dto(row: Library) -> LibraryDTO:
    return LibraryDTO(id=row.id, name=row.name, created_at=row.created_at)


def _membership_dto(row: LibraryMember) -> MembershipDTO:
    return MembershipDTO(
        library_id=row.library_id,
        account_id=row.account_id,
        role=row.role,
        created_at=row.created_at,
    )


def _get_membership_row(db: Session, *, library_id: int, account_id: int) -> LibraryMember | None:
    return db.scalar(
        select(LibraryMember).where(
            LibraryMember.library_id == library_id,
            LibraryMember.account_id == account_id,
        )
    )


def _owner_count(db: Session, library_id: int) -> int:
    return db.scalar(
        select(func.count())
        .select_from(LibraryMember)
        .where(LibraryMember.library_id == library_id, LibraryMember.role == "owner")
    )


# ── 库 ───────────────────────────────────────────────────────────────────


def create_library(db: Session, *, owner_account_id: int, name: str) -> LibraryDTO:
    """创建一个库，并把 `owner_account_id` 直接设成它的第一个 owner 成员——
    这两步在同一个函数里原子地做，调用方（比如注册流程）不需要自己再补一次
    `add_member`，也就不会出现"库建好了但没人管理"的中间态。
    """
    name = name.strip()
    if not name:
        raise ValueError("库名不能为空")

    library = Library(name=name)
    db.add(library)
    db.flush()

    db.add(LibraryMember(library_id=library.id, account_id=owner_account_id, role="owner"))
    db.flush()

    return _library_dto(library)


def get_library(db: Session, library_id: int) -> LibraryDTO:
    """按 id 查库，**不做成员校验**——调用方如果需要"这个账号能不能看这个库"，
    应该用 `resolve_scope`，不是本函数。本函数给的是纯粹的"这个 id 存在吗"，
    比如 URL slug `/l/<name-slug>-<id>/` 解析出 id 之后先查个名字。
    """
    row = db.get(Library, library_id)
    if row is None:
        raise LibraryNotFound(f"库不存在：{library_id}")
    return _library_dto(row)


def rename_library(db: Session, *, library_id: int, name: str, acting_account_id: int) -> LibraryDTO:
    """改库名。任何成员都能改，不要求 owner——改名不是安全敏感操作，见
    README「刻意裁剪的范围」。仍然要先过 `resolve_scope`，确保 acting 账号
    至少是这个库的成员，不是随便一个账号都能改别人库的名字。
    """
    resolve_scope(db, account_id=acting_account_id, library_id=library_id)

    name = name.strip()
    if not name:
        raise ValueError("库名不能为空")

    row = db.get(Library, library_id)
    if row is None:
        raise LibraryNotFound(f"库不存在：{library_id}")
    row.name = name
    db.flush()
    return _library_dto(row)


def list_libraries_for_account(db: Session, account_id: int) -> list[LibraryDTO]:
    """一个账号能看到的所有库——库切换器用这个。"""
    rows = db.scalars(
        select(Library)
        .join(LibraryMember, LibraryMember.library_id == Library.id)
        .where(LibraryMember.account_id == account_id)
        .order_by(Library.created_at)
    )
    return [_library_dto(row) for row in rows]


# ── 成员与权限 ─────────────────────────────────────────────────────────────


def resolve_scope(db: Session, *, account_id: int, library_id: int) -> MembershipDTO:
    """其它所有按库划分数据的模块在碰任何一条属于某个 library_id 的数据之前，
    都应该先调这个函数——它是整个多库隔离模型的唯一入口。

    库不存在、或者库存在但 `account_id` 不是它的成员，报同一个
    `LibraryNotFound`：不这样做的话，一个账号可以靠"库 id 不存在"和"库
    id 存在但我进不去"两种不同的报错，枚举出别人有多少个库、id 分别是什么。
    """
    row = _get_membership_row(db, library_id=library_id, account_id=account_id)
    if row is None:
        raise LibraryNotFound(f"库不存在：{library_id}")
    return _membership_dto(row)


def list_members(db: Session, *, library_id: int, acting_account_id: int) -> list[MembershipDTO]:
    """列出一个库的所有成员。只要求 acting 账号本身是成员（任何角色都行），
    不要求 owner——"我们这个库里都有谁"不是只有 owner 能看的信息。
    """
    resolve_scope(db, account_id=acting_account_id, library_id=library_id)
    rows = db.scalars(
        select(LibraryMember)
        .where(LibraryMember.library_id == library_id)
        .order_by(LibraryMember.created_at)
    )
    return [_membership_dto(row) for row in rows]


def add_member(
    db: Session,
    *,
    library_id: int,
    account_id: int,
    acting_account_id: int,
    role: str = DEFAULT_ROLE,
) -> MembershipDTO:
    """给库加一个成员。要求 acting 账号是该库的 owner——管理成员名单是
    owner 的特权（見 README「权限粒度」一节，v1 只分 owner/member 两级，
    owner 能管成员，member 不能）。
    """
    if role not in ROLES:
        raise ValueError(f"role 必须是 {sorted(ROLES)} 之一，收到 {role!r}")

    acting = resolve_scope(db, account_id=acting_account_id, library_id=library_id)
    if acting.role != "owner":
        raise NotLibraryOwner(f"账号 {acting_account_id} 不是库 {library_id} 的 owner")

    if _get_membership_row(db, library_id=library_id, account_id=account_id) is not None:
        raise DuplicateMembership(f"账号 {account_id} 已经是库 {library_id} 的成员")

    row = LibraryMember(library_id=library_id, account_id=account_id, role=role)
    db.add(row)
    db.flush()
    return _membership_dto(row)


def remove_member(db: Session, *, library_id: int, account_id: int, acting_account_id: int) -> None:
    """移除一个成员。要求 acting 账号是 owner；如果目标正好是这个库唯一的
    owner，拒绝——库不能变成没有 owner（之后没人能再管理成员名单）。
    """
    acting = resolve_scope(db, account_id=acting_account_id, library_id=library_id)
    if acting.role != "owner":
        raise NotLibraryOwner(f"账号 {acting_account_id} 不是库 {library_id} 的 owner")

    row = _get_membership_row(db, library_id=library_id, account_id=account_id)
    if row is None:
        raise MembershipNotFound(f"账号 {account_id} 不是库 {library_id} 的成员")

    if row.role == "owner" and _owner_count(db, library_id) <= 1:
        raise LastOwnerRequired(f"库 {library_id} 不能移除最后一个 owner")

    db.delete(row)
    db.flush()


def update_member_role(
    db: Session,
    *,
    library_id: int,
    account_id: int,
    new_role: str,
    acting_account_id: int,
) -> MembershipDTO:
    """改一个成员的角色。同样要求 acting 账号是 owner；把最后一个 owner
    降级为 member 和直接移除他是同一种危险操作，同样拒绝。
    """
    if new_role not in ROLES:
        raise ValueError(f"role 必须是 {sorted(ROLES)} 之一，收到 {new_role!r}")

    acting = resolve_scope(db, account_id=acting_account_id, library_id=library_id)
    if acting.role != "owner":
        raise NotLibraryOwner(f"账号 {acting_account_id} 不是库 {library_id} 的 owner")

    row = _get_membership_row(db, library_id=library_id, account_id=account_id)
    if row is None:
        raise MembershipNotFound(f"账号 {account_id} 不是库 {library_id} 的成员")

    if row.role == "owner" and new_role != "owner" and _owner_count(db, library_id) <= 1:
        raise LastOwnerRequired(f"库 {library_id} 不能降级最后一个 owner")

    row.role = new_role
    db.flush()
    return _membership_dto(row)
