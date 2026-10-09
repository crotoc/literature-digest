"""features/exporting 的业务逻辑：把文库里的条目导出成参考文献文件（可带
附件打包），以及单条/多条引用的各种衍生文本（格式化引用串 / BibTeX citekey
/ LaTeX `\\cite{}` / 单条 BibTeX/CSL-JSON）。

组合 `caps/{bibformats,citation,archive,blobstore,template}` +
`domain/{works,attachments,settings}`。本模块不开表。

**没有依赖 `domain/libraries`**：导出是只读操作，且计划里"全选所有筛选
结果"的访问域校验已经在 `features/library_browse.resolve_selection` 那一步
做完——调用方把已经校验过的 `work_ids` 传进来，本模块只需要用
`library_id` + `work_ids` 的组合查询（`domain.works.list_works` 的
`work_ids` 过滤），这样任何不属于 `library_id` 的 id 会被静默排除在结果
之外（和 `library_browse` 处理跨库 id 同一个套路），不需要再单独校验一次
"这些 id 是不是真的在这个库里"。
"""

from __future__ import annotations

import io
import os

from caps.archive import entry, write_zip_to
from caps.bibformats import Record, serialize
from caps.citation import generate_bibtex_key, latex_cite, list_styles, render_citation
from caps.template import ON_MISSING_EMPTY, render_bracket
from domain.attachments import list_attachments_for_work
from domain.settings import resolve_setting, set_account_setting
from domain.works import WorkDTO, get_work, list_identifiers, list_works

DEFAULT_CITATION_STYLE = "apa"
DEFAULT_FILENAME_TEMPLATE = "[firstauthor:1]_[year]_[title:60]"

SETTINGS_MODULE = "exporting"
STYLE_SETTING_KEY = "default_citation_style"

_FORMAT_EXTENSIONS = {"ris": "ris", "bibtex": "bib", "csljson": "json"}


def _work_to_record(db, work: WorkDTO) -> Record:
    """`WorkDTO` 和 `Record` 几乎同构（这不是巧合——`domain.works` 本来就
    直接复用了 `caps.bibformats` 的 `ITEM_TYPES`/`Person`），唯一的区别是
    标识符：`Record` 把 doi/pmid/isbn/issn 放在自己身上，`WorkDTO` 的标识符
    活在单独的 `work_identifiers` 表里，所以这里要多查一次。"""
    identifiers = {row.scheme: row.value for row in list_identifiers(db, work.id)}
    return Record(
        item_type=work.item_type,
        title=work.title,
        authors=work.authors,
        year=work.year,
        month=work.month,
        day=work.day,
        container_title=work.container_title,
        volume=work.volume,
        issue=work.issue,
        pages=work.pages,
        publisher=work.publisher,
        doi=identifiers.get("doi"),
        pmid=identifiers.get("pmid"),
        isbn=identifiers.get("isbn"),
        issn=identifiers.get("issn"),
        abstract=work.abstract,
        language=work.language,
        note=work.note,
    )


def _naming_values(work: WorkDTO) -> dict:
    first_author = work.authors[0] if work.authors else None
    family = (first_author.family or first_author.literal) if first_author else None
    return {
        "firstauthor": family or "unknown",
        "year": work.year if work.year is not None else "nd",
        "title": work.title or "untitled",
    }


def export_bibliography(db, *, library_id: int, work_ids, format: str) -> str:
    """把给定的条目导出成一份纯参考文献文本（RIS/BibTeX/CSL-JSON 之一），
    不含附件。"""
    works = list_works(db, library_id=library_id, work_ids=list(work_ids))
    records = [_work_to_record(db, work) for work in works]
    return serialize(format, records)


def export_with_attachments_zip(
    db,
    *,
    library_id: int,
    work_ids,
    format: str,
    blob_store,
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
) -> bytes:
    """同上，但把每条条目的附件一起打进一个 ZIP：根目录一份参考文献文件 +
    `attachments/` 下按 `filename_template` 命名的各个附件原文件（后缀保留
    原始文件名的后缀）。

    这是计划里"RIS + PDF ZIP"这一行的落地。**不是**"Zotero RIS（含附件
    相对路径关联）"——那需要在 RIS 记录里写一个指向 ZIP 内相对路径的字段，
    `caps/bibformats` 目前没有对应的字段约定，见 README 裁剪范围。
    """
    works = list_works(db, library_id=library_id, work_ids=list(work_ids))
    records = [_work_to_record(db, work) for work in works]
    bibliography_text = serialize(format, records)
    bib_name = f"bibliography.{_FORMAT_EXTENSIONS[format]}"

    entries = [entry(bib_name, bibliography_text.encode("utf-8"))]
    for work in works:
        for attachment in list_attachments_for_work(db, work.id):
            rendered = render_bracket(filename_template, _naming_values(work), on_missing=ON_MISSING_EMPTY)
            ext = os.path.splitext(attachment.filename)[1]
            entries.append(entry(f"attachments/{rendered}{ext}", blob_store.open(attachment.digest)))

    buffer = io.BytesIO()
    # on_duplicate="rename"：撞名交给 write_zip_to 自己处理——它是在
    # sanitize_entry_name() 之后的名字上判重的，比在这里自己先查重更准确
    # （两个不同的原始文件名完全可能净化后撞到一起，在这里判重会漏掉这种
    # 情况）。
    write_zip_to(buffer, entries, on_duplicate="rename")
    return buffer.getvalue()


def cite_record_text(db, *, work_id: int, format: str) -> str:
    """单条条目的 BibTeX/CSL-JSON/RIS 文本——"复制单条 BibTeX/CSL-JSON"用。"""
    work = get_work(db, work_id)
    return serialize(format, [_work_to_record(db, work)])


def cite_formatted(db, *, work_id: int, style: str = DEFAULT_CITATION_STYLE) -> str:
    """一条格式化好的引用串（APA/Vancouver）。"""
    work = get_work(db, work_id)
    return render_citation(_work_to_record(db, work), style=style)


def cite_keys(db, *, work_ids) -> dict[int, str]:
    """给一组条目各生成一个 BibTeX citekey，互相之间去重（同一批里两条都是
    "smith2019..." 时第二条自动加字母后缀）。"""
    keys: dict[int, str] = {}
    existing: list[str] = []
    for work_id in work_ids:
        work = get_work(db, work_id)
        key = generate_bibtex_key(_work_to_record(db, work), existing_keys=existing)
        existing.append(key)
        keys[work_id] = key
    return keys


def cite_latex(db, *, work_ids, command: str = "cite") -> str:
    """拼一条 LaTeX `\\cite{...}`，覆盖给定的全部条目。"""
    keys = cite_keys(db, work_ids=work_ids)
    return latex_cite(list(keys.values()), command=command)


def resolve_citation_style(db, *, account_id: int) -> str:
    """账号 → 站点 → 代码默认值（`DEFAULT_CITATION_STYLE`）三级回退，取当前
    生效的默认引用样式。"""
    resolution = resolve_setting(
        db,
        module=SETTINGS_MODULE,
        key=STYLE_SETTING_KEY,
        account_id=account_id,
        default=DEFAULT_CITATION_STYLE,
    )
    return resolution.value


def set_default_citation_style(db, *, account_id: int, style: str) -> None:
    if style not in list_styles():
        raise ValueError(f"不认识的引用样式：{style!r}（支持：{sorted(list_styles())}）")
    set_account_setting(db, account_id=account_id, module=SETTINGS_MODULE, key=STYLE_SETTING_KEY, value=style)
