"""features/organizing 的业务逻辑：对一批文献做批量打标签/去标签/加入或
移出文件夹、移入回收站/恢复/彻底删除——每一种都是"瞬时批量"（父 job 一行
+ counts_json，只有失败项才建子行），和"导入"那种每项一行的长流程批量不
是同一个粒度规则。

组合 domain/{tags,folders,notes,attachments,works,jobs} + caps/slug。
本模块不开表。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from caps.slug import normalize
from domain.attachments import delete_attachment, list_attachments_for_work
from domain.folders import (
    WorkFolderNotFound,
    add_work_to_folder,
    get_folder,
    list_folders_for_work,
    remove_work_from_folder,
)
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.notes import set_note
from domain.tags import (
    TagDTO,
    WorkTagNotFound,
    add_tag_to_work,
    create_tag,
    get_tag,
    list_tags_for_work,
    remove_tag_from_work,
)
from domain.works import WorkDTO, get_work, purge_work, restore_work, soft_delete_work
from infra.errors import AppError, Conflict

JOB_KIND_ADD_TAG = "organize_add_tag"
JOB_KIND_REMOVE_TAG = "organize_remove_tag"
JOB_KIND_ADD_FOLDER = "organize_add_folder"
JOB_KIND_REMOVE_FOLDER = "organize_remove_folder"
JOB_KIND_SOFT_DELETE = "organize_soft_delete"
JOB_KIND_RESTORE = "organize_restore"
JOB_KIND_PURGE = "organize_purge"
JOB_KIND_ITEM = "organize_batch_item"
# 瞬时批量：和 features/uploading 的 upload_batch 同一个粒度规则（父 job
# 一行 + counts_json，只有失败项才建子行）。计划里"两处已定"把"打标签/去
# 标签/加移文件夹/删除"明确列进瞬时批量这一类，这里是字面落实。
_PARENT_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}
_CHILD_TRANSITIONS = {"queued": {"failed"}}


class WorkNotInTrash(Conflict):
    """彻底删除前必须先移入回收站——防止跳过回收站这层安全网直接硬删。"""

    code = "work_not_in_trash"


@dataclass(frozen=True)
class BatchOutcome:
    work_id: int
    status: str  # "done" | "failed"
    reason: str | None = None


@dataclass(frozen=True)
class BatchResult:
    job: JobDTO
    outcomes: tuple[BatchOutcome, ...]


def _run_batch(
    db: Session,
    *,
    account_id: int,
    library_id: int,
    kind: str,
    work_ids: Sequence[int],
    apply_one: Callable[[int], None],
) -> BatchResult:
    total = len(work_ids)
    job = create_job(
        db, account_id=account_id, kind=kind, library_id=library_id,
        counts={"total": total, "done": 0, "failed": 0},
    )
    job = transition(db, job.id, "running", allowed=_PARENT_TRANSITIONS)

    outcomes: list[BatchOutcome] = []
    done = 0
    failed = 0
    for work_id in work_ids:
        try:
            apply_one(work_id)
        except (AppError, ValueError) as exc:
            failed += 1
            child = create_job(
                db, account_id=account_id, kind=JOB_KIND_ITEM, library_id=library_id, parent_job_id=job.id
            )
            transition(db, child.id, "failed", reason=str(exc), allowed=_CHILD_TRANSITIONS)
            outcomes.append(BatchOutcome(work_id=work_id, status="failed", reason=str(exc)))
        else:
            done += 1
            outcomes.append(BatchOutcome(work_id=work_id, status="done"))
        update_counts(db, job.id, {"total": total, "done": done, "failed": failed})

    final_status = "failed" if failed else "succeeded"
    job = transition(db, job.id, final_status, allowed=_PARENT_TRANSITIONS)
    return BatchResult(job=job, outcomes=tuple(outcomes))


def _check_work_scope(
    db: Session, *, library_id: int, work_id: int, include_deleted: bool = False
) -> WorkDTO:
    work = get_work(db, work_id, include_deleted=include_deleted)
    if work.library_id != library_id:
        raise ValueError(f"work_id 不属于这个库：{work_id}")
    return work


# ── 标签 ─────────────────────────────────────────────────────────────────


def bulk_add_tag(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int], tag_id: int
) -> BatchResult:
    tag = get_tag(db, tag_id)
    if tag.library_id != library_id:
        raise ValueError(f"tag_id 不属于这个库：{tag_id}")

    def apply_one(work_id: int) -> None:
        _check_work_scope(db, library_id=library_id, work_id=work_id)
        add_tag_to_work(db, library_id=library_id, work_id=work_id, tag_id=tag_id)

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_ADD_TAG,
        work_ids=work_ids, apply_one=apply_one,
    )


def bulk_remove_tag(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int], tag_id: int
) -> BatchResult:
    tag = get_tag(db, tag_id)
    if tag.library_id != library_id:
        raise ValueError(f"tag_id 不属于这个库：{tag_id}")

    def apply_one(work_id: int) -> None:
        _check_work_scope(db, library_id=library_id, work_id=work_id)
        try:
            remove_tag_from_work(db, work_id=work_id, tag_id=tag_id)
        except WorkTagNotFound:
            # 这个文献本来就没打这个标签——目标状态（"没有这个标签"）已经
            # 达成，不该算批量失败。混合选区里这种情况是常态（三态勾选框
            # 的"部分"态，批量去标签本就是冲着"把剩下有的也去掉"去的），
            # 不是真的出了错。和 remove_from_folder 同一个判断。
            pass

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_REMOVE_TAG,
        work_ids=work_ids, apply_one=apply_one,
    )


def create_tag_and_apply(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int], name: str
) -> tuple[TagDTO, BatchResult]:
    """"顺手新建标签再打上"：先用 caps.slug.normalize 收敛掉全角/半角、
    首尾空白这类琴面差异（避免用户敲错一个空格就建出两个看起来一样的
    标签），再交给 domain.tags.create_tag——它自己的同名幂等判断是大小写
    敏感的精确匹配，这条收敛只做在它之前，不改它本身的语义。
    """
    normalized = normalize(name)
    tag = create_tag(db, library_id=library_id, name=normalized)
    result = bulk_add_tag(db, account_id=account_id, library_id=library_id, work_ids=work_ids, tag_id=tag.id)
    return tag, result


# ── 文件夹 ───────────────────────────────────────────────────────────────


def bulk_add_to_folder(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int], folder_id: int
) -> BatchResult:
    folder = get_folder(db, folder_id)
    if folder.library_id != library_id:
        raise ValueError(f"folder_id 不属于这个库：{folder_id}")

    def apply_one(work_id: int) -> None:
        _check_work_scope(db, library_id=library_id, work_id=work_id)
        add_work_to_folder(db, library_id=library_id, work_id=work_id, folder_id=folder_id)

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_ADD_FOLDER,
        work_ids=work_ids, apply_one=apply_one,
    )


def bulk_remove_from_folder(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int], folder_id: int
) -> BatchResult:
    folder = get_folder(db, folder_id)
    if folder.library_id != library_id:
        raise ValueError(f"folder_id 不属于这个库：{folder_id}")

    def apply_one(work_id: int) -> None:
        _check_work_scope(db, library_id=library_id, work_id=work_id)
        try:
            remove_work_from_folder(db, work_id=work_id, folder_id=folder_id)
        except WorkFolderNotFound:
            pass  # 本来就不在这个文件夹里，目标状态已达成，同 bulk_remove_tag

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_REMOVE_FOLDER,
        work_ids=work_ids, apply_one=apply_one,
    )


# ── 回收站 ───────────────────────────────────────────────────────────────


def bulk_soft_delete(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int]
) -> BatchResult:
    def apply_one(work_id: int) -> None:
        _check_work_scope(db, library_id=library_id, work_id=work_id, include_deleted=True)
        soft_delete_work(db, work_id)

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_SOFT_DELETE,
        work_ids=work_ids, apply_one=apply_one,
    )


def bulk_restore(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int]
) -> BatchResult:
    def apply_one(work_id: int) -> None:
        _check_work_scope(db, library_id=library_id, work_id=work_id, include_deleted=True)
        restore_work(db, work_id)

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_RESTORE,
        work_ids=work_ids, apply_one=apply_one,
    )


def _purge_cascade(db: Session, *, library_id: int, work_id: int, blob_store) -> None:
    """跨 5 个 domain 的级联顺序。domain.works.purge_work 自己的文档写明
    "不级联 tags/folders/notes/attachments……跨 domain 的级联顺序由
    features/organizing 编排，它会依次调各个 domain 自己的 purge 函数"——
    这里就是那段编排。顺序本身不敏感（互不依赖），但必须在最后才删
    works 行本身，否则前面几步会失去 work_id 还指向一篇真实文献这个
    前提（虽然这几个 domain 其实都不检查 work_id 是否存在，但语义上
    "先清关联、再删本体"更安全）。
    """
    for tag in list_tags_for_work(db, work_id):
        remove_tag_from_work(db, work_id=work_id, tag_id=tag.id)
    for folder in list_folders_for_work(db, work_id):
        remove_work_from_folder(db, work_id=work_id, folder_id=folder.id)
    set_note(db, library_id=library_id, work_id=work_id, content=None)
    for attachment in list_attachments_for_work(db, work_id):
        orphaned = delete_attachment(db, attachment.id)
        if orphaned:
            blob_store.delete(attachment.digest)
    purge_work(db, work_id)


def purge_works(
    db: Session, *, account_id: int, library_id: int, work_ids: Sequence[int], blob_store
) -> BatchResult:
    """彻底删除。只对已经在回收站里的文献生效——`WorkNotInTrash` 就是这道
    安全网：不能跳过"先移入回收站"这一步直接硬删，误操作至少还有一次
    回收站里的缓冲。
    """

    def apply_one(work_id: int) -> None:
        work = _check_work_scope(db, library_id=library_id, work_id=work_id, include_deleted=True)
        if work.deleted_at is None:
            raise WorkNotInTrash(f"文献 {work_id} 还没有移入回收站，不能彻底删除")
        _purge_cascade(db, library_id=library_id, work_id=work_id, blob_store=blob_store)

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_PURGE,
        work_ids=work_ids, apply_one=apply_one,
    )
