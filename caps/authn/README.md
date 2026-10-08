# caps/authn

三件认证原语：密码哈希、带时效的签名、随机令牌。无表、纯计算，单测不建库。

## 为什么是三件而不是一件

三者都是认证需要的技术能力，总是一起被 `features/accounts_auth` 用到，所以合在一个 cap。
但**算法刻意不同**，因为输入的熵不同：

| 对象 | 熵 | 算法 | 为什么 |
|---|---|---|---|
| 密码 | 低（人想出来的） | argon2id，慢且吃内存 | 必须让离线爆破变贵 |
| 随机令牌 | 256 位 | sha256，快 | 没有爆破面；用 argon2 会让扩展**每次调 API** 都付 64 MiB + 3 轮 |
| 会话 cookie | — | HMAC 签名 | 只要"没被改过 + 没过期"，不需要存 |

把算法"统一"是这里最容易犯的错，会在两头同时出问题：密码用快哈希等于不设防，
令牌用慢哈希等于给每个 API 请求加一次 KDF。

## 对外承诺

```python
from caps.authn import (
    hash_password, check_password, PasswordParams,
    sign, unsign,
    new_token, hash_token, verify_token, lookup_prefix_of,
)

# 密码
stored = hash_password(plain)                  # 盐和参数都写在哈希串里，不需要另存字段
r = check_password(stored, plain)              # PasswordCheck(ok, needs_rehash)；也可直接当 bool 用
if r.ok and r.needs_rehash:                    # 登录成功是唯一能拿到明文的时刻
    save(hash_password(plain))                 # 顺手把旧参数的哈希升级

# 会话 cookie 签名
token = sign(session_id, secret=key)
session_id = unsign(token, secret=key, max_age_seconds=14 * 86400)   # TokenExpired / TokenInvalid

# 随机令牌（API token 与密码重置令牌同一个形状）
t = new_token(prefix="ld")      # NewToken(plaintext, lookup_prefix, hashed)
#   plaintext    只在这一刻存在，给用户看一次
#   lookup_prefix 明文入库，用来 O(1) 定位（而不是扫全表比哈希）
#   hashed        入库的值
verify_token(presented, stored_hash)           # 常数时间比对
lookup_prefix_of(presented)                    # 从用户出示的令牌算出查库前缀
```

异常：`MalformedHash` / `HashingFailed` / `TokenInvalid` / `TokenExpired`（是 `TokenInvalid` 的子类），
共同基类 `AuthnError`。

### 五条值得知道的性质

1. **`MalformedHash` 不等于密码错误。** 库里存的不是 argon2 哈希说明数据坏了
   （迁移写错 / 字段被截断 / 存了明文），刻意**抛异常而不是返回"密码不对"**——
   后者会把一次数据事故伪装成一次登录失败，然后再也没人发现。调用方应当捕获并大声记日志。
2. **`needs_rehash` 是真用得上的。** 调高 argon2 参数后，旧哈希会在用户下次登录成功时
   被标记为该升级——那是唯一还能拿到明文的时刻。
3. **`sign` 的 payload 不保密。** 任何人都能把 token 里的 base64 解回去。
   签名只保证没被改过、没过期。会话 id 本身不是秘密（它在库里有行），所以这样是够的。
4. **token 恒为 URL 安全 ASCII。** 用 `URLSafeTimedSerializer` 而不是裸 `TimestampSigner`，
   因为后者签原始字节，payload 带中文时签出来的 token 不是 ASCII，塞不进 Cookie / HTTP 头。
5. **`purpose` 是用途命名空间。** 会话 cookie 拿去当密码重置令牌会被拒。
6. **`secret` 可以是列表**：最后一个用于签名，全部用于校验。轮换时把新密钥**追加在末尾**，
   老 cookie 自然能用到过期。

### `NewToken.__repr__` 会隐藏明文

明文绝不该进日志或 traceback，所以 repr 是 `NewToken(plaintext=<已隐藏>, lookup_prefix=..., hashed=...)`。
单测 `test_repr_hides_plaintext` 盯着这条。

### `lookup_prefix` 泄露了什么

它是明文的前 12 个字符，以明文入库。等于公开了 256 位里的约 48 位，
剩下 200 多位远超任何爆破门槛。换来的是按前缀建索引、O(1) 定位，
而不是把每个请求的令牌拿去和全表的哈希逐个比。

## 依赖方向

标准库（`hmac` `secrets` `hashlib`）+ `argon2-cffi` + `itsdangerous`。
**不依赖任何 infra / domain / adapters / features。**
密钥由调用方作为参数传进来，本模块**不读 config、不持有全局状态**。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 密码长度 / 复杂度要求 | `features/accounts_auth`（plan 定的是用户名 ≥3、密码 ≥12）。`hash_password("")` 在这一层是合法调用 |
| 会话时长、令牌有效期 | 调用方作为参数传入；默认值是 `domain/settings` 的事 |
| 会话入库 / 吊销 / `last_used_at` | `domain/accounts`（有表） |
| 登录失败次数限制、锁定、验证码 | `features/accounts_auth` + `domain/usage` |
| 密钥从哪来、怎么轮换 | `infra/config`（`.env`）。本模块只接受传进来的密钥 |
| TOTP / 二步验证 | v1 不做 |
| 日志脱敏 | `caps/redact` |
