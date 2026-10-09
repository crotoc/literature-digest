# domain/notes

每篇文献最多一条自由文本笔记。全模块只有一张表 `work_notes`，是目前架构里
最简单的 domain 模块。

## 对外承诺

```python
from domain.notes import WorkNoteDTO, get_note, set_note, list_notes_for_works

note = set_note(db, library_id=lib.id, work_id=work.id, content="值得复现的消融实验")
get_note(db, work.id)                      # WorkNoteDTO | None
set_note(db, library_id=lib.id, work_id=work.id, content="")  # 清空，返回 None

list_notes_for_works(db, [w1.id, w2.id, w3.id])  # {work_id: WorkNoteDTO, ...}，只含写过笔记的
```

没有定义任何异常。

## 设计要点

### 为什么 `get_note` 返回 `None` 而不是抛"找不到"异常

这是本项目目前唯一一个"主查询函数不抛 NotFound"的 domain 模块，是刻意的：
"这篇文献还没有笔记"是绝大多数文献永远会停留的正常状态（不是例外情况），
强迫调用方为一个几乎总会发生的"错误"写 try/except 很别扭。对比
`domain/works.get_work`——一个不存在的 `work_id` 才是真正的异常状态，两者
的"找不到"在语义上不是一回事：前者是"这个资源的某个可选附属信息还没填"，
后者是"你引用的东西根本不存在"。

### 为什么"一篇文献最多一条笔记"，不是一个笔记列表

架构文档和旧单体都只把笔记当成一个可编辑的自由文本字段（像个备注框），不
是时间线式的多条日志（那是 `domain/works/work_provenance` 在做的事，语义
完全不同：溯源是系统自动记的、只增不改的历史；笔记是用户手写的、随时覆盖
的当前状态）。`UniqueConstraint("work_id")` 把这个约束落到数据库层。

### `set_note` 为什何清空等于删行，而不是存一个空字符串

避免库里堆满"曾经被创建过，但内容早就被清空"的空壳行——那些行对任何查询
都没有信息量，留着只会让 `work_notes` 表比实际"有笔记的文献数"虚增。清空
后 `get_note` 返回 `None`，和"这篇文献从来没人写过笔记"是完全相同的外部
可观察状态，调用方不需要、也没有办法区分这两种历史。

### 为什么同时提供单条 `get_note` 和批量 `list_notes_for_works`

卡片列表视图（旧页面梳理①"卡片 5 个可展开面板"之一就是笔记）一屏要同时
渲染几十张卡片的笔记预览，逐条调 `get_note` 会是 N+1 查询；`annotating`
feature 编辑单篇笔记时只需要一条，没必要为了单条场景强迫调用方套一层
批量接口。两个函数分别服务这两种天然不同的调用形态。

## 依赖方向

`infra/db`（`Base`、`new_memory_session`）。**不依赖任何其它 domain /
adapters / features**——不 import `domain.works`，`work_id` 是裸整数。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 笔记的历史版本/多条时间线 | v1 不做；语义上和 `domain/works/work_provenance` 的"只增不改日志"不同，真要做是另一张表 |
| 富文本/Markdown 渲染 | v1 当作纯文本存储；渲染是 `app/pages` 的展示层职责，不是本模块的数据形状 |
| 笔记的协作编辑/多用户并发冲突处理 | v1 不做；`set_note` 是整条覆盖写，后写覆盖先写，没有版本冲突检测 |
| 按笔记内容全文检索 | 等真的需要再加；v1 没有被要求 |
