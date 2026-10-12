# features/diagnostics

全文下载诊断轨迹的只读查询与展示。

## 职责

- 读取 `domain/traces` 中的轨迹和步骤
- 再过一遍脱敏（纵深防御——虽然存库时已脱敏）
- 为 UI 提供展示友好的摘要

## 接口

- `get_trace_for_display(trace_id)` → DiagnosticTraceDTO
- `list_traces_for_work_display(work_id, limit)` → list[DiagnosticTraceDTO]

## 约定

- 只读，不修改 traces
- 所有输出都再过 `caps/redact`（纵深防御）
