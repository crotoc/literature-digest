# features/metadata_lookup

从外部数据源（Crossref / PubMed）按 DOI / PMID 查回标准化元数据，填进文库里
一条已有的 work（只填空字段，不覆盖），并记一条 `work_provenance`。本模块不开表。

## 为什么没有依赖 `domain/libraries`

和 `features/exporting` 同一个理由：`refresh_work_metadata` 只做"这个
work_id 是不是真的属于这个 library_id"的边界校验（防御性的，不是访问控制），
访问控制交给调用方（`app/pages`）在路由层已经做过的 `resolve_scope`。

## `_fill_missing_fields` 和 `features/importing` 里同名函数重复

两个 feature 之间禁止互相 import（规则 4）。这段不到 15 行的"只填空字段"
策略在两个模块里各留一份——`domain.works.update_work` 本身刻意保持"覆盖式
更新"的策略无关性，"只填空字段"是调用方（feature 层）的决定，不是 domain
的不变量，所以不值得为它单独开一个 domain/caps 模块。

## `config["source"]` 判别字段：一个本模块自己定的约定

`domain/connections` 的 `source_credential` 这个 `kind` 本身不区分"这是给
哪个外部数据源用的"——那是它故意不知道的业务知识，它只认 `kind` 这一层。
本模块约定用 `config["source"]` 当判别字段：

```python
config = {"source": "crossref", "mailto": "researcher@example.org"}
config = {"source": "pubmed"}   # api_key 是凭据，走 secret_plain，不放明文 config 里
```

一个账号下同一个 `source` 可以配多条 `source_credential` 连接；`is_default=True`
的那条优先，没有默认时取第一条匹配的。

## 一处真实的自查修正：`secret_key` 不是 `app_secret_key` 本身

`domain.connections.get_decrypted_secret(db, connection_id, *, secret_key)` 的
`secret_key` 是真正喂给 `cryptography.Fernet` 的密钥（44 字符 URL 安全
base64），不是随便一个字符串——这是 `caps.secrets` 的硬性要求，不是本模块
的决定。

写第一版时犯了两次错，按时间顺序都值得记下来：

1. **第一次**：定义了一个语义常量 `_PUBMED_API_KEY_SECRET_KEY = "api_key"`，
   当成 `secret_key=` 传给 `get_decrypted_secret`——把它错当成"这个凭据叫
   什么名字"的标签字段。重新读 `domain.connections.get_decrypted_secret`
   和 `caps.secrets.decrypt` 的签名才发现 `secret_key` 字面意思就是加密密钥
   本身。
2. **第二次（修复第一次时又犯的）**：改成直接传 `settings().app_secret_key`，
   以为"反正是真密钥就行"。但 `infra.config.Settings.app_secret_key` 只是
   一句**人能管理的口令**（校验规则只要求 ≥32 字符），不保证是合法的 44
   字符 base64 Fernet 密钥——`Fernet(key)` 在大多数口令上会直接抛
   `InvalidKey`。重新读 `caps/secrets/service.py` 才注意到模块里专门有一个
   `derive_key(passphrase, *, salt, purpose) -> str`，正是为"口令 → 合法
   Fernet 密钥"这一步设计的，`purpose` 还能让同一个口令给不同用途派生出
   互不相通的密钥（文档原话："拿凭据密钥去解会话数据解不开"）。

最终做法：`infra.config.Settings` 加一个新字段 `app_secret_salt`（部署级盐值，
校验 ≥8 字符，和 `derive_key` 的要求对齐），本模块内部：

```python
def _connections_secret_key() -> str:
    cfg = settings()
    return derive_key(cfg.app_secret_key, salt=cfg.app_secret_salt, purpose="connections")
```

`purpose="connections"` 是刻意选的通用值，不是 `"metadata_lookup"`——因为
将来别的 feature（E2 的 `ai_enrichment` 解密 `ai_profile` 凭据、E5 的
`fulltext` 解密 `download_proxy` 凭据）要解密的仍然是同一张 `connections`
表，`purpose` 分的是"子系统"（一个口令对应一套密钥空间），不是"connection
kind"。本模块是第一个需要解密 connections 凭据的 feature，所以在这里把这条
约定钉下来；后续模块应该复用同一个 `purpose="connections"` 字符串，而不是
各自发明一个。这也解释了为什么这个推导函数没有放进 `domain/connections`——
该模块的 README 已经明确写了"密钥从哪来不是本模块的知识"，调用方才知道。

## 依赖方向

依赖 `adapters/sources/{crossref,pubmed}` + `caps/{bibformats,httpfetch,secrets}`
+ `domain/{works,connections}` + `infra/config`。不依赖任何其它 `features/*`
（符合规则 4）。

## 限速器是模块级单例

`_CROSSREF_RATE_LIMITER`/`_PUBMED_RATE_LIMITER` 在模块加载时各建一次，不是
每次查询各建一个——和 `caps/httpfetch` 的 README 约定一致，限速器要跨调用
复用才真的限得住速率。

## 批量查询不编排 `domain.jobs`

`lookup_records` 只做"批量 + 单项失败隔离"，不内置任何进度上报或 job 建
行——本模块没有依赖 `domain.jobs`。按计划里"瞬时批量只在失败时建子行、长
流程批量每项一行"的粒度规则，这件事该由调用方（比如未来回抓一批文献元数据
的报告分析流程）决定，不该在这个纯查询模块里预先决定编排粒度。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| NIH Reporter 数据源 | 计划里排在 v1 之后（`adapters/sources/nih_reporter.py` 还没写） |
| `refresh_work_metadata` 批量版本 | 调用方可以自己循环调用单条版本；真要批量编排+进度上报，归属调用它的上层 feature，不归这里 |
| 查询结果缓存/去重（同一个 DOI 短时间内查两次） | v1 没有这个真实需求；`caps/httpfetch` 的限速器已经防止了高频轰炸数据源 |
| 自动探测 source（给一个 identifier 猜它是 DOI 还是 PMID） | `source` 永远由调用方显式指定，不做"看起来像什么就当什么"的启发式猜测 |
| 把 PubMed 的 `api_key` 以外的凭据字段（比如未来的 OAuth token）也走 `secret_plain` | 当前两个数据源只需要一个密钥字段；出现第二个凭据字段时再决定怎么扩展 `domain.connections` 的 schema |
