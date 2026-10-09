# domain/connections

统一管理四类"对外连接"：`ai_profile`（AI 模型配置）、`telegram_destination`（推送目的地）、
`source_credential`（文献源凭据，如 Crossref/PubMed）、`download_proxy`（下载代理/EZproxy）。

## 为什么四类合并成一张表

旧单体对这四类分别写了四套几乎相同的 CRUD + 各自的"测试连接"逻辑（见规避清单 #6）。
四者的字段形状高度重合：都有 `name` / `enabled` / 一份不透明的配置 / 可能有一个密钥 /
需要"测试连接"的结果。真正不同的只是 `config_json` 里装的内容和 `kind` 本身。按三层判定标准，
这是"有自己的表"的业务实体，但不值得为每个 kind 建一张结构几乎相同的表——合并成一张表、
用 `kind` 区分，`config_json` 承载 kind 特有的结构化配置（如 `ai_profile` 的 `model`/`base_url`，
`source_credential` 的 `mailto`），上层调用者按 `kind` 自行解释 `config` 的内容。

`KINDS = frozenset({"ai_profile", "telegram_destination", "source_credential", "download_proxy"})`
是唯一的合法取值集合，`create_connection` 会拒绝其他值。

## 为什么 is_default 用"自动降级"而不是数据库约束

AI 配置语义上只能有一个默认（调用时不知道用哪个就用默认那个），但 Telegram 目的地可以同时
有多个全部启用（推送给多个群）。这意味着"是否允许多个默认"本身不是跨 kind 统一的硬约束，
而"同一时刻至多一个默认"才是不变量——且只对有默认语义的 kind 才有意义。

实现"同一 (account_id, kind) 下至多一个 is_default=True"有两种办法：

1. 数据库层条件唯一索引（如 SQLite 的 `sqlite_where=connections.c.is_default`）。
2. 服务层查询现有默认项，创建/设置新默认前先把它降级。

`infra/db.py` 的 `database_url` 是可配置的（当前是 sqlite，未来可能换 Postgres），条件唯一索引的
语法在不同后端之间不通用（SQLite 用 `sqlite_where`，Postgres 用 `postgresql_where`），而服务层
自动降级是后端无关的，并且已经在 `domain/attachments` 的"每个 work 至多一个 main 附件"上验证过
这个模式是安全的（旧的那个不会消失，只是失去标记，没有数据丢失风险）。所以这里复用同一个模式：
`_demote_existing_default` 在 `create_connection(..., is_default=True)` 和
`set_default_connection` 两处被调用。

## 为什么 domain/connections 直接 import caps.secrets，但从不生成/存储密钥

架构上 domain → caps 是允许的依赖方向（`domain/works` 直接用 `caps.bibformats.Person` 也是同样
的先例）。`secret_plain` 通过 `caps.secrets.encrypt(plaintext, key=secret_key)` 加密后存进
`secret_ciphertext`；`get_decrypted_secret` 用 `caps.secrets.decrypt` 解密。

但加密密钥本身（`secret_key`）永远是调用方传入的必填参数，本模块不生成、不持久化、不缓存它。
密钥的生命周期管理（从哪来、怎么轮换、存在哪里）是 `infra/config` 和调用方（上层 feature）的
职责——这让 `domain/connections` 保持无状态：它只知道"给我密钥我就能加/解密"，不知道密钥本身
从哪来。`create_connection` 和 `rotate_secret` 都要求：给了 `secret_plain` 就必须同时给
`secret_key`，否则抛 `ValueError`（拒绝"加密了但将来解不开"的中间状态）。

## 为什么 record_check_result 不自己去测试连接

`caps/probe` 是刻意做薄的协议层：只定义 `ProbeResult` DTO 和"adapter 必须导出
`check(config) -> ProbeResult`"的约定，具体怎么测一个 AI key 是否有效、一个 Telegram bot token
是否能发消息、一个代理是否可达——这些测法彼此毫无共性，如果把它们塞进
`domain/connections`，这个模块就会变成一个按 `kind` 分支的大 switch，而且会被迫
import 各个 `adapters/llm`、`adapters/delivery` 等模块，违反 domain 不应该知道
具体 adapter 实现的分层原则。

