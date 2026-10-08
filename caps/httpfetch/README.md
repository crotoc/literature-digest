# caps/httpfetch

对外发 HTTP 请求的统一出口。无表、无业务逻辑，单测不碰真实网络
（HTTP 层用 `httpx.MockTransport`，等待用假 `sleep()`，SSRF 检查用 IP 字面量
或注入 `resolve()`）。

## 为什么是 cap：谁会用它

`adapters/sources/*`（查 DOI/PMID）、`adapters/resolvers/*`（推导 PDF 候选链接后
试探性请求）、`adapters/fetchers/direct.py`（无身份直接下载 OA 全文）、
`adapters/llm/*`（HTTP 兜底的模型 API）——这些都要"发一个请求，内置超时/重试/
429 退避/UA/限速/SSRF 防护"，各写一遍必然参差不齐，所以下沉成一个 cap。

## 对外承诺

```python
from caps.httpfetch import fetch, RateLimiter, FetchResult

result = fetch("https://api.crossref.org/works/10.1038/s41586-019-1234-5")
result.status_code  # int，4xx/5xx 默认不抛异常，照常返回给调用方判断
result.headers       # dict[str, str]，key 已转小写
result.content       # bytes
result.url           # 跟完重定向后的最终 URL
result.attempts      # 总尝试次数（含重试、含每一跳重定向）

# 给同一个数据源的多次调用复用的限速器（调用方持有，不是 fetch() 自己维护全局状态）
limiter = RateLimiter(min_interval_seconds=1.0)
fetch(url, rate_limiter=limiter)

# 需要把 4xx/5xx 当错误处理时显式要求
fetch(url, raise_for_status=True)  # 抛 HttpStatusError，带 .status_code / .result
```

异常：`UnsafeURL`（SSRF 拦截）/ `NetworkError`（DNS/连接/超时，重试耗尽后）/
`TooManyRedirects` / `HttpStatusError`，共同基类 `HttpFetchError`。

## 设计决策

### 4xx/5xx 默认不是异常

这一层不替上层决定"错误状态码算不算失败"——404 对 `metadata_lookup` 可能是
正常的"这个 DOI 没查到"，不该每次都包一层 try/except。只有传输层真的没拿到
响应（DNS 失败、连接被拒、超时、SSRF 拦截、重定向太多）才抛异常。需要"4xx/5xx
就当失败"的调用方用 `raise_for_status=True` 显式要。

### 429 退避优先用 `Retry-After`，没有才退回指数退避

出版商/数据源 API 明确告诉你该等多久时，没理由自己猜。`Retry-After` 只支持
秒数格式，不支持 HTTP-date（`Wed, 21 Oct ...`）——遇到后者退回指数退避，
不是遗漏，是没必要为一个少见格式多引入一个日期解析依赖。

### 重定向手动跟随，不用 httpx 自带的 `follow_redirects`

这是 SSRF 防护里最容易被漏掉的一环：出版商页面 / ezproxy 改写 URL 的跳转目标
**不是调用方自己拼的**，可能被诱导指向内网地址。如果交给 httpx 自动跟随，
初始 URL 过了安全检查之后的每一跳都不会再被检查。本模块手动接管重定向循环，
**每一跳都重新跑一次 `check_url_safety`**，包括相对 Location 解析之后的结果。

### SSRF 防护是单次检查，不是防 DNS rebinding

`check_url_safety` 检查当前这次解析出的 IP 是否安全，但"检查通过"和"真正建立
连接"之间有个时间窗——如果攻击者能让同一个域名在这个窗口内从公网 IP 换成内网
IP（DNS rebinding），这层防护可能被绕过。httpx 不提供"按已校验的 IP 连接"的
钩子，要堵这个洞得自己接管 socket 层，v1 认为这超出当前威胁模型（防的是
"被诱导访问内部服务"，不是对抗能控制 DNS 的持续攻击者），不做。

### 限速器是调用方持有的对象，不是 fetch() 的全局状态

和 `caps/blobstore` 的 `Backend` 协议是同一个理由：cap 本身不持状态。
`RateLimiter(min_interval_seconds=...)` 由调用方创建、在多次 `fetch()` 之间
复用（比如每个数据源一个模块级单例），`fetch()` 只是在发请求前调用
`limiter.wait()`。

### `resolve` / `sleep` / `transport` 是测试钩子

默认值分别是真实 DNS（`socket.getaddrinfo`）、真实 `time.sleep`、`None`
（走真实网络）。生产代码不传这三个参数。测试靠这三个钩子在完全不碰网络的
情况下覆盖重试/退避/SSRF/重定向逻辑——SSRF 检查额外靠 IP 字面量
（`http://127.0.0.1/`）绕开真实 DNS，因为 `getaddrinfo` 对数字 IP 不发网络查询。

## 依赖方向

标准库（`socket` `ipaddress` `time` `urllib.parse`）+ `httpx`（本 cap 让它从
`dev` 升级为运行时依赖）。**不依赖任何 infra / domain / adapters / features，
也不依赖别的 cap。**

## 刻意裁剪的范围

| 不做 | 归谁 |
|---|---|
| 决定 4xx/5xx 算不算错误 | 调用方（`raise_for_status` 开关 + 各 feature 自己的判断） |
| 防 DNS rebinding | v1 不做，见上 |
| HTTP-date 格式的 `Retry-After` | 没必要，退回指数退避 |
| 连接池跨多次 `fetch()` 复用 | 每次 `fetch()` 独立开关 `httpx.Client`；v1 调用量级不需要跨调用复用连接池 |
| 认证（API key / OAuth） | 调用方自己拼进 `headers`；这里只管传输层 |
| 响应体自动解析（JSON/XML） | 调用方自己解析 `result.content` |
| 真正验证 `proxy` 行为 | 只做参数穿透，单测不验证代理生效（需要真实网络环境） |
