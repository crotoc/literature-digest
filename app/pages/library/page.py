"""app/pages/library：文献库卷目列表（只读）。

v1 范围的刻意裁剪：本增量只接 features/library_browse 的
list_library_page()——按 view(all/trash) + 排序 + 分页浏览卡片列表。
标签侧栏 AND 筛选、文件夹筛选、三态勾选/全选所有筛选结果、批量打标签/
移文件夹/删除、单篇编辑、附件上传/预览这些全部留给后续增量（各自对应
organizing/annotating/uploading/pdf_reading 等 feature，还没建页面）。
这样本页面只依赖 library_browse 一个 feature，和已有的 auth/home 保持
同一个"一页对一个主 feature"的节奏，不提前画一张本增量兑现不了的大饼。

URL 用 `/l/<name-slug>-<id>/`——只认尾部数字 id，slug 前缀纯装饰，不校验
是否和库名匹配（库改名后旧链接依然能打开，不需要重定向）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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

router = APIRouter()
nav = NavItem(key="library", label="文献库", path="/library", icon="library", order=1)


def _library_url(library: LibraryDTO) -> str:
    return f"/l/{slugify(library.name, separator='-')}-{library.id}/"


def _parse_library_id(slug_and_id: str) -> int:
    _, _, tail = slug_and_id.rpartition("-")
    if not tail.isdigit():
        raise HTTPException(status_code=404, detail="不是合法的文库地址")
    return int(tail)


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

    library_id = _parse_library_id(slug_and_id)
    try:
        resolve_scope(session, account_id=account.id, library_id=library_id)
    except LibraryNotFound:
        raise HTTPException(status_code=404, detail="文库不存在或你不是它的成员") from None

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
