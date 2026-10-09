"""app/pages/home：登录态分发点 + 最小着陆页。

v1 范围的刻意裁剪：计划里"研究动态仪表盘"的四个统计卡/intent 列表/
最近报告/按 intent 分组的推送，全部依赖 E3（intents/discovery）和
E4（reports/delivery）阶段才会存在的 feature——这些现在都还没有，
提前搭一个读不到真实数据的空壁板没有意义。本页 v1 只做它必须做的那部分：
未登录时把 "/" 分发到 /login；登录后给一个最小的、确认登录态真实工作的
着陆页。等 E3/E4 的 feature 落地后再回来扩充这个模板，不需要改路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account
from app.shell.registry import NavItem
from app.shell.templating import templates
from domain.accounts import AccountDTO

router = APIRouter()
nav = NavItem(key="home", label="首页", path="/", icon="home", order=0)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, account: AccountDTO | None = Depends(current_account)):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "home/index.html", {"active_nav": "home", "account": account})
