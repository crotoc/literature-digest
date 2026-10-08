# caps/secrets

对称加密静态存储的凭据 + 给界面用的掩码显示。无表、纯计算，单测不建库、不读 `.env`。

## 和 caps/authn 的根本区别

`domain/connections` 要存 AI 的 API key、出版商账号、EZproxy 凭据、Telegram bot token。
这些东西**必须能解回明文**（要拿去登录），所以不能像密码那样只存单向哈希。
一句话：**密码存哈希，凭据存密文。** 搞混任何一个方向都是事故。

算法用 Fernet（AES-128-CBC + HMAC-SHA256，密文自带时间戳和完整性校验）。
不自己拼 AES——自己拼最容易漏掉 MAC 或者重用 IV。

## 对外承诺

```python
from caps.secrets import generate_key, derive_key, encrypt, decrypt, rotate, mask, looks_encrypted

key = generate_key()                                   # 44 字符 URL 安全 base64
key = derive_key(passphrase, salt=..., purpose="connections")   # 从 .env 的口令派生

token = encrypt(api_key, key=key)                      # 密文是 ASCII，直接进文本列
api_key = decrypt(token, key=key)                      # 失败抛 DecryptFailed
token = rotate(token, key=[old, new])                  # 重新加密到当前密钥

mask("sk-proj-abcdef1234")                             # Masked(display="••••••1234", last="1234")
looks_encrypted(value)                                 # 启发式：挑出迁移时漏加密的明文行
```

异常：`InvalidKey` / `DecryptFailed`，共同基类 `SecretsError`。

### ⚠️ 密钥列表顺序：本项目和 cryptography 原生相反

本项目内 `caps/authn` 和 `caps/secrets` **语义一致：列表里最后一个是当前密钥**，
轮换时把新密钥**追加在末尾**。

`cryptography` 原生的 `MultiFernet` 用**第一个**加密。本模块内部做了反转。

宁可在一个地方反转一次，也不要让调用方记两套相反的规则——那种错**不会报错**，
只会在轮换那天静默地用错密钥。单测 `test_last_key_encrypts` 钉住这条。

### 四条刻意的性质

1. **`DecryptFailed` 不区分原因。** 密钥不对 / 被篡改 / 超期抛的是同一个类型。
   区分开就等于告诉攻击者"你的密钥对了但时间过期了"，那是可以用来试探的信号。
   要诊断去看日志。单测 `test_failure_reason_not_distinguishable` 盯着。
2. **异常消息里绝不带明文或密钥**（它们会进日志）。单测
   `test_plaintext_never_in_exception_message` 盯着。
3. **`max_age_seconds` 默认不设。** 久期凭据不该因为存得久而失效；只给短寿命的东西用。
4. **相同明文的密文每次不同**（新 IV）。所以**不能靠比密文判断两个凭据是否相同**。

### 掩码的两条性质

1. **短值全遮。** 长度 <= `keep` 时一个字符都不露——否则一个 4 字符的密钥会被完整显示。
2. **圆点数量固定 6 个，不反映真实长度。** 长度本身也是信息（能据此判断是哪家的 key 格式），
   不值得为了"好看"泄露。

`mask(None)` 会抛 `TypeError` 而不是显示成空串——悄悄接受 `None` 会掩盖掉
"调用方取错字段"这种 bug。

### `derive_key` 的 purpose

`purpose` 进 HKDF 的 info，所以同一个 `.env` 口令能给不同用途派生出**互不相通**的密钥：
拿凭据密钥去解会话数据解不开。单测 `test_derive_differs_by_purpose` 盯着。

## 依赖方向

标准库（`base64`）+ `cryptography`。
**不依赖任何 infra / domain / adapters / features。** 密钥由调用方传入，不读 config、不持全局状态。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 密钥从哪来、怎么保管、怎么排期轮换 | `infra/config`（`.env`）+ 运维。本模块只接受传进来的密钥 |
| 把库里旧密文逐行刷一遍 | `domain/connections` + `domain/jobs`（要遍历表，不在这一层）。本模块只提供单条 `rotate` |
| 决定哪些字段该加密 | `domain/connections` 的 schema |
| 密码哈希 | `caps/authn`（**不要用本模块存密码**） |
| 日志脱敏、prompt 脱敏、probe 结果脱敏 | `caps/redact`。本模块的 `mask` 只管"把一个密钥遮起来给界面回显" |
| 外部 KMS / HSM | v1 不做 |
| 非对称加密、签名 | 用不到 |
