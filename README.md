# literature-digest-v2

Paperpile 式文献管理工具（服务端 + 浏览器扩展）。第三轮重构，代码零复用。

架构真源：Obsidian `Work/Project/literature-digest/refs/v2-module-architecture.md`，
仓库内摘要见 `docs/ARCHITECTURE.md`（不建第二份设计文档）。

## 分层（机械判定，无解释空间）

| 判定 | 归属 |
|---|---|
| 有自己的表 | `domain/` |
| 可插拔、同类多实现、删一个其余全绿 | `adapters/` |
| 无表、单一技术能力、纯函数或纯 IO | `caps/` |

`features/` 是超模块（组合 caps + domain + adapters），`app/` 只做装配。

## 开发

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env     # 改 APP_SECRET_KEY
scripts/dev-server.sh    # http://127.0.0.1:18002
scripts/lint.sh          # 7 条依赖方向检查
pytest                   # 全量
```
