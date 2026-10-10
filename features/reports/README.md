# features/reports

E4 的报告漏斗编排：将一次 discovery run 的候选快照物化为报告，逐条标题筛选，再对保留条目进行摘要分析。

- 每轮 discovery run 只有一份报告；重复物化不会重复报告、条目或投递历史。
- 标题筛选和摘要分析逐条失败隔离：单条 LLM 错误保留在条目上，报告阶段为 `partial`，其余条目继续。
- 每次阶段运行都有一条 `domain/jobs` 记录，保存 total/done/failed 等进度快照。

依赖 `adapters/llm`、`domain/{intents,reports,jobs}`；不管理 AI profile、不发送消息、不回写文章标签。这些属于装配层、`features/delivery` 和已有的 AI 富化能力。
