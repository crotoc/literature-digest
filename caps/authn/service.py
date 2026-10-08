"""三件认证原语：密码哈希、带时效的签名、随机令牌。

三者都是"认证需要的技术能力"，但**用的算法刻意不同**，原因是输入的熵不同：

| 对象 | 熵 | 算法 | 为什么 |
|---|---|---|---|
| 密码 | 低（人想出来的） | argon2id，慢且吃内存 | 必须让离线爆破变贵 |
| 随机令牌 | 256 位 | sha256，快 | 没有爆破面；用 argon2 会让扩展每次调 API 都付 64 MiB + 3 轮 |
| 会话 cookie | — | HMAC 签名 | 只要"没被改过 + 没过期"，不需要存 |

把这三件放进一个 cap，是因为它们都无表、都是纯计算，而且总是一起被
`features/accounts_auth` 用到。但**算法选择不能统一**——那会在两头都出错。

本模块不含任何策略：密码长度、令牌有效期、会话时长都由调用方作为参数传进来。
"""

import hmac
import secrets as stdlib_secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from hashlib import sha256

from argon2 import PasswordHasher
from argon2.exceptions import HashingError, InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

# ── 异常 ───────────────────────────────────────────────────────────────────

class AuthnError(Exception):
    """本模块所有异常的基类。"""


