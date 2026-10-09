"""caps/bibformats 聚合层。实际逻辑在 record.py（共享模型）和 parsers/{ris,bibtex,csljson}.py
（各格式实现）——这个文件只做聚合 + 按格式名分发，不自己处理任何字段映射。"""

from __future__ import annotations

from collections.abc import Sequence

from caps.bibformats.parsers.bibtex import parse_bibtex, serialize_bibtex
from caps.bibformats.parsers.csljson import parse_csljson, serialize_csljson
from caps.bibformats.parsers.ris import parse_ris, serialize_ris
from caps.bibformats.record import (
    ITEM_TYPES,
    BibFormatsError,
    ParseError,
    Person,
    Record,
    UnsupportedFormat,
)

# 这个模块本身不用 ITEM_TYPES/BibFormatsError/ParseError/Person——它们是从 record.py
# 转手再出给 contract.py 的（聚合层的工作就是把 record.py + parsers/* 的东西收到一处）。
# __all__ 显式声明，这样 ruff 的"未使用 import"检查知道这是有意的再导出，不会被
# --fix 悄悄删掉导致 contract.py 的 import 炸掉。
__all__ = [
    "ITEM_TYPES",
    "SUPPORTED_FORMATS",
    "BibFormatsError",
    "ParseError",
    "Person",
    "Record",
    "UnsupportedFormat",
    "parse",
    "parse_bibtex",
    "parse_csljson",
    "parse_ris",
    "serialize",
    "serialize_bibtex",
    "serialize_csljson",
    "serialize_ris",
]

SUPPORTED_FORMATS = frozenset({"ris", "bibtex", "csljson"})

_PARSERS = {"ris": parse_ris, "bibtex": parse_bibtex, "csljson": parse_csljson}
_SERIALIZERS = {"ris": serialize_ris, "bibtex": serialize_bibtex, "csljson": serialize_csljson}


def parse(fmt: str, text: str) -> list[Record]:
    """按格式名分发到对应的解析器。`fmt` 是 `"ris"` / `"bibtex"` / `"csljson"` 之一。"""
    try:
        parser = _PARSERS[fmt]
    except KeyError:
        raise UnsupportedFormat(
            f"不支持的格式: {fmt!r}（支持: {sorted(SUPPORTED_FORMATS)}）"
        ) from None
    return parser(text)


def serialize(fmt: str, records: Sequence[Record]) -> str:
    """按格式名分发到对应的序列化器。"""
    try:
        serializer = _SERIALIZERS[fmt]
    except KeyError:
        raise UnsupportedFormat(
            f"不支持的格式: {fmt!r}（支持: {sorted(SUPPORTED_FORMATS)}）"
        ) from None
    return serializer(records)
