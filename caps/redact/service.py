"""脱敏。三个地方共用：结构化日志、发给 LLM 的 prompt、连接测试的 probe 结果。

下沉成 cap 的理由：这三处要是各写一套，迟早有一处漏。

## 两种脱敏手段，可靠性差一个数量级

- **已知值替换**（`known_values`）：可靠性高。知道那串密钥长什么样，
  就能精确抹掉。有 `domain/connections` 在手的场合用它（prompt、probe）。
- **模式匹配**（内置正则）：可靠性低。只能抓已知形状，新的密钥格式一定抓不到。
  只当兜底，尤其是日志里来路不明的字符串。

所以**能用已知值就用已知值**，模式匹配只是最后一道网。README 里把这点写在最前面。

## 刻意不做的一条规则

**不脱敏"长十六进制串"。** 看起来很合理，但 `caps/blobstore` 的 sha256 摘要正是
64 位十六进制，而它是排查附件问题时最关键的线索。把它抹掉等于让日志失去用处。
只脱敏**带可辨识前缀或结构**的东西（`sk-`、`Bearer`、JWT 三段式、Fernet 的 `gAAAAA`……）。
"看着像随机串就抹"是拿可观测性换一个抓不全的安全感。
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

PLACEHOLDER = "<redacted>"
TOO_DEEP = "<too-deep>"
CYCLE = "<cycle>"
DEFAULT_MAX_DEPTH = 6
MIN_KNOWN_VALUE_LENGTH = 8

# 按键名脱敏：**精确**匹配归一后的键名。
# 为什么要精确：本项目满眼都是 `author` / `authors`，而 `author` 含有 `auth`——
# 用子串匹配 "auth" 会把每一个作者名都抹掉。单测 test_author_fields_survive 钉住这条。
SENSITIVE_KEY_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "password",
        "passwd",
        "newpassword",
        "oldpassword",
        "passwordhash",
        "secret",
        "clientsecret",
        "secretkey",
        "apikey",
        "apitoken",
        "accesstoken",
        "refreshtoken",
        "bottoken",
        "token",
        "tokenhash",
        "credential",
        "credentials",
        "privatekey",
        "sessionid",
        "sessiontoken",
        "csrftoken",
        "signature",
        "fernetkey",
        "encryptionkey",
    }
)

# 这些键名的值当 URL 处理：query 整段丢掉。
# 为什么必须单列：query 里的会话 id / 一次性参数**不匹配任何模式**，
# 走 redact_text 会原样留下（demo 里 `&sid=zz` 就是这么漏出来的）。
URL_KEY_NAMES: frozenset[str] = frozenset(
    {
        "url",
        "uri",
        "href",
        "location",
        "referer",
        "referrer",
        "endpoint",
        "callbackurl",
        "redirecturl",
        "redirecturi",
        "webhookurl",
        "baseurl",
        "proxyurl",
        "pdfurl",
        "sourceurl",
    }
)

# 含这些子串就算敏感。只收**不会误伤**的（对比 "auth" 之于 "author"）。
SENSITIVE_KEY_SUBSTRINGS: tuple[str, ...] = ("password", "apikey", "secret", "credential")

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Authorization 头的值
    ("bearer", re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9+/_.~=-]{8,}", re.IGNORECASE)),
    # OpenAI / Anthropic 风格
    ("sk", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{12,}")),
    # GitHub
    ("github", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    # Telegram bot token：数字 id + 冒号 + 35 位
    ("telegram", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}")),
    # JWT 三段式
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # caps/secrets 产出的 Fernet 密文
    ("fernet", re.compile(r"\bgAAAAA[A-Za-z0-9_-]{20,}={0,2}")),
    # argon2 哈希
    ("argon2", re.compile(r"\$argon2[a-z]*\$[^\s\"']+")),
    # key=value 形式里的敏感键
    (
        "kv",
        re.compile(
            r"\b(password|passwd|secret|token|api[_-]?key|access[_-]?token|auth)"
            r"\s*[=:]\s*(\"[^\"]*\"|'[^']*'|[^\s&,;}\"']+)",
            re.IGNORECASE,
        ),
    ),
)

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


@dataclass(frozen=True)
class RedactionPolicy:
    """脱敏策略。调用方按场合传不同的策略，本模块不内置业务判断。"""

    known_values: tuple[str, ...] = ()
    """已知的具体秘密值（从 `domain/connections` 解出来的那些）。最可靠的一道。"""

    sensitive_key_names: frozenset[str] = SENSITIVE_KEY_NAMES
    sensitive_key_substrings: tuple[str, ...] = SENSITIVE_KEY_SUBSTRINGS
    url_key_names: frozenset[str] = URL_KEY_NAMES
    """这些键的值按 URL 脱敏（query 整段丢掉）而不是按自由文本。"""
    use_builtin_patterns: bool = True
    extra_patterns: tuple[re.Pattern[str], ...] = ()
    mask_emails: bool = True
    """邮箱**部分**遮（`c***@outlook.com`）而不是整条抹掉——邮箱是账号标识，
    整条抹掉会让登录类问题没法排查。"""

    placeholder: str = PLACEHOLDER
    max_depth: int = DEFAULT_MAX_DEPTH
    min_known_value_length: int = MIN_KNOWN_VALUE_LENGTH
    """比这更短的已知值不替换——两三个字符的"密钥"会把正文打成筛子。"""

    _sorted_known: tuple[str, ...] = field(init=False, repr=False, compare=False, default=())

    def __post_init__(self) -> None:
        usable = [
            v for v in self.known_values if isinstance(v, str) and len(v) >= self.min_known_value_length
        ]
        # 长的先替换，避免短值先把长值切断
        object.__setattr__(self, "_sorted_known", tuple(sorted(set(usable), key=len, reverse=True)))


DEFAULT_POLICY = RedactionPolicy()


def with_known_values(values: Iterable[str], *, policy: RedactionPolicy = DEFAULT_POLICY) -> RedactionPolicy:
    """在已有策略上加一批已知秘密值。"""
    merged = tuple(policy.known_values) + tuple(values)
    return RedactionPolicy(
        known_values=merged,
        sensitive_key_names=policy.sensitive_key_names,
        sensitive_key_substrings=policy.sensitive_key_substrings,
        url_key_names=policy.url_key_names,
        use_builtin_patterns=policy.use_builtin_patterns,
        extra_patterns=policy.extra_patterns,
        mask_emails=policy.mask_emails,
        placeholder=policy.placeholder,
        max_depth=policy.max_depth,
        min_known_value_length=policy.min_known_value_length,
    )


# ── 键名 ───────────────────────────────────────────────────────────────────

def normalize_key(key: str) -> str:
    """归一键名：小写 + 去掉非字母数字。`X-Api-Key` / `api_key` → `xapikey` / `apikey`。"""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: str, *, policy: RedactionPolicy = DEFAULT_POLICY) -> bool:
    normalized = normalize_key(key)
    if normalized in policy.sensitive_key_names:
        return True
    return any(token in normalized for token in policy.sensitive_key_substrings)


def is_url_key(key: str, *, policy: RedactionPolicy = DEFAULT_POLICY) -> bool:
    return normalize_key(key) in policy.url_key_names


# ── 文本 ───────────────────────────────────────────────────────────────────

def _mask_email(match: re.Match[str]) -> str:
    return f"{match.group(1)}***{match.group(2)}"


def redact_text(text: str, *, policy: RedactionPolicy = DEFAULT_POLICY) -> str:
    """脱敏一段自由文本。

    顺序是刻意的：**先替已知值**（最可靠），再跑模式（兜底），最后处理邮箱。
    """
    if not isinstance(text, str) or not text:
        return text

    out = text
    for value in policy._sorted_known:
        out = out.replace(value, policy.placeholder)

    if policy.use_builtin_patterns:
        for name, pattern in _VALUE_PATTERNS:
            # key=value 形式保留键名："password=<redacted>" 比整段消失更好排查
            replacement = rf"\1={policy.placeholder}" if name == "kv" else policy.placeholder
            out = pattern.sub(replacement, out)
    for pattern in policy.extra_patterns:
        out = pattern.sub(policy.placeholder, out)

    if policy.mask_emails:
        out = _EMAIL.sub(_mask_email, out)
    return out


# ── URL ────────────────────────────────────────────────────────────────────

def redact_url(url: str, *, keep: str = "path", policy: RedactionPolicy = DEFAULT_POLICY) -> str:
    """脱敏一个 URL。

    Args:
        keep:
            - `"path"`（默认）：保留 scheme + host + path，**丢掉 query、fragment
              和 userinfo**。query 里有东西时留一个 `?<redacted>` 作为痕迹，
              这样"有没有带参数"这个信息还在。
            - `"host"`：只保留 scheme + host。给浏览器扩展回报
              "最终落在哪个域名"用——路径本身也可能带令牌（`/reset/<token>`）。

    拿不动的输入（不是 URL）走 `redact_text` 兜底，不抛异常——脱敏在日志路径上，
    不能因为输入怪就把调用方打断。
    """
    if keep not in {"path", "host"}:
        raise ValueError('keep 只能是 "path" 或 "host"')
    if not isinstance(url, str) or not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(url, policy=policy)
    if not parts.scheme or not parts.netloc:
        return redact_text(url, policy=policy)

    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    if parts.username or parts.password:
        # userinfo 里的 user:pass 一定不能留
        host = f"{policy.placeholder}@{host}"

    if keep == "host":
        return urlunsplit((parts.scheme, host, "", "", ""))
    query = policy.placeholder if parts.query else ""
    return urlunsplit((parts.scheme, host, parts.path, query, ""))


# ── 结构化数据 ─────────────────────────────────────────────────────────────

def redact_mapping(
    data: object,
    *,
    policy: RedactionPolicy = DEFAULT_POLICY,
    _depth: int = 0,
    _seen: frozenset[int] | None = None,
    _url_mode: bool = False,
) -> object:
    """递归脱敏字典 / 列表 / 标量，给结构化日志用。

    规则：
      - 键名敏感 → 整个值换成 placeholder（不管值长什么样）
      - 键名在 `url_key_names` 里 → 值（含**嵌套在列表里**的）走 `redact_url`。
        resolvers 产出的候选链接就是一个列表，所以必须往下传
      - 键名不敏感、值是字符串 → 走 `redact_text`
      - 嵌套 dict / list / tuple → 递归
      - 超过 `max_depth` → `"<too-deep>"`；遇到环 → `"<cycle>"`

    返回的是新对象，**不原地改调用方的数据**。
    """
    seen = _seen or frozenset()
    if _depth > policy.max_depth:
        return TOO_DEEP

    if isinstance(data, Mapping):
        if id(data) in seen:
            return CYCLE
        nested = seen | {id(data)}
        result: dict = {}
        for key, value in data.items():
            if is_sensitive_key(key, policy=policy):
                result[key] = policy.placeholder
            else:
                result[key] = redact_mapping(
                    value,
                    policy=policy,
                    _depth=_depth + 1,
                    _seen=nested,
                    # query 里的会话 id 不匹配任何模式，必须按 URL 整段丢掉。
                    # 一旦进了 URL 键，下面所有字符串都按 URL 处理（候选链接是列表）。
                    _url_mode=_url_mode or is_url_key(key, policy=policy),
                )
        return result

    if isinstance(data, str):
        if _url_mode:
            return redact_text(redact_url(data, policy=policy), policy=policy)
        return redact_text(data, policy=policy)

    if isinstance(data, Sequence) and not isinstance(data, bytes | bytearray):
        if id(data) in seen:
            return CYCLE
        nested = seen | {id(data)}
        items = [
            redact_mapping(
                item, policy=policy, _depth=_depth + 1, _seen=nested, _url_mode=_url_mode
            )
            for item in data
        ]
        return tuple(items) if isinstance(data, tuple) else items

    return data
