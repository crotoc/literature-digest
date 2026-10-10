# features/tag_grouping

库内标签池的"归并"编排：手动建组、AI 分组提案、AI 重复标签合并——三个计划
里写的产品功能，实现上是同一件事：把一组已确定的 `tag_id` 合并成一个目标
标签，区别只在于这组 `tag_id` 从哪来（用户手选 / LLM 按语义聚类提案 / LLM
按近似重复检测提案）。

## 对外暴露什么（见 `contract.py`）

- `merge_tags(db, *, account_id, library_id, tag_ids, target_name) -> MergeResult`
  —— 唯一真正改库的函数。把 `tag_ids` 合并进名为 `target_name` 的标签（同名
  幂等复用 `domain.tags.create_tag` 的既有逻辑），源标签下的全部文献转移到
  目标标签上，源标签被删除。
- `propose_ai_groups(db, *, library_id, provider, model, api_key, base_url=None, transport=None, resolve=None) -> list[TagClusterProposal]`
  —— 让 LLM 按语义主题给当前标签池分组。**只提案，不自动合并。**
- `propose_ai_duplicates(db, *, library_id, provider, model, api_key, ...) -> list[TagClusterProposal]`
  —— 让 LLM 找出写法不同但语义相同的重复标签。**只提案，不自动合并。**
- `TagClusterProposal(name, tag_ids)`：一组建议合并的标签 + 建议的合并后名字。
- `MergeResult(job, target_tag, merged_tag_ids, works_retagged)`。
- `TagGroupingError`（`AppError` 子类，502）：LLM 调用失败，或 provider 不
  支持。
- `JOB_KIND_MERGE`：供页面层按 kind 查询/展示 job 历史用。

两个 `propose_*` 函数返回的是提案，不是最终结果——页面层把提案展示给用户
确认后，再显式调用 `merge_tags()` 才会真正改库。这是刻意的产品选择：合并
会删标签、而 `domain.tags.delete_tag()` 本身没有撤销机制，不能让 LLM 的
输出直接落地。

## 依赖方向

`domain/{tags,jobs}` + `adapters/llm`（`openai_compat` / `anthropic`）。
本模块**没有自己的表**，也没有 `models.py`——`domain/tags/README.md` 明确
要求"编排类"功能不得改动它的表结构，合并操作全部通过 `domain.tags` 已有
的 contract 函数完成（`create_tag` / `list_work_ids_for_tag` /
`add_tag_to_work` / `remove_tag_from_work` / `delete_tag`）。

`api_key` / `base_url` / `model` 全部由调用方显式传入，本模块不读取
`domain.connections`——和 `adapters/llm` 两个适配器自己的约定一致（它们
也不知道凭据从哪来）。调用方（目前是测试，以后是 `app/pages`）负责从
`domain.connections` 的 `ai_profile` 连接里解析出这三样再传进来。这样
`features/tag_grouping` 不需要 import `features/connection_setup`，维持
features 之间互不 import 的边界。

`provider` 的分发（`openai_compat` ↔ `anthropic`）是本模块自己维护的一张
小表（`_PROVIDERS`），不是从 `adapters/llm` 借来的共享机制——`adapters/llm`
的两个模块之间没有共享 `contract.py`，每个用到它们的 feature 自己决定怎么
在两者间切换。`features/ai_enrichment` 以后会需要同样的分发逻辑，两处各
自维护一份，不提炼成 features 共享的东西。

## job 粒度：为什么不用子 job

计划「两处已定」把"打标签/去标签/加移文件夹/删除"归为"瞬时批量"（父 job
一行 + `counts_json`，只有失败项才建子行），和"全文下载/AI 评估/导入"这种
每项一行的长流程批量不是同一类。

本模块的合并操作比 `features/organizing` 的批量操作更简单：所有可能失败
的情况（`tag_ids` 少于 2 个、某个 `tag_id` 不存在、某个 `tag_id` 不属于这
个库）都是**结构性校验**，在建 job **之前**就能一次性查完、一次性拒绝——
不存在"合并到一半某一项失败"的情况。所以这里甚至不需要
`features/organizing` 那种"失败项建子 job"的失败隔离：job 只会原样走完
`queued → running → succeeded`，`counts_json` 记录 `tags_merged` 和
`works_retagged` 两个数字留痕。

## 刻意裁剪的范围

| 不做 | 为什么 |
|---|---|
| AI 建议自动应用 | 人工确认是必经步骤，合并不可撤销 |
| 合并历史/撤销 | `domain.tags.delete_tag()` 本身不可撤销，合并继承同样的限制；真要恢复只能手动重新打标签 |
| 跨库合并 | 标签池本身就是库内范围（见 `domain/tags/models.py`），`tag_ids` 必须全部属于同一个 `library_id` |
| 自定义提示词模板 | 只有"语义聚类"/"近似重复"两套固定 system 提示词；真要可配置模板是 `caps/template` 的知识，留给 `ai_enrichment` 如果需要再接 |
| LLM 调用重试/修正 | 和 `adapters/llm` 自己的裁剪一致——校验失败算一次失败，不在这层"重新提示模型修正" |

## 测试

`pytest features/tag_grouping -q`，全部用内存 SQLite（`db`/`library_id`
fixture，和 `features/organizing/tests/conftest.py` 同一套写法）+
`httpx.MockTransport` 模拟 LLM 响应（和 `adapters/llm/tests/` 同一套写法），
不依赖真实网络或真实 API key。
