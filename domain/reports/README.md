# domain/reports

一次研究意图发现运行的报告、报告条目与每个投递目的地的持久记录。

## 对外承诺

- 一份报告保留候选、标题筛选与 AI 分析三个漏斗阶段的状态和计数。
- 同一报告内的 `(source, source_id)` 条目幂等；同一报告到同一 connection 的投递记录也幂等。
- 每个条目的标题筛选和 AI 分析可独立失败；调用方据此汇总报告阶段的 `partial` 状态。
- 本模块只持久化跨模块 id 与不透明 JSON；不解释 intent、connection、LLM 或 delivery adapter 的语义。

## 依赖边界

`service.py` 只依赖本模块的 `models.py`、SQLAlchemy 和 `infra`。外部只能从
`domain.reports` 导入 contract 暴露的 DTO、异常和函数，不能导入 ORM 模型。

## 刻意裁剪

- 不创建或校验跨 domain 的 intent、discovery run、connection。
- 不运行标题筛选、AI 分析、渲染或实际投递；这些属于 E4 的 features/adapters。
- 不在本模块 commit/rollback；事务由调用者的 Session 生命周期控制。
