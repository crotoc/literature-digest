# features/logs_viewer

日志查看：读 / 清 / 导。

对应旧单体设置页的 `logs` 分区（见 refs 的页面梳理②）。这是 v1 范围里
最后一个 feature——完成它之后 v1 的全部 `features/` 模块就齐了，下一步
进入 `app/` 装配层。

## 对外表面

- `LogEntryDTO(ts, level, logger, msg, request_id, exc, extra)`
- `list_entries(*, log_file, min_level=None, search=None, request_id=None, since=None, until=None, limit=200, offset=0, policy=DEFAULT_POLICY) -> list[LogEntryDTO]`
  —— 最新的排前面，给查看页用。
- `clear_log(*, log_file) -> int`
  —— 清空日志文件，返回清空前的字节数。
- `export_log(*, log_file, min_level=None, search=None, request_id=None, since=None, until=None, policy=DEFAULT_POLICY) -> str`
  —— 导出 JSON Lines 文本，按文件原本的时间顺序（旧→新）。

## 依赖方向

`infra/logging`（写）+ `caps/redact`（读取时脱敏）。**本模块不开表**，
也不依赖任何 `domain/`——这是和前面十一个 `features/` 模块都不一样的
地方：它操作的是文件，不是数据库行。

## `infra/logging.py` 原来写不了文件——这次补上了

读 plan 原文的 caps 复核结论才发现一个隐藏的前提缺口：`logstore`
被判定"删除，和 infra/logging 重叠：写在 logging，读/清/导在
features/logs_viewer"，这句话的前提是 `infra/logging` 已经把日志写到了
一个 `logs_viewer` 能读的地方——但实际读源码后发现 `infra/logging.
configure()` 原来**只接一个 `StreamHandler(sys.stdout)`，没有任何文件
落盘**，`infra/config.Settings` 也没有 `log_file` 字段。和之前
`features/connection_setup` 发现两个 adapter 缺 `check()` 是同一类情况
——依赖方缺了一个结构性前提，这次顺手补上：

- `infra/config.Settings` 新增 `log_file: Path`，默认
  `REPO_ROOT/data/app.log`。和 `blob_root` 一样**常驻、非 `None`**——
  不做"不配置就没有日志文件"的可选分支，本模块的读/清/导三个操作永远
  有一个具体文件可以指向。
- `infra/logging.configure(level, log_file=None)` 现在可以额外接一个
  `log_file` 参数：给了就在原有的 `StreamHandler` 之外再加一个
  `FileHandler`，两者共用同一个 `JsonFormatter`——保证落盘格式和屏幕上
  看到的一致，`list_entries`/`export_log` 解析的就是这份格式。
- `app/main.py` 的调用点从 `app_logging.configure(config.log_level)`
  改成 `app_logging.configure(config.log_level, config.log_file)`，是
  这次改动唯一触达的装配层代码。

这三处改动之外，确认了全量 1340 个既有测试（含改动前）全部通过，证明
"默认行为不变、只是多了一个可选 handler"这个判断站得住。

## 读取链路里处处脱敏，不是只脱 `msg`

每一行先整体解析成原始 dict，**一次性**整体过 `caps.redact.
redact_mapping`，再拆成 `LogEntryDTO`——不是分别对 `msg` 调
`redact_text`、对 `extra` 调 `redact_mapping` 两次。好处：`redact_mapping`
本身的"键名敏感 → 整体替换""键名是 URL → 走 `redact_url`"这些规则对
顶层任意字段都生效，不需要本模块自己再判断哪个字段该走哪条脱敏路径。

`extra` 字段就是 `redact_mapping` 结果里刨除 `ts/level/logger/msg/
request_id/exc` 六个已知字段之后剩下的部分——不是重新脱敏一遍。

## v1 只有 pattern-based 脱敏，没有接 `known_values`

`caps/redact` 自己的文档说得很清楚：精确匹配已知密钥原文最可靠，正则
模式识别是兜底。要用上"已知值"这一层，需要先解密 `domain.connections`
表里存的凭据原文传给 `RedactionPolicy(known_values=...)`——那是业务层
知识，而 `logs_viewer` 按 plan 的依赖声明刻意不连任何 `domain/`。所以
本模块只能用得上兜底这一层，这是架构边界带来的权衡，不是没想到。真要
补上"已知值"这层，得在 `app/` 装配层把两者接起来（调 `connection_setup`
拿到解密后的凭据列表，再传给这里的 `policy` 参数），不属于本模块自己
该做的事。

## 为什么坏行直接跳过、不抛异常

日志文件可能正被 `FileHandler` 并发追加，读到的最后一行有可能是还没
写完的半行 JSON；也可能有旧格式遗留的非 JSON 行。`_parse_line` 对
"不是合法 JSON"和"解析出来不是对象"（比如一行孤立的字符串/数字）都
直接跳过，不让一行坏数据拖垒整个查看请求。

## `clear_log` 为什么可以放心从外部截断

`infra.logging` 的 `FileHandler` 默认 `mode="a"`（追加），操作系统层面
每次写入都重新定位到当前文件末尾（`O_APPEND`），不依赖进程内部缓存的
偏移量。所以从外部把文件截断为空是安全的：哪怕 handler 还持有着那个
文件描述符，下一次写入也会从新的（空）文件末尾开始，不会出现"写到旧
偏移量、文件中间留空洞"的问题。这不是本模块自己加的保护，是
"`a` 模式 + `O_APPEND`"这个操作系统事实的自然结果，写在这里是为了
让以后改动 `infra/logging` 的人知道这条隐含约束不能破坏。

## `list_entries` 倒序、`export_log` 不倒序

`list_entries` 是给"看"用的，约定最新的在前面，符合日志查看页的习惯。
`export_log` 是给"导出成文件"用的，约定保持文件原本的时间顺序（旧→新）
——导出的 `.log` 文件应该能从头往下读，和原始文件语义一致，不该让人
拿到一份倒着排的日志去手动倒回来。

## 筛选字段的已知局限

- `since`/`until` 按字符串字典序比较 `ts`，不解析成真正的 datetime——
  这在 `JsonFormatter` 固定输出 `%Y-%m-%dT%H:%M:%S%z` 格式、同一进程
  时区不变的前提下是对的（字典序和时间顺序一致），但如果以后
  `JsonFormatter` 的时间格式改了，这里要跟着改，不是独立正确的逻辑。
- `min_level` 只认 `DEBUG/INFO/WARNING/ERROR/CRITICAL` 五个标准
  `logging` 级别名；不认识的级别名当作最低级（0），不会被任何
  `min_level` 过滤掉——宁可多显示，不做"不认识就隐藏"的假设。

## 刻意裁剪的范围

- **不做日志轮转/归档**：`clear_log` 只是整体清空，不切分成按日期/大小
  归档的多个文件——轮转策略如果要做，应该是部署层面（如
  `logrotate`）的事，不属于这个 feature。
- **不做实时推送（WebSocket/SSE 跟随日志尾部）**：`list_entries` 是
  一次性查询，查看页如果要"自动刷新"，是前端按固定间隔重新请求，不是
  本模块的职责。
- **`since`/`until` 不提供"最近 N 分钟"这类相对时间的便捷封装**：
  由调用方（`app/pages/settings`）自己把相对时间换算成绝对 `ts` 字符串
  再传进来。
