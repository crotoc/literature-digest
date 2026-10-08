"""冒烟页：骨架阶段用来验证装配机制。

它存在的意义是让 registry / base.html / lint 在没有任何业务模块时就能跑通。
第一个真实页面（auth）落地后可以删掉它，删掉后其余 pytest 必须仍然全绿。
"""

from .page import nav, router

__all__ = ["nav", "router"]
