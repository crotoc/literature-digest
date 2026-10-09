# domain/works

文献条目本身，整个系统里最核心的一张表，外加四张附属表：标识符
（`work_identifiers`）、文献间关系（`work_relations`）、来源溯源
（`work_provenance`）、疑似重复候选（`duplicate_candidates`）。

## 对外承诺

```python
from domain.works import (
    WorkDTO, IdentifierDTO, RelationDTO, ProvenanceDTO, DuplicateCandidateDTO,
    WorkNotFound, IdentifierConflict, IdentifierBelongsToDeletedWork,
    IdentifierNotFound, RelationNotFound, DuplicateCandidateNotFound,
    CANDIDATE_STATUSES, compute_title_year_key,
    create_work, get_work, update_work, soft_delete_work, restore_work, purge_work,
    list_works,
    add_identifier, find_by_identifier, list_identifiers, remove_identifier,
    find_candidates_by_title_year_key,
    record_duplicate_candidate, list_duplicate_candidates, resolve_duplicate_candidate,
    add_relation, list_relations, remove_relation,
    record_provenance, list_provenance,
)

work = create_work(db, library_id=lib.id, title="Attention Is All You Need", year=2017,
                    authors=(Person(family="Vaswani", given="Ashish"),))

add_identifier(db, library_id=lib.id, work_id=work.id, scheme="doi", value="https://doi.org/10.5555/AIAYN")
# value_norm == "10.5555/aiayn"——不同写法会落到同一个标识符上

candidates = find_candidates_by_title_year_key(db, library_id=lib.id, title_year_key=work.title_year_key)
record_duplicate_candidate(db, library_id=lib.id, work_id=work.id, candidate_work_id=other.id, reason="...")

soft_delete_work(db, work.id)   # 移入回收站
restore_work(db, work.id)       # 恢复
purge_work(db, work.id)         # 彻底删除（只级联本模块自己的表）
```

异常：`WorkNotFound`（404）、`IdentifierConflict`（409，携带 `existing_work_id`）、
`IdentifierBelongsToDeletedWork`（409，携带 `deleted_work_id`）、
`IdentifierNotFound` / `RelationNotFound` / `DuplicateCandidateNotFound`（均 404）。

## 设计要点

### 为什么 doi / pmid / isbn 不是 `works` 表上的列，而是另一张 `work_identifiers`

一条文献可能同时有 DOI、PMID、arXiv ID、ISBN 等多个标识符，而且集合是开放的
（新来源带来新 scheme 不需要迁移表结构）。拆成一张 `(library_id, scheme,
value, value_norm)` 的表，`UniqueConstraint("library_id", "scheme",
"value_norm")` 就能在库内防重，还天然支持"按任意一种标识符查到是哪条
work"（`find_by_identifier`）——这是导入去重的入口。

### `add_identifier` 为什么把"冲突"拆成两个不同的异常

一个标识符命中已存在的行时，那一行背后的 work 可能：

- 还活着、且不是当前要挂的这条 → `IdentifierConflict`：这是真正的歧义，调用方
  （`features/importing`）需要决定是报错给用户还是改走别的流程，所以携带
  `existing_work_id` 供它查看那条记录。
- 已经被软删 → `IdentifierBelongsToDeletedWork`：架构文档里明确这种情况的语义
  是"恢复它"而不是"冲突"——不同的信号需要调用方做不同的事（提示"这篇你删过，
  要恢复吗"，而不是"这篇已存在，无法导入"），所以必须是两个不同的异常类型，
  不能合并成一个 `IdentifierConflict` 让调用方自己再去查一次 work 的
  `deleted_at`。

两者都**不会**自动处理——本模块只负责把"命中了谁、那条记录死没死"这个事实
准确地报出来，合并/恢复/报错是调用方的业务决定。

同一个标识符再挂到**同一条**记录上是幂等的，直接返回已有行，不重复插入、不
报错——重复导入同一份数据（比如用户两次点了导入同一个 BibTeX 文件）不应该
报错。

### `restore_work` 为什么不需要重新检查标识符唯一性

