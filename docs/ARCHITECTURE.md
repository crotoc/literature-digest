# 架构（仓库内摘要）

**设计真源**在 Obsidian：`Work/Project/literature-digest/refs/v2-module-architecture.md`
（三层判定标准 / 依赖方向 / 7 条 lint / 完整模块清单 / 旧实现五页功能梳理 / 10 条规避清单）。
本文件只放写代码时要反复看的那几条，**不建第二份设计文档**。

## 分层判定（机械执行）

| 判定 | 归属 |
|---|---|
| 有自己的表 | `domain/` |
| 可插拔、同类多实现、删一个其余全绿 | `adapters/` |
| 无表、单一技术能力、纯函数或纯 IO | `caps/` |

## 依赖方向

```
infra/     ← 谁都能用
caps/      ← 只能用 infra；caps 之间允许单向依赖（有向无环）
adapters/  ← 只能用 infra + caps；禁止 import 任何 domain / features
domain/    ← 能用 infra + caps + adapters；跨 domain 只许 import 对方 contract
features/  ← 能用 infra + caps + adapters + 任意多个 domain；features 之间互不 import
app/       ← 只调 features / domain 的 contract；唯一允许横向组合多个 feature 的层
```

## 每个模块四件套

- `contract.py` —— 唯一允许被外部 import 的东西（DTO + 签名 + 异常 + 设置声明）
- `service.py` —— 接收 `Session`，不自己造 session
- `models.py` —— 仅 domain 层有，只有本模块 service 能 import
- `tests/` —— **必须能单独跑绿**
- `README.md` —— 对外承诺 + 依赖方向 + **刻意裁剪掉的范围**

## 装配机制

- **页面注册表**：`app/pages/<x>/__init__.py` 导出 `router` + `nav`，`app/shell/registry.py` 发现并挂载。加页面 = 新建目录，不改 shell。
- **设置注册表**：每个模块在 `contract.py` 声明设置表单片段，`app/pages/settings` 只负责拼。加设置项不改设置页。
- **`app/api/extension/`**：浏览器扩展是第二个客户端，不是 feature。

## 写代码时最容易犯的几条（完整 10 条见 refs）

1. 判断 identifier 是否存在**直接查表**，不信 SQLAlchemy relationship 缓存
2. `init_db()` 必须晚于 `registry.discover()`（否则新进程首次请求 no such table）
3. 不用 `@app.exception_handler(Exception)`——会丢 `X-Request-Id`；异常处理在自家中间件里
4. `container_class` 是可覆盖的 Jinja block，不要写死
5. 不要 API + form 双套路由，一条路由 + HTMX
6. 同类东西不要各写一套 CRUD（旧代码四套几乎一样的 → `domain/connections`）
7. 设置值不塞业务表的 JSON 列
8. 不造不入库的影子 ORM 对象，不用魔法主键
9. 状态和错误消息分两个字段（`status` + `reason` + 显式转移表）
