"""caps/probe。详见 contract.py。"""

from caps.probe.contract import CheckFn, ProbeResult, fail, ok, run_check

__all__ = [
    "CheckFn",
    "ProbeResult",
    "fail",
    "ok",
    "run_check",
]
