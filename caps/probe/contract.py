"""caps/probe 的对外表面。外部只许 import 这里的东西。"""

from caps.probe.service import CheckFn, ProbeResult, fail, ok, run_check

__all__ = [
    "CheckFn",
    "ProbeResult",
    "fail",
    "ok",
    "run_check",
]
