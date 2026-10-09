"""app/pages/importing：粘贴 RIS/BibTeX/CSL-JSON 文本导入到文库。

只调 features/importing 一个 feature，和已有的 auth/home/library 保持
同一个"一页对一个主 feature"的节奏。

v1 范围的刻意裁剪：
  - 导入目标库固定是账号的第一个库（即注册时自动建的个人库）——和
    app/pages/library 的 /library 入口用同一条 list_libraries_for_account()
    取第一个的规则，不额外做"选库"的下拉（库切换 UI 等有多库场景的真实
    需求时再补）。
  - 文件上传（直接传 .ris/.bib 文件）不在本增量——features/uploading 管的
    是附件（PDF 等），文本粘贴导入已经覆盖了"把元数据搬进库里"这个最
    核心的需求，文件选择器只是省去复制粘贴这一步的 UX 糖，不是新能力。
  - 导入结果只平铺展示 counts + 每条 outcome（work_id/status/reason），
    不做"点进去看这条新建的文献"——文献详情/单篇页面还没有独立路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.bibformats import SUPPORTED_FORMATS
from domain.accounts import AccountDTO
from domain.libraries import list_libraries_for_account
from features.importing import import_records
from infra.errors import AppError

router = APIRouter()
nav = NavItem(key="importing", label="导入", path="/import", icon="upload", order=2)


@router.get("/import", response_class=HTMLResponse)
def import_form(request: Request, account: AccountDTO | None = Depends(current_account)):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "importing/index.html",
        {"active_nav": "importing", "formats": sorted(SUPPORTED_FORMATS)},
    )


@router.post("/import", response_class=HTMLResponse)
def import_submit(
    request: Request,
    format: str = Form(...),
    raw_text: str = Form(...),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    form_state = {"active_nav": "importing", "formats": sorted(SUPPORTED_FORMATS), "format": format}

    libraries = list_libraries_for_account(session, account.id)
    if not libraries:
        return templates.TemplateResponse(
            request,
            "importing/index.html",
            {**form_state, "error": "这个账号没有任何文库"},
            status_code=500,
        )

    try:
        result = import_records(
            session,
            account_id=account.id,
            library_id=libraries[0].id,
            format=format,
            raw_text=raw_text,
        )
    except AppError as error:
        return templates.TemplateResponse(
            request,
            "importing/index.html",
            {**form_state, "error": error.message, "raw_text": raw_text},
            status_code=error.status_code,
        )

    return templates.TemplateResponse(
        request,
        "importing/result.html",
        {"active_nav": "importing", "job": result.job, "outcomes": result.outcomes},
    )
