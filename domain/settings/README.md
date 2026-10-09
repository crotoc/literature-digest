# domain/settings

站点级 + 账号级两张设置表，行式存储，三级回退（账号 → 站点 → 代码默认值）。

## 为什么是行式 `(module, key) -> value_json`，不是业务表 JSON 列

旧单体把设置值直接塞进业务表的 JSON 列——`/settings/storage` 那几个设置项
（`library_name` / `main_pdf_template` / `attachment_template` / `conflict_policy`）
全存进了 `workspace.fulltext_proxy_json`。这是规避清单第 7 条明确列出的坑：
设置项和"工作区"这个业务实体本来毫无关系，混进同一个 JSON 列之后，想单独
读/改某一项设置就必须先读出整个 blob 再回写整个 blob，而且任何模块想加一个
新设置项都要去改 `workspace` 表的结构或者那个 JSON 的隐式 schema。

行式存储把每一条设置变成独立的一行 `(module, key, value)`，新增设置项不需要
改任何表结构——这也是"加设置项不改设置页"这条装配层设计原则在存储层的
落地（见 `app/pages/settings` 的拼装机制）。

## 三级回退：账号 → 站点 → 代码默认值

```python
resolve_setting(db, module="exporting", key="default_csl_style", account_id=alice.id, default="apa")
# → SettingResolution(value=..., source="account" | "site" | "default")
```

三级含义：

1. **账号级**（`settings_account` 表）：这个账号自己覆盖过这项设置。
2. **站点级**（`settings_site` 表）：没有账号覆盖，但站点管理员配置了全站值。
3. **代码默认值**：前两级都没有，落到调用方传入的 `default` 参数——**这一级
   根本不落库**，它就是声明这个设置项的那个 feature 在自己代码里写的默认值。

`SettingResolution` 除了 `value` 还带 `source`，是刻意设计——设置页的 UI 需要
知道"用户现在看到的这个值，是不是他自己改过的"，以便显示类似"（继承自站点
设置）"或"（已覆盖）"的提示，以及决定"重置为站点默认"按钮要不要出现。

`account_id=None` 时直接跳过账号级（未登录场景，或这项设置本来就不支持账号
覆盖——这类判断留给调用方，本模块不区分"这项设置允不允许账号覆盖"）。

## 本模块不校验任何设置项的类型/取值范围

`value_json` 可以是字符串、数字、布尔值、字典、列表——任何 JSON 可序列化的
东西，本模块原样存取，不做任何 schema 校验。字段类型/合法取值这些约束属于
各 `features/<x>` 在自己 `contract.py` 里声明的设置表单定义，由
`app/pages/settings` 在渲染/提交表单时校验，不该下沉到存储层。

## API

```python
resolve_setting(db, *, module, key, account_id=None, default=None) -> SettingResolution

get_site_setting(db, *, module, key) -> object | None        # 不回退，没有就是 None
set_site_setting(db, *, module, key, value) -> SiteSettingDTO  # upsert
delete_site_setting(db, *, module, key) -> None               # 幂等，不存在也不报错
list_site_settings(db, *, module=_UNSET) -> list[SiteSettingDTO]

get_account_setting(db, *, account_id, module, key) -> object | None
set_account_setting(db, *, account_id, module, key, value) -> AccountSettingDTO
delete_account_setting(db, *, account_id, module, key) -> None
list_account_settings(db, *, account_id, module=_UNSET) -> list[AccountSettingDTO]
```

`set_*` 都是 upsert——调用方不需要先查一遍判断是该 insert 还是该 update。
`delete_*` 都是幂等的——"确保这条覆盖不存在"这个操作本身就该是幂等的，不需要
调用方先判断存不存在再决定要不要调。这两点和 `domain/connections.clear_secret`
"清掉即可，不管之前有没有"是同一种设计倾向。

## 依赖方向

只依赖 `infra.db`（`Session` 类型）。不依赖任何其他 `domain/*`、`caps/*`、
`adapters/*`、`features/*`。

## 刻意裁剪的范围

| 没做的事 | 原因 |
|---|---|
| 库级设置 | 按原计划设计，设置全部是账号级共享（跨所有库），只有 `folders`/`tags` 是库级私有数据，不需要第四级回退 |
| 设置项的类型/取值范围校验 | 属于各 feature 声明设置表单时的职责，不是存储层的事 |
| 设置变更历史/审计日志 | v1 不需要；真要加，是在这张表之上叠一张 `settings_history`，不改这两张表的结构 |
| 批量导入/导出设置快照 | 没有明确需求，属于过度设计；真有需要可以后续在 `features/` 层加 |
