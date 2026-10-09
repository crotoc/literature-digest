"""features/connection_setup：数据源凭据的管理 + 测试连接。

**v1 范围只有 `source_credential` 一种 kind**（Crossref/PubMed 的访问凭据）
——`ai_profile`/`telegram_destination`/`download_proxy` 三种 kind 留给
E2/E4/E5 阶段各自的 feature 自己开一份同构但独立的管理模块，不在这里提前
实现（`domain/connections` 本身四种 kind 共用一张表，但"怎么管理某一种
kind"是各 feature 自己的业务知识，和 `domain/jobs` 不认识具体 kind 是
同一个分层原则）。

组合 `domain/connections` + `caps/secrets`（派生解密密钥）+ `caps/probe`
（强制 adapter 的 check() 结果形状）+ `adapters/sources/{crossref,pubmed}`
的 `check()`。本模块不开表。

**`SOURCES` / `_SOURCE_CONFIG_KEY` / 密钥派生 purpose 都是和
`features/metadata_lookup` 重复的常量/约定**——features 之间禁止互相
import（lint 规则 4），这是架构要求下必要的重复，不是没注意到。两边必须
手动保持同步：
  - `config["source"]` 当判别字段，取值 `{"crossref", "pubmed"}`
  - `derive_key(app_secret_key, salt=app_secret_salt, purpose="connections")`
    派生出解密 `connections` 表凭据用的密钥
"""

from __future__ import annotations

from functools import partial

from adapters.sources.crossref import check as _crossref_check
from adapters.sources.pubmed import check as _pubmed_check
from caps.httpfetch import default_resolve
from caps.probe import run_check
from caps.secrets import derive_key
from domain.connections import (
    ConnectionDTO,
    create_connection,
    delete_connection,
    get_connection,
    get_decrypted_secret,
    list_connections,
    record_check_result,
    rotate_secret,
    set_default_connection,
    update_connection,
)
from infra.config import settings

KIND = "source_credential"
SOURCES = frozenset({"crossref", "pubmed"})
_SOURCE_CONFIG_KEY = "source"
_CONNECTIONS_SECRET_PURPOSE = "connections"

_SOURCE_CHECKS = {"crossref": _crossref_check, "pubmed": _pubmed_check}

_UNSET = object()


class UnknownSource(ValueError):
    """`source` 不在 `SOURCES` 里。"""


class WrongKind(ValueError):
    """这个 `connection_id` 不是 `source_credential` 类型——本模块的 v1
    范围决定了它只认这一种 kind，拒绝误操作别的 kind（哪怕现在还没有别的
    feature 真的建出来别的 kind）。"""

    def __init__(self, connection_id: int, actual_kind: str) -> None:
        super().__init__(f"连接 #{connection_id} 是 {actual_kind!r} 类型，不是 {KIND!r}")
        self.connection_id = connection_id
        self.actual_kind = actual_kind


def _secret_key() -> str:
    cfg = settings()
    return derive_key(cfg.app_secret_key, salt=cfg.app_secret_salt, purpose=_CONNECTIONS_SECRET_PURPOSE)


def _validate_source(source: str) -> None:
    if source not in SOURCES:
        raise UnknownSource(f"不认识的来源：{source!r}（支持：{sorted(SOURCES)}）")


def _require_source_credential(connection: ConnectionDTO) -> None:
    if connection.kind != KIND:
        raise WrongKind(connection.id, connection.kind)


def create_source_credential(
    db,
    *,
    account_id: int,
    source: str,
    name: str,
    mailto: str | None = None,
    api_key: str | None = None,
    enabled: bool = True,
    is_default: bool = False,
) -> ConnectionDTO:
    """`mailto` 只给 `source="crossref"` 用（明文存 config，不是凭据——
    Crossref 的 polite pool 本来就是公开约定，不需要加密）；`api_key` 只给
    `source="pubmed"` 用（当成凭据加密存）。两者给错了来源直接拒绝，不是
    默默忽略——用户选错来源时应该立刻看到报错，不是以为填了却没生效。
    """
    _validate_source(source)
    if source == "crossref" and api_key is not None:
        raise ValueError("source='crossref' 不接受 api_key（它没有凭据，只有 mailto）")
    if source == "pubmed" and mailto is not None:
        raise ValueError("source='pubmed' 不接受 mailto（它的凭据是 api_key）")

    config = {_SOURCE_CONFIG_KEY: source}
    secret_plain = None
    secret_key = None
    if source == "crossref":
        if mailto:
            config["mailto"] = mailto
    elif api_key:
        secret_plain = api_key
        secret_key = _secret_key()

    return create_connection(
        db,
        account_id=account_id,
        kind=KIND,
        name=name,
        config=config,
        secret_plain=secret_plain,
        secret_key=secret_key,
        enabled=enabled,
        is_default=is_default,
    )


