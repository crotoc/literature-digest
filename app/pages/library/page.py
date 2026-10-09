"""app/pages/library：文献库卷目列表 + 删除/回收站恢复 + 单篇打标签/文件夹/笔记/元数据编辑/附件 + 导出/引用。

v1 范围的刻意裁剪：读路径接 features/library_browse 的
list_library_page()——按 view(all/trash) + 排序 + 分页浏览卡片列表。
写路径目前只接 features/organizing 的单项操作（work_ids=[单个 id]）：
软删/恢复、"顺手新建标签再打上"、去掉某一个标签；外加 features/annotating
的笔记读写（list_library_page 的 LibraryCard 已经带了 note 字段，省了
一次额外查询）、标量元数据编辑（标题/年份/期刊或书名/摘要，authors 等
结构化字段不在内）、整库导出（RIS/BibTeX/CSL-JSON 文本文件）和单条
引用（每张卡片下面一行格式化引用串 + 三种格式各自的下载链接）。

整库导出现在有两种产物：纯文本参考文献文件，和"RIS/BibTeX/CSL-JSON +
附件原文件"的 ZIP（`export_with_attachments_zip`，导出表单的"含附件"
勾选框决定走哪条）——这是计划「③ 导出」里"RIS + PDF ZIP"这一行，之前
没做是因为要等 features/uploading 落地的附件才有意义，现在附件已经有了。
格式化引用串的样式现在接的是 `features.exporting.resolve_citation_style`
（账号→站点→代码默认三级回退，账号级在 app/pages/settings 改），不再
固定写死 APA。LaTeX cite 命令/citation key 复制是多选批量场景（`cite_keys`/`cite_latex`
天生接收一组 work_ids），和这页目前全是单篇操作的调法不是一回事，留给
批量/选择集机制落地之后。单篇加入/移出文件夹接的也是 features/organizing
的单项操作（`bulk_add_to_folder`/`bulk_remove_from_folder`，work_ids=[单个
id]）——它们内部已经用 `_check_work_scope` 校验了 work_id 真的属于传入的
library_id（和 bulk_remove_tag 同一个安全模型），所以这两条路由不需要像
笔记/元数据那样另外调 `_require_work_in_library`。

侧栏筛选（标签 AND 筛选 + 单选文件夹）接的是 `features.library_browse`
早就支持的 `tag_ids`/`folder_id` 参数——`list_library_page` 和背后的
`resolve_selection`（给「两处已定」第 2 条"全选所有筛选结果"用）本来就
认识这两个参数，只是页面层这次才接上，不是重新设计。**刻意裁剪**：
`tag_ids`/`folder_id` 只在 GET 这条读路径上生效，单篇操作（打标签/删除/
编辑等）走的各个 POST 路由重定向回列表页时**不会**保留当前筛选——和
这页本来就有的"点全部/回收站 tab 会丢排序方向"是同一类已接受的粗糙边
（`_back_to_list` 只认 view/sort_by/sort_dir/page 四个字段），真要补齐
需要把 tag_ids/folder_id 一并塞进 `hidden_state()` 宏和全部 ~13 个
`_back_to_list` 调用点,属于下一个增量的范围,不在这次顺带做。"未归档"
（没有任何文件夹的文献）不支持——`_compile_filter_work_ids` 的
`folder_id` 语义是"属于这个文件夹"，没有"不属于任何文件夹"这个反向
查询。

批量操作（`/l/{slug_and_id}/batch`）接的是 `features.organizing` 的
`bulk_add_tag`/`bulk_remove_tag`/`bulk_add_to_folder`/`bulk_remove_from_folder`/
`bulk_soft_delete`/`bulk_restore`/`purge_works`——这次选区只认页面上
**显式勾选**的 checkbox（`work_ids` 原样提交），不接「两处已定」第 2 条
"全选所有筛选结果"（`resolve_selection` 的 `mode="all_filtered"`，那个
还要把 tag_ids/folder_id 筛选条件也带进请求，留给下一个增量）。
`work_ids` 列表里混进不属于这个库的 id 不需要在页面层单独挡——
`_run_batch` 对每一项单独走 `_check_work_scope`，校验失败只记一条
failed outcome、不中断其余项、不抛到页面层，和 `features/dedupe_review`
那次一样是"feature 自己已经做对了,页面层不需要再包一层"；但
`target_tag_id`/`target_folder_id` 本身不属于这个库时是顶层直接抛
`ValueError`（这两个值只有一份，不逐项校验），页面层接住转成提示。
批量彻底删除（`purge`）额外要求勾选 `confirm_purge`——这个操作不可
撤销，批量放大了误点的影响面，比单篇删除多一道确认合理。批量导出
（只导出选中项而不是整库）、`cite_keys`/`cite_latex` 批量引用复制，
仍然和"全选所有筛选结果"一起留给下一个增量。

附件这次只接 features/uploading 的单文件上传/下载/删除三个单项操作，
`BlobStore` 实例由 app/shell/deps.py 的 `blob_store()` 依赖注入（装配层
在启动时组出 `BlobStore(LocalFsBackend(...))`，caps/blobstore 自己不认识
adapters/storage，这条线必须在这一层接）。v1 范围裁剪：不做
`webkitdirectory` 整目录批量上传（`upload_batch`，这次每次只传一个文件）、
重名策略固定用默认的 `"rename"`（追加编号），不开 UI 让用户选
ask/overwrite——那是"重名策略"这个独立决策，不该现在顺带定下来。
`upload_file`/`remove_attachment`/`download_attachment` 三个函数都只认
`attachment_id`、不核对它是不是真的属于传入的 `library_id`（`upload_file`
内部会查 `work_id` 的库，但 `remove_attachment`/`download_attachment`
两个连 `work_id` 都不查，直接拿 `attachment_id` 查改）——和
`_require_work_in_library` 同一类问题，所以这里也在页面层补了
`_require_attachment_in_library`。

本增量补上"设为 Main PDF"（`domain.attachments.set_main_attachment`）和
内嵌查看（`features.pdf_reading.open_for_view`，只对
`VIEWABLE_CONTENT_TYPES` 白名单内的类型——目前只有 `application/pdf`——
给一个浏览器原生渲染的查看链接，`Content-Disposition: inline`，不是
下载）。`set_main_attachment` 同样只认 `attachment_id`、不核对
`library_id`，复用已有的 `_require_attachment_in_library` 补上这道检查。

URL 用 `/l/<name-slug>-<id>/`——只认尾部数字 id，slug 前缀纯装饰，不校验
是否和库名匹配（库改名后旧链接依然能打开，不需要重定向）。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from app.shell.deps import blob_store, current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.bibformats import SUPPORTED_FORMATS
from caps.slug import slugify
from domain.accounts import AccountDTO
from domain.attachments import AttachmentDTO, AttachmentNotFound, get_attachment, set_main_attachment
from domain.folders import FolderNotFound, list_folders
from domain.libraries import LibraryDTO, LibraryNotFound, list_libraries_for_account, resolve_scope
from domain.tags import TagNotFound, list_tags
from domain.works import WorkNotFound, get_work, list_work_ids
from features.annotating import set_work_note, update_metadata
from features.exporting import (
    cite_formatted,
    cite_record_text,
    export_bibliography,
    export_with_attachments_zip,
    resolve_citation_style,
)
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
    bulk_add_tag,
    bulk_add_to_folder,
    bulk_remove_from_folder,
    bulk_remove_tag,
    bulk_restore,
    bulk_soft_delete,
    create_tag_and_apply,
    purge_works,
)
from features.pdf_reading import VIEWABLE_CONTENT_TYPES, NotViewable, open_for_view
from features.uploading import (
    FilenameConflict,
    UploadRejected,
    download_attachment,
    remove_attachment,
    upload_file,
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


def _require_attachment_in_library(session, library_id: int, attachment_id: int) -> AttachmentDTO:
    """`features.uploading.download_attachment`/`remove_attachment` 都只认
    `attachment_id`，连 `work_id` 都不查——和 `_require_work_in_library` 同
    一类问题，必须在页面层补上，否则账号 A 能对着自己的文库 URL、拿一个
    属于账号 B 的 attachment_id 去下载/删除 B 的附件。
    """
    try:
        attachment = get_attachment(session, attachment_id)
    except AttachmentNotFound:
        raise HTTPException(status_code=404, detail="附件不存在") from None
    if attachment.library_id != library_id:
        raise HTTPException(status_code=404, detail="附件不存在或不属于这个文库") from None
    return attachment


def _back_to_list(slug_and_id: str, *, view: str, sort_by: str, sort_dir: str, page: int) -> RedirectResponse:
    url = f"/l/{slug_and_id}/?view={view}&sort_by={sort_by}&sort_dir={sort_dir}&page={page}"
    return RedirectResponse(url, status_code=303)


def _back_to_list_with_error(
    slug_and_id: str, *, view: str, sort_by: str, sort_dir: str, page: int, error: str
) -> RedirectResponse:
    url = (
        f"/l/{slug_and_id}/?view={view}&sort_by={sort_by}&sort_dir={sort_dir}&page={page}"
        f"&error={quote(error)}"
    )
    return RedirectResponse(url, status_code=303)


BATCH_ACTIONS = ("add_tag", "remove_tag", "add_folder", "remove_folder", "delete", "restore", "purge")


def _require_target_id(value: str, label: str) -> int:
    value = value.strip()
    if not value.isdigit():
        raise HTTPException(status_code=400, detail=f"没有选 {label}")
    return int(value)


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
    tag_ids: list[int] = Query([]),
    folder_id: int | None = None,
    error: str | None = None,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    library_id = _require_library(session, account.id, slug_and_id)

    if view not in VIEWS or sort_by not in SORT_KEYS or sort_dir not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="筛选/排序参数不对")

    page_size = DEFAULT_PAGE_SIZE
    try:
        library_page = list_library_page(
            session,
            library_id=library_id,
            view=view,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tag_ids=tag_ids,
            folder_id=folder_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    except (TagNotFound, FolderNotFound):
        raise HTTPException(status_code=400, detail="筛选条件里有不存在的标签/文件夹") from None
    except ValueError as error_detail:
        raise HTTPException(status_code=400, detail=str(error_detail)) from None
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

    # 给每张卡片"加入文件夹"下拉、以及侧栏"按文件夹筛选"共用——库内文件夹
    # 数量级和标签池一样小，一次查询够用，不需要为此单开 features 函数。
    folders = list_folders(session, library_id=library_id)
    # 侧栏"按标签 AND 筛选"用；同一份 all_tags 也给上面"打标签"下拉复用
    # 省一次查询——两处本来就是同一个库内标签池。
    all_tags = list_tags(session, library_id=library_id)

    return templates.TemplateResponse(
        request,
        "library/index.html",
        {
            "active_nav": "library",
            "slug_and_id": slug_and_id,
            "library_page": library_page,
            "citation_by_work_id": citation_by_work_id,
            "folders": folders,
            "all_tags": all_tags,
            "view": view,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "page": page,
            "tag_ids": tag_ids,
            "folder_id": folder_id,
            "total_pages": total_pages,
            "error": error,
            "viewable_content_types": VIEWABLE_CONTENT_TYPES,
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
    with_attachments: bool = False,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    store=Depends(blob_store),
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

    if with_attachments:
        # 计划「③ 导出」里的"RIS + PDF ZIP"——附件原文件和参考文献文件
        # 平级打进一个 ZIP，不是"Zotero RIS 含附件相对路径关联"（那个
        # 需要 Record 多一个专用字段约定，见 features/exporting 的
        # README 刻意裁剪范围）。
        zip_body = export_with_attachments_zip(
            session, library_id=library_id, work_ids=work_ids, format=format, blob_store=store
        )
        return Response(
            content=zip_body,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="library.zip"'},
        )

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


@router.post("/l/{slug_and_id}/works/{work_id}/attachments/upload")
def upload_attachment_route(
    slug_and_id: str,
    work_id: int,
    file: UploadFile,
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    store=Depends(blob_store),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_work_in_library(session, library_id, work_id)

    if not file.filename:
        return _back_to_list_with_error(
            slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page, error="没有选择文件"
        )

    try:
        upload_file(
            session,
            library_id=library_id,
            work_id=work_id,
            filename=file.filename,
            content=file.file,
            blob_store=store,
        )
    except (UploadRejected, FilenameConflict) as error:
        return _back_to_list_with_error(
            slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page, error=error.message
        )
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.get("/l/{slug_and_id}/works/{work_id}/attachments/{attachment_id}/download")
def download_attachment_route(
    slug_and_id: str,
    work_id: int,
    attachment_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    store=Depends(blob_store),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_work_in_library(session, library_id, work_id)
    attachment = _require_attachment_in_library(session, library_id, attachment_id)

    _, stream = download_attachment(session, attachment_id, blob_store=store)
    return StreamingResponse(
        stream,
        media_type=attachment.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.filename)}"},
    )


@router.post("/l/{slug_and_id}/works/{work_id}/attachments/{attachment_id}/delete")
def delete_attachment_route(
    slug_and_id: str,
    work_id: int,
    attachment_id: int,
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    store=Depends(blob_store),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_work_in_library(session, library_id, work_id)
    _require_attachment_in_library(session, library_id, attachment_id)

    remove_attachment(session, attachment_id, blob_store=store)
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.post("/l/{slug_and_id}/works/{work_id}/attachments/{attachment_id}/set-main")
def set_main_attachment_route(
    slug_and_id: str,
    work_id: int,
    attachment_id: int,
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
    _require_attachment_in_library(session, library_id, attachment_id)

    set_main_attachment(session, attachment_id)
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)


@router.get("/l/{slug_and_id}/works/{work_id}/attachments/{attachment_id}/view")
def view_attachment_route(
    slug_and_id: str,
    work_id: int,
    attachment_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    store=Depends(blob_store),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)
    _require_work_in_library(session, library_id, work_id)
    attachment = _require_attachment_in_library(session, library_id, attachment_id)

    try:
        _, stream = open_for_view(session, attachment_id, blob_store=store)
    except NotViewable:
        raise HTTPException(status_code=415, detail="这个附件的类型不支持在线查看") from None
    return StreamingResponse(
        stream,
        media_type=attachment.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(attachment.filename)}"},
    )


@router.post("/l/{slug_and_id}/batch")
def batch_action_route(
    slug_and_id: str,
    action: str = Form(...),
    work_ids: list[int] = Form([]),
    target_tag_id: str = Form(""),
    target_folder_id: str = Form(""),
    confirm_purge: bool = Form(False),
    view: str = Form(DEFAULT_VIEW),
    sort_by: str = Form(DEFAULT_SORT_BY),
    sort_dir: str = Form(DEFAULT_SORT_DIR),
    page: int = Form(1),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    store=Depends(blob_store),
):
    """批量操作——这次只接"显式勾选"这一种选区（`work_ids` 是页面上被
    勾中的 checkbox 原样提交回来的），不接"全选所有筛选结果"
    （`features.library_browse.resolve_selection` 的 `mode="all_filtered"`）
    ——那个还要把当前的 tag_ids/folder_id 筛选条件也带进这条路由,是下一个
    增量的范围,「两处已定」第 2 条本来就把这两件事分开写。

    `work_ids` 里混进不属于这个库的 id 不需要在这一层单独挡——
    `features.organizing` 每个 `bulk_*` 函数内部的 `_check_work_scope`
    对每一项单独校验，校验失败只记一条 "failed" outcome、不中断其余项、
    不抛到这一层，和 `features/dedupe_review` 那次一样,是"feature 自己已经
    做对了,页面层不需要再包一层"的情况。只有 `target_tag_id`/`target_folder_id`
    本身不属于这个库时才是顶层直接抛 `ValueError`（这两个值只有一份，不是
    逐项校验），这一层需要接住。
    """
    if account is None:
        return RedirectResponse("/login", status_code=303)
    library_id = _require_library(session, account.id, slug_and_id)

    if action not in BATCH_ACTIONS:
        raise HTTPException(status_code=400, detail=f"不认识的批量操作：{action!r}")
    if not work_ids:
        return _back_to_list_with_error(
            slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page, error="没有选中任何文献"
        )

    try:
        if action == "add_tag":
            tag_id = _require_target_id(target_tag_id, "标签")
            result = bulk_add_tag(session, account_id=account.id, library_id=library_id, work_ids=work_ids, tag_id=tag_id)
        elif action == "remove_tag":
            tag_id = _require_target_id(target_tag_id, "标签")
            result = bulk_remove_tag(session, account_id=account.id, library_id=library_id, work_ids=work_ids, tag_id=tag_id)
        elif action == "add_folder":
            folder_id = _require_target_id(target_folder_id, "文件夹")
            result = bulk_add_to_folder(
                session, account_id=account.id, library_id=library_id, work_ids=work_ids, folder_id=folder_id
            )
        elif action == "remove_folder":
            folder_id = _require_target_id(target_folder_id, "文件夹")
            result = bulk_remove_from_folder(
                session, account_id=account.id, library_id=library_id, work_ids=work_ids, folder_id=folder_id
            )
        elif action == "delete":
            result = bulk_soft_delete(session, account_id=account.id, library_id=library_id, work_ids=work_ids)
        elif action == "restore":
            result = bulk_restore(session, account_id=account.id, library_id=library_id, work_ids=work_ids)
        else:  # "purge"
            if not confirm_purge:
                return _back_to_list_with_error(
                    slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page,
                    error="彻底删除需要先勾选确认，这个操作无法撤销",
                )
            result = purge_works(
                session, account_id=account.id, library_id=library_id, work_ids=work_ids, blob_store=store
            )
    except ValueError as error_detail:
        # 只有 target_tag_id/target_folder_id 不属于这个库时才会走到这里——
        # 那是在 `_run_batch` 的逐项循环之前就做的一次性校验，直接抛出。
        # `purge_works` 对"文献还没进回收站"的校验（`WorkNotInTrash`）发生
        # 在 `apply_one` 内部，`_run_batch` 自己会 catch 住（它继承
        # `AppError`，和逐项的 ValueError 走的是同一条 catch），变成一条
        # failed outcome，不会跑到这个 except 块——下面的 failed 计数
        # 分支才是处理它的地方，这里不需要也不应该单独接 WorkNotInTrash。
        return _back_to_list_with_error(
            slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page, error=str(error_detail)
        )

    failed = sum(1 for outcome in result.outcomes if outcome.status == "failed")
    if failed:
        done = len(result.outcomes) - failed
        return _back_to_list_with_error(
            slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page,
            error=f"批量操作完成：{done} 篇成功，{failed} 篇失败",
        )
    return _back_to_list(slug_and_id, view=view, sort_by=sort_by, sort_dir=sort_dir, page=page)
