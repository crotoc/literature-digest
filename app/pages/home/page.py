"""app/pages/home：登录态分发点 + 研究意图仪表盘（v1 切片）。

v1 范围的刻意裁剪：计划里"研究动态仪表盘"的四个统计卡/最近报告/按 intent
分组的推送，依赖 E4（reports/delivery）阶段才会存在的 feature——那部分
还没有，暂不出现在这里。E3（intents/discovery）落地后，本页现在只扩充
intent 列表本身：名称 + 启用/停用 toggle + "立即运行"按钮（同步调用
`features.discovery.run_discovery`，和 v1 其余"同步执行"的约定一致，
不走 job 轮询）。未登录时仍是把 "/" 分发到 /login。

`run_discovery` 需要的 `transport`/`resolve` 复用 `app/shell/deps` 里
`features/metadata_lookup` 已经声明好的那两个依赖（`metadata_lookup_transport`/
`metadata_lookup_resolve`）——discovery 打的也是 Crossref/PubMed，和
metadata_lookup 是同一条网络边界，没必要再声明一组新的。

`_require_intent_owned` 和 `app/pages/settings` 里的同名函数逻辑完全一样，
但两个页面模块互不 import（装配层没有互相调用的先例），按项目约定各自
复制一份，理由同 `features/discovery` 里重复 `_find_source_connection`
那段 docstring。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import current_account, db, metadata_lookup_resolve, metadata_lookup_transport
from app.shell.registry import NavItem
from app.shell.templating import templates
from domain.accounts import AccountDTO
from domain.intents import (
    IntentDTO,
    IntentNotFound,
    get_intent,
    list_discovery_runs,
    list_intents,
    update_intent,
)
from features.discovery import IntentDisabled, NoReadyQuery, run_discovery

router = APIRouter()
nav = NavItem(key="home", label="首页", path="/", icon="home", order=0)


def _require_intent_owned(session, account_id: int, intent_id: int) -> IntentDTO:
    try:
        intent = get_intent(session, intent_id)
    except IntentNotFound:
        raise HTTPException(status_code=404, detail="研究意图不存在") from None
    if intent.account_id != account_id:
        raise HTTPException(status_code=404, detail="研究意图不存在或不属于这个账号") from None
    return intent


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    error: str | None = None,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    intents = list_intents(session, account_id=account.id)
    intent_latest_runs = {}
    for intent in intents:
        runs = list_discovery_runs(session, intent_id=intent.id, limit=1)
        intent_latest_runs[intent.id] = runs[0] if runs else None

    return templates.TemplateResponse(
        request,
        "home/index.html",
        {
            "active_nav": "home",
            "account": account,
            "error": error,
            "intents": intents,
            "intent_latest_runs": intent_latest_runs,
        },
    )


@router.post("/intents/{intent_id}/toggle")
def toggle_intent_route(
    intent_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    intent = _require_intent_owned(session, account.id, intent_id)

    update_intent(session, intent_id, enabled=not intent.enabled)
    return RedirectResponse("/", status_code=303)


@router.post("/intents/{intent_id}/run-now")
def run_intent_now_route(
    intent_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    transport=Depends(metadata_lookup_transport),
    resolve=Depends(metadata_lookup_resolve),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_intent_owned(session, account.id, intent_id)

    try:
        run_discovery(
            session, account_id=account.id, intent_id=intent_id, transport=transport, resolve=resolve
        )
    except (IntentDisabled, NoReadyQuery) as error:
        return RedirectResponse(f"/?error={quote(str(error))}", status_code=303)
    return RedirectResponse("/", status_code=303)