直觉上"恢复一条记录"好像需要重新确认它的标识符没有被别人占用，但实际上不
需要：`add_identifier` 的设计从一开始就保证了**同一个 `(library_id, scheme,
value_norm)` 不会同时属于两条"未被软删"的记录**——命中一条已删记录的标识符
时走的是 `IdentifierBelongsToDeletedWork` 分支，不会把那一行的 `work_id` 改
成新记录、也不会插入一条新行去抢占它。换句话说，`work_identifiers` 表里的行
从被插入到被 `remove_identifier`/`purge_work` 删除之前，`work_id` 永远不变。
软删一条 work 期间，它名下的标识符行原地不动，恢复时自然还唯一地属于它。

### `title_year_key`：粗筛指纹，不是判重

`compute_title_year_key`（`fingerprint.py`）只是"标题规范化 + 年份"拼出来的
一个字符串，复用 `caps/slug.slugify` 做字符层规范化。它故意很粗：标题几乎一样
但年份差一年的两条记录不会落在同一个键上——这是有意的保守，宁可漏判交给人工
在 `duplicate_candidates` 里确认，也不自动合并两条可能是预印本/正式版这种
关系、而不是同一篇文献重复导入的记录。`find_candidates_by_title_year_key`
只返回候选，`record_duplicate_candidate` 只是把"这两条疑似重复"这件事记下来
等人工 `resolve_duplicate_candidate`，整条链路里没有一步会自动把两条记录合
并或删除任意一条——那是 `dedupe_review` feature 编排出来的人工决定。

### `update_work` 为什么不自动重跑疑似重复检查

改标题或年份确实会让 `title_year_key` 变化，所以 `update_work` 会重算它。但
不会自动调 `find_candidates_by_title_year_key` 并落候选——那是一次库范围的
扫描，默认在每次编辑时都触发代价不小，也容易在用户还在逐字编辑、没保存完
整的情况下就弹出一堆候选。v1 把"编辑后要不要重新扫描"交给调用方
（`annotating`/`dedupe_review`）显式决定。

### `purge_work` 的级联边界：只清本模块自己的表

`purge_work` 删除 `work_identifiers` / `work_relations`（双向）/
`work_provenance` / `duplicate_candidates`（双向，无论这条 work 是主角还是
候选）以及 `works` 行本身。**不**级联 `tags` / `folders` / `notes` /
`attachments`——那些是别的 domain 的表，本模块既没有知识也没有权限去动它们。
跨 domain 的完整级联顺序（"彻底删除一篇文献"要依次清哪几个 domain）由
`features/organizing` 编排，它会依次调各个 domain 自己的 purge 函数。

### 查询默认排除软删项

`get_work` / `list_works` / `find_by_identifier` / `find_candidates_by_title_year_key`
全部默认 `deleted_at IS NULL`，想看回收站必须显式传 `include_deleted=True`——
默认安全方向是"看不到已删的"。软删只写 `works.deleted_at`，不碰任何关联行
（标识符/关系/溯源/候选全部原样留着），确保恢复时一切照旧。

### `authors` 复用 `caps.bibformats.Person`，不另造 `AuthorDTO`

domain → caps 的依赖方向是架构允许的，`Person`（family/given/literal）的形状
和这里需要的完全一致，没有理由另造一个重复的 DTO。持久化成 `authors_json`
（JSON 列，`[{"family":...,"given":...,"literal":...}, ...]`），
`_person_to_dict` / `_person_from_dict` 是仅在本模块内部用的转换细节，不对外
暴露。

### `list_works` 的 `sort_by`/`work_ids`/`list_work_ids`/`count_works`：给 `features/library_browse` 开的口子，不是本模块自己要用

这四个参数/函数是在实现 `features/library_browse`（第一个要消费本模块读
接口的 feature）时加的,不是预先设计好等着被用。具体分工：

- `sort_by`/`sort_dir`：本模块只维护一个**内部**的列名→SQLAlchemy 列对象
  映射（`_SORT_COLUMNS`，目前是 `created_at`/`updated_at`/`year`/`title`
  四个），不认识的名字直接 `ValueError`——这是技术层面"不能把任意字符串
  拼进 SQL"的安全网，不是产品层面的排序选项白名单。真正决定"页面上暴露
  哪些排序选项、叫什么名字"（比如要不要加"第一作者"）仍然是
  `features/library_browse` 的职责；本模块故意不导出这个映射表,
  这样它改动的时候不用碰这一层。
