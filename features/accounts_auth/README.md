# features/accounts_auth

注册 / 登录 / 会话恢复 / 密码重置的编排层。组合 `caps/authn`（密码哈希、
会话签名）+ `domain/{accounts,libraries}`（账号/会话/库的持久化）。本模块
不开表——四件套里没有 `models.py`，这是 `features/` 层和 `domain/` 层的
固定区别（计划文档："`models.py`（仅 domain 层有...）"）。

## 为什么策略常量（用户名/密码长度、session 时长、重置 TTL）放在这一层

`caps/authn/service.py` 的模块 docstring 写得很明白：「本模块不含任何策略：
密码长度、令牌有效期、会话时长都由调用方作为参数传进来」。`domain/accounts`
同理——它的 `create_account` 不检查用户名/密码格式,`request_password_reset`
的 `ttl_seconds` 也是参数而不是常量。这条"策略不下沉"的链条最终要有个人接住，
接住它的就是这一层：`MIN_USERNAME_LENGTH` `MIN_PASSWORD_LENGTH` 这两个常量
定义在 `service.py`，`ttl_seconds` 默认值直接复用 `domain.accounts` 的
`DEFAULT_PASSWORD_RESET_TTL_SECONDS`(没有必要在这一层另造一个同名常量)。

`allow_self_signup` 和 `session_secret`/`smtp_configured` 依然是**参数**，
不是本模块读的全局配置——`register()`/`login()`/`request_password_reset()`
全部不读 `infra.config.settings()`。原因和全项目所有 `domain/`、`adapters/`
模块一致：读全局状态会让单测绑死在 `.env` 能不能解析上，也会让"这个函数在
什么条件下做什么"从函数签名里消失。调用方（`app/pages/auth`）负责从
`infra.config.settings()` 读出 `allow_self_signup` / `app_secret_key`，
再传进来——这正是 `app/` 装配层存在的意义（lint 规则 5：`app/pages/**`
不能直接碰 ORM，但调 `features/*` 的 contract 传参数完全合法）。

## `register()`：两次 domain 调用为什么是"原子"的，不需要本模块自己管事务

`register()` 依次调用 `domain.accounts.create_account` 和
`domain.libraries.create_library`，中间不 `commit`——事务边界在
`app/shell/deps.py`（请求路径）或 `infra.db.session_scope`（后台任务），这是
全项目约定（`domain/accounts/service.py` 模块 docstring 的原话）。如果第二步
失败，第一步插入的 `Account` 行也还没真正落盘（只 `flush()` 过,属于同一个
还没 commit 的事务），调用方的异常处理会让整个事务回滚，不会出现"账号建好了
但没有库"的中间态。`domain.libraries.create_library` 本身也在**一次调用内**
把库和它的第一个 owner 成员一起建好（见该模块 docstring），本模块不需要再
额外调 `add_member`。

## `resume_session()`：为什么不是单纯的 `unsign()` 包装

`caps.authn.unsign()` 只负责"签名没被改过 + 没过期"，拿到的 payload 是个
字符串形式的 session id；本模块还要再查一次 `domain.accounts.get_session()`
确认**这个会话行还活着**——两者缺一不可：

- 只校验签名不查库：用户登出后，没被吊销的旧 cookie 在 `max_age_seconds`
  窗口内还能被拿来恢复会话（jwt 风格系统常踩的坑）。
- 只查库不校验签名：等于允许任何人伪造一个 session id 直接查库。

`int(session_id_str)` 那一层 `try/except` 是防御性的——正常情况下走不到,
因为签名已经保证 payload 没被篡改；写在这里是为了让"万一真的出现一个格式不对
的 payload"时报出 `TokenInvalid` 而不是裸的 `ValueError`。

## 密码重置：`smtp_configured` 为什么是参数,不是本模块查的配置

计划文档原话：「含密码重置（SMTP 未配时入口显示不可用,不保存邮件凭据）」。
v1 范围内 `adapters/delivery/mailer` 还没有（计划里是 E4 阶段才做）,
`infra/config.py` 现在也确实没有任何 SMTP 字段——所以 `smtp_configured` 在
v1 的真实调用点上几乎总会是 `False`,`request_password_reset()` 几乎总会
抛 `PasswordResetUnavailable`。

把它做成参数而不是在本模块里硬编码"直接拒绝",是为了 E4 接上真的 mailer
之后不用改这个函数的签名或调用约定——`app/pages/auth` 到时候从
`infra.config.settings()` 读出"SMTP 配好了没",原样传进来就行。这个函数
"发令牌"的逻辑(反枚举:邮箱不存在返回 `None`,原样继承自
`domain.accounts.request_password_reset`)已经是完整、可用的——缺的只是
"谁把这个明文令牌真的发出去",那是调用方(以后是 mailer adapter)的职责,
本模块从一开始就不碰,也不保存任何邮件凭据。

`consume_password_reset()` 多做一步 `MIN_PASSWORD_LENGTH` 校验——这是刻意
加的,`domain.accounts.consume_password_reset` 本身不管密码强度(它不知道
这条策略)。不加这一步的话,"通过重置链接设置一个比注册时还弱的密码"会成为
绕开注册密码强度要求的后门。

## 依赖方向

依赖 `caps/authn` + `domain/{accounts,libraries}`。不依赖任何其它
`features/*`(符合规则 4),不直接碰 ORM/`models.py`(符合规则 3 的精神——
虽然规则 3 字面上管的是 domain 之间,但本模块同样只通过 `domain.accounts`/
`domain.libraries` 的 contract 拿东西)。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 真的发密码重置邮件 | `adapters/delivery/mailer` 是 E4 阶段的东西;本模块只负责发令牌、校验令牌 |
| 用户名大小写无关唯一性 | `domain.accounts` 本身就没做(见该模块 README 的裁剪范围),本模块不额外加 |
| 密码复杂度规则(大小写/数字/符号) | 计划只要求长度 ≥12,没要求字符种类 |
| 登录失败次数限制 / 账号锁定 | 计划未提;真要做属于新的一条策略,不是这个编排层现在的职责 |
| 邮箱格式的严格校验(正则) | 计划原话是"邮箱含 @",故意只做这一条;更严格的校验要靠发确认邮件验证,但那依赖还不存在的 mailer |
| "记住我"/多种会话时长档位 | `login()` 不内置任何 session 时长——cookie 多久过期完全是 `app/pages/auth` 设置 cookie 时的事,`resume_session()` 的 `max_age_seconds` 由调用方每次显式给 |
| 多因素认证 / 社交登录 | 不在 v1 范围 |
| session 列表管理页(查看/吊销其它设备的会话) | `domain.accounts.list_sessions`/`revoke_session` 已经具备,只是没有对应的编排函数——等 `app/pages/auth` 真的需要这个页面时再加,不先猜 |
