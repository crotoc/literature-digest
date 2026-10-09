"""app/pages/settings：账号级设置页。

计划里设置页一共 10 个分区（intents/tags/ai/telegram/sources/proxy/
data-files/citations/schedule/logs），装配方式是"每个模块在自己
contract.py 声明设置表单片段，这页只负责拼"。目前落地了三个分区：

- citations：features/exporting 的 `resolve_citation_style`/
  `set_default_citation_style`（账号→站点→代码默认三级回退）。
- sources：features/connection_setup 管理的数据源凭据（v1 只有
  source_credential 一种 kind，对应 Crossref/PubMed）——创建/改名改
  mailto/启停用/设默认/换 PubMed API key/删除/测试连接。
- logs：features/logs_viewer 的 `list_entries`/`clear_log`/`export_log`。
  这个分区不碰任何 domain——日志文件是 `infra.config.settings().log_file`
  指向的单个 JSON Lines 文件，和"账号"这个概念本来就不是一一对应关系
  （整个进程共用一份日志），所以这里没有、也不需要任何按账号过滤的
  ownership 检查：任何登录账号都能看/清/导全部日志，这和旧单体的权限
  边界一致（这个项目目前没有管理员/普通用户的角色区分，domain/accounts
  里没有 role 字段）。真正的安全边界在 features/logs_viewer 内部——
  读取链路强制过一遍 caps/redact 脱敏，页面层拿到的 LogEntryDTO 里已经
  不含明文密钥。日志文件路径没有在这里直接调 `settings().log_file`，
  而是经 `app/shell/deps.log_file` 依赖注入——和 `blob_store()` 同一个
  理由：`clear_log()` 真的会截断那个文件，测试和部署环境解析出的默认
  路径是同一个（`tests/conftest.py` 没单独配 `LOG_FILE`），不走依赖
  注入换成临时文件，跑一次测试就会清空真实的 `data/app.log`。

v1 范围的刻意裁剪：ai/telegram/proxy 三个分区（domain/connections 同一
张表能装，但"怎么管理某一种 kind"是各自 feature 的业务知识，留给
E2/E4/E5 阶段）、data-files 概览、intents/schedule 全部不在这个增量。
三个分区还没多到需要抽"设置分区注册"机制的程度，继续手写拼页面。

安全：features/connection_setup 的 update/rotate_api_key/delete/
set_default/check 五个函数都只用 `get_connection(db, connection_id)` +
"kind 是不是 source_credential" 两道检查，**不核对这个连接是不是真的
属于当前账号**——和 _require_work_in_library/_require_attachment_in_library
同一类问题，必须在页面层补 `_require_source_credential_owned`，否则
账号 A 能对着 /settings 页面、拿一个属于账号 B 的 connection_id 去改/删/
转走 B 的凭据。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.shell.deps import current_account, db
from app.shell.deps import log_file as log_file_dependency
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.citation import list_styles
from domain.accounts import AccountDTO
from domain.connections import ConnectionDTO, ConnectionNotFound, get_connection
from features.connection_setup import (
    KIND as SOURCE_CREDENTIAL_KIND,
)
from features.connection_setup import (
    SOURCES,
    check_connection,
    create_source_credential,
    delete_source_credential,
    list_source_credentials,
    rotate_api_key,
    set_default_source_credential,
    update_source_credential,
)
from features.exporting import resolve_citation_style, set_default_citation_style
from features.logs_viewer import clear_log, export_log, list_entries

router = APIRouter()
nav = NavItem(key="settings", label="设置", path="/settings", icon="gear", order=50)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_LIST_LIMIT = 200


def _require_source_credential_owned(session, account_id: int, connection_id: int) -> ConnectionDTO:
    try:
        connection = get_connection(session, connection_id)
    except ConnectionNotFound:
        raise HTTPException(status_code=404, detail="连接不存在") from None
    if connection.account_id != account_id or connection.kind != SOURCE_CREDENTIAL_KIND:
        raise HTTPException(status_code=404, detail="连接不存在或不属于这个账号") from None
    return connection


def _back_to_settings(error: str | None = None, *, notice: str | None = None) -> RedirectResponse:
    url = "/settings"
    params = {}
    if error:
        params["error"] = error
    if notice:
        params["notice"] = notice
    if params:
        url += "?" + "&".join(f"{key}={quote(value)}" for key, value in params.items())
    return RedirectResponse(url, status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_view(
    request: Request,
    error: str | None = None,
    notice: str | None = None,
    log_min_level: str | None = None,
    log_search: str | None = None,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    log_path=Depends(log_file_dependency),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    current_style = resolve_citation_style(session, account_id=account.id)
    connections = list_source_credentials(session, account_id=account.id)
    log_min_level = log_min_level or None
    log_search = log_search or None
    log_entries = list_entries(
        log_file=log_path,
        min_level=log_min_level,
        search=log_search,
        limit=LOG_LIST_LIMIT,
    )
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {
            "active_nav": "settings",
            "styles": sorted(list_styles()),
            "current_style": current_style,
            "connections": connections,
            "sources": sorted(SOURCES),
            "error": error,
            "notice": notice,
            "log_entries": log_entries,
            "log_levels": LOG_LEVELS,
            "log_min_level": log_min_level or "",
            "log_search": log_search or "",
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


@router.post("/settings/connections/create")
def create_source_credential_route(
    source: str = Form(...),
    name: str = Form(...),
    mailto: str = Form(""),
    api_key: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    if source not in SOURCES:
        raise HTTPException(status_code=400, detail=f"不支持的来源：{source!r}")
    if not name.strip():
        return _back_to_settings("连接名不能为空")

    try:
        create_source_credential(
            session,
            account_id=account.id,
            source=source,
            name=name.strip(),
            mailto=mailto.strip() or None,
            api_key=api_key.strip() or None,
        )
    except ValueError as error:
        return _back_to_settings(str(error))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/connections/{connection_id}/toggle")
def toggle_source_credential_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    connection = _require_source_credential_owned(session, account.id, connection_id)

    update_source_credential(session, connection_id, enabled=not connection.enabled)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/connections/{connection_id}/set-default")
def set_default_source_credential_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_source_credential_owned(session, account.id, connection_id)

    set_default_source_credential(session, connection_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/connections/{connection_id}/rotate-key")
def rotate_api_key_route(
    connection_id: int,
    api_key: str = Form(...),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_source_credential_owned(session, account.id, connection_id)

    if not api_key.strip():
        return _back_to_settings("API key 不能为空")

    try:
        rotate_api_key(session, connection_id, api_key=api_key.strip())
    except ValueError as error:
        return _back_to_settings(str(error))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/connections/{connection_id}/check")
def check_source_credential_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_source_credential_owned(session, account.id, connection_id)

    check_connection(session, connection_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/connections/{connection_id}/delete")
def delete_source_credential_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_source_credential_owned(session, account.id, connection_id)

    delete_source_credential(session, connection_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/logs/clear")
def clear_log_route(
    account: AccountDTO | None = Depends(current_account),
    log_path=Depends(log_file_dependency),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    cleared_bytes = clear_log(log_file=log_path)
    return _back_to_settings(notice=f"已清空日志（{cleared_bytes} 字节）")


@router.get("/settings/logs/export")
def export_log_route(
    log_min_level: str | None = None,
    log_search: str | None = None,
    account: AccountDTO | None = Depends(current_account),
    log_path=Depends(log_file_dependency),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    body = export_log(
        log_file=log_path,
        min_level=log_min_level or None,
        search=log_search or None,
    )
    return PlainTextResponse(
        body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="app-log-export.jsonl"'},
    )