class MalformedHash(AuthnError):
    """存的不是一个 argon2 哈希。

    这**不等于密码错误**，而是数据坏了（迁移写错、字段被截断、存了明文……）。
    刻意不吞成"密码不对"——那会把一次数据事故伪装成一次登录失败，
    然后再也没人发现。调用方应当捕获它并大声记日志。
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(f"不是合法的 argon2 哈希{'：' + detail if detail else ''}")
        self.detail = detail


class HashingFailed(AuthnError):
    """argon2 自身失败（通常是内存参数要不到）。"""


class TokenInvalid(AuthnError):
    """签名不符、被改过，或者用途（purpose）对不上。"""


class TokenExpired(TokenInvalid):
    """签名本身是对的，但超过了 max_age。"""


# ── 密码 ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PasswordParams:
    """argon2id 代价参数。

    默认值取 argon2-cffi 的推荐值（RFC 9106 的低内存档）。
    调高是为了跟上硬件；调低只应该在测试里做。
    """

    time_cost: int = 3
    memory_cost: int = 65536
    """单位 KiB。65536 = 64 MiB。"""
    parallelism: int = 4


DEFAULT_PASSWORD_PARAMS = PasswordParams()


@dataclass(frozen=True)
class PasswordCheck:
    """一次密码校验的结果。

    刻意不是一个 bool：`needs_rehash` 让调用方能在用户登录成功的那一刻
    （也是唯一能拿到明文的时刻）顺手把哈希升级到新参数。
    """

    ok: bool
    needs_rehash: bool = False

    def __bool__(self) -> bool:
        return self.ok


def _hasher(params: PasswordParams) -> PasswordHasher:
    return PasswordHasher(
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
    )


def hash_password(plain: str, *, params: PasswordParams = DEFAULT_PASSWORD_PARAMS) -> str:
    """把明文密码哈希成可入库的字符串（自带盐和参数，不需要另存字段）。

    Raises:
        HashingFailed
    """
    try:
        return _hasher(params).hash(plain)
    except HashingError as error:
        raise HashingFailed(str(error)) from error


def check_password(
    stored_hash: str,
    plain: str,
    *,
    params: PasswordParams = DEFAULT_PASSWORD_PARAMS,
) -> PasswordCheck:
    """校验密码。

    Args:
        stored_hash: 库里存的那串。
        plain: 用户这次输入的明文。
        params: 当前的目标参数——用来判断旧哈希是否该升级。

    Returns:
        PasswordCheck。密码不对就是 `ok=False`，不抛异常。

    Raises:
        MalformedHash: `stored_hash` 根本不是 argon2 哈希（数据问题，不是密码问题）。
    """
    hasher = _hasher(params)
    try:
        hasher.verify(stored_hash, plain)
    except VerifyMismatchError:
        return PasswordCheck(ok=False)
    except InvalidHashError as error:
        raise MalformedHash(str(error)) from error
    except VerificationError as error:
        # argon2 认得这个哈希的格式，但校验没通过且不是"不匹配"——
        # 当成数据问题而不是密码问题，同样别悄悄吞掉
        raise MalformedHash(str(error)) from error
    except (UnicodeEncodeError, TypeError, AttributeError) as error:
        # argon2-cffi 在把哈希串编成 ascii 时就会炸（库里存了中文、
        # 存了 None、编码被弄坏……）。这类异常必须在这里收成 MalformedHash，
        # 否则一行坏数据会变成一个 500 而不是一条可读的错误。
        raise MalformedHash(f"{type(error).__name__}: {error}") from error
    return PasswordCheck(ok=True, needs_rehash=hasher.check_needs_rehash(stored_hash))


# ── 带时效的签名 ───────────────────────────────────────────────────────────

SESSION_PURPOSE = "session"


def _serializer(secret: str | Sequence[str], purpose: str) -> URLSafeTimedSerializer:
    if isinstance(secret, str):
        keys: list[str] = [secret]
    else:
        keys = list(secret)
    if not keys or any(not k for k in keys):
        raise ValueError("secret 不能为空")
    # itsdangerous 的语义：列表里**最后一个**用于签名，全部用于校验。
    # 所以轮换密钥时把新的追加在末尾，老 cookie 自然还能用到过期。
    #
    # 用 URLSafeTimedSerializer 而不是裸的 TimestampSigner：后者签的是原始字节，
    # payload 带非 ASCII 字符（中文会话名之类）时签出来的 token 不是 ASCII，
    # 塞不进 Cookie / HTTP 头。这个序列化器会先把 payload 编成 URL 安全的 base64，
    # 所以 token 恒为 ASCII。代价是 payload 不再肉眼可读，但它从来也不保密——
    # 任何人都能 base64 解回去，见 docstring。
    return URLSafeTimedSerializer(keys, salt=purpose)


def sign(payload: str, *, secret: str | Sequence[str], purpose: str = SESSION_PURPOSE) -> str:
    """给一段文本加上 HMAC 签名和时间戳。

    Args:
        payload: 要带的内容（通常是会话 id）。**它不保密**——
            任何人都能把 token 里的 base64 解回去。签名只保证没被改过。
        secret: 单个密钥，或密钥列表（最后一个签名，全部可校验，用于轮换）。
        purpose: 用途命名空间。用途不同的令牌互相不能冒用——
            一个会话 cookie 拿去当密码重置令牌会被拒。
    """
    return _serializer(secret, purpose).dumps(payload)


def unsign(
    token: str,
    *,
    secret: str | Sequence[str],
    max_age_seconds: int,
    purpose: str = SESSION_PURPOSE,
) -> str:
    """校验签名与时效，取回 payload。

    Raises:
        TokenExpired: 签名对但超时。
        TokenInvalid: 签名不符 / 被改过 / purpose 对不上 / 格式不对。
    """
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds 必须为正")
    try:
        payload = _serializer(secret, purpose).loads(token, max_age=max_age_seconds)
    except SignatureExpired as error:
        raise TokenExpired(str(error)) from error
    except (BadData, UnicodeDecodeError, TypeError) as error:
        raise TokenInvalid(str(error)) from error
    if not isinstance(payload, str):
        # 只有拿着密钥的人才签得出非字符串 payload，但别让它悄悄往上走
        raise TokenInvalid(f"payload 不是字符串：{type(payload).__name__}")
    return payload


# ── 随机令牌 ───────────────────────────────────────────────────────────────

TOKEN_BYTES = 32
LOOKUP_PREFIX_CHARS = 12


@dataclass(frozen=True)
class NewToken:
    """一个刚生成的令牌。明文只在这一刻存在过。"""

    plaintext: str = field(repr=False)
    """完整令牌。**只在这一次能拿到**，入库的是 `hashed`，给用户看一次就没了。"""

    lookup_prefix: str
    """明文存库用于 O(1) 定位的前缀。也用来在界面上显示"ld_a1b2…"。"""

    hashed: str
    """入库的值。"""

    def __repr__(self) -> str:
        # 明文绝不该进日志或 traceback
        return f"NewToken(plaintext=<已隐藏>, lookup_prefix={self.lookup_prefix!r}, hashed={self.hashed!r})"


def new_token(*, prefix: str = "", nbytes: int = TOKEN_BYTES) -> NewToken:
    """生成一个高熵随机令牌。

    API token（`domain/accounts.api_tokens`）和密码重置令牌（`password_resets`）
    用的是同一个形状：随机生成、明文只给一次、入库存哈希、按前缀定位。

    Args:
        prefix: 可读前缀，如 `"ld"` → `"ld_xxxx…"`。方便用户和日志区分令牌种类。
        nbytes: 随机字节数。32 字节 = 256 位。

    Returns:
        NewToken。`lookup_prefix` 是明文的前 12 个字符——它会以明文入库，
        等于公开了 256 位里的约 48 位，剩下的仍然远超爆破门槛。
    """
    if nbytes < 16:
        raise ValueError("nbytes 至少 16（128 位）")
    if prefix and not prefix.isalnum():
        raise ValueError("prefix 只能是字母数字")

    body = stdlib_secrets.token_urlsafe(nbytes)
    plaintext = f"{prefix}_{body}" if prefix else body
    return NewToken(
        plaintext=plaintext,
        lookup_prefix=plaintext[:LOOKUP_PREFIX_CHARS],
        hashed=hash_token(plaintext),
    )


def hash_token(plaintext: str) -> str:
    """令牌 → 入库哈希。

    用 sha256 而不是 argon2 是**刻意的**：令牌有 256 位熵，没有字典爆破面，
    慢哈希在这里只会让扩展每次调 API 都付 64 MiB 内存和 3 轮计算。
    低熵的密码才需要慢哈希——见 `hash_password`。
    """
    return sha256(plaintext.encode("utf-8")).hexdigest()


def verify_token(plaintext: str, hashed: str) -> bool:
    """常数时间比对令牌。"""
    return hmac.compare_digest(hash_token(plaintext), hashed)


def lookup_prefix_of(plaintext: str) -> str:
    """从用户出示的令牌算出用于查库的前缀。"""
    return plaintext[:LOOKUP_PREFIX_CHARS]
