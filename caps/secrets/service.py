"""对称加密静态存储的凭据，外加给界面用的掩码显示。

用在哪：`domain/connections` 要存 AI 的 API key、出版商账号、EZproxy 凭据、
Telegram bot token。这些东西**必须能解回明文**（要拿去登录），所以不能像密码那样
只存单向哈希——这是本模块和 `caps/authn` 的根本区别。

算法用 Fernet（AES-128-CBC + HMAC-SHA256，密文自带时间戳和完整性校验）。
不自己拼 AES，因为自己拼最容易漏掉 MAC 或者重用 IV。

## 密钥列表的顺序

本项目内 `caps/authn` 和 `caps/secrets` **语义一致：列表里最后一个是当前密钥**，
轮换时把新密钥追加在末尾。

注意这和 `cryptography` 原生的 `MultiFernet` **相反**（它用第一个加密），
本模块内部做了反转。宁可在一个地方反转一次，也不要让调用方记两套相反的规则——
那种错不会报错，只会在轮换那天静默地用错密钥。
"""

import base64
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASK_CHARACTER = "•"
MASK_WIDTH = 6
DEFAULT_KEEP = 4

_TOKEN_PREFIX = "gAAAAA"
_FERNET_KEY_BYTES = 32


# ── 异常 ───────────────────────────────────────────────────────────────────

class SecretsError(Exception):
    """本模块所有异常的基类。"""


class InvalidKey(SecretsError):
    def __init__(self, detail: str) -> None:
        super().__init__(f"不是合法的 Fernet 密钥：{detail}")
        self.detail = detail


class DecryptFailed(SecretsError):
    """解不开。可能是密钥不对、密文被改过，或者超过了 max_age。

    刻意**不区分**这几种原因：区分开就等于告诉攻击者"你的密钥对了但时间过期了"，
    那是一个可以用来试探的信号。要诊断去看日志，不要看异常类型。
    """


# ── 密钥 ───────────────────────────────────────────────────────────────────

def generate_key() -> str:
    """生成一个新的 Fernet 密钥（44 字符的 URL 安全 base64）。"""
    return Fernet.generate_key().decode("ascii")


def derive_key(passphrase: str, *, salt: str, purpose: str) -> str:
    """从任意口令派生一个 Fernet 密钥。

    让 `.env` 里放一句人能管理的口令，而不是强制放一个 44 字符的 base64。
    `purpose` 进 HKDF 的 info，所以同一个口令能给不同用途派生出**互不相通**的密钥：
    拿凭据密钥去解会话数据解不开。

    Args:
        passphrase: 口令，至少 16 字符。
        salt: 部署级盐值，和口令一起放 `.env`，换了就解不开旧密文。
        purpose: 用途标签，如 `"connections"`。

    Raises:
        InvalidKey: 口令或盐太短。
    """
    if len(passphrase) < 16:
        raise InvalidKey("口令至少 16 字符")
    if len(salt) < 8:
        raise InvalidKey("salt 至少 8 字符")
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=_FERNET_KEY_BYTES,
        salt=salt.encode("utf-8"),
        info=purpose.encode("utf-8"),
    ).derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _cipher(key: str | Sequence[str]) -> MultiFernet:
    keys = [key] if isinstance(key, str) else list(key)
    if not keys or any(not k for k in keys):
        raise InvalidKey("密钥不能为空")
    try:
        # 反转：本项目约定"最后一个是当前密钥"，而 MultiFernet 用第一个加密
        fernets = [Fernet(k) for k in reversed(keys)]
    except (ValueError, TypeError) as error:
        raise InvalidKey(str(error)) from error
    return MultiFernet(fernets)


# ── 加解密 ─────────────────────────────────────────────────────────────────

def encrypt(plaintext: str, *, key: str | Sequence[str]) -> str:
    """加密。密文是 URL 安全 ASCII，可以直接进数据库的文本列。

    Args:
        key: 单个密钥，或密钥列表（**最后一个用于加密**，全部可用于解密）。
    """
    return _cipher(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(
    token: str,
    *,
    key: str | Sequence[str],
    max_age_seconds: int | None = None,
) -> str:
    """解密。

    Args:
        max_age_seconds: 给定时，密文比这更老就拒绝。存久期凭据时**不要**传它
            （凭据不该因为存得久而失效）；只给短寿命的东西用。

    Raises:
        DecryptFailed: 密钥不对 / 密文被改过 / 超期。不区分原因，见该异常的 docstring。
        InvalidKey: 密钥本身格式不对。
    """
    if max_age_seconds is not None and max_age_seconds <= 0:
        raise ValueError("max_age_seconds 必须为正")
    cipher = _cipher(key)
    try:
        raw = cipher.decrypt(token.encode("utf-8"), ttl=max_age_seconds)
    except (InvalidToken, TypeError, UnicodeEncodeError) as error:
        raise DecryptFailed(f"{type(error).__name__}") from error
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DecryptFailed("明文不是 UTF-8") from error


def rotate(token: str, *, key: str | Sequence[str]) -> str:
    """把密文重新加密到**当前**密钥下，明文不变。

    轮换密钥后拿它把库里的旧密文逐行刷一遍，刷完就能把老密钥从列表里删掉。

    Raises:
        DecryptFailed / InvalidKey
    """
    try:
        return _cipher(key).rotate(token.encode("utf-8")).decode("ascii")
    except (InvalidToken, TypeError, UnicodeEncodeError) as error:
        raise DecryptFailed(f"{type(error).__name__}") from error


def looks_encrypted(value: str) -> bool:
    """像不像本模块产出的密文。

    **启发式**，用途是迁移时挑出漏加密的明文行，不是安全判据。
    依据是 Fernet 密文固定以版本字节 0x80 开头，base64 之后恒为 `gAAAAA`。
    """
    return isinstance(value, str) and value.startswith(_TOKEN_PREFIX)


# ── 掩码显示 ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Masked:
    """给界面看的掩码结果。"""

    display: str
    """如 `"••••••cdef"`。"""

    last: str
    """保留在明处的尾部。"""

    def __str__(self) -> str:
        return self.display


def mask(value: str, *, keep: int = DEFAULT_KEEP) -> Masked:
    """把一个密钥遮成 `••••••last4`，用于设置页回显。

    两条刻意的性质：

    1. **短值全遮。** 长度 <= keep 时一个字符都不露——否则一个 4 字符的密钥
       会被完整显示出来。
    2. **圆点数量固定 6 个，不反映真实长度。** 长度本身也是信息
       （能据此判断是哪家的 key 格式），不值得为了"好看"泄露。

    Args:
        keep: 尾部保留几个字符。必须非负。
    """
    if not isinstance(value, str):
        # 掩码是安全展示函数：悄悄把 None 显示成空串会掩盖掉"调用方取错字段"这种 bug
        raise TypeError(f"value 必须是字符串，收到 {type(value).__name__}")
    if keep < 0:
        raise ValueError("keep 不能为负")
    if not value:
        return Masked(display="", last="")
    last = value[-keep:] if keep and len(value) > keep else ""
    return Masked(display=MASK_CHARACTER * MASK_WIDTH + last, last=last)
