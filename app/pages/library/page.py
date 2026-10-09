"""app/pages/library：文献库卷目列表 + 删除/回收站恢复 + 单篇打标签。

v1 范围的刻意裁剪：读路径接 features/library_browse 的
list_library_page()——按 view(all/trash) + 排序 + 分页浏览卡片列表。
写路径目前只接 features/organizing 的单项操作（work_ids=[单个 id]）：
软删/恢复、"顺手新建标签再打上"、去掉某一个标签。标签侧栏 AND 筛选、
文件夹筛选、三态勾选/全选所有筛选结果、批量打标签/移文件夹、彻底删除、
单篇编辑、附件上传/预览这些全部留给后续增量——批量操作要先有"选择集"这个
前端状态才有意义,而「两处已定」第 2 条明确选择集的服务端解析是独立的
一块,不该现在就为了这几个按钮囫囵顺带做了。

URL 用 `/l/<name-slug>-<id>/`——只认尾部数字 id，slug 前缀纯装饰，不校验
是否和库名匹配（库改名后旧链接依然能打开，不需要重定向）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.slug import slugify
from domain.accounts import AccountDTO
from domain.libraries import LibraryDTO, LibraryNotFound, list_libraries_for_account, resolve_scope
from features.library_browse import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT_BY,
    DEFAULT_SORT_DIR,
    DEFAULT_VIEW,
    SORT_KEYS,
    VIEWS,
    list_library_page,
)
from features.organizing import bulk_remove_tag, bulk_restore, bulk_soft_delete, create_tag_and_apply

router = APIRouter()
nav = NavItem(key="library", label="文献库", path="/library", icon="library", order=1)


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


def _back_to_list(slug_and_id: str, *, view: str, sort_by: str, sort_dir: str, page: int) -> RedirectResponse:
    url = f"/l/{slug_and_id}/?view={view}&sort_by={sort_by}&sort_dir={sort_dir}&page={page}"
    return RedirectResponse(url, status_code=303)


@router.get("/library", response_class=HTMLResponse)
def library_entry(account: AccountDTO | None = Depends(current_account), session=Depends(db)):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    libraries = list_libraries_for_account(session, account.id)
    if not libraries:
        # 注册时必然自动建一个同名个人库（features/accounts_auth.register），
        # 走到这里说明数据状态本身坏了，不是用户能自己恢复的场景。
        raise HTTPException(status_code=500, detail="这个账号没有任何文库")
    return RedirectResponse(_library_url(libraries[0]), status_code=303)


@router.get("/l/{slug_and_id}/", response_class=HTMLResponse)
def library_view(
    slug_and_id: str,
    request: Request,
    view: str = DEFAULT_VIEW,
    sort_by: str = DEFAULT_SORT_BY,
    sort_dir: str = DEFAULT_SORT_DIR,
    page: int = Query(1, ge=1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    library_id = _require_library(session, account.id, slug_and_id)

    if view not in VIEWS or sort_by not in SORT_KEYS or sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="筛选/排序参数不对")

    page_size = DEFAULT_PAGE_SIZE
    library_page = list_library_page(
        session,
        library_id=library_id,
        view=view,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    total_pages = max(1, -(-library_page.total // page_size))

    return templates.TemplateResponse(
        request,
        "library/index.html",
        {
            "active_nav": "library",
            "slug_and_id": slug_and_id,
            "library_page": library_page,
            "view": view,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "page": page,
            "total_pages": total_pages,
        },
    )


@router.post("/l/{slug_and_id}/works/{work_id}/delete")
def delete_work(
    slug_and_id: str,
    work_id: int,
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    bulk_soft_delete(session, account_id=account.id, library_id=library_id, work_ids=[work_id])
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/restore")
def restore_work_route(
    slug_and_id: str,
    work_id: int,
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    bulk_restore(session, account_id=account.id, library_id=library_id, work_ids=[work_id])
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/tags/add")
def add_tag_route(
    slug_and_id: str,
    work_id: int,
    name: str = Form(""),
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    if name.strip():
        # 空名字直接忽略、不报错——这页目前没有"操作失败请提示"这套机制，
        # 和删除/恢复按钮一样；真要做统一在后续增量一起加。
        create_tag_and_apply(
            session, account_id=account.id, library_id=library_id, work_ids=[work_id], name=name
        )
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/tags/{tag_id}/remove")
def remove_tag_route(
    slug_and_id: str,
    work_id: int,
    tag_id: int,
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    bulk_remove_tag(session, account_id=account.id, library_id=library_id, work_ids=[work_id], tag_id=tag_id)
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)
