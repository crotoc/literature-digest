"""features/dedupe_review 的业务逻辑：人工复核 `domain.works` 粗筛出来的
疑似重复候选（`title_year_key` 相同）——驳回（两条确实不是同一篇）或合并
（确实是同一篇，迁移标签/文件夹/笔记/标识符/附件后把其中一条彻底删除）。

组合 domain/{works,folders,tags,attachments,notes,jobs}。本模块不开表，
不依赖任何 caps——`merge_works` 迁移附件时始终先落地新引用再删旧引用（和
`features/uploading`/`features/organizing` 的覆盖顺序坑同一个原理），所以
从未真的需要删 blob，不需要 `caps/blobstore`。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from domain.attachments import create_attachment, list_attachments_for_work
from domain.attachments import delete_attachment as _delete_attachment_row
from domain.folders import add_work_to_folder, list_folders_for_work
from domain.folders import remove_work_from_folder as _remove_work_from_folder
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.notes import get_note, set_note
from domain.tags import add_tag_to_work, list_tags_for_work
from domain.tags import remove_tag_from_work as _remove_tag_from_work
from domain.works import (
    DuplicateCandidateDTO,
    WorkDTO,
    add_identifier,
    get_work,
    list_duplicate_candidates,
    list_identifiers,
    purge_work,
    remove_identifier,
    resolve_duplicate_candidate,
    soft_delete_work,
)
from infra.errors import AppError

JOB_KIND_DISMISS = "dedupe_dismiss_batch"
JOB_KIND_ITEM = "dedupe_dismiss_batch_item"
# 瞬时批量：和 features/organizing/uploading 同一个粒度规则（父 job 一行
# + counts_json，只有失败项才建子行）。这里的批量单位是 candidate_id，
# 不是 work_id，_run_batch 用通用的 item_id 命名，不是漏改。
_PARENT_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}
_CHILD_TRANSITIONS = {"queued": {"failed"}}


@dataclass(frozen=True)
class CandidatePairDTO:
    candidate: DuplicateCandidateDTO
    work: WorkDTO
    candidate_work: WorkDTO


@dataclass(frozen=True)
class BatchOutcome:
    item_id: int
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
    item_ids: Sequence[int],
    apply_one: Callable[[int], None],
) -> BatchResult:
    total = len(item_ids)
    job = create_job(
        db, account_id=account_id, kind=kind, library_id=library_id,
        counts={"total": total, "done": 0, "failed": 0},
    )
    job = transition(db, job.id, "running", allowed=_PARENT_TRANSITIONS)

    outcomes: list[BatchOutcome] = []
    done = 0
    failed = 0
    for item_id in item_ids:
        try:
            apply_one(item_id)
        except (AppError, ValueError) as exc:
            failed += 1
            child = create_job(
                db, account_id=account_id, kind=JOB_KIND_ITEM, library_id=library_id, parent_job_id=job.id
            )
            transition(db, child.id, "failed", reason=str(exc), allowed=_CHILD_TRANSITIONS)
            outcomes.append(BatchOutcome(item_id=item_id, status="failed", reason=str(exc)))
        else:
            done += 1
            outcomes.append(BatchOutcome(item_id=item_id, status="done"))
        update_counts(db, job.id, {"total": total, "done": done, "failed": failed})

    final_status = "failed" if failed else "succeeded"
    job = transition(db, job.id, final_status, allowed=_PARENT_TRANSITIONS)
    return BatchResult(job=job, outcomes=tuple(outcomes))


def list_pending_candidates(db: Session, *, library_id: int) -> list[CandidatePairDTO]:
    """展开成复核 UI 要的"左右两篇完整文献"对照视图。

    会跳过其中一侧已经被（通过别的路径，比如 `features/organizing` 的
    批量软删）移入回收站的候选——这种候选已经不需要复核了：既然有一侧
    已经被用户用别的方式"处理掉"了，再让人在这里判断"是不是同一篇"没有
    意义。真正被彻底删除（purge）的那一侧不会走到这里：
    `domain.works.purge_work` 自己的级联会清掉所有指向它的
    `duplicate_candidate` 行，这里永远查不到那种残留。
    """
    pairs: list[CandidatePairDTO] = []
    for candidate in list_duplicate_candidates(db, library_id=library_id, status="pending"):
        work = get_work(db, candidate.work_id, include_deleted=True)
        candidate_work = get_work(db, candidate.candidate_work_id, include_deleted=True)
        if work.deleted_at is not None or candidate_work.deleted_at is not None:
            continue
        pairs.append(CandidatePairDTO(candidate=candidate, work=work, candidate_work=candidate_work))
    return pairs


def bulk_dismiss_candidates(
    db: Session, *, account_id: int, library_id: int, candidate_ids: Sequence[int]
) -> BatchResult:
    """驳回一批候选：标成 "dismissed"，两条记录都保留，谁也不动。

    一次性把这个库当前所有 "pending" 候选取出来建一张 id 索引，比在循环
    里逐个查更省一次往返；同时这张索引自然就是"候选存在、属于这个库、
    还处于 pending"三个条件的联合校验——不在这张索引里的 id，三个条件
    至少有一个不成立，统一报一条失败原因即可，不需要分别判断是哪一个
    （调用方无论如何都只能重新打开复核列表看当前状态，不需要精确区分）。
    """
    pending_by_id = {c.id: c for c in list_duplicate_candidates(db, library_id=library_id, status="pending")}

    def apply_one(candidate_id: int) -> None:
        if candidate_id not in pending_by_id:
            raise ValueError(f"候选 {candidate_id} 不存在、不属于这个库，或已经被处理过")
        resolve_duplicate_candidate(db, candidate_id, status="dismissed")

    return _run_batch(
        db, account_id=account_id, library_id=library_id, kind=JOB_KIND_DISMISS,
        item_ids=candidate_ids, apply_one=apply_one,
    )


def _migrate_identifiers(db: Session, *, library_id: int, keep_work_id: int, merge_work_id: int) -> None:
    """先从 merge_work 摘下来，再挂到 keep_work 上——顺序不能反。标识符的
    唯一性是全库级别的，`add_identifier` 在标识符还挂在 merge_work（没被
    软删）身上时会直接报 `IdentifierConflict`，所以必须先 `remove_identifier`
    腾出位置。腾出来之后 `add_identifier` 不可能再冲突：同一个
    `(library_id, scheme, value_norm)` 全库唯一，merge_work 和 keep_work
    不可能同时各自持有一份一样的。
    """
    for identifier in list_identifiers(db, merge_work_id):
        remove_identifier(db, identifier.id)
        add_identifier(
            db,
            library_id=library_id,
            work_id=keep_work_id,
            scheme=identifier.scheme,
            value=identifier.value,
        )


def _migrate_tags(db: Session, *, keep_work_id: int, merge_work_id: int) -> None:
    for tag in list_tags_for_work(db, merge_work_id):
        add_tag_to_work(db, library_id=tag.library_id, work_id=keep_work_id, tag_id=tag.id)
        _remove_tag_from_work(db, work_id=merge_work_id, tag_id=tag.id)


def _migrate_folders(db: Session, *, keep_work_id: int, merge_work_id: int) -> None:
    for folder in list_folders_for_work(db, merge_work_id):
        add_work_to_folder(db, library_id=folder.library_id, work_id=keep_work_id, folder_id=folder.id)
        _remove_work_from_folder(db, work_id=merge_work_id, folder_id=folder.id)


def _migrate_note(db: Session, *, library_id: int, keep_work_id: int, merge_work_id: int) -> None:
    """两边都写了笔记时拼接保留，谁的都不丢——"合并"这个动作本身就是在
    向用户承诺"信息不会因为选了保留哪一条而消失"，笔记是用户手写的自由
    文本，没有字段级的"该信哪边"规则可用，只能原样拼起来留给用户自己
    整理。
    """
    merge_note = get_note(db, merge_work_id)
    if merge_note is None:
        return
    keep_note = get_note(db, keep_work_id)
    if keep_note is None:
        merged_content = merge_note.content
    else:
        merged_content = f"{keep_note.content}\n\n---\n\n{merge_note.content}"
    set_note(db, library_id=library_id, work_id=keep_work_id, content=merged_content)
    set_note(db, library_id=library_id, work_id=merge_work_id, content=None)


def _migrate_attachments(db: Session, *, library_id: int, keep_work_id: int, merge_work_id: int) -> None:
    """迁移进来的附件统一落成 "other" 角色，即使原本在 merge_work 上是
    "main"——不去猜"两个 main 该留哪个"，避免悄悄顶掉 keep_work 用户已经
    选定的 main。真要换 main，合并后用户自己用 `set_main_attachment` 选一次
    更明确。

    先在 keep_work 下建一条引用同一个 digest 的新附件行，再删 merge_work
    那条旧行——这个顺序保证 `delete_attachment` 在判断"是否变孤儿"的那一刻
    总能看到新行也在引用这个 digest，不会误删仍在用的 blob（和
    `features/uploading` 的 overwrite 顺序坑同一个原理）。按这个顺序，
    `delete_attachment` 在这里永远返回 `False`（从未孤儿过），所以合并
    这个操作全程不需要调用任何 blob 删除，不需要 `caps/blobstore`。
    """
    for attachment in list_attachments_for_work(db, merge_work_id):
        create_attachment(
            db, library_id=library_id, work_id=keep_work_id, filename=attachment.filename,
            digest=attachment.digest, size=attachment.size, role="other",
            rel_path=attachment.rel_path, content_type=attachment.content_type,
        )
        _delete_attachment_row(db, attachment.id)


def merge_works(db: Session, *, library_id: int, keep_work_id: int, merge_work_id: int) -> WorkDTO:
    """把 `merge_work_id` 合并进 `keep_work_id`：迁移标签/文件夹/笔记/
    标识符/附件后彻底删除 `merge_work_id`。

    不接收 `candidate_id` 参数——merge 完成后，任何指向 `merge_work_id`
    的 `duplicate_candidate` 行（不只是触发这次合并的那一条，包括它可能
    牵连的别的候选对）都会随 `domain.works.purge_work` 自己的级联一起被
    删掉，不需要本模块再显式 `resolve_duplicate_candidate`——那一行即将
    被删，标不标 "confirmed" 没有留存的意义。

    合并前经过 `domain.works.purge_work` 本该先软删的约定（和
    `features/organizing.purge_works` 同一道安全网）：这里先
    `soft_delete_work` 再 `purge_work`，不是多余的两步，是"彻底删除前必须
    先进回收站"这条规则在本模块内的落实。
    """
    if keep_work_id == merge_work_id:
        raise ValueError("keep_work_id 和 merge_work_id 不能是同一条记录")

    keep_work = get_work(db, keep_work_id)
    merge_work = get_work(db, merge_work_id)
    if keep_work.library_id != library_id:
        raise ValueError(f"keep_work_id 不属于这个库：{keep_work_id}")
    if merge_work.library_id != library_id:
        raise ValueError(f"merge_work_id 不属于这个库：{merge_work_id}")

    _migrate_tags(db, keep_work_id=keep_work_id, merge_work_id=merge_work_id)
    _migrate_folders(db, keep_work_id=keep_work_id, merge_work_id=merge_work_id)
    _migrate_note(db, library_id=library_id, keep_work_id=keep_work_id, merge_work_id=merge_work_id)
    _migrate_identifiers(db, library_id=library_id, keep_work_id=keep_work_id, merge_work_id=merge_work_id)
    _migrate_attachments(db, library_id=library_id, keep_work_id=keep_work_id, merge_work_id=merge_work_id)

    soft_delete_work(db, merge_work_id)
    purge_work(db, merge_work_id)

    return get_work(db, keep_work_id)
