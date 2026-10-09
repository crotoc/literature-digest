# features/connection_setup

数据源凭据的管理 + 测试连接。

**v1 范围只有 `source_credential` 一种 kind**（Crossref/PubMed 的访问
凭据）。`domain/connections` 本身是四种 kind（`ai_profile` /
`telegram_destination` / `source_credential` / `download_proxy`）共用的
一张表，但"怎么管理某一种 kind"是业务知识，不是 `domain/connections` 该
知道的——E2/E4/E5 阶段的 `ai_enrichment`/`delivery`/`fulltext` 会各自开
一份同构但独立的管理模块，不在这里提前实现。

## 对外表面

- `KIND = "source_credential"`，`SOURCES = {"crossref", "pubmed"}`
- `UnknownSource(ValueError)`、`WrongKind(ValueError)`
- `create_source_credential(db, *, account_id, source, name, mailto=None, api_key=None, enabled=True, is_default=False)`
- `list_source_credentials(db, *, account_id)`
- `update_source_credential(db, connection_id, *, name=UNSET, mailto=UNSET, enabled=UNSET)`
- `rotate_api_key(db, connection_id, *, api_key)`
- `delete_source_credential(db, connection_id)`
- `set_default_source_credential(db, connection_id)`
- `check_connection(db, connection_id, *, transport=None, resolve=default_resolve) -> ConnectionDTO`

## 依赖方向

`domain/connections` + `caps/{secrets,httpfetch}`（后者只用它的
`default_resolve` 默认值，不发请求）+ `caps/probe` + `adapters/sources/
{crossref,pubmed}` 的 `check()`。本模块不开表。

## 和 `features/metadata_lookup` 重复的几个常量/约定——刻意的

`SOURCES`、`config["source"]` 当判别字段、`derive_key(..., purpose=
"connections")` 派生解密密钥——这几样和 `features/metadata_lookup` 里的
同名常量/约定完全一样。features 之间禁止互相 import（lint 规则 4），没有
第三个地方可以放这份共享知识（放 `domain/connections` 不行，它故意不认识
"source"这个判别字段是哪个业务层的概念；放 `caps/secrets` 也不行，
`purpose` 字符串是两个 feature 商定的用途标签，不是 cap 自己的知识）。
所以两边各自维护一份，**改动时必须同步改两处**，这是记在这里的一条
运维提醒，不是设计缺陷。

## adapters 原来没有 `check()`，这次补上了

读 `caps/probe` 的协议声明才发现：`adapters/sources/crossref.py` 和
`pubmed.py` 原来都没有实现 `check(config) -> ProbeResult`，这是本模块
存在的前提，所以本次顺手给两个 adapter 都补上了：

- `crossref.check(config)`：查一个已知稳定存在的 DOI（`_PROBE_DOI`），
  `config["mailto"]` 没给也能测通——polite pool 只是限速优待，不是访问
  门槛。
- `pubmed.check(config)`：查一个已知稳定存在的 PMID（`_PROBE_PMID`），
  `config["api_key"]` 没给也能测通，只是限速更严。

两个 `check()` 都额外接受可选的 `transport`/`resolve` 测试钩子，和
`lookup_by_doi`/`lookup_by_pmid` 自己的约定一致——这不影响它们满足
`caps.probe.CheckFn` 协议：`run_check()` 只会以单个 `config` 位置参数
调用 `check`，多余的可选关键字参数不影响这一点。

## `check_connection` 的 `resolve` 不能漏传

`caps/httpfetch` 的 SSRF 防护会先 `resolve()` host 再决定要不要真的发
请求。只 mock 掉 HTTP 传输层（`transport`）、不连带 mock `resolve`，单测
仍然会在真实网络上发一次 DNS 查询——这是写测试时踩到的一个坑，所以
`check_connection` 和两个 adapter 的 `check()` 一样，`transport`/`resolve`
两个测试钩子都要接、都要转发，缺一个都不算把网络隔离干净。

## Crossref 的 `mailto` 是明文，PubMed 的 `api_key` 是凭据

两者在 `create_source_credential` 里走不同路径：`mailto` 直接进
`config`（Crossref 的 polite pool 本来就是公开约定，没有保密需求）；
`api_key` 走 `secret_plain` 加密存储。传错了直接拒绝（比如给
`source="crossref"` 传 `api_key`），不是默默忽略——选错来源却以为填了凭据
却没生效，比报错更容易让人踩坑。

## 为什么处处要校验 `WrongKind`

虽然 v1 阶段"除了 `source_credential`，别的 kind 还没有任何 feature 真的
建出来"，但 `domain.connections.get_connection` 本身不按 kind 过滤——
任何 `connection_id` 都能查到。本模块的每一个写操作（update/rotate/
delete/set_default/check_connection）都先确认这条连接确实是
`source_credential`，拒绝误操作到将来别的 feature 建的连接上，而不是
假设"现在反正只有这一种，不会有事"。

## 刻意裁剪的范围

- **不负责"账号默认值的全局展示/设置页分区拼装"**：那是 `app/pages/
  settings` 的事，本模块只提供数据和操作。
- **`ai_profile`/`telegram_destination`/`download_proxy` 三种 kind 的
  管理一概不做**：留给 E2/E4/E5 阶段对应的 feature。