def list_source_credentials(db, *, account_id: int) -> list[ConnectionDTO]:
    return list_connections(db, account_id=account_id, kind=KIND)


def update_source_credential(
    db,
    connection_id: int,
    *,
    name=_UNSET,
    mailto=_UNSET,
    enabled=_UNSET,
) -> ConnectionDTO:
    """改名字/开关/（仅 crossref）mailto。换 `api_key` 走 `rotate_api_key`
    ——改凭据和改普通字段分开，和 `domain.connections` 本身
    `update_connection`/`rotate_secret` 两个函数分开是同一个理由。
    """
    connection = get_connection(db, connection_id)
    _require_source_credential(connection)

    kwargs = {}
    if name is not _UNSET:
        kwargs["name"] = name
    if enabled is not _UNSET:
        kwargs["enabled"] = enabled
    if mailto is not _UNSET:
        if connection.config.get(_SOURCE_CONFIG_KEY) != "crossref":
            raise ValueError("只有 source='crossref' 的连接才有 mailto")
        config = dict(connection.config)
        if mailto:
            config["mailto"] = mailto
        else:
            config.pop("mailto", None)
        kwargs["config"] = config

    if not kwargs:
        return connection
    return update_connection(db, connection_id, **kwargs)


def rotate_api_key(db, connection_id: int, *, api_key: str) -> ConnectionDTO:
    connection = get_connection(db, connection_id)
    _require_source_credential(connection)
    if connection.config.get(_SOURCE_CONFIG_KEY) != "pubmed":
        raise ValueError("只有 source='pubmed' 的连接才有 api_key")
    return rotate_secret(db, connection_id, secret_plain=api_key, secret_key=_secret_key())


def delete_source_credential(db, connection_id: int) -> None:
    connection = get_connection(db, connection_id)
    _require_source_credential(connection)
    delete_connection(db, connection_id)


def set_default_source_credential(db, connection_id: int) -> ConnectionDTO:
    connection = get_connection(db, connection_id)
    _require_source_credential(connection)
    return set_default_connection(db, connection_id)


def check_connection(
    db, connection_id: int, *, transport=None, resolve=default_resolve
) -> ConnectionDTO:
    """实际测一次连接：取配置 +（pubmed 才有的）解密凭据，交给对应
    `adapters/sources/*.check()`，经 `caps.probe.run_check` 强制结果形状，
    落库。返回更新后的 `ConnectionDTO`（带上最新的 `last_check_*` 字段）。

    `transport`/`resolve` 是测试钩子，原样转发给 adapter 的 `check()`——
    两个都要转发，不能只给 `transport`：`caps/httpfetch` 的 SSRF 防护会先
    `resolve()` host 再决定要不要真的发请求，只 mock 掉 HTTP 层但不 mock
    `resolve` 的话，单测仍然会触发一次真实 DNS 查询。生产代码两个都不传。
    """
    connection = get_connection(db, connection_id)
    _require_source_credential(connection)
    source = connection.config.get(_SOURCE_CONFIG_KEY)
    _validate_source(source)

    if source == "crossref":
        probe_config = {"mailto": connection.config.get("mailto")}
    else:
        api_key = (
            get_decrypted_secret(db, connection_id, secret_key=_secret_key())
            if connection.has_secret
            else None
        )
        probe_config = {"api_key": api_key}

    check_fn = partial(_SOURCE_CHECKS[source], transport=transport, resolve=resolve)
    result = run_check(check_fn, probe_config)
    return record_check_result(db, connection_id, ok=result.ok, message=result.message)
