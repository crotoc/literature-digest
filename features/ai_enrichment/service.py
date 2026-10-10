"""features/ai_enrichment：AI 对单篇/批量文献的打标签 + 点评，以及它自己的
`ai_profile` 连接管理。

**`ai_profile` 的 CRUD 为什么长在这里、不长在 `features/connection_setup`**：
`domain/connections` 四种 kind 共用一张表，但`features/connection_setup`
自己的 README 明确写了"怎么管理某一种 kind 是业务知识"——v1 只实现了
`source_credential`，`ai_profile`/`telegram_destination`/`download_proxy`
"留给 E2/E4/E5 阶段各自的 feature 自己开一份同构但独立的管理模块"。本模块
就是那个"自己的 feature"：`create_ai_profile`/`list_ai_profiles`/
`update_ai_profile`/`rotate_ai_profile_key`/`delete_ai_profile`/
`set_default_ai_profile`/`check_ai_profile` 这一组函数，和
`connection_setup` 管理 `source_credential` 的那一组函数结构上完全对称，
`_secret_key()`/`derive_key(..., purpose="connections")` 这个常量/约定也是
刻意重复的一份（features 之间禁止互相 import，见
`features/connection_setup/README.md` 同一条说明）。

**AI 点评怎么落库**：`domain/works` 没有、也不该新增 AI 专用字段——点评
结果（summary/relevance/novelty + 原始 payload）走已有的
`record_provenance(work_id, source="ai_enrichment", ...)` 追加一条历史
记录（不是覆盖"当前状态"，和 `WorkProvenance` 本身"只追加不去重"的语义
一致：每次重新点评都是一条新记录，想看"最新一次"就取
`list_provenance()` 里第一条 `source == "ai_enrichment"` 的）；AI 建议的
标签则是真标签，走 `domain/tags.create_tag`（同名幂等）+
`add_tag_to_work`，这样"AI 重新打标签"产生的标签和手动打的标签完全一样，
侧栏筛选、三态聚合都直接能用，不需要任何特殊通道。

**`caps/template` 用在哪**：发给 LLM 的 prompt 不是手工拼 f-string，是
`render_jinja()` 渲一个固定模板——和 `caps/template` 自己 README 里写的
"Jinja（给 prompt 用）"直接对应。

**`caps/redact` 用在哪**：
1. 发出去之前：用 `with_known_values([api_key])` 对渲染好的 prompt 文本过
   一遍 `redact_text`——这是纯防御性的一步，正常情况下 `api_key` 根本不会
   出现在文献的标题/摘要里，但万一真出现了（比如用户粘贴笔记时手滑），
   不应该让它原样发给第三方 LLM 服务。
2. 存库之前：用 `redact_mapping` 对要写进 `payload_json` 的结构化结果过
   一遍——这条记录以后会在页面上展示，和日志是同一类"会被人看到的输出"。

**job 粒度**：单篇点评 `assess_work()` 不建 job——它是一次同步调用，失败
就直接抛异常，没有"部分成功"的概念。批量点评 `bulk_assess_works()` 走
计划「两处已定」里"长流程批量"这一类（每项一个子 job，不是像
`features/organizing` 那样只有失败才建子行）——AI 调用是真正的网络 I/O，
慢且可能单项超时，调用方需要能单独看每一项的状态，这和"打标签/去文件夹"
这种纯本地数据库操作的"瞬时批量"不是同一类。连接解析（哪个 provider/
model/api_key）在批量开始前**只做一次**，不是每项重新解析——一个连接
配置错误不应该制造出一整批内容相同的失败子 job。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from pydantic import BaseModel
from sqlalchemy.orm import Session

from adapters.llm import anthropic, openai_compat
from adapters.llm.anthropic import AnthropicError
from adapters.llm.openai_compat import OpenAiCompatError
from caps.httpfetch import default_resolve
from caps.probe import run_check
from caps.redact import redact_mapping, redact_text, with_known_values
from caps.secrets import derive_key
from caps.template import render_jinja
from domain.connections import (
    ConnectionDTO,
    create_connection,
    delete_connection,
    get_connection,
    get_decrypted_secret,
    list_connections,
    rotate_secret,
    set_default_connection,
    update_connection,
)
from domain.connections import record_check_result as _record_check_result
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.tags import add_tag_to_work, create_tag
from domain.usage import increment_usage
from domain.works import WorkDTO, get_work, record_provenance
from infra.config import settings
from infra.errors import AppError

KIND = "ai_profile"
PROVIDERS = frozenset({"openai_compat", "anthropic"})
JOB_KIND_BULK_ASSESS = "ai_enrichment_bulk_assess"
JOB_KIND_ASSESS_ITEM = "ai_enrichment_assess_item"
_CONNECTIONS_SECRET_PURPOSE = "connections"
_PARENT_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}
_CHILD_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}

_PROVIDER_FNS = {
    "openai_compat": openai_compat.complete_structured,
    "anthropic": anthropic.complete_structured,
}
_PROVIDER_CHECKS = {
    "openai_compat": openai_compat.check,
    "anthropic": anthropic.check,
}
_PROVIDER_ERRORS = (OpenAiCompatError, AnthropicError)

_SYSTEM_PROMPT = (
    "你是一个文献筛选助手，帮研究者快速判断一篇文献值不值得细看、属于哪些主题。"
)
_PROMPT_TEMPLATE = (
    "请阅读下面这篇文献的元数据，给出：\n"
    "- summary：2-3 句话的中文点评，说明这篇文献大致讲了什么\n"
    "- relevance：0 到 1 之间的小数，这篇文献对专注该领域的研究者的参考价值\n"
    "- novelty：0 到 1 之间的小数，内容的新颖程度\n"
    "- tags：3-6 个适合当标签使用的中文或英文短词\n\n"
    "标题：{{ title }}\n"
    "作者：{{ authors }}\n"
    "年份：{{ year }}\n"
    "期刊/来源：{{ container_title }}\n"
    "摘要：{{ abstract }}\n"
)
_PLACEHOLDER = "（无）"

_UNSET = object()


class WrongKind(ValueError):
    """这个 `connection_id` 不是 `ai_profile` 类型。"""

    def __init__(self, connection_id: int, actual_kind: str) -> None:
        super().__init__(f"连接 #{connection_id} 是 {actual_kind!r} 类型，不是 {KIND!r}")
        self.connection_id = connection_id
        self.actual_kind = actual_kind


class UnknownProvider(ValueError):
    """`provider` 不在 `PROVIDERS` 里。"""


class AiEnrichmentError(AppError):
    """调用 LLM 失败，或者 LLM 的输出没有可用的结果。"""

    status_code = 502
    code = "ai_enrichment_llm_error"


@dataclass(frozen=True)
class AssessmentResult:
    work_id: int
    summary: str
    relevance: float
    novelty: float
    tags: tuple[str, ...]
    model: str
    provenance_id: int


@dataclass(frozen=True)
class BatchAssessOutcome:
    work_id: int
    status: str  # "done" | "failed"
    reason: str | None = None


@dataclass(frozen=True)
class BatchAssessResult:
    job: JobDTO
    outcomes: tuple[BatchAssessOutcome, ...]


class _AssessmentModel(BaseModel):
    summary: str
    relevance: float
    novelty: float
    tags: list[str]


# ── ai_profile 连接管理 ──────────────────────────────────────────────────


def _secret_key() -> str:
    cfg = settings()
    return derive_key(cfg.app_secret_key, salt=cfg.app_secret_salt, purpose=_CONNECTIONS_SECRET_PURPOSE)


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise UnknownProvider(f"不认识的 provider：{provider!r}（支持：{sorted(PROVIDERS)}）")


def _require_ai_profile(connection: ConnectionDTO) -> None:
    if connection.kind != KIND:
        raise WrongKind(connection.id, connection.kind)


def create_ai_profile(
    db: Session,
    *,
    account_id: int,
    provider: str,
    name: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    enabled: bool = True,
    is_default: bool = False,
) -> ConnectionDTO:
    _validate_provider(provider)
    if not api_key.strip():
        raise ValueError("api_key 不能为空")

    config: dict[str, object] = {"provider": provider, "model": model}
    if base_url:
        config["base_url"] = base_url

    return create_connection(
        db,
        account_id=account_id,
        kind=KIND,
        name=name,
        config=config,
        secret_plain=api_key,
        secret_key=_secret_key(),
        enabled=enabled,
        is_default=is_default,
    )


def list_ai_profiles(db: Session, *, account_id: int) -> list[ConnectionDTO]:
    return list_connections(db, account_id=account_id, kind=KIND)


def update_ai_profile(
    db: Session,
    connection_id: int,
    *,
    name=_UNSET,
    model=_UNSET,
    base_url=_UNSET,
    enabled=_UNSET,
) -> ConnectionDTO:
    connection = get_connection(db, connection_id)
    _require_ai_profile(connection)

    kwargs: dict[str, object] = {}
    if name is not _UNSET:
        kwargs["name"] = name
    if enabled is not _UNSET:
        kwargs["enabled"] = enabled
    if model is not _UNSET or base_url is not _UNSET:
        config = dict(connection.config)
        if model is not _UNSET:
            config["model"] = model
        if base_url is not _UNSET:
            if base_url:
                config["base_url"] = base_url
            else:
                config.pop("base_url", None)
        kwargs["config"] = config

    if not kwargs:
        return connection
    return update_connection(db, connection_id, **kwargs)


def rotate_ai_profile_key(db: Session, connection_id: int, *, api_key: str) -> ConnectionDTO:
    connection = get_connection(db, connection_id)
    _require_ai_profile(connection)
    if not api_key.strip():
        raise ValueError("api_key 不能为空")
    return rotate_secret(db, connection_id, secret_plain=api_key, secret_key=_secret_key())


def delete_ai_profile(db: Session, connection_id: int) -> None:
    connection = get_connection(db, connection_id)
    _require_ai_profile(connection)
    delete_connection(db, connection_id)


def set_default_ai_profile(db: Session, connection_id: int) -> ConnectionDTO:
    connection = get_connection(db, connection_id)
    _require_ai_profile(connection)
    return set_default_connection(db, connection_id)


def check_ai_profile(db: Session, connection_id: int, *, transport=None, resolve=None) -> ConnectionDTO:
    connection = get_connection(db, connection_id)
    _require_ai_profile(connection)
    provider = connection.config.get("provider")
    _validate_provider(provider)

    api_key = None
    if connection.has_secret:
        api_key = get_decrypted_secret(db, connection_id, secret_key=_secret_key())
    probe_config = {
        "api_key": api_key,
        "model": connection.config.get("model"),
        "base_url": connection.config.get("base_url"),
    }

    check_fn = partial(
        _PROVIDER_CHECKS[provider], transport=transport, resolve=resolve or default_resolve
    )
    result = run_check(check_fn, probe_config)
    return _record_check_result(db, connection_id, ok=result.ok, message=result.message)


# ── 点评 ─────────────────────────────────────────────────────────────────


def _describe_work(work: WorkDTO) -> dict[str, str]:
    authors = ", ".join(
        person.literal or " ".join(filter(None, [person.given, person.family])) for person in work.authors
    )
    return {
        "title": work.title or _PLACEHOLDER,
        "authors": authors or _PLACEHOLDER,
        "year": str(work.year) if work.year is not None else _PLACEHOLDER,
        "container_title": work.container_title or _PLACEHOLDER,
        "abstract": work.abstract or _PLACEHOLDER,
    }


def resolve_ai_profile(db: Session, connection_id: int) -> tuple[str, str, str | None, str]:
    """返回 `(provider, model, base_url, api_key)`。批量点评只调一次，不是
    每项重新解析——见模块 docstring 的「job 粒度」一节。

    公开导出（不加下划线）：`features/tag_grouping` 的 AI 分组/重复提案
    不依赖 `domain/connections`（按设计故意不知道"连接"这个概念，只认
    裸的 provider/model/api_key/base_url），所以 app/pages/library 需要
    一个地方把"这个账号选的 ai_profile 连接"解析成这四个值，再转手喂给
    `tag_grouping`——这个解析逻辑已经在这里写好了，没必要在页面层或
    `tag_grouping` 里再写一遍（那会是第三处 `_secret_key()` 派生逻辑）。"""
    connection = get_connection(db, connection_id)
    _require_ai_profile(connection)
    provider = connection.config.get("provider")
    _validate_provider(provider)
    model = connection.config.get("model")
    if not model:
        raise ValueError(f"ai_profile 连接 #{connection_id} 没有配置 model")
    base_url = connection.config.get("base_url")
    api_key = None
    if connection.has_secret:
        api_key = get_decrypted_secret(db, connection_id, secret_key=_secret_key())
    if not api_key:
        raise ValueError(f"ai_profile 连接 #{connection_id} 没有配置 api_key")
    return provider, model, base_url, api_key


def _current_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def assess_work(
    db: Session,
    *,
    account_id: int,
    library_id: int,
    work_id: int,
    connection_id: int,
    period: str | None = None,
    transport=None,
    resolve=None,
) -> AssessmentResult:
    """对单篇文献做一次 AI 点评：解析 `ai_profile` 连接 → 渲染 prompt →
    调 LLM → 把建议标签写进 `domain.tags` → 把结构化结果存成一条
    `domain.works` provenance → 记一次用量。"""
    provider, model, base_url, api_key = resolve_ai_profile(db, connection_id)
    return _assess_with_profile(
        db,
        account_id=account_id,
        library_id=library_id,
        work_id=work_id,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        period=period,
        transport=transport,
        resolve=resolve,
    )


def _assess_with_profile(
    db: Session,
    *,
    account_id: int,
    library_id: int,
    work_id: int,
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str,
    period: str | None,
    transport,
    resolve,
) -> AssessmentResult:
    work = get_work(db, work_id)
    if work.library_id != library_id:
        raise ValueError(f"work_id 不属于这个库：{work_id}")

    prompt = render_jinja(_PROMPT_TEMPLATE, _describe_work(work))
    prompt = redact_text(prompt, policy=with_known_values([api_key]))

    kwargs: dict[str, object] = {
        "response_model": _AssessmentModel,
        "messages": [{"role": "user", "content": prompt}],
        "model": model,
        "api_key": api_key,
        "system": _SYSTEM_PROMPT,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if transport is not None:
        kwargs["transport"] = transport
    if resolve is not None:
        kwargs["resolve"] = resolve

    try:
        result = _PROVIDER_FNS[provider](**kwargs)
    except _PROVIDER_ERRORS as exc:
        raise AiEnrichmentError(f"调用 LLM 失败：{exc}") from exc

    tags = tuple(name.strip() for name in result.tags if name.strip())
    for name in tags:
        tag = create_tag(db, library_id=library_id, name=name)
        add_tag_to_work(db, library_id=library_id, work_id=work_id, tag_id=tag.id)

    raw_payload = {
        "summary": result.summary,
        "relevance": result.relevance,
        "novelty": result.novelty,
        "tags": list(tags),
    }
    payload = redact_mapping(raw_payload)
    provenance = record_provenance(
        db, work_id=work_id, source="ai_enrichment", source_id=model, payload=payload
    )

    increment_usage(db, account_id=account_id, period=period or _current_period(), kind="ai_enrichment")

    return AssessmentResult(
        work_id=work_id,
        summary=result.summary,
        relevance=result.relevance,
        novelty=result.novelty,
        tags=tags,
        model=model,
        provenance_id=provenance.id,
    )


def bulk_assess_works(
    db: Session,
    *,
    account_id: int,
    library_id: int,
    work_ids: Sequence[int],
    connection_id: int,
    period: str | None = None,
    transport=None,
    resolve=None,
) -> BatchAssessResult:
    """批量点评——"长流程批量"：每项一个子 job（不止失败项），和
    `features/organizing` 的"瞬时批量"（只有失败才建子行）不是同一个粒度
    规则，理由见模块 docstring。"""
    provider, model, base_url, api_key = resolve_ai_profile(db, connection_id)

    total = len(work_ids)
    job = create_job(
        db, account_id=account_id, kind=JOB_KIND_BULK_ASSESS, library_id=library_id,
        counts={"total": total, "done": 0, "failed": 0},
    )
    job = transition(db, job.id, "running", allowed=_PARENT_TRANSITIONS)

    outcomes: list[BatchAssessOutcome] = []
    done = 0
    failed = 0
    for work_id in work_ids:
        child = create_job(
            db, account_id=account_id, kind=JOB_KIND_ASSESS_ITEM, library_id=library_id, parent_job_id=job.id
        )
        child = transition(db, child.id, "running", allowed=_CHILD_TRANSITIONS)
        try:
            _assess_with_profile(
                db, account_id=account_id, library_id=library_id, work_id=work_id,
                provider=provider, model=model, base_url=base_url, api_key=api_key,
                period=period, transport=transport, resolve=resolve,
            )
        except (AppError, ValueError) as exc:
            failed += 1
            transition(db, child.id, "failed", reason=str(exc), allowed=_CHILD_TRANSITIONS)
            outcomes.append(BatchAssessOutcome(work_id=work_id, status="failed", reason=str(exc)))
        else:
            done += 1
            transition(db, child.id, "succeeded", allowed=_CHILD_TRANSITIONS)
            outcomes.append(BatchAssessOutcome(work_id=work_id, status="done"))
        update_counts(db, job.id, {"total": total, "done": done, "failed": failed})

    final_status = "failed" if failed else "succeeded"
    job = transition(db, job.id, final_status, allowed=_PARENT_TRANSITIONS)
    return BatchAssessResult(job=job, outcomes=tuple(outcomes))
