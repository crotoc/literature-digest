"""features/metadata_lookup 的业务逻辑：从外部数据源（Crossref/PubMed）按
DOI/PMID 查回标准化元数据，并把查到的字段填进文库里一条已有的 work（只填
空字段，不覆盖——和 `features/importing` 的合并策略一致）。

组合 `adapters/sources/{crossref,pubmed}` + `caps/httpfetch` +
`domain/{works,connections}`。本模块不开表。

**没有依赖 `domain/libraries`**：和 `features/exporting` 同一个理由——
`refresh_work_metadata` 只做"这个 work_id 是不是真的属于这个 library_id"
的边界校验（防御性的，不是访问控制），访问控制交给调用方（`app/pages`）
在路由层已经做过的 `resolve_scope`。
"""

from __future__ import annotations

from dataclasses import dataclass

from adapters.sources.crossref import CrossrefError
from adapters.sources.crossref import lookup_by_doi as _crossref_lookup_by_doi
from adapters.sources.pubmed import PubMedError
from adapters.sources.pubmed import lookup_by_pmid as _pubmed_lookup_by_pmid
from caps.bibformats import Record
from caps.httpfetch import RateLimiter, default_resolve
from caps.secrets import derive_key
from domain.connections import get_decrypted_secret, list_connections
from domain.works import WorkDTO, get_work, list_identifiers, record_provenance, update_work
from infra.config import settings
from infra.errors import AppError

SOURCES = frozenset({"crossref", "pubmed"})
_SOURCE_IDENTIFIER_SCHEME = {"crossref": "doi", "pubmed": "pmid"}

# `source_credential` 连接本身不区分"这是给哪个外部数据源用的"——那是
# domain/connections 故意不知道的业务知识（它只认 kind 这一层）。本模块
# 自己约定：用 `config["source"]` 当判别字段，`config = {"source": "crossref",
# "mailto": "..."}` 或 `config = {"source": "pubmed"}`（PubMed 的 api_key
# 是凭据，走 secret_plain/get_decrypted_secret，不放明文 config 里）。
_SOURCE_CONFIG_KEY = "source"

# `domain.connections.get_decrypted_secret` 的 `secret_key` 是真正的 Fernet
# 密钥，不是 `infra.config.settings().app_secret_key` 本身——后者只是人能
# 管理的口令，不保证是合法的 44 字符 base64 密钥。本模块是第一个要解密
# connections 凭据的 feature，所以在这里把"用 app_secret_key + app_secret_salt
# 派生出 connections 用途的密钥"钉成约定（`caps.secrets.derive_key` 的
# `purpose` 就是为此设计的：同一个口令给不同用途派生出互不相通的密钥）。
# 之后别的 feature（如 E2 的 ai_enrichment、E5 的 fulltext）要解密别的
# connections kind，应该复用同一个 purpose="connections"——它们解密的仍是
# 同一张 `connections` 表，purpose 分的是"子系统"而不是"connection kind"。
_CONNECTIONS_SECRET_PURPOSE = "connections"


def _connections_secret_key() -> str:
    cfg = settings()
    return derive_key(cfg.app_secret_key, salt=cfg.app_secret_salt, purpose=_CONNECTIONS_SECRET_PURPOSE)


# 两个数据源各自的发布限速：Crossref 建议 polite pool 下不超过 ~1 req/s；
# PubMed 没有 API key 时是 3 req/s（間隔 ~0.34s）。按 `caps/httpfetch` 的
# README 约定，限速器是"每个数据源一个模块级单例，在多次调用之间复用"，
# 不是每次查询各建一个——否则限速完全不起作用。
_CROSSREF_RATE_LIMITER = RateLimiter(min_interval_seconds=1.0)
_PUBMED_RATE_LIMITER = RateLimiter(min_interval_seconds=0.34)

_MERGE_FIELDS = (
    "title",
    "authors",
    "year",
    "month",
    "day",
    "container_title",
    "volume",
    "issue",
    "pages",
    "publisher",
    "abstract",
    "language",
    "note",
)


class MetadataLookupFailed(AppError):
    """`adapters/sources/*` 报告查询本身失败（非 404/查无此条目）时，统一
    包成这一个异常——调用方不需要分别认识 `CrossrefError`/`PubMedError`
    两种数据源各自的异常类型。"""

    status_code = 502
    code = "metadata_lookup_failed"


@dataclass(frozen=True)
class LookupOutcome:
    record: Record | None = None
    error: str | None = None


def _validate_source(source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"不认识的来源：{source!r}（支持：{sorted(SOURCES)}）")


def _find_source_connection(db, *, account_id: int, source: str):
    connections = list_connections(db, account_id=account_id, kind="source_credential", enabled_only=True)
    candidates = [c for c in connections if c.config.get(_SOURCE_CONFIG_KEY) == source]
    if not candidates:
        return None
    defaults = [c for c in candidates if c.is_default]
    return (defaults or candidates)[0]


def _is_empty(value) -> bool:
    return value is None or value == ()


