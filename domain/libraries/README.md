# domain/libraries

多库支持：库本身 + 成员关系。全系统所有"按库划分的数据"（works / folders /
tags / notes / attachments）在碰任何一条数据之前，都要先过本模块的
`resolve_scope()`——它是整个多库隔离模型的唯一入口。

## 对外承诺

```python
from domain.libraries import (
    LibraryDTO, MembershipDTO, ROLES, DEFAULT_ROLE,
    create_library, get_library, rename_library, list_libraries_for_account,
    resolve_scope, list_members, add_member, remove_member, update_member_role,
)

# 注册流程：建库 + 把注册者设成 owner，一个函数、一个事务
library = create_library(db, owner_account_id=account.id, name="Alice's library")

# 其它模块碰任何 library_id 范围的数据前，先问这一句
membership = resolve_scope(db, account_id=account.id, library_id=library.id)
# 不是成员 / 库不存在 → 同一个 LibraryNotFound，不泄露哪种

members = list_members(db, library_id=library.id, acting_account_id=account.id)
add_member(db, library_id=library.id, account_id=other.id, acting_account_id=account.id, role="member")
remove_member(db, library_id=library.id, account_id=other.id, acting_account_id=account.id)
update_member_role(db, library_id=library.id, account_id=other.id, new_role="owner", acting_account_id=account.id)
```

异常：`LibraryNotFound` / `MembershipNotFound`（均继承 `infra.errors.NotFound`，
404）、`DuplicateMembership`（`Conflict`，409）、`NotLibraryOwner`（403）、
`LastOwnerRequired`（409）。

## 设计要点

### `resolve_scope` 为什么把"库不存在"和"不是成员"报成同一个异常

如果分开报，一个账号可以靠"库 id 不存在" vs "库 id 存在但我进不去"两种不同
的报错，枚举出这个系统里到底有哪些库 id 是真的——这和 `domain/accounts`
里"账号不存在"和"密码错误"合并成同一个 `InvalidCredentials` 是同一类防御，
一以贯之。相对地，`list_members` / `add_member` / `remove_member` /
`update_member_role` 里用到的 `MembershipNotFound` 不做这种合并——因为能走
到这几个函数时，acting 账号已经先过了 `resolve_scope` 的 owner 校验（证明
了自己对这个库的可见权），"库里到底有没有某个账号"对一个已验证的 owner
不是需要隐藏的信息。

### 权限粒度：v1 只有 owner / member 两级，owner 的特权只覆盖成员管理

加成员、移除成员、改角色，这三件事都要求 acting 账号是 owner。但**改库名
不要求 owner**——任何成员都能改（`rename_library` 内部只调
`resolve_scope` 确认"你至少是个成员"，不检查角色）。这个不对称是故意的：
改名不是一个有安全后果的操作（谁都能看到库名，改错了随便改回来），而成员
管理（谁能进来、谁能把别人踢出去、谁说了算）才是真正需要守门的部分。v1 不
做更细的权限（比如"能读不能写"的只读成员、按文件夹/标签的局部权限）——见
下方裁剪表。

### 为什么不能移除或降级最后一个 owner

`LastOwnerRequired` 守住一个不变量：库永远至少有一个 owner。如果允许把
唯一的 owner 移除或降级成 member，这个库就会进入"没有任何账号能再管理
成员名单"的死锁状态——没人能把自己或别人重新设回 owner，库就报废了。
`remove_member` 和 `update_member_role` 各自单独检查这条（不是共享一个
辅助函数去重——两处检查都只有两行，独立写比抽一个共享函数更直接）。

### `LibraryMember.account_id` 刻意不是数据库外键

它是裸 `int`，不是 `ForeignKey("accounts.id")`——虽然 `library_id` 指回
本模块自己的 `libraries` 表**是**真 FK。原因：跨 domain 的 DB 级 FK 会要求
任何建 `library_members` 表的地方都必须先有 `accounts` 表存在，这会把
"domain 之间只许经由 contract 互相访问"的 Python 级规则在 schema 层面
悄悄绕回去——而且会让 `domain/libraries/tests` 没法在不 import
`domain.accounts.models` 的情况下独立建表、独立跑绿（lint 规则 3 本来就
禁止 `domain/libraries/**` 出现 `from domain.accounts.models import ...`）。
账号确实存在这件事由调用方保证：`create_library(owner_account_id=...)` /
`add_member(account_id=...)` 只会被已经拿到一个真实 `AccountDTO.id` 的
调用方（`features/accounts_auth`）调用。**这是本项目跨 domain 引用的通用
约定，不止这一处**：FK 只建在同一个 domain 模块内部，跨 domain 的整数引用
全部裸存。

## 依赖方向

`infra/db`（`Base`、`new_memory_session`）+ `infra/errors`（`NotFound` /
`Conflict` / `AppError`）。**不依赖任何其它 domain / adapters / features**——
包括不依赖 `domain/accounts`（上一节解释了为什么）。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 比 owner/member 更细的权限（只读成员、按文件夹/标签的局部权限） | v1 不做；真要做需要先定义出比"两级角色"复杂得多的授权模型 |
| 删除库 | v1 不做——没有对应的产品入口（页面梳理里没出现"删库"），真要支持要先定义级联到 works/folders/tags/notes/attachments 的策略，比 works 的回收站更复杂 |
| 邀请链接 / 待接受的邀请状态 | v1 的 `add_member` 是立即生效的，没有"邀请中"这个中间态；要做就是在本模块加一张 `library_invitations` 表 |
| 库内数据条数 / 成员数上限 | v1 不设上限；要做的话是 `domain/usage` 的配额知识，不是这里的 |
| 退出库时如果是最后一个成员（不只是最后一个 owner）要不要连带删库 | v1 不处理这种级联；一个库可以处于"所有成员都退出了，但库和它的数据还在"的状态，不视为 bug |
