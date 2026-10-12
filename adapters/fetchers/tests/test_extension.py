"""adapters/fetchers/extension 单测：分发、命令、异步句柄。"""

import pytest
from adapters.fetchers.extension import (
    Candidate, dispatch, Dispatched, Command, Observation
)


def test_dispatch_returns_dispatched():
    """dispatch() 返回 Dispatched 异步句柄。"""
    candidate = Candidate(
        url="https://example.com/paper",
        kind="article_page",
        source="generic_html",
        priority=4,
    )
    result = dispatch(candidate)
    assert isinstance(result, Dispatched)
    assert len(result.handle) > 0


def test_dispatch_idempotent():
    """多次 dispatch 同一候选生成不同 handle。"""
    candidate = Candidate(
        url="https://example.com/paper",
        kind="article_page",
        source="generic_html",
        priority=4,
    )
    r1 = dispatch(candidate)
    r2 = dispatch(candidate)
    assert r1.handle != r2.handle


def test_command_structure():
    """命令结构：op、url、hint、req_id。"""
    cmd = Command(
        op="open_hidden",
        url="https://example.com/paper",
        req_id="job-123",
    )
    assert cmd.op == "open_hidden"
    assert cmd.url == "https://example.com/paper"


def test_observation_structure():
    """观察结构：url、title、captcha/password/pdf 标记。"""
    obs = Observation(
        url="https://example.com/paper",
        title="Example Paper",
        has_captcha=True,
        has_password_field=False,
        found_pdf_links=["https://example.com/pdf"],
    )
    assert obs.has_captcha
    assert not obs.has_password_field
    assert len(obs.found_pdf_links) == 1
