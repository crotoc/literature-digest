# adapters/delivery

把一份报告推送给用户的可插拔适配器：`telegram.py`（Telegram Bot API）、
`mailer.py`（SMTP）。v1/E4 只做这两种目的地。

## 两边对外只提供两个原语

`send_message(...)`（发一条/一封）+ `check(config)`（满足
`caps.probe.CheckFn` 协议的连接测试）。`telegram.py` 额外导出
`edit_message(...)`（原地编辑已发的消息，`mailer.py` 没有这个概念——
邮件发出去之后没有"编辑已发邮件"这种操作，这不是遗漏，是协议本身没有
这个能力）。

一条报告该怎么渲染成文本、要不要分段、分段策略、哪些目的地该跳过重发——
全部是 `features/delivery` 的编排知识，这两个适配器只负责"把一段给定的
文本发到一个给定的目的地"，不下沉任何渲染/分段逻辑到这一层。

## 两边的协议完全不同，测试注入点也不同

`telegram.py` 走 HTTP，所以和 `adapters/sources`/`adapters/llm` 一样走
`caps/httpfetch.fetch()`，测试钩子是 `transport`/`resolve`。`mailer.py`
走 SMTP（不是 HTTP），没法复用 `caps/httpfetch`，直接用标准库
`smtplib`；测试钩子换成 `smtp_class`（默认按 `use_tls` 在
`smtplib.SMTP`/`smtplib.SMTP_SSL` 之间选，测试传一个不发真实网络流量的
替身类进来）。这是两个适配器签名不对称的唯一原因——协议本身不对称，
硬凑成一套接口反而会扭曲其中一边。

## Telegram 的 `bot_token` 放在 URL 路径里

Telegram Bot API 的官方约定本身就是
`https://api.telegram.org/bot<token>/<method>`，`bot_token` 因此出现在
URL 路径而不是 query 参数或 header 里——和 `adapters/sources/pubmed.py`
把 `api_key` 放进 query 参数是同一类"遵照上游 API 的约定，不是本模块
自创的做法"。`caps/httpfetch` 本身不写任何日志，所以这不会把 token 意外
留在日志里。

## `check()` 都是发一次真实的测试消息/邮件，不是"仅验证格式"

和 `adapters/sources`/`adapters/llm` 的 `check()` 一样——只有真的打一次
目标端点，才能确认凭据/地址真的有效，不是"格式看起来对"。两边都需要
目的地本身的地址（Telegram 的 `chat_id`、邮件的 `to_address`）才能测，
缺任一必需字段直接返回失败结果，不抛异常。

## 业务失败走返回值的字段，不是 HTTP/SMTP 协议层异常

Telegram 用 200 状态码 + 响应体 `ok` 字段表达业务失败（比如"消息和原文
完全一样所以编辑被拒绝""bot 被用户拉黑"）——`_call()` 统一检查这个字段，
`ok=false` 时抛 `TelegramError`，调用方不需要认识 Telegram 响应体的
具体形状。SMTP 的认证/发送失败走 `smtplib` 自己的异常类型，`mailer.py`
统一包成 `MailerError`，调用方同样不需要认识 `smtplib` 的异常层级。
两边异常类型各自独立、不共享基类——和 `adapters/sources` 的
`CrossrefError`/`PubMedError` 同一个理由。

## 依赖方向

`telegram.py` 依赖 `caps/httpfetch` + `caps/probe`。`mailer.py` 依赖
标准库 `smtplib`/`email` + `caps/probe`。两者都不依赖任何 `domain/*`、
`features/*`，符合规则 2。`bot_token`/`chat_id`/`host`/`port`/
`username`/`password`/`from_address`/`to_address` 全部由调用方显式
传入——来自 `domain/connections` 里 `telegram_destination`/
`mailer_destination` 这两种 kind 的连接配置，本模块不知道、也不关心
它们从哪来。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| Telegram 富媒体消息（图片/文件/按钮） | v1 的调用方（报告推送）只需要纯文本 + HTML 标签格式化，没有调用方需要附件 |
| 邮件 HTML 正文/附件 | 同上，`EmailMessage.set_content()` 只发纯文本 |
| 邮件群发 CC/BCC | v1 每条 delivery 对应一个目的地一个收件人，群发是编排知识不是适配器职责 |
| Telegram 消息编辑/发送失败时自动重试 | 编排知识，留给 `features/delivery` 决定要不要重试、重试几次 |
| SMTP 连接池/复用 | 每次 `send_message()` 独立建连接——v1 投递频率（定时报告）不需要为性能做这个优化 |
| 其余投递渠道（Slack/企业微信/短信等） | v1/E4 没有调用方需要；真要加，是第三个独立的 adapter 文件，不改这两个 |