- `work_ids`：按标签/文件夹筛选时，`features/library_browse` 先从
  `domain.tags.list_work_ids_for_tag` / `domain.folders.list_work_ids_in_folder`
  拿到一组裸 id（它们本来就不认识 `domain.works`），再传进来在
  `library_id`/`include_deleted` 之外加一条 `IN (...)` 过滤——这样"按标签筛选
  + 分页 + 排序"始终是一次 SQL 查询，不需要先查全量再在 Python 里做交集
  （后者会让"全选所有筛选结果"退化成 O(n) 次查询，正是「两处已定」第 2 条
  明确要避免的）。传空列表（区别于不传/`None`）是"筛选交出零候选"的正常
  情形，查出空结果而不是报错或退化成"不筛选"。
- `list_work_ids`：裸 id 版的 `list_works`,命名和用意对齐
  `domain.folders.list_work_ids_in_folder`/`domain.tags.list_work_ids_for_tag`
  ——`resolve_selection()`（"全选所有筛选结果"展开成显式 id 列表）只要 id,
  不需要为几万条文献把整行数据搬进内存。
- `count_works`：配 `list_works` 同一套过滤条件的计数,给分页算总页数用。

本模块先给一个能跑、符合直觉（最近改的排在前面）的默认序
（`updated_at desc`），不在没有消费方的情况下预先猜全部排序选项——"第一作者"
排序目前仍然缺一个可供排序的列（`authors_json` 是 JSON，直接按它排序要么
绑定 SQLite 的 `json_extract` 方言、要么另加一个常驻同步的派生列,两者都还
没有足够的消费方证明值得做,留在刻意裁剪范围里）。

### `updated_at` 靠列级 `onupdate`，不是在每个改字段的函数里手动赋值

`models.py` 的 `Work.updated_at` 用 `onupdate=_utcnow`（而不是只有
`insert_default`）——这样只要这一行被任何 UPDATE 语句触碰（`update_work`
改字段、`soft_delete_work`/`restore_work` 改 `deleted_at`），`updated_at` 都
会自动跟着刷新，不需要在每一个会修改 work 的函数里重复写
`row.updated_at = _utcnow()`。这是本模块实现过程中被单元测试
（`test_list_works_orders_by_updated_at_desc`）当场抓到的一个真实 bug——
最初版本只在插入时设置过一次，编辑后 `updated_at` 永远不变，`list_works`
的默认排序因此是错的。

## 依赖方向

`infra/db`（`Base`、`new_memory_session`）+ `infra/errors`（`NotFound` /
`AppError`）+ `caps/bibformats`（`ITEM_TYPES`、`Person`）+ `caps/slug`
（经由 `fingerprint.py`）。**不依赖任何其它 domain / adapters /
features**——跨 domain 的 `library_id` 引用是裸 `int`，约定详见
`domain/libraries/README.md`「`LibraryMember.account_id` 刻意不是数据库外键」
一节。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 标识符 scheme 的白名单校验（本模块接受任意字符串当 scheme） | v1 不做；真要限定需要先定义支持的 scheme 全集，而且新来源随时可能带来新 scheme |
| 自动合并两条被判定为重复的记录 | `dedupe_review`——合并要决定保留哪条的哪些字段、怎么迁移关联的 tags/folders/notes/attachments，比这里的"记一条候选"复杂得多 |
| 编辑后自动重新扫描疑似重复 | 调用方显式决定（见上文设计要点） |
| `purge_work` 级联到 tags/folders/notes/attachments | `features/organizing` 编排跨 domain 顺序 |
| 按第一作者排序、年份范围/`item_type` 筛选 | `features/library_browse`；前者缺一个可排序的派生列，后两者目前没有消费方 |
| `work_relations` 的 `relation_type` 词表校验 | v1 不限定具体取值（比如 "preprint_of"/"duplicate_of"/"confirmed_not_duplicate"），由调用方的业务知识决定用什么词；放这里要穷举会预先猜错 |
| 标识符 `value_norm` 的更严格校验（真的验证 DOI 字符集、ISBN 校验位） | v1 只做大小写折叠 + 常见前缀剥离，不做格式级校验 |
