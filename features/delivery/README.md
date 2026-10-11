# features/delivery

E4 的 Telegram 报告投递：管理 `telegram_destination` 连接，将报告渲染成安全 HTML 文本，按 Telegram 限制分段发送，并把每个目的地的状态和 message id 独立记录。

- 已成功的 `(report, destination)` 会跳过重发。
- refresh 使用既有 message id 原地编辑；分段数变化时明确拒绝，避免误编辑。
- 单个目的地失败会落为 failed，其余目的地仍继续；仅全部失败才抛投递错误。

邮件 adapter 保持独立可测；当前连接模型只定义 `telegram_destination`，因此邮件目的地不在本 feature 的 E4 接入范围。
