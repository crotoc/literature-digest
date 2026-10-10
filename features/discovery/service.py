"""features/discovery：按 `features/query_planner` 编译好的检索式，去
Crossref/PubMed 跑一轮搜索，把"没见过的"结果落成 `domain.intents.
Candidate`，供设置页/首页展示给用户。

组合 `adapters/sources/{crossref,pubmed}` + `domain/{intents,works,
libraries,connections,jobs,schedules}`。本模块不开表。

**两层去重**，缺一不可：

1. `domain.intents.is_seen()`——"这个 intent 这一轮之前见过这条
   `(source, source_id)` 吗"，跨轮增量的核心机制，防止同一个检索式一直
   命中同一批旧文献就一直重复入候选。
2. 本模块自己加的"已经在库里了吗"——用 `domain.works.find_by_identifier`
   按 DOI/PMID 查这个账号能看到的**所有**库（`domain.libraries.
   list_libraries_for_account`），命中就直接 `mark_seen` 跳过、不建候选。
   这一层是`domain/intents`自己不会做的（它不认识 `domain.works`），
   道理很直接：用户已经手动收进库里的文献，不该被研究雷达当"新发现"
   再播报一遍。两层共用同一张 `intent_seen` 标记表，命中任一层都会写
   标记，这样下一轮两层都不用再查一次。

**`run_discovery` 失败不抛异常，落一条 `status="failed"` 的
`DiscoveryRun`**——和 `features/query_planner.compile_intent_query` 同一个
理由：这是一次可能被 cron 触发的后台动作，失败本身是需要留痕展示给用户
看的正常结果，不该让异常把调用方打断。

**不做"按来源的部分失败"处理**：Crossref/PubMed 任一个报错，整次运行
直接记为失败，不尝试"保留另一个来源已经查到的那部分结果"——`discovery_
runs` 的状态本来就只有粗粒度的 `running`/`succeeded`/`failed`（见
`domain/intents/README.md`），没有"部分成功"这个状态，强行在这里做部分
成功只会让"这次到底算成功还是失败"变得含糊。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from adapters.sources.crossref import CrossrefError
from adapters.sources.crossref import search as _crossref_search
from adapters.sources.pubmed import PubMedError
from adapters.sources.pubmed import search as _pubmed_search
from caps.bibformats import Record
from caps.httpfetch import RateLimiter, default_resolve
from caps.secrets import derive_key
from domain.connections import get_decrypted_secret, list_connections
from domain.intents import (
    DiscoveryRunDTO,
    create_candidate,
    create_discovery_run,
    finish_discovery_run,
    get_intent,
    get_latest_ready_version,
    is_seen,
    mark_seen,
)
from domain.jobs import create_job, transition
from domain.libraries import list_libraries_for_account
from domain.schedules import ScheduleDTO, list_due_schedules, mark_run
from domain.works import find_by_identifier
from infra.config import settings

SOURCES = ("crossref", "pubmed")
_SOURCE_IDENTIFIER_SCHEME = {"crossref": "doi", "pubmed": "pmid"}
_SOURCE_CONFIG_KEY = "source"
_CONNECTIONS_SECRET_PURPOSE = "connections"

JOB_KIND = "discovery_run"
_JOB_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded", "failed"}}

_SCHEDULE_TARGET_KIND = "intent"

# 两个数据源各自的发布限速，数值和 `features/metadata_lookup` 一致——
# 两个 feature 之间禁止互相 import（规则 4），这组常量只能各留一份。
_CROSSREF_RATE_LIMITER = RateLimiter(min_interval_seconds=1.0)
_PUBMED_RATE_LIMITER = RateLimiter(min_interval_seconds=0.34)


class IntentDisabled(ValueError):
    """意图已停用，不能跑发现。"""


class NoReadyQuery(ValueError):
    """这个意图还没有一条 `status == "ready"` 的检索式版本。"""


def _connections_secret_key() -> str:
    cfg = settings()
    return derive_key(cfg.app_secret_key, salt=cfg.app_secret_salt, purpose=_CONNECTIONS_SECRET_PURPOSE)


def _find_source_connection(db, *, account_id: int, source: str):
    connections = list_connections(db, account_id=account_id, kind="source_credential", enabled_only=True)
    candidates = [c for c in connections if c.config.get(_SOURCE_CONFIG_KEY) == source]
    if not candidates:
        return None
    defaults = [c for c in candidates if c.is_default]
    return (defaults or candidates)[0]


def _record_to_payload(record: Record) -> dict:
    return {
        "item_type": record.item_type,
        "title": record.title,
        "authors": [
            {"family": p.family, "given": p.given, "literal": p.literal} for p in record.authors
        ],
        "year": record.year,
        "month": record.month,
        "day": record.day,
        "container_title": record.container_title,
        "volume": record.volume,
        "issue": record.issue,
        "pages": record.pages,
        "publisher": record.publisher,
        "doi": record.doi,
        "pmid": record.pmid,
        "isbn": record.isbn,
        "issn": record.issn,
        "url": record.url,
        "abstract": record.abstract,
        "language": record.language,
        "note": record.note,
    }


def _date_window(lookback_days: int, *, today: date) -> tuple[str, str, str]:
    """返回 `(crossref_from_pub_date, pubmed_mindate, pubmed_maxdate)`。两个
    来源的日期格式不一样（`"YYYY-MM-DD"` vs `"YYYY/MM/DD"`），换算是本模块
    的职责，两个 `adapters.sources.*.search()` 只管原样拼进请求参数。"""
    since = today - timedelta(days=lookback_days)
    return (
        since.strftime("%Y-%m-%d"),
        since.strftime("%Y/%m/%d"),
        today.strftime("%Y/%m/%d"),
    )


def _already_in_library(db, *, account_id: int, source: str, source_id: str) -> bool:
    scheme = _SOURCE_IDENTIFIER_SCHEME[source]
    libraries = list_libraries_for_account(db, account_id)
    return any(
        find_by_identifier(db, library_id=library.id, scheme=scheme, value=source_id) is not None
        for library in libraries
    )


def _search_source(
    db, *, account_id: int, source: str, query: str, lookback_days: int, limit: int, today: date,
    transport, resolve,
) -> list[tuple[str, Record]]:
    from_pub_date, mindate, maxdate = _date_window(lookback_days, today=today)
    connection = _find_source_connection(db, account_id=account_id, source=source)

    if source == "crossref":
        mailto = connection.config.get("mailto") if connection else None
        return _crossref_search(
            query, mailto=mailto, from_pub_date=from_pub_date, rows=limit,
            rate_limiter=_CROSSREF_RATE_LIMITER, transport=transport, resolve=resolve,
        )

    api_key = (
        get_decrypted_secret(db, connection.id, secret_key=_connections_secret_key())
        if connection is not None
        else None
    )
    return _pubmed_search(
        query, api_key=api_key, mindate=mindate, maxdate=maxdate, retmax=limit,
        rate_limiter=_PUBMED_RATE_LIMITER, transport=transport, resolve=resolve,
    )


def run_discovery(
    db,
    *,
    account_id: int,
    intent_id: int,
    today: date | None = None,
    transport=None,
    resolve=default_resolve,
) -> DiscoveryRunDTO:
    """跑一轮发现：编译好的检索式分别喂给两个来源，按两层去重落新候选。

    `intent.candidate_limit` 是这一轮最多新建多少条候选——按来源顺序
    （先 crossref 再 pubmed）遍历结果，一旦新候选数够了就不再看剩下的
    结果（不会"看了但因为去重跳过"的也继续数到总数里，省下来的 HTTP
    调用没有意义，但已经发出去的两次 `search()` 调用本身不按候选数提前
    截断——那是 `rows`/`retmax` 参数的事，不是这里的事）。

    `IntentDisabled`/`NoReadyQuery` 是前置校验，直接抛，不建 job/run——
    还没发生任何检索动作，不是"这次运行失败了"，是"根本没法开始这次运行"，
    不值得留一条 `DiscoveryRun` 记录。真正发出检索请求之后的失败（见下面
    `except (CrossrefError, PubMedError)`）才会落一条 `status="failed"`
    的 `DiscoveryRun`。
    """
    intent = get_intent(db, intent_id)
    if not intent.enabled:
        raise IntentDisabled(f"研究意图 #{intent_id} 已停用，不能跑发现")

    version = get_latest_ready_version(db, intent_id)
    if version is None:
        raise NoReadyQuery(f"研究意图 #{intent_id} 还没有可用的检索式，请先编译一次查询")

    query = version.query_json.get("query")
    if not query:
        raise NoReadyQuery(f"研究意图 #{intent_id} 最新版本的检索式是空的")

    job = create_job(db, account_id=account_id, kind=JOB_KIND)
    job = transition(db, job.id, "running", allowed=_JOB_TRANSITIONS)
    run = create_discovery_run(db, intent_id=intent_id, intent_version_id=version.id, job_id=job.id)

    today = today or datetime.now(UTC).date()
    try:
        results: list[tuple[str, str, Record]] = []
        for source in SOURCES:
            hits = _search_source(
                db, account_id=account_id, source=source, query=query,
                lookback_days=intent.lookback_days, limit=intent.candidate_limit, today=today,
                transport=transport, resolve=resolve,
            )
            results.extend((source, source_id, record) for source_id, record in hits)
    except (CrossrefError, PubMedError) as exc:
        transition(db, job.id, "failed", reason=str(exc), allowed=_JOB_TRANSITIONS)
        return finish_discovery_run(
            db, run.id, status="failed", candidate_count=0, new_candidate_count=0, error=str(exc)
        )

    candidate_count = 0
    new_candidate_count = 0
    for source, source_id, record in results:
        candidate_count += 1
        if new_candidate_count >= intent.candidate_limit:
            break
        if is_seen(db, intent_id=intent_id, source=source, source_id=source_id):
            continue
        if _already_in_library(db, account_id=account_id, source=source, source_id=source_id):
            mark_seen(db, intent_id=intent_id, source=source, source_id=source_id)
            continue
        mark_seen(db, intent_id=intent_id, source=source, source_id=source_id)
        create_candidate(
            db, discovery_run_id=run.id, intent_id=intent_id, source=source, source_id=source_id,
            title=record.title, url=record.url, payload=_record_to_payload(record),
        )
        new_candidate_count += 1

    transition(db, job.id, "succeeded", allowed=_JOB_TRANSITIONS)
    return finish_discovery_run(
        db, run.id, status="succeeded", candidate_count=candidate_count,
        new_candidate_count=new_candidate_count,
    )


def run_due_intents(
    db, *, as_of: datetime | None = None, transport=None, resolve=default_resolve
) -> list[DiscoveryRunDTO]:
    """按 `domain.schedules` 挑出此刻该跑的、`target_kind="intent"` 的调度，
    逐个跑一遍 `run_discovery` 再标记 `last_run_at`。

    `domain.schedules.list_due_schedules()` 是跨全部账号、不分 target_kind
    的通用查询（它自己的 README 写明"谁来认领哪个 target_kind 是调用方的
    事"）——本模块就是"认领 `target_kind="intent"`"的那个调用方，E4 的
    `reports`/`delivery` 如果也要用调度，会是另一个独立的调用方筛另一个
    `target_kind`，彼此不需要知道对方的存在。

    谁来定期调这个函数（真正的"轮询循环"）是部署/`app/` 层的事——和
    `caps/schedule`、`domain/schedules` 自己刻意不做轮询循环是同一个
    理由，这里只负责"被调一次就把该跑的都跑了"。

    单个意图的前置校验失败（`IntentDisabled`/`NoReadyQuery`，比如调度
    还留着但意图后来被停用了）不会打断整批——跳过这一个，照样标记
    `last_run_at`（不标记的话每次轮询都会重新认领同一个坏掉的调度，没有
    意义），继续跑其余到期的意图。
    """
    moment = as_of or datetime.now(UTC).replace(tzinfo=None)
    due: list[ScheduleDTO] = [
        schedule
        for schedule in list_due_schedules(db, as_of=moment)
        if schedule.target_kind == _SCHEDULE_TARGET_KIND
    ]

    runs = []
    for schedule in due:
        try:
            run = run_discovery(
                db, account_id=schedule.account_id, intent_id=schedule.target_id,
                transport=transport, resolve=resolve,
            )
        except (IntentDisabled, NoReadyQuery):
            pass
        else:
            runs.append(run)
        mark_run(db, target_kind=_SCHEDULE_TARGET_KIND, target_id=schedule.target_id, when=moment)
    return runs
