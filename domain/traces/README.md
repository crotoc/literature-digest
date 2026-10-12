# domain/traces

记录和展示全文下载流程的诊断轨迹。

## 作用范围

- `Trace`：一次下载尝试的元数据（work_id、job_id、源 URL、最终状态）
- `TraceStep`：每一步的细粒度记录（resolver 推导、fetcher 尝试、用户交互、错误和建议）

## 敏感信息处理

所有 `TraceStep.observation` 的内容在存库前过 `caps/redact` 脱敏：
- URL 中的 cookie、token、auth 参数移除
- 用户邮箱、机构名只保留泛化信息
- 页面 HTML 片段不存储

展示时 `features/diagnostics` 再过一遍脱敏（纵深防御）。

## 约定

- `account_id`、`library_id`、`work_id`、`job_id`：跨 domain 引用，刻意不建数据库外键
- `final_status` 枚举：pending / success / failed / user_action_required
- 一篇文献可有多条 `Trace`（多次尝试）；一条 `Trace` 可有多条 `TraceStep`（多步过程）