def _fill_missing_fields(db, *, existing: WorkDTO, record: Record) -> WorkDTO:
    """和 `features/importing` 里同名逻辑一字不差——两个 feature 之间禁止
    互相 import（规则 4），这段不到 15 行的策略性代码值得各自留一份，不值得
    为它单独开一个 domain/caps 模块：`domain.works.update_work` 本身刻意
    保持"覆盖式更新"的策略无关性（见该模块 README），"只填空字段"是调用方
    （feature 层）的决定，不是 domain 的不变量。"""
    updates = {}
    for name in _MERGE_FIELDS:
        current = getattr(existing, name)
        incoming = getattr(record, name)
        if _is_empty(current) and not _is_empty(incoming):
            updates[name] = incoming
    if not updates:
        return existing
    return update_work(db, existing.id, **updates)


def _record_to_payload(record: Record) -> dict:
    """落进 `work_provenance.payload_json` 的"这次查到了什么"快照——不是
    `Record` 本身（它带 `Person` 这种非 JSON 原生类型），手动摊平成普通
    dict。"""
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


def lookup_record(
    db, *, account_id: int, source: str, identifier: str, transport=None, resolve=default_resolve
) -> Record | None:
    """查一次。查无此条目返回 `None`（和两个 adapter 的约定一致，是正常
    分支）；查询本身失败（网络/5xx/响应体不符预期）抛 `MetadataLookupFailed`。

    `transport`/`resolve` 原样转发给对应的 `adapters/sources/*.lookup_by_*`
    ——和那两个 adapter 一样，是测试钩子，生产代码不传。
    """
    _validate_source(source)
    connection = _find_source_connection(db, account_id=account_id, source=source)
    try:
        if source == "crossref":
            mailto = connection.config.get("mailto") if connection else None
            return _crossref_lookup_by_doi(
                identifier,
                mailto=mailto,
                rate_limiter=_CROSSREF_RATE_LIMITER,
                transport=transport,
                resolve=resolve,
            )
        api_key = (
            get_decrypted_secret(db, connection.id, secret_key=_connections_secret_key())
            if connection is not None
            else None
        )
        return _pubmed_lookup_by_pmid(
            identifier,
            api_key=api_key,
            rate_limiter=_PUBMED_RATE_LIMITER,
            transport=transport,
            resolve=resolve,
        )
    except (CrossrefError, PubMedError) as exc:
        raise MetadataLookupFailed(f"查询 {source} 失败：{identifier}") from exc


def lookup_records(
    db, *, account_id: int, source: str, identifiers, transport=None, resolve=default_resolve
) -> dict[str, LookupOutcome]:
    """批量查询，单个失败隔离——不影响其它 identifier 的结果。本模块不内置
    任何批量进度编排（没有依赖 `domain.jobs`）：调用方（比如未来回抓一批
    文献元数据的报告分析流程）自己决定要不要把每一项包成一个子 job。"""
    outcomes: dict[str, LookupOutcome] = {}
    for identifier in identifiers:
        try:
            record = lookup_record(
                db,
                account_id=account_id,
                source=source,
                identifier=identifier,
                transport=transport,
                resolve=resolve,
            )
        except MetadataLookupFailed as exc:
            outcomes[identifier] = LookupOutcome(error=str(exc))
        else:
            outcomes[identifier] = LookupOutcome(record=record)
    return outcomes


def refresh_work_metadata(
    db,
    *,
    account_id: int,
    library_id: int,
    work_id: int,
    source: str,
    identifier: str | None = None,
    transport=None,
    resolve=default_resolve,
) -> WorkDTO:
    """用 `source` 查一次元数据，把查到的字段只填进当前为空的部分（不覆盖
    已有字段），并记一条 `work_provenance`。

    `identifier` 不给时，从这条 work 已有的标识符里按 `source` 对应的
    scheme（doi/pmid）取——这是最常见的用法："这条文献已经有 DOI 了，去
    Crossref 查一下完整元数据补上"。显式传 `identifier` 是给"这条文献还
    没有标识符，但我知道它的 DOI/PMID 是什么"这种场景用的。
    """
    _validate_source(source)
    work = get_work(db, work_id)
    if work.library_id != library_id:
        raise ValueError(f"work_id 不属于这个库：{work_id}")

    if identifier is None:
        scheme = _SOURCE_IDENTIFIER_SCHEME[source]
        known = {row.scheme: row.value for row in list_identifiers(db, work_id)}
        identifier = known.get(scheme)
        if identifier is None:
            raise ValueError(f"work #{work_id} 没有 {scheme} 标识符，且未显式提供 identifier")

    record = lookup_record(
        db,
        account_id=account_id,
        source=source,
        identifier=identifier,
        transport=transport,
        resolve=resolve,
    )
    if record is None:
        return work

    updated = _fill_missing_fields(db, existing=work, record=record)
    record_provenance(
        db, work_id=work_id, source=source, source_id=identifier, payload=_record_to_payload(record)
    )
    return updated
