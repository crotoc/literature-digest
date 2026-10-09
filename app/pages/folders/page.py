"""app/pages/folders：文件夹的建/改名/移动嵌套/删除。

domain/folders 本身没有表 library_id 可以跳过的"系统文件夹"概念，也不
认识 work_id 属于哪个库——`rename_folder`/`move_folder`/`delete_folder`
三个函数签名里**根本没有 `library_id` 参数**，只认 `folder_id`，从不
核对调用方传来的文库和这个文件夹是不是同一个。和 app/pages/library 里
`_require_work_in_library` 要解决的是同一类问题：这个检查天生就该长在
页面这一层，不是 domain 的责任（domain 机械、不认权限）。

v1 范围的刻意裁剪：
  - 把文献加入/移出文件夹（`add_work_to_folder`/`remove_work_from_folder`）
    不在本增量——这是文献库卡片上的操作，和"管理文件夹树本身"是两个不同
    的 UI 入口，归 app/pages/library 的下一个增量。
  - 这页只管文件夹树的结构，不管每个文件夹里有哪些文献（那是
    library_browse 的"按文件夹筛选"，本来就该在文献库页面里，不是这里）。
  - 删除一个文件夹会级联删掉它的全部子文件夹（domain.folders.delete_folder
    自己的语义，自底向上删），但从不删任何 work——只清理 work↔folder 的
    关联行。页面上没做"删除前先确认子树有几层/多少篇文献受影响"这类二次
    确认，和文献库页面删除/恢复按钮同样没有确认弹窗是一致的尺度。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.slug import slugify
from domain.accounts import AccountDTO
from domain.folders import (
    FolderCycle,
    FolderDTO,
    FolderNotFound,
    create_folder,
    delete_folder,
    get_folder,
    list_folders,
    move_folder,
    rename_folder,
)
from domain.libraries import LibraryDTO, LibraryNotFound, list_libraries_for_account, resolve_scope

router = APIRouter()
nav = NavItem(key="folders", label="文件夹", path="/folders", icon="folder", order=3)


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


def _require_folder_in_library(session, library_id: int, folder_id: int) -> FolderDTO:
    try:
        folder = get_folder(session, folder_id)
    except FolderNotFound:
        raise HTTPException(status_code=404, detail="文件夹不存在") from None
    if folder.library_id != library_id:
        raise HTTPException(status_code=404, detail="文件夹不存在或不属于这个文库") from None
    return folder


def _flatten_tree(folders: list[FolderDTO]) -> list[tuple[FolderDTO, int]]:
    """按父子关系拼成一份"父节点后面紧跟它的子节点"的缩进扁平列表，模板
    直接按 depth 加缩进渲染，不需要递归 Jinja 宏。
    """
    children_by_parent: dict[int | None, list[FolderDTO]] = {}
    for folder in folders:
        children_by_parent.setdefault(folder.parent_folder_id, []).append(folder)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda f: f.name)

    ordered: list[tuple[FolderDTO, int]] = []

    def visit(parent_id: int | None, depth: int) -> None:
        for folder in children_by_parent.get(parent_id, []):
            ordered.append((folder, depth))
            visit(folder.id, depth + 1)

    visit(None, 0)
    return ordered


def _back_to_list(slug_and_id: str) -> RedirectResponse:
    return RedirectResponse(f"/l/{slug_and_id}/folders/", status_code=303)


@router.get("/folders", response_class=HTMLResponse)
def folders_entry(account: AccountDTO | None = Depends(current_account), session=Depends(db)):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    libraries = list_libraries_for_account(session, account.id)
    if not libraries:
        raise HTTPException(status_code=500, detail="这个账号没有任何文库")
    return RedirectResponse(f"{_library_url(libraries[0])}folders/", status_code=303)


@router.get("/l/{slug_and_id}/folders/", response_class=HTMLResponse)
def folders_view(
    slug_and_id: str,
    request: Request,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    error: str | None = None,
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    folders = list_folders(session, library_id=library_id)
    tree = _flatten_tree(folders)

    return templates.TemplateResponse(
        request,
        "folders/index.html",
        {
            "active_nav": "folders",
            "slug_and_id": slug_and_id,
            "tree": tree,
            "folders": folders,
            "error": error,
        },
    )


@router.post("/l/{slug_and_id}/folders/create")
def create_folder_route(
    slug_and_id: str,
    name: str = Form(""),
    parent_folder_id: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    if not name.strip():
        return RedirectResponse(
            f"/l/{slug_and_id}/folders/?error={quote('文件夹名不能为空')}", status_code=303
        )

    parent_id: int | None = None
    if parent_folder_id.strip():
        if not parent_folder_id.isdigit():
            raise HTTPException(status_code=400, detail="parent_folder_id 不是合法的 id")
        parent_id = int(parent_folder_id)
        _require_folder_in_library(session, library_id, parent_id)

    create_folder(session, library_id=library_id, name=name, parent_folder_id=parent_id)
    return _back_to_list(slug_and_id)


@router.post("/l/{slug_and_id}/folders/{folder_id}/rename")
def rename_folder_route(
    slug_and_id: str,
    folder_id: int,
    name: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_folder_in_library(session, library_id, folder_id)

    if not name.strip():
        return RedirectResponse(
            f"/l/{slug_and_id}/folders/?error={quote('文件夹名不能为空')}", status_code=303
        )

    rename_folder(session, folder_id, name)
    return _back_to_list(slug_and_id)


@router.post("/l/{slug_and_id}/folders/{folder_id}/move")
def move_folder_route(
    slug_and_id: str,
    folder_id: int,
    new_parent_folder_id: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_folder_in_library(session, library_id, folder_id)

    new_parent_id: int | None = None
    if new_parent_folder_id.strip():
        if not new_parent_folder_id.isdigit():
            raise HTTPException(status_code=400, detail="new_parent_folder_id 不是合法的 id")
        new_parent_id = int(new_parent_folder_id)
        _require_folder_in_library(session, library_id, new_parent_id)

    try:
        move_folder(session, folder_id, new_parent_id)
    except FolderCycle as error:
        return RedirectResponse(
            f"/l/{slug_and_id}/folders/?error={quote(error.message)}", status_code=303
        )
    return _back_to_list(slug_and_id)


@router.post("/l/{slug_and_id}/folders/{folder_id}/delete")
def delete_folder_route(
    slug_and_id: str,
    folder_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_folder_in_library(session, library_id, folder_id)

    delete_folder(session, folder_id)
    return _back_to_list(slug_and_id)
