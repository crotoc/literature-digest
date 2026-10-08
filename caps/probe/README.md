# caps/probe

纯协议层。无表、无 IO、无网络——这个 cap 不知道怎么测任何一种具体连接，
只规定"测完之后交出来的形状"和"怎么调用才算守规矩"。

## 为什么要这么薄

`domain/connections` 统一存了四类连接（ai_profile / telegram_destination /
source_credential / download_proxy），每一类的"测试连接"按钮点下去要做的事
完全不同（查 crossref 的一个已知 DOI、给 telegram 发一条测试消息、ping 一个
代理）。如果把"怎么测"也塞进这个 cap，就会变成一个按 `kind` 分支的大 switch，
而且 `caps/probe` 不能 import `adapters/*`（caps 只能依赖 infra），没地方放
这些具体知识。

所以"怎么测"留在各 `adapters/*` 自己导出的 `check(config) -> ProbeResult`里；
这个 cap 只提供双方都要用到的公共部分：结果的形状（`ProbeResult`）和一个
强制调用约定的小工具（`run_check`）。`adapters/` 和 `domain/connections`
都在这个 cap 之上，靠它们"共同认识"的这一份薄协议解耦，不需要互相 import。

## 对外承诺

```python
from caps.probe import ProbeResult, ok, fail, run_check, CheckFn

# adapter 这边：
def check(config: SourceCredentialConfig) -> ProbeResult:
    try:
        resp = httpfetch.fetch(f"https://api.crossref.org/works/{config.test_doi}")
    except HttpFetchError as exc:
        return fail(f"连不上 crossref: {exc}")
    if resp.status_code != 200:
        return ok(f"API 可达，但状态码是 {resp.status_code}", status_code=resp.status_code)
    return ok("连接正常")

# features/connection_setup 这边：永远走 run_check，不直接调 adapter 的 check
result = run_check(adapter.check, connection_config)
if result.ok:
    ...
```

`ProbeResult`：`ok: bool` + `message: str`（给人看的一句话）+ `detail`
（自由格式的附加信息——状态码/延迟/异常类型，显示层自己挑要展示的字段）。

## `run_check` 在强制什么

`check()` 是别人（各 adapter 作者）实现的，`run_check` 不信任它自觉遵守约定：

- **忘了自己 try/except、测试过程中真的抛了异常** → 接住，包成 `ProbeResult(ok=False, ...)`，
  不让异常一路冒到页面请求处理里把整个请求炸掉——点一下"测试连接"不该导致 500
- **返回的不是 `ProbeResult`**（比如手滑写成 `return True` 或忘了 `return`）→
  同样包成失败结果，而不是让这种格式错误的返回值混进调用方的
  "测试通过/不通过"逻辑，制造一个看起来正常但语义错误的 `True.ok` 之类的bug

两种情况都在 `detail` 里留痕（`exception_type` / `actual_type`），方便调试
"为什么这个连接测试总是失败"时不用去猜。

## 依赖方向

标准库（`dataclasses` `typing`）。**不依赖任何 infra / domain / adapters /
features，也不依赖别的 cap。** 这是这个 cap 存在的全部意义——它必须严格地
在 `adapters/` 和 `domain/connections` 两者之下，否则两边就没有公共语言。

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 任何具体连接怎么测（crossref/pubmed/telegram/llm/proxy） | 各 `adapters/*` 自己的 `check()` |
| 把测试结果存进数据库（`last_checked_at` 等） | `domain/connections` |
| 超时控制 | `check()` 实现自己决定（通常会借 `caps/httpfetch` 的超时） |
| 重试 | 同上，测试连接不该自动重试掩盖间歇性故障 |
| 异步/并发测试多个连接 | v1 不需要；`features/connection_setup` 要做的话在它自己那层做 |
