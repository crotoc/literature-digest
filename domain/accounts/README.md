# domain/accounts

账号、登录会话、API token、密码重置令牌——四张表（`accounts` /
`account_sessions` / `api_tokens` / `password_resets`），业务逻辑直接构建在
`caps/authn` 的三件原语（密码哈希 / 带时效的签名 / 随机令牌）之上。

这是 v1 范围里第一个 `domain/` 模块，建立了后续所有 domain 模块都要遵守的
形状：`contract.py`（唯一对外表面）/ `service.py`（接收 `Session`，自己不建
session、不 commit、不 rollback）/ `models.py`（只有本模块的 `service.py`
能 import）/ `tests/`（单独跑绿）/ `README.md`。

## 对外承诺

```python
from domain.accounts import (
    AccountDTO, SessionDTO, ApiTokenDTO,
    create_account, get_account, verify_password,
    create_session, get_session, revoke_session, list_sessions,
    create_api_token, verify_api_token, revoke_api_token, list_api_tokens,
    request_password_reset, consume_password_reset,
)

# 注册
account = create_account(db, username="alice", email="Alice@Example.com", password="correct horse battery")
# email 统一转小写入库；username 大小写敏感（见下方裁剪表）

# 登录：用户名或邮箱都认，密码错 / 账号不存在报同一个 InvalidCredentials
account = verify_password(db, username_or_email="alice", password="correct horse battery")

# 会话：行本身只负责"活着/已吊销"，cookie 的签名、过期时长不是本模块的知识
session = create_session(db, account.id)          # SessionDTO(id, ...)
session = get_session(db, session.id)             # 顺手刷新 last_seen_at；SessionNotFound / SessionRevoked
revoke_session(db, session.id)                     # 幂等

# API token：明文只在创建那一刻拿得到
token, plaintext = create_api_token(db, account.id, name="我的笔记本")
verified = verify_api_token(db, plaintext)         # 顺手刷新 last_used_at；TokenNotFound / TokenRevoked
revoke_api_token(db, token.id, account_id=account.id)  # 必须核对归属

# 密码重置
result = request_password_reset(db, email="alice@example.com")  # 邮箱不存在返回 None，不抛异常
if result:
    _account, plaintext = result   # 调用方自己发邮件，本模块不知道怎么发
consume_password_reset(db, plaintext=plaintext, new_password="new password")
# PasswordResetInvalid / PasswordResetUsed / PasswordResetExpired 分开报
```

异常全部派生自 `infra.errors.AppError`（见下方"异常设计"一节），不是本模块
自己的新基类——这是和 caps 层的一个刻意差异：caps 的异常互相独立，因为 caps
不被 app 的统一错误中间件处理；domain 的异常必须能被 `AppError` 的 4xx 渲染
机制认出来。

## 设计要点

### 为什么 service.py 返回 DTO 而不是 ORM 对象

`Account` / `AccountSession` / `ApiToken` / `PasswordReset` 这四个 ORM 类只
活在本模块内部，外部一律拿到 `AccountDTO` / `SessionDTO` / `ApiTokenDTO`
这三个冻结 dataclass。原因：ORM 对象绑定着创建它的那个 `Session`，一旦那
个 Session 被关闭（请求结束）再访问字段会是 `DetachedInstanceError`；而且
ORM 对象允许被意外改字段（`account.password_hash = "123"` 不会报错），DTO
是冻结的，调用方改不了。`PasswordReset` 没有对应的 DTO——它只在本模块内部
流转（发放/消费），从没有必要被外部拿到。

### 登录为什么把"账号不存在"和"密码错误"合并成同一个异常

`verify_password` 不管是用户名/邮箱查不到人，还是密码核对失败，都抛
`InvalidCredentials`，消息完全一样。如果分开报（比如"用户名不存在"
vs "密码错误"），攻击者可以用这个区分去枚举哪些用户名/邮箱已经注册过。
`consume_password_reset` 故意反过来——三种"令牌不对"的情形分开报
（`PasswordResetInvalid` / `PasswordResetUsed` / `PasswordResetExpired`），
因为这里不存在同样的泄露问题：出示令牌本身已经证明"这个人有权操作某个
账号"，分开报不会多泄露任何信息，而对用户更友好。

### 登录成功时顺手升级哈希参数

`check_password` 返回的 `PasswordCheck.needs_rehash` 如果是 `True`，
`verify_password` 会立刻用当前 `password_params` 重新哈希一遍密码并写回——
这是全流程里唯一还拿得到明文的时刻（调高 argon2 代价参数后，不会有专门的
批量迁移作业，而是等用户自然登录时逐个升级）。

### 时间字段为什么统一存"朴素 UTC"而不是带时区的 datetime

