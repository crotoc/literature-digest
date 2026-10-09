# domain/tags

库内扁平的标签池（没有层级，和 `domain/folders` 的树不同）+ work↔tag 多对
多关联 + 批量打标签 UI 要用的三态聚合。两张表：`tags`、`work_tags`。

## 对外承诺

```python
from domain.tags import (
    TAG_STATES, TagDTO, WorkTagDTO, TagStateDTO,
    TagNotFound, TagNameTaken, WorkTagNotFound,
    create_tag, get_tag, rename_tag, delete_tag,
    list_tags, reorder_tags,
    add_tag_to_work, remove_tag_from_work,
    list_tags_for_work, list_work_ids_for_tag,
    aggregate_states,
)

tag = create_tag(db, library_id=lib.id, name="Machine Learning")  # 重名直接复用已有的
add_tag_to_work(db, library_id=lib.id, work_id=work.id, tag_id=tag.id)

list_tags_for_work(db, work.id)        # [TagDTO, ...]
list_work_ids_for_tag(db, tag.id)      # [work_id, ...]——裸 id，不是 WorkDTO

reorder_tags(db, library_id=lib.id, tag_ids_in_order=[t3.id, t1.id, t2.id])  # 必须是全集

# 批量打标签对话框的"不确定态复选框"数据源
states = aggregate_states(db, library_id=lib.id, work_ids=[w1.id, w2.id, w3.id])
# [TagStateDTO(tag=..., state="all"|"some"|"none"), ...]，覆盖库里每一个标签
```

异常：`TagNotFound`（404）、`WorkTagNotFound`（404）、`TagNameTaken`
（409——改名撞到另一个已存在的标签名）。

## 设计要点

### `create_tag` 为什么重名直接复用已有标签，而不是报错

对应旧单体页面梳理④里的"顺手新建标签再打上"（`manual_tag_new`）：用户在
给文献打标签的输入框里敲一个新名字，可能恰好和库里已有的标签同名（没打算
新建一个重复的，只是不记得已经有了）。如果报错，前端就要先查一遍标签池再
决定调 create 还是直接用现有 id，徒增一次往返；直接幂等返回已有标签，调用
方不需要关心"这个名字是不是已经存在"。真正的改名冲突检查在 `rename_tag`
里（显式操作，用户确实想把一个已有标签改成另一个名字，这时候撞车是需要
报出来的真实冲突，所以 `TagNameTaken` 只出现在这条路径上）。

### 为什么标签池是扁平的，不像文件夹那样允许嵌套

旧单体和架构文档里标签从来没有过层级结构的要求——"标签分组"（AI 分组提案
/手动建组）是 E1/E2 `features/tag_grouping` 要加的能力，而且那是"把多个标签
归到一个组名下"的编排，不改变 `tags` 表本身的形状，所以不下沉成本模块的表
结构变化。

### `reorder_tags` 为什么要求传入全集，不支持只重排一部分

如果只传一个子集，没传的那些标签的新位置是没有定义的——排在最前？最后？
原地不动但要和传入的部分交织出一个新顺序？任何选择都是在猜用户想要什么。
拖拽排序这个操作在 UI 上天然就是"整个列表重新排一遍"，所以直接要求调用方
传整个库的标签 id 全集，不一致就拒绝，没有歧义。

### 三态聚合：为什么覆盖库里*所有*标签，而不是只覆盖已经打在选中文献上的那些

批量打标签对话框需要展示库里全部标签各自的勾选框状态（包括一个都没打过的
标签，状态是明确的 "none"，不是"不显示"），这样用户才能在同一个对话框里
一次性勾上新标签、取消某个"all"的标签。如果只返回命中过的标签子集，UI 还
要自己再查一遍 `list_tags` 做差集，没有必要让调用方做这个合并。`work_ids`
传空列表时全部标签状态都是 "none"——没有文献可比较，谈"全部/部分"没有意义。

### 为什么不认识 `domain.works`

和 `domain/folders` 的理由完全一样：`WorkTag.work_id` 是跨 domain 的裸整数，
`list_work_ids_for_tag` 只返回 `int`，展开成完整文献记录是调用方
（`library_browse`）的工作。

## 依赖方向

`infra/db`（`Base`、`new_memory_session`）+ `infra/errors`（`NotFound` /
`AppError`）。**不依赖任何其它 domain / adapters / features**。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 标签分组 / AI 分组提案 / AI 重复标签合并 | E1/E2 `features/tag_grouping`，不改变本模块的表结构 |
| 标签名大小写不敏感去重（"ML" 和 "ml" 视为同一个标签） | v1 按精确字符串匹配；真要做需要先定义规范化规则（casefold？locale？），和 `caps/slug` 的职责有重叠但语义不同，不预先猜 |
| 批量打标签/去标签的编排（含进度上报） | `features/organizing` |
| 按标签筛选文献列表、AND 筛选、点标签文字单标签筛选 | `features/library_browse` |
| 验证 `work_id` 对应的文献确实存在 | 调用方职责（本模块不 import `domain.works`） |