所以 `domain/connections.record_check_result(db, connection_id, *, ok, message)` 只做
"存储一个已经算出来的结果"——`ok`/`message`/`last_checked_at` 三个字段。真正跑
`check()` 的职责留给 `features/connection_setup`：它读出 connection 的 `kind` 和
`config`，调用对应 adapter 的 `check()`，拿到 `ProbeResult` 后再调这里存下来。

## API

```python
create_connection(db, *, account_id, kind, name, config=None, enabled=True,
                  is_default=False, secret_plain=None, secret_key=None) -> ConnectionDTO
get_connection(db, connection_id) -> ConnectionDTO                      # 不存在抛 ConnectionNotFound
list_connections(db, *, account_id, kind=_UNSET, enabled_only=False) -> list[ConnectionDTO]
update_connection(db, connection_id, *, name=_UNSET, config=_UNSET, enabled=_UNSET) -> ConnectionDTO
set_default_connection(db, connection_id) -> ConnectionDTO               # 自动降级同 account+kind 的旧默认
delete_connection(db, connection_id) -> None
rotate_secret(db, connection_id, *, secret_plain, secret_key) -> ConnectionDTO
clear_secret(db, connection_id) -> ConnectionDTO
get_decrypted_secret(db, connection_id, *, secret_key) -> str | None
record_check_result(db, connection_id, *, ok, message) -> ConnectionDTO
```

`ConnectionDTO` 只暴露 `has_secret: bool`，从不包含密文或明文密钥——避免调用方不小心把密钥
序列化进日志或 API 响应。

`update_connection` 刻意不接受 `is_default` 参数——降级/升级默认状态必须经过
`set_default_connection`，因为那是唯一会触发"降级旧默认"副作用的路径；如果
`update_connection` 也能改 `is_default`，就会有两条路径能把 `is_default` 设为
`True` 却只有一条会做降级，产生"同时两个默认"的数据不一致。

## 依赖方向

`domain/connections` 依赖 `infra.db`（`Session` 类型）+ `caps.secrets`（加解密）。
不依赖任何其他 `domain/*`，不依赖任何 `adapters/*`（测试连接的 adapter 调用由
`features/connection_setup` 负责，不在这里）。

## 本次实现中自己发现并修正的 bug

写 `record_check_result` 时最初有一行错误的占位代码：

```python
row.last_checked_at = row.updated_at  # 占位，下面立即被 onupdate 刷新成当前时间
```

这行代码的注释本身的推理是错的：SQLAlchemy 的 `onupdate` 只会刷新它所装饰的那一列
（`updated_at` 自己），不会连带刷新 `last_checked_at`；而且赋值取的是更新前的旧
`updated_at` 值，语义上也是反的（本意是"记录这次检查发生的时间"，结果记成了"上次
修改这行的时间"）。在写完整个 `service.py` 后代码审查时发现并改为
`row.last_checked_at = _utcnow()`，在任何测试运行之前就已修正——标准 33 个测试里的
`test_record_check_result_ok` 验证了 `last_checked_at is not None`，确认修正后行为正确。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 按 kind 分表 | 四类字段形状高度重合，`config_json` 足够承载差异，分表是过度设计 |
| 数据库层条件唯一索引保证 is_default 唯一 | 后端可移植性；服务层自动降级已有先例且已证明安全 |
| 这里实现任何 `check()` 具体测试逻辑 | 属于各 `adapters/*` 的知识，下沉到这里会让模块认识它不该认识的 adapter |
| 密钥生成/轮换策略/密钥存储 | `infra/config` 和调用方的职责，本模块保持无状态 |
| 对 `secret_plain` 做格式校验（如判断是不是真的像一个 API key） | 密钥格式因 kind 而异，且这类校验价值有限，交给调用方 |
