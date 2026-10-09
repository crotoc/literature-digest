"""冒烟页：验证装配机制（页面注册表/base.html/异常处理中间件）的固定基准页。

最初写它是为了在骨架阶段、业务模块还一个都没有时验证装配机制能跑通。
现在 auth/library/settings 等真实页面都已经落地，但这个页面没有删掉——
tests/test_skeleton.py 的三个用例（container_class 可覆盖性、骨架渲染、
导航发现）和 app/pages/home/tests 的一个用例直接拿它当"任意一个真实
页面"的稳定基准在用，删掉它要同时把那几条测试改去认另一个页面的具体
文案，收益（少一个页面）配不上这个改动量，所以保留。
"""

from .page import nav, router

__all__ = ["nav", "router"]
