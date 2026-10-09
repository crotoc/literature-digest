"""app/pages/library：文献库卷目列表 + 删除/回收站恢复 + 单篇打标签/文件夹/笔记/元数据编辑 + 导出/引用。

v1 范围的刻意裁剪：读路径接 features/library_browse 的
list_library_page()——按 view(all/trash) + 排序 + 分页浏览卡片列表。
写路径目前只接 features/organizing 的单项操作（work_ids=[单个 id]）：
软删/恢复、"顺手新建标签再打上"、去掉某一个标签；外加 features/annotating
的笔记读写（list_library_page 的 LibraryCard 已经带了 note 字段，省了
一次额外查询）、标量元数据编辑（标题/年份/期刊或书名/摘要，authors 等
结构化字段不在内）、整库导出（RIS/BibTeX/CSL-JSON 文本文件）和单条
引用（每张卡片下面一行格式化引用串 + 三种格式各自的下载链接）。

刻意裁剪：导出不含附件的 ZIP（要 features/uploading 落地的附件才有
意义）；格式化引用串的样式现在接的是 `features.exporting.resolve_citation_style`
（账号→站点→代码默认三级回退，账号级在 app/pages/settings 改），不再
固定写死 APA。LaTeX cite 命令/citation key 复制是多选批量场景（`cite_keys`/`cite_latex`
天生接收一组 work_ids），和这页目前全是单篇操作的调法不是一回事，留给
批量/选择集机制落地之后。单篇加入/移出文件夹接的也是 features/organizing
的单项操作（`bulk_add_to_folder`/`bulk_remove_from_folder`，work_ids=[单个
id]）——它们内部已经用 `_check_work_scope` 校验了 work_id 真的属于传入的
library_id（和 bulk_remove_tag 同一个安全模型），所以这两条路由不需要像
笔记/元数据那样另外调 `_require_work_in_library`。标签侧栏 AND 筛选、文件
夹筛选、三态勾选/全选所有筛选结果、批量打标签/移文件夹/编辑/导出（只导出
选中项而不是整库）、彻底删除、附件上传/预览这些全部留给后续增量——批量
操作要先有"选择集"
这个前端状态才有意义,而「两处已定」第 2 条明确选择集的服务端解析是
独立的一块,不该现在就为了这几个按钮囫囵顺带做了。

URL 用 `/l/<name-slug>-<id>/`——只认尾部数字 id，slug 前缀纯装饰，不校验
是否和库名匹配（库改名后旧链接依然能打开，不需要重定向）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.bibformats import SUPPORTED_FORMATS
from caps.slug import slugify
from domain.accounts import AccountDTO
from domain.folders import list_folders
from domain.libraries import LibraryDTO, LibraryNotFound, list_libraries_for_account, resolve_scope
from domain.works import WorkNotFound, get_work, list_work_ids
from features.annotating import set_work_note, update_metadata
from features.exporting import cite_formatted, cite_record_text, export_bibliography, resolve_citation_style
from features.library_browse import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT_BY,
    DEFAULT_SORT_DIR,
    DEFAULT_VIEW,
    SORT_KEYS,
    VIEWS,
    list_library_page,
)
from features.organizing import (
    bulk_add_to_folder,
    bulk_remove_from_folder,
    bulk_remove_tag,
    bulk_restore,
    bulk_soft_delete,
    create_tag_and_apply,
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


def _require_library(session, account_id: int, slug_and_id: str) -> int:
    library_id = _parse_library_id(slug_and_id)
    try:
        resolve_scope(session, account_id=account_id, library_id=library_id)
    except LibraryNotFound:
        raise HTTPException(status_code=404, detail="文库不存在或你不是它的成员") from None
    return library_id


def _require_work_in_library(session, library_id: int, work_id: int):
    """features/organizing 的批量操作自己做了跨库 work_id 校验
    （`_check_work_scope`），但 features/annotating 的 `set_work_note` /
    `update_metadata` 都只认 work_id、从不核对它是不是真的属于传入的
    `library_id`——两者都是直接拿 work_id 查/改行，`library_id` 参数只用来
    给新建的笔记行或重复扫描定范围，不是访问控制。所以这个检查必须留在
    页面这一层，否则账号 A 能对着自己的文库 URL、拿一个属于账号 B 的
    work_id 去改 B 的笔记/元数据。
    """
    try:
        work = get_work(session, work_id)
    except WorkNotFound:
        raise HTTPException(status_code=404, detail="文献不存在") from None
    if work.library_id != library_id:
        raise HTTPException(status_code=404, detail="文献不存在或不属于这个文库") from None
    return work


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

    # 逐条 N+1 查询——和 library_browse 自己给 tags/folders/attachments 用的
    # 是同一个权衡（见它自己的 docstring）：一页几十条，批量版本不值得为此
    # 多开一个 features/exporting 的函数。
    #
    # 只在 all 视图算：cite_formatted 内部调 domain.works.get_work 时不认
    # include_deleted 这个参数（它自己没打算支持"引用一篇已经软删的文献"），
    # trash 视图里全是软删条目，调了必定 WorkNotFound。这不是裁剪掉一个
    # 真的能用的功能，是 features/exporting 这一层本来就没有这个能力——
    # 回收站是"要不要恢复"的分流页，不是编辑/引用的地方，和下面模板里
    # 笔记/元数据编辑面板只在 all 视图露出是同一个方向。
    citation_by_work_id = {}
    if view == "all":
        citation_style = resolve_citation_style(session, account_id=account.id)
        citation_by_work_id = {
            card.work.id: cite_formatted(session, work_id=card.work.id, style=citation_style)
            for card in library_page.items
        }

    # 给每张卡片"加入文件夹"下拉用——库内文件夹数量级和标签池一样小，
    # 一次查询够用，不需要为此单开 features 函数。
    folders = list_folders(session, library_id=library_id)

    return templates.TemplateResponse(
        request,
        "library/index.html",
        {
            "active_nav": "library",
            "slug_and_id": slug_and_id,
            "library_page": library_page,
            "citation_by_work_id": citation_by_work_id,
            "folders": folders,
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


@router.post("/l/{slug_and_id}/works/{work_id}/folders/add")
def add_folder_route(
    slug_and_id: str,
    work_id: int,
    folder_id: str = Form(""),
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
    # bulk_add_to_folder 内部的 _check_work_scope 校验不通过时抛的是裸
    # ValueError（会变成未处理的 500），不是像 _require_work_in_library
    # 这样的 HTTPException(404)——这里先自己查一遍，把跨库 work_id 这个
    # 攻击面挡在页面层，和笔记/元数据路由统一成同一种失败方式。
    _require_work_in_library(session, library_id, work_id)
    folder_id_text = folder_id.strip()
    if folder_id_text:
        # 没选文件夹（下拉空值）直接忽略、不报错——和空标签名同一个尺度。
        if not folder_id_text.isdigit():
            raise HTTPException(status_code=400, detail="folder_id 不是合法的 id")
        # folder_id 不属于这个库会从 bulk_add_to_folder 内部直接抛
        # ValueError，和 tag_id 越界时的既有行为一致，不在这里额外兜底。
        bulk_add_to_folder(
            session,
            account_id=account.id,
            library_id=library_id,
            work_ids=[work_id],
            folder_id=int(folder_id_text),
        )
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/folders/{folder_id}/remove")
def remove_folder_route(
    slug_and_id: str,
    work_id: int,
    folder_id: int,
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
    _require_work_in_library(session, library_id, work_id)
    bulk_remove_from_folder(
        session, account_id=account.id, library_id=library_id, work_ids=[work_id], folder_id=folder_id
    )
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/note")
def save_note_route(
    slug_and_id: str,
    work_id: int,
    content: str = Form(""),
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
    _require_work_in_library(session, library_id, work_id)
    # 空白内容按"删这条笔记"处理——domain.notes.set_note 自己的语义是
    # content=None 即删除；这里把"用户把文本框清空再保存"自然映射过去，
    # 不需要单独一个"删除笔记"按钮。
    set_work_note(session, library_id=library_id, work_id=work_id, content=content.strip() or None)
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/edit")
def edit_metadata_route(
    slug_and_id: str,
    work_id: int,
    title: str = Form(""),
    year: str = Form(""),
    container_title: str = Form(""),
    abstract: str = Form(""),
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
    _require_work_in_library(session, library_id, work_id)

    year_text = year.strip()
    if not year_text:
        year_value = None
    elif year_text.isdigit():
        year_value = int(year_text)
    else:
        raise HTTPException(status_code=400, detail="年份必须是数字")

    # update_metadata 用 _UNSET 哨兵区分"没传"和"传了 None"——这里四个
    # 字段永远一起传（哪怕没变），就不需要先比对旧值再决定传不传，代价是
    # 每次保存都等价于"全量覆盖这四个字段"，比"只传真正变了的字段"简单，
    # 够用。authors/identifiers 等其余字段这个表单不碰，留给再下一个增量
    # （authors 是结构化列表，牵扯另一套输入设计，不该和这四个标量字段
    # 混在一个决策里）。
    update_metadata(
        session,
        library_id=library_id,
        work_id=work_id,
        title=title.strip() or None,
        year=year_value,
        container_title=container_title.strip() or None,
        abstract=abstract.strip() or None,
    )
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


_EXPORT_CONTENT_TYPES = {
    "ris": "application/x-research-info-systems",
    "bibtex": "text/x-bibtex",
    "csljson": "application/json",
}
_EXPORT_EXTENSIONS = {"ris": "ris", "bibtex": "bib", "csljson": "json"}


@router.get("/l/{slug_and_id}/export")
def export_library_route(
    slug_and_id: str,
    format: str = "ris",
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    if format not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式：{format!r}")

    # 只导出未删除的条目——回收站里的东西用户已经表示不要了，导出当作
    # "还在库里的内容快照"，跟 library_browse 的 all 视图默认排除已删
    # 是同一个方向。不分页、不走选择集：v1 范围就是"整库"，批量/选中项
    # 导出留给选择集机制落地之后。
    work_ids = list_work_ids(session, library_id=library_id)
    body = export_bibliography(session, library_id=library_id, work_ids=work_ids, format=format)

    ext = _EXPORT_EXTENSIONS[format]
    return Response(
        content=body,
        media_type=_EXPORT_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="library.{ext}"'},
    )


@router.get("/l/{slug_and_id}/works/{work_id}/cite")
def cite_work_route(
    slug_and_id: str,
    work_id: int,
    format: str = "ris",
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_work_in_library(session, library_id, work_id)

    if format not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式：{format!r}")

    body = cite_record_text(session, work_id=work_id, format=format)

    ext = _EXPORT_EXTENSIONS[format]
    return Response(
        content=body,
        media_type=_EXPORT_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="work-{work_id}.{ext}"'},
    )
