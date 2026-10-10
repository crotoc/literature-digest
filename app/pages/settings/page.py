"""app/pages/settings：账号级设置页。

计划里设置页一共 10 个分区（intents/tags/ai/telegram/sources/proxy/
data-files/citations/schedule/logs），装配方式是"每个模块在自己
contract.py 声明设置表单片段，这页只负责拼"。目前落地了三个分区：

- citations：features/exporting 的 `resolve_citation_style`/
  `set_default_citation_style`（账号→站点→代码默认三级回退）。
- attachment-naming：features/uploading 的 `resolve_naming_template`/
  `set_naming_template`——账号级的附件重命名方括号模板（`[firstauthor]`/
  `[year]`/`[title]`），和 citations 同一类"只有一个账号级设置值"的最简
  分区。这是扒功能清单时漏掉的一个早就完整可用的设置项，一直没有页面
  调它（和 metadata_lookup 同一类"feature 完整、页面缺口"）。
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

- ai：features/ai_enrichment 管理的 ai_profile 连接（provider +
  model + api_key + base_url，provider 目前支持 openai_compat/
  anthropic）——创建/改名改 model/base_url/启停用/设默认/换 key/删除/
  测试连接，和 sources 分区结构对称。
- intents：`domain/intents` 的研究意图 CRUD（名称/研究问题/候选上限/
  回溯天数/手写检索式覆盖）+ `features/query_planner.compile_intent_query`
  （编译检索式，要选一个 ai_profile 连接，内部复用 `ai_llm_transport`/
  `ai_llm_resolve` 这两个已有依赖——和 `/ai-assess` 走的是同一条 LLM
  边界）+ `domain/schedules.upsert_schedule`（调度，`target_kind="intent"`
  是这个页面和 `features.discovery` 两边分别认定的字符串约定，`discovery`
  自己不对外导出这个常量——见该模块 README「调度认领」一节）。E3 阶段
  的「立即运行」按钮放在首页，不在这里（见 `app/pages/home`）。

v1/E2 范围的刻意裁剪：telegram/proxy 两个分区（domain/connections 同一
张表能装，但"怎么管理某一种 kind"是各自 feature 的业务知识，留给
E4/E5 阶段）、data-files 概览全部不在这个增量。
分区还没多到需要抽"设置分区注册"机制的程度，继续手写拼页面。

安全：features/connection_setup 和 features/ai_enrichment 的
update/rotate_*_key/delete/set_default/check 这组函数都只用
`get_connection(db, connection_id)` + "kind 对不对" 两道检查，**不核对
这个连接是不是真的属于当前账号**——和 _require_work_in_library/
_require_attachment_in_library 同一类问题，必须在页面层补
`_require_source_credential_owned`/`_require_ai_profile_owned`，否则
账号 A 能对着 /settings 页面、拿一个属于账号 B 的 connection_id 去改/删/
转走 B 的凭据。`domain/intents.get_intent`/`domain/schedules` 同样不核对
账号归属，这里补一个结构相同的 `_require_intent_owned`。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.shell.deps import ai_llm_resolve, ai_llm_transport, current_account, db
from app.shell.deps import log_file as log_file_dependency
from app.shell.registry import NavItem
from app.shell.templating import templates
from caps.citation import list_styles
from caps.schedule import InvalidCronExpression
from caps.template import TemplateError
from domain.accounts import AccountDTO
from domain.connections import ConnectionDTO, ConnectionNotFound, get_connection
from domain.intents import (
    IntentDTO,
    IntentNotFound,
    create_intent,
    delete_intent,
    get_intent,
    list_intent_versions,
    list_intents,
    update_intent,
)
from domain.schedules import delete_schedule, list_schedules, upsert_schedule
from features.ai_enrichment import (
    KIND as AI_PROFILE_KIND,
)
from features.ai_enrichment import (
    PROVIDERS,
    check_ai_profile,
    create_ai_profile,
    delete_ai_profile,
    list_ai_profiles,
    resolve_ai_profile,
    rotate_ai_profile_key,
    set_default_ai_profile,
    update_ai_profile,
)
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
from features.query_planner import compile_intent_query
from features.uploading import resolve_naming_template, set_naming_template

router = APIRouter()
nav = NavItem(key="settings", label="设置", path="/settings", icon="gear", order=50)

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_LIST_LIMIT = 200

# `domain/schedules` 的 `target_kind` 没有枚举类型，字符串约定由认领它的
# feature（`features.discovery`）和调它的页面各自认定——discovery 自己的
# `_SCHEDULE_TARGET_KIND` 是私有常量，不对外导出，这里独立声明同一个字符串。
INTENT_SCHEDULE_TARGET_KIND = "intent"


def _require_source_credential_owned(session, account_id: int, connection_id: int) -> ConnectionDTO:
    try:
        connection = get_connection(session, connection_id)
    except ConnectionNotFound:
        raise HTTPException(status_code=404, detail="连接不存在") from None
    if connection.account_id != account_id or connection.kind != SOURCE_CREDENTIAL_KIND:
        raise HTTPException(status_code=404, detail="连接不存在或不属于这个账号") from None
    return connection


def _require_ai_profile_owned(session, account_id: int, connection_id: int) -> ConnectionDTO:
    try:
        connection = get_connection(session, connection_id)
    except ConnectionNotFound:
        raise HTTPException(status_code=404, detail="连接不存在") from None
    if connection.account_id != account_id or connection.kind != AI_PROFILE_KIND:
        raise HTTPException(status_code=404, detail="连接不存在或不属于这个账号") from None
    return connection


def _require_intent_owned(session, account_id: int, intent_id: int) -> IntentDTO:
    try:
        intent = get_intent(session, intent_id)
    except IntentNotFound:
        raise HTTPException(status_code=404, detail="研究意图不存在") from None
    if intent.account_id != account_id:
        raise HTTPException(status_code=404, detail="研究意图不存在或不属于这个账号") from None
    return intent


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
    current_naming_template = resolve_naming_template(session, account_id=account.id)
    connections = list_source_credentials(session, account_id=account.id)
    ai_profiles = list_ai_profiles(session, account_id=account.id)
    intents = list_intents(session, account_id=account.id)
    intent_latest_versions = {}
    for intent in intents:
        versions = list_intent_versions(session, intent.id)
        intent_latest_versions[intent.id] = versions[0] if versions else None
    intent_schedules = {
        schedule.target_id: schedule
        for schedule in list_schedules(session, account_id=account.id)
        if schedule.target_kind == INTENT_SCHEDULE_TARGET_KIND
    }
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
            "current_naming_template": current_naming_template or "",
            "connections": connections,
            "sources": sorted(SOURCES),
            "ai_profiles": ai_profiles,
            "ai_providers": sorted(PROVIDERS),
            "intents": intents,
            "intent_latest_versions": intent_latest_versions,
            "intent_schedules": intent_schedules,
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


@router.post("/settings/attachment-naming")
def save_attachment_naming_template_route(
    template: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    """空字符串写成 `None`——`resolve_naming_template()`/`upload_file()` 的
    约定是 `None` 表示"保留原始文件名"，不是"模板是空字符串"（见
    features/uploading 自己的 docstring）。模板里有不认识的占位符时，
    `set_naming_template` 内部调 `caps.template.validate_bracket_template`
    抛 `TemplateError`（具体是 `UnknownPlaceholder`），这里原样显示
    `str(error)`——那条消息已经是"模板里有不认识的占位：[...]；可用的是
    [...]"这种可读文案，不需要再加工。
    """
    if account is None:
        return RedirectResponse("/login", status_code=303)

    try:
        set_naming_template(session, account_id=account.id, template=template.strip() or None)
    except TemplateError as error:
        return _back_to_settings(str(error))
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


# ── ai_profile 连接 ──────────────────────────────────────────────────────


@router.post("/settings/ai-profiles/create")
def create_ai_profile_route(
    provider: str = Form(...),
    name: str = Form(...),
    model: str = Form(...),
    api_key: str = Form(...),
    base_url: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"不支持的 provider：{provider!r}")
    if not name.strip():
        return _back_to_settings("连接名不能为空")

    try:
        create_ai_profile(
            session,
            account_id=account.id,
            provider=provider,
            name=name.strip(),
            model=model.strip(),
            api_key=api_key.strip(),
            base_url=base_url.strip() or None,
        )
    except ValueError as error:
        return _back_to_settings(str(error))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ai-profiles/{connection_id}/toggle")
def toggle_ai_profile_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    connection = _require_ai_profile_owned(session, account.id, connection_id)

    update_ai_profile(session, connection_id, enabled=not connection.enabled)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ai-profiles/{connection_id}/set-default")
def set_default_ai_profile_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_ai_profile_owned(session, account.id, connection_id)

    set_default_ai_profile(session, connection_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ai-profiles/{connection_id}/rotate-key")
def rotate_ai_profile_key_route(
    connection_id: int,
    api_key: str = Form(...),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_ai_profile_owned(session, account.id, connection_id)

    if not api_key.strip():
        return _back_to_settings("API key 不能为空")

    rotate_ai_profile_key(session, connection_id, api_key=api_key.strip())
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ai-profiles/{connection_id}/check")
def check_ai_profile_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_ai_profile_owned(session, account.id, connection_id)

    check_ai_profile(session, connection_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/ai-profiles/{connection_id}/delete")
def delete_ai_profile_route(
    connection_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_ai_profile_owned(session, account.id, connection_id)

    delete_ai_profile(session, connection_id)
    return RedirectResponse("/settings", status_code=303)


# ── 研究意图（intents）────────────────────────────────────────────────────


@router.post("/settings/intents/create")
def create_intent_route(
    name: str = Form(...),
    question_text: str = Form(...),
    candidate_limit: int = Form(40),
    lookback_days: int = Form(7),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)

    try:
        create_intent(
            session,
            account_id=account.id,
            name=name,
            question_text=question_text,
            candidate_limit=candidate_limit,
            lookback_days=lookback_days,
        )
    except ValueError as error:
        return _back_to_settings(str(error))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/intents/{intent_id}/update")
def update_intent_route(
    intent_id: int,
    name: str = Form(...),
    question_text: str = Form(...),
    candidate_limit: int = Form(...),
    lookback_days: int = Form(...),
    query_override: str = Form(""),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_intent_owned(session, account.id, intent_id)

    try:
        update_intent(
            session,
            intent_id,
            name=name,
            question_text=question_text,
            candidate_limit=candidate_limit,
            lookback_days=lookback_days,
            query_override=query_override.strip() or None,
        )
    except ValueError as error:
        return _back_to_settings(str(error))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/intents/{intent_id}/toggle")
def toggle_intent_route(
    intent_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    intent = _require_intent_owned(session, account.id, intent_id)

    update_intent(session, intent_id, enabled=not intent.enabled)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/intents/{intent_id}/delete")
def delete_intent_route(
    intent_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_intent_owned(session, account.id, intent_id)

    delete_schedule(session, target_kind=INTENT_SCHEDULE_TARGET_KIND, target_id=intent_id)
    delete_intent(session, intent_id)
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/intents/{intent_id}/compile")
def compile_intent_query_route(
    intent_id: int,
    connection_id: int = Form(...),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
    transport=Depends(ai_llm_transport),
    resolve=Depends(ai_llm_resolve),
):
    """选哪个 ai_profile 连接来编译是调用方（这个页面）的决定，`query_planner`
    本身不认识 `domain/connections`——和 `/l/.../ai-assess` 走的是同一条
    `resolve_ai_profile()` 解析路径（provider/model/api_key/base_url），
    理由见 `features/ai_enrichment.resolve_ai_profile` 自己的 docstring。
    编译失败时 `compile_intent_query` 不抛异常，而是落一条 `status="failed"`
    的版本（见 `features/query_planner` README）——这里读返回值的
    `status` 字段来决定提示文案，不是 try/except。
    """
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_intent_owned(session, account.id, intent_id)
    _require_ai_profile_owned(session, account.id, connection_id)

    provider, model, base_url, api_key = resolve_ai_profile(session, connection_id)
    version = compile_intent_query(
        session,
        intent_id=intent_id,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        transport=transport,
        resolve=resolve,
    )
    if version.status == "failed":
        return _back_to_settings(f"检索式编译失败：{version.error}")
    return _back_to_settings(notice="检索式编译成功")


@router.post("/settings/intents/{intent_id}/schedule")
def upsert_intent_schedule_route(
    intent_id: int,
    cron: str = Form(...),
    enabled: str | None = Form(None),
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_intent_owned(session, account.id, intent_id)

    try:
        upsert_schedule(
            session,
            account_id=account.id,
            target_kind=INTENT_SCHEDULE_TARGET_KIND,
            target_id=intent_id,
            cron=cron.strip(),
            enabled=enabled is not None,
        )
    except InvalidCronExpression as error:
        return _back_to_settings(str(error))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/intents/{intent_id}/schedule/delete")
def delete_intent_schedule_route(
    intent_id: int,
    account: AccountDTO | None = Depends(current_account),
    session=Depends(db),
):
    if account is None:
        return RedirectResponse("/login", status_code=303)
    _require_intent_owned(session, account.id, intent_id)

    delete_schedule(session, target_kind=INTENT_SCHEDULE_TARGET_KIND, target_id=intent_id)
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
