# caps/redact

脱敏。无表、纯函数，单测不建库、不联网。

## 为什么是 cap

三个地方要用：**结构化日志**（`infra/logging`）、**发给 LLM 的 prompt**
（`features/ai_enrichment`）、**连接测试的 probe 结果**（`features/connection_setup`、
`features/diagnostics`）。这三处各写一套的话，迟早有一处漏。

## 两种手段，可靠性差一个数量级

- **已知值替换**（`known_values`）：可靠性**高**。知道那串密钥长什么样就能精确抹掉。
  有 `domain/connections` 在手的场合用它（prompt、probe）。
- **模式匹配**（内置正则）：可靠性**低**。只能抓已知形状，新的密钥格式一定抓不到。
  只当兜底，尤其是日志里来路不明的字符串。

**能用已知值就用已知值。** 把模式匹配当成主要手段是这里最容易骗自己的地方。

## 对外承诺

```python
from caps.redact import redact_text, redact_url, redact_mapping, with_known_values, RedactionPolicy

redact_text("password=hunter2")            # "password=<redacted>"  ← 保留键名，便于排查
redact_text("crinsane@outlook.com")        # "c***@outlook.com"     ← 部分遮，不是整条抹掉

redact_url("https://x.com/a?token=abc")    # "https://x.com/a?<redacted>"
redact_url(url, keep="host")               # "https://www.nature.com"  ← 给扩展回报用

redact_mapping(log_record)                 # 递归；敏感键名整值替换，其余字符串过 redact_text

policy = with_known_values(connection_secrets)   # 最可靠的一道
redact_text(prompt, policy=policy)
```

### 刻意不做的一条规则：不脱敏"长十六进制串"

看起来很合理，但 `caps/blobstore` 的 sha256 摘要正是 64 位十六进制，而它是排查附件问题时
**最关键的线索**。把它抹掉等于让日志失去用处。request_id、UUID 同理。

所以只脱敏**带可辨识前缀或结构**的东西：`sk-` / `sk-ant-` / `ghp_` / `github_pat_` /
Telegram 的 `<id>:<35位>` / JWT 三段式 / Fernet 的 `gAAAAA` / `$argon2…` /
`Bearer|Basic|Token <值>` / `key=value` 形式的敏感键。

"看着像随机串就抹"是拿可观测性换一个抓不全的安全感。
单测 `TestLongHexIsNotRedacted` 钉住这条。

### ⚠️ 键名匹配必须精确，因为 `author` 含有 `auth`

本项目满眼都是 `author` / `authors` / `first_author` / `corresponding_author`。
如果按子串匹配 `"auth"`，**每一个作者名都会被抹掉**——这是本模块最容易犯的错。

所以：敏感键名走**精确匹配**（归一后小写去非字母数字，`X-Api-Key` → `xapikey`），
只有 `password` / `apikey` / `secret` / `credential` 四个**不会误伤**的子串参与子串匹配。
`authorization` 在精确名单里，`author` 既不在名单里也不含那四个子串。

单测 `test_author_fields_survive` + 16 个 `test_not_sensitive` 用例钉住这条。

### 邮箱是部分遮，不是整条抹

`crinsane@outlook.com` → `c***@outlook.com`。邮箱是账号标识，整条抹掉会让登录类问题
没法排查。不想要可以 `RedactionPolicy(mask_emails=False)`。

### `redact_url` 的两档

| `keep` | 留下 | 用在哪 |
|---|---|---|
| `"path"`（默认） | scheme + host + path，query 换成 `?<redacted>` 留个痕迹 | 日志 |
| `"host"` | scheme + host | 扩展回报"最终落在哪个域名"——**路径本身也可能带令牌**（`/reset/<token>`） |

两档都一定丢掉 userinfo（`user:pass@`），这是 EZproxy URL 的常见形态。

输入不是 URL 时**不抛异常**，退回 `redact_text`——脱敏在日志路径上，不能因为输入怪
就把调用方打断。

### `redact_mapping` 的性质

- 敏感键名 → 整个值换成 placeholder（不看值长什么样）
- 键名在 `url_key_names` 里（`url` / `pdf_url` / `referer` / `redirect_uri` …）→
  值按 **URL** 脱敏，而且**往下传**：嵌套在列表或字典里的字符串一样按 URL 处理。
  为什么必须单列：query 里的会话 id、一次性 ticket **不匹配任何模式**，
  走 `redact_text` 会原样留下。为什么必须往下传：`adapters/resolvers` 产出的
  候选链接是一个**列表**
- 非敏感键的字符串值 → 仍然过一遍 `redact_text`
- 嵌套 dict / list / tuple 递归；tuple 仍返回 tuple
- `bytes` 不当序列展开
- **不原地改调用方的数据**（有单测盯着）
- 超过 `max_depth`（默认 6）→ `"<too-deep>"`；遇到环 → `"<cycle>"`；
  同一对象被两个兄弟键引用**不算环**

## 依赖方向

只用标准库（`re` `urllib.parse` `dataclasses`）。
**不依赖任何 infra / domain / adapters / features。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 把密钥遮成 `••••••last4` 给界面回显 | `caps/secrets.mask`。本模块管的是"从一堆文本里找出并抹掉秘密"，是另一件事 |
| 决定哪些连接的秘密值要喂进 `known_values` | `features/connection_setup`（它能解密）。本模块只接受传进来的值 |
| 写日志、读日志、清日志 | `infra/logging` + `features/logs_viewer` |
| 判定"这个域名算不算登录页" | `features/fulltext` 的启发式 |
| 真正的 PII 合规（GDPR 删除权之类） | 不在范围内 |
