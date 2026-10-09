"""app/pages/settings：账号级设置页，第一个增量只接引用样式。

计划里设置页一共 10 个分区（intents/tags/ai/telegram/sources/proxy/
data-files/citations/schedule/logs），装配方式是"每个模块在自己
contract.py 声明设置表单片段，这页只负责拼"。这次只落地 citations 一个
分区：features/exporting 的 `resolve_citation_style`/
`set_default_citation_style` 早就写好了（账号→站点→代码默认三级回退），
只是一直没有页面能调它们——app/pages/library 的模块 docstring 里一直
写着"账号级改引用样式的设置项要等设置页落地才能接上"，这次把这句裁剪
去掉。

v1 范围的刻意裁剪：connections（ai/telegram/sources/proxy 四个分区，
对应 domain/connections，还没建那张表）、data-files 概览、日志查看器、
intents/schedule（E3 阶段才有）全部不在这个增量——这页目前就是一个
"引用样式"单选表单，不是完整设置页骨架。等下一个分区落地时才需要决定
要不要抽一个"设置分区注册"的通用机制；现在只有一个分区，抽象没有意义。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.citation import list_styles
from domain.accounts import AccountDTO
from features.exporting import resolve_citation_style, set_default_citation_style

router = APIRouter()
nav = NavItem(key="settings", label="设置", path="/settings", icon="gear", order=50)


@router.get("/settings", response_class=HTMLResponse)
def settings_view(
    request: Request,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    current_style = resolve_citation_style(session, account_id=account.id)
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            "active_nav": "settings",
            "styles": sorted(list_styles()),
            "current_style": current_style,
        },
    )


@router.post("/settings/citation-style")
def save_citation_style_route(
    style: str = Form(...),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    if style not in list_styles():
        raise HTTPException(status_code=400, detail=f"不支持的引用样式：{style!r}")

    set_default_citation_style(session, account_id=account.id, style=style)
    return RedirectResponse("/settings", status_code=303)
