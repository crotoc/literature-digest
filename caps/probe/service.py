"""caps/probe 实现：纯协议层。只定义 ProbeResult 的形状和 check() 的调用约定，
不知道任何具体连接（crossref/pubmed/telegram/llm/proxy）该怎么测——那是各
adapter 自己的知识，这里再多放一行都会变成按 kind 分支的大 switch。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProbeResult:
    """一次连接测试的结果。`detail` 是自由格式的附加信息（状态码、响应摘要、异常类型等），
    显示层决定展示哪些字段，这个 cap 不关心里面装的是什么。"""

    ok: bool
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)


def ok(message: str, **detail: Any) -> ProbeResult:
    """构造一个成功结果。detail 用关键字参数传，省得每个 adapter 自己拼字典。"""
    return ProbeResult(ok=True, message=message, detail=detail)


def fail(message: str, **detail: Any) -> ProbeResult:
    """构造一个失败结果。"""
    return ProbeResult(ok=False, message=message, detail=detail)


class CheckFn(Protocol):
    """约定：对外连接的 adapter 必须导出一个满足这个签名的 `check` 函数，接收该连接的
    配置、返回 ProbeResult。这只是给类型检查器看的协议声明——Python 没有运行时强制
    接口这种东西，真正的强制发生在 run_check()。"""

    def __call__(self, config: Any) -> ProbeResult: ...


def run_check(check: CheckFn, config: Any) -> ProbeResult:
    """调用一个 adapter 的 check()，并强制它遵守协议，而不是信任它自觉遵守：

    - adapter 实现忘了自己 try/except、测试过程中抛了异常 → 这里接住，包成失败结果，
      不让异常一路冒到 `features/connection_setup` 炸掉整个请求
    - adapter 返回的不是 ProbeResult（比如手滑返回了 bool 或 None）→ 同样包成失败结果，
      而不是让这种格式错误悄悄混进调用方的"测试通过/不通过"逻辑里

    `features/connection_setup` 应该一律通过这个函数调 adapter 的 check，不直接调用。
    """
    try:
        result = check(config)
    except Exception as exc:
        return fail(str(exc) or type(exc).__name__, exception_type=type(exc).__name__)

    if not isinstance(result, ProbeResult):
        return fail(
            f"check() 必须返回 ProbeResult，实际返回了 {type(result).__name__}",
            actual_type=type(result).__name__,
        )
    return result
