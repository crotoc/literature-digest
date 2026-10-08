"""字符串规范化 + slugify。

设计要点：
  - 保留**任何语言**的字母和数字（按 Unicode 类别判断，不是 ASCII 白名单），
    所以中文标题 slugify 之后仍然可读，不会变成一串下划线。
  - 分隔符统一归一为一个，连续的折叠成一个，首尾去掉。
  - 纯符号 / 纯 emoji 这种去掉后啥都不剩的输入，落一个**稳定 hash**，
    而不是返回空串——空串会在文件名、URL slug、唯一键里引发更难查的问题。
"""

import hashlib
import unicodedata

_HASH_PREFIX = "x"
_HASH_LENGTH = 10


def normalize(text: str) -> str:
    """NFKC 规范化 + 折叠空白 + 去首尾。

    NFKC 让全角/半角、兼容字符落到同一表示，这样"ＡＢＣ"和"ABC"是一个东西。
    """
    folded = unicodedata.normalize("NFKC", text)
    return " ".join(folded.split())


def _is_kept(char: str) -> bool:
    """字母或数字（任何语言）才保留。"""
    return unicodedata.category(char)[0] in {"L", "N"}


def _stable_hash(source: str) -> str:
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()  # noqa: S324 — 非安全用途
    return f"{_HASH_PREFIX}{digest[:_HASH_LENGTH]}"


def slugify(text: str, *, separator: str = "_", max_length: int | None = None) -> str:
    """把任意字符串变成安全的 slug。

    Args:
        text: 原始字符串。
        separator: 分隔符，默认 `_`。
        max_length: 截断长度（按字符数，不是字节）；截断后不留尾随分隔符。

    Returns:
        非空 slug。去掉非字母数字后为空时返回 `x<sha1 前 10 位>`，
        对同一输入恒定。

    Raises:
        ValueError: separator 自身含字母或数字，或 max_length <= 0。
    """
    if any(_is_kept(char) for char in separator):
        raise ValueError("separator 不能含字母或数字")
    if max_length is not None and max_length <= 0:
        raise ValueError("max_length 必须为正")

    source = normalize(text)
    pieces: list[str] = []
    current: list[str] = []
    for char in source.casefold():
        if _is_kept(char):
            current.append(char)
        elif current:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))

    slug = separator.join(pieces)
    if not slug:
        # 纯符号/纯 emoji：用规范化后的原文算 hash，保证同输入同输出
        return _stable_hash(source)

    if max_length is not None and len(slug) > max_length:
        slug = slug[:max_length].rstrip(separator)
        if not slug:  # max_length 比第一段还短
            return _stable_hash(source)[:max_length]
    return slug
