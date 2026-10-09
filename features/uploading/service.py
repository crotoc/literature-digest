"""features/uploading 的业务逻辑：接收上传的字节，校验（`caps/fileprobe`）
+ 内容寻址存储（`caps/blobstore`）+ 挂到一篇文献名下（`domain/attachments`），
外加重名策略（ask/overwrite/rename）的编排和"整目录批量上传"的瞬时批量 job
追踪（`domain/jobs`）。

组合 `caps/{fileprobe,blobstore,template}` + `domain/{attachments,jobs,
settings}`。本模块不开表。

**依赖 `domain/works`，尽管计划表里只写了 `domain/{attachments,jobs}`**：
`domain/attachments/README.md` 的刻意裁剪范围里明确写着"验证 work_id 对应
的文献确实存在——调用方职责（本模块不 import domain.works）"，本模块就是
那个调用方，所以这里补上这一步（防御性的边界校验，不是访问控制，访问控制
仍然是调用方 `app/pages` 在路由层做的事，和 `features/exporting`/
`features/metadata_lookup` 同一个套路）。重命名模板还需要用到文献的
标题/年份/第一作者，这也要求本模块认识 `domain.works.WorkDTO`。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import BinaryIO

from caps.fileprobe import FileProbeError, check_upload
from caps.template import ON_MISSING_EMPTY, render_bracket, validate_bracket_template
from domain.attachments import (
    DEFAULT_ROLE,
    AttachmentDTO,
    create_attachment,
    delete_attachment,
    get_attachment,
    list_attachments_for_work,
)
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.settings import resolve_setting, set_account_setting
from domain.works import WorkDTO, get_work
from infra.errors import Conflict, ValidationFailed

ON_CONFLICT_POLICIES = frozenset({"ask", "overwrite", "rename"})
DEFAULT_ON_CONFLICT = "rename"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024

SETTINGS_MODULE = "uploading"
NAMING_TEMPLATE_SETTING_KEY = "attachment_filename_template"
_NAMING_TEMPLATE_KEYS = ("firstauthor", "year", "title")

JOB_KIND = "upload_batch"
JOB_KIND_ITEM = "upload_batch_item"
# 瞬时批量：父 job 一行 + counts_json，只有失败项才建子行——和"打标签/
# 删除/加移文件夹"同一类，不是"导入"那一类。理由见本模块 README：批量
# 上传里每一项的成功结果没有值得单独留痕的业务含义（不像导入要区分
# created/merged/flagged_duplicate），只有失败原因值得单独记一行。
_PARENT_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}
_CHILD_TRANSITIONS = {"queued": {"failed"}}


class UploadRejected(ValidationFailed):
    """`caps.fileprobe.check_upload` 校验未通过（太大/类型不允许/损坏）时
    统一包装——调用方不需要分别认识 `FileTooLarge`/`UnsupportedMediaType`/
    `CorruptFile` 三种类型。"""

    code = "upload_rejected"


class FilenameConflict(Conflict):
    """`on_conflict="ask"` 时，撞到已有同名附件——本模块不替用户决定覆盖
    还是重命名，回 UI 问人。"""

    code = "filename_conflict"


@dataclass(frozen=True)
class UploadInput:
    filename: str
    content: bytes | BinaryIO
    rel_path: str | None = None
    role: str = DEFAULT_ROLE


@dataclass(frozen=True)
class UploadOutcome:
    filename: str
    status: str  # "created" | "failed"
    attachment_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BatchUploadResult:
    job: JobDTO
    outcomes: tuple[UploadOutcome, ...]


def _check_work_scope(db, *, library_id: int, work_id: int) -> WorkDTO:
    work = get_work(db, work_id)
    if work.library_id != library_id:
        raise ValueError(f"work_id 不属于这个库：{work_id}")
    return work


def _naming_values(work: WorkDTO) -> dict:
    """和 `features/exporting` 里同名函数一字不差——两个 feature 之间禁止
    互相 import（规则 4），这段"从 WorkDTO 取文件命名用的占位值"策略值得
    各自留一份，不值得为它单独开一个 domain/caps 模块。"""
    first_author = work.authors[0] if work.authors else None
    family = (first_author.family or first_author.literal) if first_author else None
    return {
        "firstauthor": family or "unknown",
        "year": work.year if work.year is not None else "nd",
        "title": work.title or "untitled",
    }


def _resolve_filename(*, work: WorkDTO, original_filename: str, naming_template: str | None) -> str:
    if naming_template is None:
        return original_filename
    ext = os.path.splitext(original_filename)[1]
    rendered = render_bracket(naming_template, _naming_values(work), on_missing=ON_MISSING_EMPTY)
    return f"{rendered}{ext}"


def _dedupe_filename(filename: str, existing_names: set[str]) -> str:
    if filename not in existing_names:
        return filename
    stem, ext = os.path.splitext(filename)
    n = 1
    while True:
        candidate = f"{stem} ({n}){ext}"
        if candidate not in existing_names:
            return candidate
        n += 1


def _reset(content: bytes | BinaryIO) -> None:
    """`caps.fileprobe.check_upload` 要求可 seek 的流（它要回头看 PDF 结构）
    并且读完后不保证把流复位；`caps.blobstore.BlobStore.put` 不要求可 seek，
    但如果给的是同一个流对象，必须先手动复位到开头，否则算出来的摘要只是
    "探查之后剩下的那一段"，不是完整文件——两个 cap 的 README 都各自提到了
    这条差异，这里是两者相遇的地方，必须手动接上。"""
    if hasattr(content, "seek"):
        content.seek(0)


def resolve_naming_template(db, *, account_id: int) -> str | None:
    """账号级的附件重命名模板，三级回退（账号→站点→默认 `None`）。`None`
    表示"保留用户上传时的原始文件名"，不是"模板为空字符串"。"""
    return resolve_setting(
        db, module=SETTINGS_MODULE, key=NAMING_TEMPLATE_SETTING_KEY, account_id=account_id, default=None
    ).value


def set_naming_template(db, *, account_id: int, template: str | None) -> None:
    if template is not None:
        validate_bracket_template(template, allowed_keys=_NAMING_TEMPLATE_KEYS)
    set_account_setting(
        db, account_id=account_id, module=SETTINGS_MODULE, key=NAMING_TEMPLATE_SETTING_KEY, value=template
    )


def upload_file(
    db,
    *,
    library_id: int,
    work_id: int,
    filename: str,
    content: bytes | BinaryIO,
    blob_store,
    role: str = DEFAULT_ROLE,
    rel_path: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allowed_media_types: frozenset[str] | set[str] | None = None,
    on_conflict: str = DEFAULT_ON_CONFLICT,
    naming_template: str | None = None,
) -> AttachmentDTO:
    """上传一个文件，挂到 `work_id` 名下。

    `naming_template` 不给时保留原始文件名；给了就用 `caps/template` 的
    方括号语法重命名（后缀保留原始文件名的后缀）。渲染结果（或原始文件名）
    如果和这篇文献已有的某个附件撞名，按 `on_conflict` 处理：

    - `"ask"`：抛 `FilenameConflict`，本模块不替用户决定，回 UI 问人。
    - `"overwrite"`：新文件存好之后再删旧的那条附件记录（顺序很重要，
      见下方实现里的注释），旧 blob 变孤儿才真的删字节。
    - `"rename"`（默认）：追加 `" (1)"`/`" (2)"`... 直到不撞名。
    """
    if on_conflict not in ON_CONFLICT_POLICIES:
        raise ValueError(f"on_conflict 必须是 {sorted(ON_CONFLICT_POLICIES)} 之一，收到 {on_conflict!r}")

    work = _check_work_scope(db, library_id=library_id, work_id=work_id)

    try:
        probe = check_upload(content, max_bytes=max_bytes, allowed_media_types=allowed_media_types)
    except FileProbeError as exc:
        raise UploadRejected(str(exc)) from exc
    _reset(content)

    final_filename = _resolve_filename(work=work, original_filename=filename, naming_template=naming_template)

    existing = list_attachments_for_work(db, work_id)
    existing_names = {a.filename for a in existing}
    conflicting = next((a for a in existing if a.filename == final_filename), None)
    if conflicting is not None:
        if on_conflict == "ask":
            raise FilenameConflict(f"附件 {final_filename!r} 已存在")
        if on_conflict == "rename":
            final_filename = _dedupe_filename(final_filename, existing_names)
            conflicting = None

    put_result = blob_store.put(content)
    attachment = create_attachment(
        db,
        library_id=library_id,
        work_id=work_id,
        filename=final_filename,
        digest=put_result.digest,
        size=put_result.blob.size,
        role=role,
        rel_path=rel_path,
        content_type=probe.media_type,
    )

    if conflicting is not None:  # on_conflict == "overwrite"：新行已经落地，这才轮到删旧行
        orphaned = delete_attachment(db, conflicting.id)
        if orphaned:
            blob_store.delete(conflicting.digest)

    return attachment


def upload_batch(
    db,
    *,
    account_id: int,
    library_id: int,
    work_id: int,
    files: Sequence[UploadInput],
    blob_store,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allowed_media_types: frozenset[str] | set[str] | None = None,
    on_conflict: str = DEFAULT_ON_CONFLICT,
    naming_template: str | None = None,
) -> BatchUploadResult:
    """整目录/多文件批量上传。每个文件独立成败（一个坏文件不影响其它文件
    继续处理），用一个父 job 追踪整体进度，只有失败的文件才建子 job——
    详见本模块 README 对"瞬时批量 vs 长流程批量"这条规则的取舍说明。
    """
    _check_work_scope(db, library_id=library_id, work_id=work_id)

    total = len(files)
    job = create_job(
        db, account_id=account_id, kind=JOB_KIND, library_id=library_id,
        counts={"total": total, "created": 0, "failed": 0},
    )
    job = transition(db, job.id, "running", allowed=_PARENT_TRANSITIONS)

    outcomes: list[UploadOutcome] = []
    created = 0
    failed = 0
    for item in files:
        try:
            attachment = upload_file(
                db,
                library_id=library_id,
                work_id=work_id,
                filename=item.filename,
                content=item.content,
                blob_store=blob_store,
                role=item.role,
                rel_path=item.rel_path,
                max_bytes=max_bytes,
                allowed_media_types=allowed_media_types,
                on_conflict=on_conflict,
                naming_template=naming_template,
            )
        except (UploadRejected, FilenameConflict) as exc:
            failed += 1
            child = create_job(
                db, account_id=account_id, kind=JOB_KIND_ITEM, library_id=library_id, parent_job_id=job.id
            )
            transition(db, child.id, "failed", reason=str(exc), allowed=_CHILD_TRANSITIONS)
            outcomes.append(UploadOutcome(filename=item.filename, status="failed", reason=str(exc)))
        else:
            created += 1
            outcomes.append(
                UploadOutcome(filename=item.filename, status="created", attachment_id=attachment.id)
            )
        update_counts(db, job.id, {"total": total, "created": created, "failed": failed})

    final_status = "failed" if failed else "succeeded"
    job = transition(db, job.id, final_status, allowed=_PARENT_TRANSITIONS)
    return BatchUploadResult(job=job, outcomes=tuple(outcomes))


def download_attachment(db, attachment_id: int, *, blob_store) -> tuple[AttachmentDTO, BinaryIO]:
    """取一条附件的元数据 + 打开它对应的字节流。本模块不把内容读进内存，
    流交给调用方（`app/pages`）自己决定怎么往 HTTP 响应里写。"""
    attachment = get_attachment(db, attachment_id)
    return attachment, blob_store.open(attachment.digest)


def remove_attachment(db, attachment_id: int, *, blob_store) -> None:
    """删除一条附件。`domain.attachments.delete_attachment` 只回答"这个
    digest 删完是不是孤儿"，真正调 `blob_store.delete` 是本模块的职责——
    和 `domain/attachments/README.md` 里说的分工一致。digest 必须在删行
    *之前*取到，`delete_attachment` 本身不返回它。"""
    attachment = get_attachment(db, attachment_id)
    orphaned = delete_attachment(db, attachment_id)
    if orphaned:
        blob_store.delete(attachment.digest)