这四张表的时间列都是普通 `DateTime`，不是 `DateTime(timezone=True)`。
实测过一次真实的坑：如果用 `datetime.now(UTC)`（带 `tzinfo`）赋值，SQLite
把它写成不带 offset 的字符串，下次把同一行从 DB 读出来时会变成**朴素**
datetime——于是"刚创建时内存里的带时区对象"和"重新查出来的朴素对象"一比较
就是 `TypeError: can't compare offset-naive and offset-aware datetimes`
（`consume_password_reset` 比较 `expires_at` 时第一次踩到）。修法不是让两边
都带时区（那需要 `DateTime(timezone=True)`，MySQL 和 SQLite 的实际存储行为
还不一样），而是统一存朴素 UTC：`models.py` 和 `service.py` 各自有一个
`_utcnow()`，返回 `datetime.now(UTC).replace(tzinfo=None)`——语义仍然是
UTC，只是不在对象上挂 `tzinfo`，这样怎么重新查都不会变形。这也是选择朴素
UTC 而不是直接写 `datetime.utcnow()` 的原因：后者在 3.12 上已标记为废弃，
但语义上我们要的恰恰是它过去的行为（朴素 UTC），`_utcnow()` 是这个行为的
非废弃等价写法。

### API token 和密码重置令牌的吊销归属校验

`revoke_api_token` 强制要求传 `account_id`，内部核对 `row.account_id ==
account_id`；不匹配时报 `TokenNotFound` 而不是更"诚实"的
`PermissionDenied`——因为后者等于告诉调用方"这个 id 其实存在，只是不是你
的"，这本身就是一次信息泄露。`request_password_reset` 同样不泄露"这个邮箱
到底注册过没有"：查不到人直接返回 `None`，不抛异常，调用方（未来的
`features/accounts_auth`）应该对这两种情况展示同一句提示文案。

### 用户名大小写敏感，邮箱不敏感——这个不对称是故意的

创建账号时 `username` 原样存、`email` 统一 `.strip().lower()`。邮箱的大小
写法天然被认为是同一个地址（`Alice@Example.com` 和 `alice@example.com`
投递到同一个邮箱），统一小写能防止靠大小写开出"两个账号、一份收件箱"。
用户名没有这种外部约定，v1 选择最简单的实现（大小写敏感、直接查）而不是
额外维护一个"规范化用户名"列——代价是 `"Alice"` 和 `"alice"` 被当成两个
不同账号，写进了下面的裁剪表。

## 依赖方向

`caps/authn`（密码哈希 / 签名 / 令牌）+ `infra/db`（`Base`、
`new_memory_session`）+ `infra/errors`（`AppError` 及其子类）。
**不依赖任何其它 domain / adapters / features。**

## 单测怎么跑、为什么不碰真实配置的 DB

`domain/accounts/tests/conftest.py` 的 `db` fixture 调 `infra.db.new_memory_session()`——
每次调用一个全新的、与 `infra.db.ENGINE`（指向 `.env` 的 `DATABASE_URL`）
完全独立的内存 SQLite。这是 lint 规则 6（"除 `infra/db.py` 外禁止
`create_engine(`/`sessionmaker(`"）唯一的例外口子，开在 `infra/db.py` 里
而不是各模块自己的 `conftest.py`，是为了让这条例外仍然只有一处、而不是每
加一个 domain 模块就在 lint 豁免名单上加一行。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 用户名大小写无关唯一性（`"Alice"` 和 `"alice"` 是两个账号） | v1 不做，见上「大小写敏感」一节；真要做需要额外一个规范化列 + 唯一索引 |
| 密码长度 / 复杂度要求 | `features/accounts_auth`（plan 定的是用户名 ≥3、密码 ≥12）；`create_account(password="")` 在这一层是合法调用，和 `caps/authn.hash_password` 的裁剪范围保持一致 |
| 注册并发撞车时的优雅处理 | 创建前用 SELECT 查重，但两个并发请求各自拿到独立的 Session，真正的 `IntegrityError` 发生在各自 commit 时（在 `app/shell/deps.py` 里，不在本模块）——那种极小概率的竞态会变成一个 500 而不是优雅的 409。要处理好需要一个跨 Session 边界的重试机制，这超出了 service 层"不管事务边界"的职责，v1 接受这个已知但概率很低的粗糙点 |
| 登录失败次数限制、锁定、验证码 | `features/accounts_auth` + `domain/usage` |
| 会话 cookie 的签名、过期时长、是否自动延期 | 调用方（`features/accounts_auth`）用 `caps.authn.sign/unsign` 决定；本模块只管会话行本身"活着/已吊销" |
| 密码重置邮件怎么发、邮件模板 | `features/accounts_auth`；本模块只发放/消费令牌，返回明文给调用方 |
| 一个账号同时持有的会话数/token 数上限 | v1 不设上限；要做的话是 `domain/usage` 的配额知识，不是这里的 |
| `last_used_at` 判断 token 是否"休眠太久该自动失效" | v1 不做；只记录，不基于它做自动吊销 |
| 账号删除 / 注销 | v1 不做；且一旦做，级联（会话/token/密码重置行要不要一起删）得单独设计 |
