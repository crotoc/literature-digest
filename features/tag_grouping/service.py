"""features/tag_grouping 的业务逻辑：手动建组 / AI 分组提案 / AI 重复标签
合并，三个看起来不同的产品功能，落到实现上是**同一个编排动作**——把一组
已确定的 tag_id 合并成一个目标标签，三者只是"这组 tag_id 怎么来的"不同
（用户手选 / LLM 按语义聚类提案 / LLM 按近似重复检测提案）。

`domain/tags/README.md` 明确要求本模块**不得改动它的表结构**——合并操作
全部通过它已有的 contract 函数完成：`create_tag`（同名幂等复用，天然适合
把目标标签名解析成一个具体 tag）+ `list_work_ids_for_tag` + `add_tag_to_work`
+ `remove_tag_from_work` + `delete_tag`。本模块自己不开表，也没有 models.py。

组合 `domain/{tags,jobs}` + `adapters/llm`。AI 相关的两个函数都是"只提案、
不自动应用"——提案结果交回调用方（页面层），用户确认要合并哪些簇之后再
显式调 `merge_tags()`，这是刻意的产品选择：合并会删标签、不可撤销（和
`domain/tags.delete_tag()` 本身一样没有撤销机制），不能让 LLM 的输出直接
落地。

job 粒度：合并走计划「两处已定」里"瞬时批量"这一类（父 job 一行 +
`counts_json`，不需要像 `features/organizing` 那样按 work 建子 job）——
区别在于 organizing 的批量是"对用户传入的、规模可能很大的 work_id 列表
逐项执行、逐项可能失败"，这里的失败点只有结构性校验（tag_id 不存在/不属
于这个库/传入少于两个 tag），全部在建 job **之前**就能查清楚、一次性拒绝，
不存在"合并到一半某一项失败"的情况，所以不需要为失败隔离建子 job。

LLM provider 的分发（`openai_compat` / `anthropic`）是本模块自己维护的一张
小表，不是从 `adapters/llm` 借来的共享机制——`adapters/llm` 的 README 明确
写了两个适配器之间没有共享 contract.py，每个 feature 要自己决定怎么在它们
之间切换；`features/ai_enrichment` 以后会需要同样的分发逻辑，但两处各自维
护一份，不提炼成 features 之间共享的东西（features 互不 import）。

刻意裁剪的范围：
- 不做"AI 建议自动应用"——人工确认是必经步骤。
- 不做合并历史/撤销——`delete_tag()` 本身就不可撤销，合并作为它的组合操作
  继承同样的限制；真要恢复，只能手动重新打标签。
- 不做"跨库合并"——`tag_ids` 必须全部属于同一个 `library_id`（标签池本身
  就是库内范围，见 `domain/tags/models.py`）。
- AI 提案的 system 提示词只区分"语义聚类"和"近似重复"两种意图，不支持
  自定义提示词模板（那是 `caps/template` 的知识，`ai_enrichment` 如果需要
  会自己接）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.orm import Session

from adapters.llm import anthropic, openai_compat
from adapters.llm.anthropic import AnthropicError
from adapters.llm.openai_compat import OpenAiCompatError
from domain.jobs import JobDTO, create_job, transition, update_counts
from domain.tags import TagDTO, add_tag_to_work, create_tag, get_tag, list_tags, list_work_ids_for_tag
from domain.tags import delete_tag as _delete_tag
from domain.tags import remove_tag_from_work as _remove_tag_from_work
from infra.errors import AppError

JOB_KIND_MERGE = "tag_grouping_merge"
# 瞬时批量：父 job 一行 + counts_json，不建子 job（原因见模块 docstring）。
_PARENT_TRANSITIONS = {"queued": {"running"}, "running": {"succeeded"}}

_PROVIDERS = {
    "openai_compat": openai_compat.complete_structured,
    "anthropic": anthropic.complete_structured,
}

_GROUP_SYSTEM_PROMPT = (
    "你是一个文献标签整理助手。下面是一个标签池，每行是 `id: 名称`。"
    "请按语义主题把其中可以归为同一类的标签分组——每组至少 2 个标签，"
    "组名用简洁的中文短语概括这组标签共同的主题。不属于任何明显主题、"
    "找不到搭档的标签直接忽略，不要为了凑数把无关标签硬塞进同一组。"
)
_DUPLICATE_SYSTEM_PROMPT = (
    "你是一个文献标签整理助手。下面是一个标签池，每行是 `id: 名称`。"
    "请找出其中表达同一个概念、只是写法不同的标签（大小写、全半角、"
    "单复数、同义缩写、明显的拼写错别字等），把每一组这样的重复标签"
    "放在一起，组名用这组里最规范/最常见的那个写法。没有重复项的标签"
    "直接忽略。"
)


class TagGroupingError(AppError):
    """调用 LLM 失败，或者 LLM 的输出没有一个能用的簇。"""

    status_code = 502
    code = "tag_grouping_llm_error"


@dataclass(frozen=True)
class TagClusterProposal:
    """一组建议合并到一起的标签——`name` 是建议的合并后标签名。"""

    name: str
    tag_ids: tuple[int, ...]


@dataclass(frozen=True)
class MergeResult:
    job: JobDTO
    target_tag: TagDTO
    merged_tag_ids: tuple[int, ...]
    works_retagged: int


class _TagClusterItem(BaseModel):
    name: str
    tag_ids: list[int]


class _TagClusteringResult(BaseModel):
    clusters: list[_TagClusterItem]


def merge_tags(
    db: Session, *, account_id: int, library_id: int, tag_ids: Sequence[int], target_name: str
) -> MergeResult:
    """把 `tag_ids` 这些标签合并成名为 `target_name` 的一个标签。

    `target_name` 走 `create_tag()` 的同名幂等逻辑：如果库里已经有同名标签
    （包括恰好就是 `tag_ids` 里的某一个），直接复用它当目标，不会被当成
    "待合并的源标签"重复处理。
    """
    unique_ids = list(dict.fromkeys(tag_ids))
    if len(unique_ids) < 2:
        raise ValueError("合并至少需要 2 个不同的标签")

    tags = [get_tag(db, tag_id) for tag_id in unique_ids]
    for tag in tags:
        if tag.library_id != library_id:
            raise ValueError(f"tag_id 不属于这个库：{tag.id}")

    target = create_tag(db, library_id=library_id, name=target_name)
    source_tag_ids = [tag.id for tag in tags if tag.id != target.id]

    job = create_job(
        db, account_id=account_id, kind=JOB_KIND_MERGE, library_id=library_id,
        counts={"tags_merged": 0, "works_retagged": 0},
    )
    job = transition(db, job.id, "running", allowed=_PARENT_TRANSITIONS)

    works_retagged = 0
    for source_tag_id in source_tag_ids:
        for work_id in list_work_ids_for_tag(db, source_tag_id):
            add_tag_to_work(db, library_id=library_id, work_id=work_id, tag_id=target.id)
            _remove_tag_from_work(db, work_id=work_id, tag_id=source_tag_id)
            works_retagged += 1
        _delete_tag(db, source_tag_id)

    update_counts(db, job.id, {"tags_merged": len(source_tag_ids), "works_retagged": works_retagged})
    job = transition(db, job.id, "succeeded", allowed=_PARENT_TRANSITIONS)

    return MergeResult(
        job=job,
        target_tag=target,
        merged_tag_ids=tuple(source_tag_ids),
        works_retagged=works_retagged,
    )


def propose_ai_groups(
    db: Session,
    *,
    library_id: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    transport=None,
    resolve=None,
) -> list[TagClusterProposal]:
    """让 LLM 按语义主题给当前标签池分组提案——只提案，不自动合并。"""
    return _propose(
        db, library_id=library_id, provider=provider, model=model, api_key=api_key,
        base_url=base_url, system=_GROUP_SYSTEM_PROMPT, transport=transport, resolve=resolve,
    )


def propose_ai_duplicates(
    db: Session,
    *,
    library_id: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    transport=None,
    resolve=None,
) -> list[TagClusterProposal]:
    """让 LLM 找出写法不同但语义相同的重复标签——只提案，不自动合并。"""
    return _propose(
        db, library_id=library_id, provider=provider, model=model, api_key=api_key,
        base_url=base_url, system=_DUPLICATE_SYSTEM_PROMPT, transport=transport, resolve=resolve,
    )


def _propose(
    db: Session,
    *,
    library_id: int,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    system: str,
    transport,
    resolve,
) -> list[TagClusterProposal]:
    tags = list_tags(db, library_id=library_id)
    if len(tags) < 2:
        return []

    tag_listing = "\n".join(f"{tag.id}: {tag.name}" for tag in tags)
    kwargs: dict[str, object] = {
        "response_model": _TagClusteringResult,
        "messages": [{"role": "user", "content": f"标签池：\n{tag_listing}"}],
        "model": model,
        "api_key": api_key,
        "system": system,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if transport is not None:
        kwargs["transport"] = transport
    if resolve is not None:
        kwargs["resolve"] = resolve

    result = _complete_structured(provider, kwargs)

    valid_tag_ids = {tag.id for tag in tags}
    proposals = []
    for cluster in result.clusters:
        name = cluster.name.strip()
        ids = tuple(dict.fromkeys(tid for tid in cluster.tag_ids if tid in valid_tag_ids))
        if not name or len(ids) < 2:
            continue
        proposals.append(TagClusterProposal(name=name, tag_ids=ids))
    return proposals


def _complete_structured(provider: str, kwargs: Mapping[str, object]) -> _TagClusteringResult:
    try:
        fn = _PROVIDERS[provider]
    except KeyError:
        raise TagGroupingError(f"不支持的 LLM provider：{provider!r}") from None

    try:
        return fn(**kwargs)
    except (OpenAiCompatError, AnthropicError) as exc:
        raise TagGroupingError(f"调用 LLM 失败：{exc}") from exc
