# domain/folders

可嵌套的文件夹 + work↔folder 多对多关联。两张表：`folders`（自引用树）、
`work_folders`（关联表）。

## 对外承诺

```python
from domain.folders import (
    FolderDTO, WorkFolderDTO,
    FolderNotFound, FolderCycle, WorkFolderNotFound,
    create_folder, get_folder, rename_folder, move_folder, delete_folder,
    list_folders,
    add_work_to_folder, remove_work_from_folder,
    list_folders_for_work, list_work_ids_in_folder,
)

projects = create_folder(db, library_id=lib.id, name="Projects")
nlp = create_folder(db, library_id=lib.id, name="NLP", parent_folder_id=projects.id)

add_work_to_folder(db, library_id=lib.id, work_id=work.id, folder_id=nlp.id)
list_work_ids_in_folder(db, nlp.id)   # [work.id, ...]——裸 id，不是 WorkDTO
list_folders_for_work(db, work.id)    # [FolderDTO, ...]

move_folder(db, nlp.id, new_parent_folder_id=None)  # 提到根层
delete_folder(db, projects.id)        # 级联删子文件夹，不删任何 work
```

异常：`FolderNotFound`（404）、`WorkFolderNotFound`（404）、`FolderCycle`
（409——把文件夹移到它自己的子孙下面）。

## 设计要点

### 为什么 `list_work_ids_in_folder` 只返回裸 `int`，不是 `WorkDTO`

`domain.folders` 不 import `domain.works`——`work_id` 在 `work_folders` 表上
是裸整数，没有数据库外键（和 `library_id` 同理，见下文）。把 id 展开成完整
的文献记录需要调用 `domain.works.get_work`，那是**调用方**（`library_browse`）
该做的事，不是本模块的职责。本模块对 work 的全部知识就是"它有一个整数 id"。

### `library_id` / `WorkFolder.work_id` 为什么不是数据库外键

同一条通用约定，详见 `domain/libraries/README.md`
「`LibraryMember.account_id` 刻意不是数据库外键」一节：跨 domain 的整数引用
全部裸存，只有同一 domain 内部的引用（`Folder.parent_folder_id` 的自引用、
`WorkFolder.folder_id` → `folders.id`）才是真 FK。

### `move_folder` 的环检测

树形结构唯一需要小心的操作就是"移动"——把一个节点挪到它自己的子孙下面会在
`parent_folder_id` 链上造出一个环，往上找父节点会死循环。`_is_ancestor_or_self`
从候选新父节点开始，沿 `parent_folder_id` 一路往根走，如果走到了"正要移动的
那个节点"，说明新父节点是它的子孙（或者就是它自己），拒绝并报 `FolderCycle`。
`create_folder` 不需要这个检查——新建的节点天然没有子孙，不可能成环。

### `delete_folder` 为什么级联删除子文件夹，但绝不碰 work 本身

文件夹和 work 是两种不同性质的东西：文件夹本身没有软删概念（架构文档里只有
`works` 才有回收站），所以"删除一个文件夹"就是真删，递归到它的全部子文件夹
——否则删除后会留下一堆"父节点不存在"的孤儿文件夹，没有任何办法通过正常
UI 再触达它们。但这个级联的边界严格止步于 `folders` / `work_folders` 两张表：
work_folders 的关联行被清掉只是说"这篇文献不再挂在这个文件夹下"，works 表
那条记录是否存在完全不受影响——这是"删文件夹不删 work"这条产品约束在本模块
里的体现。级联删除时子文件夹先于父文件夹被删（自底向上），这样即便某个真实
数据库对自引用 FK 做了强校验，删除顺序也不会违反约束。

### 为什么不限制同一父节点下文件夹名重复

v1 没有 `UniqueConstraint(library_id, parent_folder_id, name)`——Paperpile
类工具里允许同名文件夹并不罕见（不同场景下随手建的"TODO"文件夹），强制唯一
是没有被要求的额外复杂度，真要加也只是加一个约束，不影响已有调用方。

### `list_folders` 的 `parent_folder_id` 参数为什么用哨兵区分"不传"和"传 None"

传 `None` 的含义是"只看根层"（`parent_folder_id IS NULL`），和"不传"的含义
"看整个库的所有层级"是两件不同的事，必须用 `_UNSET` 哨兵区分，不能让
`None` 同时代表两种语义——这是 `domain/works.update_work` 里已经用过的同一
个哨兵模式，这里复用而不是重新发明。

## 依赖方向

`infra/db`（`Base`、`new_memory_session`）+ `infra/errors`（`NotFound` /
`AppError`）。**不依赖任何其它 domain / adapters / features**——不 import
`domain.works`、不 import `domain.libraries`。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 同名文件夹唯一性约束 | v1 不做；见上文 |
| 文件夹本身的软删/回收站 | v1 不做——只有 `works` 有这个概念；删文件夹就是真删 |
| 批量把一批 work 一次性加入/移出文件夹的编排、重名策略 | `features/organizing`（批量操作 + job 进度） |
| 按文件夹筛选文献列表、未归档视图、侧栏树的渲染 | `features/library_browse` |
| 验证 `work_id` 对应的文献确实存在 | 调用方的职责（本模块不 import `domain.works`，见设计要点） |
| 文件夹级别的权限（比如只读文件夹） | v1 不做；库级权限已经由 `domain/libraries` 覆盖，没有比库更细的授权模型 |
