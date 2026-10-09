"""app/pages/dedupe：疑似重复文献人工复核——驳回或合并。

接 features/dedupe_review 的三个操作：`list_pending_candidates`（展开成
左右两篇完整文献的对照视图）、`bulk_dismiss_candidates`（驳回，单项
批量，`candidate_ids=[单个 id]`）、`merge_works`（合并，迁移标签/文件夹
/笔记/标识符/附件后彻底删除其中一条）。

和 app/pages/folders、app/pages/library 不同的一点：这页的两个写操作
**不需要**页面层另外补归属校验——`bulk_dismiss_candidates` 内部按
`library_id` 过滤出当前"pending"候选索引，candidate_id 不在索引里就
统一报错；`merge_works` 自己校验 `keep_work_id`/`merge_work_id` 是否
真的属于传入的 `library_id`。这里仍然遵循"页面层决定要不要查、domain/
features 决定怎么查"的原则，只是这次两个 feature 函数都已经做对了，
不需要再在页面上重复一遍。

merge 路由不直接信任客户端传来的 work_id——表单只传 `candidate_id` +
`keep`（"work" 或 "candidate"，表示保留对照视图里的左边还是右边），
服务端自己从这个库当前的 pending 候选里查出真正的 work_id/
candidate_work_id 对，避免篡改表单传入一个不相关的 work_id 当作
"要保留的那一条"。

v1 范围裁剪：不做批量勾选多组候选一次性驳回/合并（候选列表通常不大，
逐组操作够用，批量勾选留给"选择集"机制落地之后）；不做"候选原因"的
筛选/排序（`reason` 字段目前只有一种取值，没有筛选的意义）。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.slug import slugify
from domain.libraries import LibraryDTO, LibraryNotFound, list_libraries_for_account, resolve_scope
from domain.works import list_duplicate_candidates
from features.dedupe_review import bulk_dismiss_candidates, list_pending_candidates, merge_works

router = APIRouter()
nav = NavItem(key="dedupe", label="去重", path="/dedupe", icon="copy", order=4)


def _library_url(library: LibraryDTO) -> str:
    return f"/l/{slugify(library.name, separator='-')}-{library.id}/"


def _parse_library_id(slug_and_id: str) -> int:
    _, _, tail = slug_and_id.rpartition("-")
    if not tail.isdigit():
        raise HTTPException(status_code=404, detail="不是合法的文库地址")
    return int(tail)


def _require_library(session, account_id: int, slug_and_id: str) -> int:
    library_id = _parse_library_id(slug_and_id)
    try:
        resolve_scope(session, account_id=account_id, library_id=library_id)
    except LibraryNotFound:
        raise HTTPException(status_code=404, detail="文库不存在或你不是它的成员") from None
    return library_id


def _back_to_list(slug_and_id: str) -> RedirectResponse:
    return RedirectResponse(f"/l/{slug_and_id}/dedupe/", status_code=303)


def _back_to_list_with_error(slug_and_id: str, error: str) -> RedirectResponse:
    return RedirectResponse(f"/l/{slug_and_id}/dedupe/?error={quote(error)}", status_code=303)


@router.get("/dedupe", response_class=HTMLResponse)
def dedupe_entry(account=Depends(current_account), session=Depends(db)):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    libraries = list_libraries_for_account(session, account.id)
    if not libraries:
        raise HTTPException(status_code=500, detail="这个账号没有任何文库")
    return RedirectResponse(f"{_library_url(libraries[0])}dedupe/", status_code=303)


@router.get("/l/{slug_and_id}/dedupe/", response_class=HTMLResponse)
def dedupe_view(
    slug_and_id: str,
    request: Request,
    error: str | None = None,
    account=Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    pairs = list_pending_candidates(session, library_id=library_id)

    return templates.TemplateResponse(
        request,
        "dedupe/index.html",
        {
            "active_nav": "dedupe",
            "slug_and_id": slug_and_id,
            "pairs": pairs,
            "error": error,
        },
    )


@router.post("/l/{slug_and_id}/dedupe/{candidate_id}/dismiss")
def dismiss_candidate_route(
    slug_and_id: str,
    candidate_id: int,
    account=Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    result = bulk_dismiss_candidates(
        session, account_id=account.id, library_id=library_id, candidate_ids=[candidate_id]
    )
    outcome = result.outcomes[0]
    if outcome.status == "failed":
        return _back_to_list_with_error(slug_and_id, outcome.reason or "驳回失败")
    return _back_to_list(slug_and_id)


@router.post("/l/{slug_and_id}/dedupe/{candidate_id}/merge")
def merge_candidate_route(
    slug_and_id: str,
    candidate_id: int,
    keep: str = Form(...),
    account=Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    if keep not in ("work", "candidate"):
        raise HTTPException(status_code=400, detail="keep 必须是 work 或 candidate")

    pending = {c.id: c for c in list_duplicate_candidates(session, library_id=library_id, status="pending")}
    candidate = pending.get(candidate_id)
    if candidate is None:
        return _back_to_list_with_error(slug_and_id, "候选不存在、不属于这个库，或已经被处理过")

    if keep == "work":
        keep_work_id, merge_work_id = candidate.work_id, candidate.candidate_work_id
    else:
        keep_work_id, merge_work_id = candidate.candidate_work_id, candidate.work_id

    try:
        merge_works(session, library_id=library_id, keep_work_id=keep_work_id, merge_work_id=merge_work_id)
    except ValueError as error:
        return _back_to_list_with_error(slug_and_id, str(error))
    return _back_to_list(slug_and_id)
