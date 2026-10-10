import json
import re

import httpx

from app.shell.deps import ai_llm_transport as ai_llm_transport_dependency
from app.shell.deps import metadata_lookup_transport as metadata_lookup_transport_dependency

PASSWORD = "correct-horse-battery-staple"


def _register(client, *, username, email):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def test_home_redirects_anonymous_to_login(client):
    response = client.get("/")

    assert response.status_code == 200  # 跟随了 303 -> /login
    assert str(response.url).endswith("/login")


def test_home_shows_username_when_authenticated(client):
    client.post(
        "/register",
        data={
            "username": "heidi",
            "email": "heidi@example.org",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "欢迎，heidi" in response.text


def test_home_is_in_nav_items(client):
    response = client.get("/healthz")  # 任意一个真实页面即可，复用它看 nav_items

    assert '<code>home</code>' in response.text


# ── 研究意图（首页切片：列表 + 启停 toggle + 立即运行）────────────────────


def _intent_id_from_html(html, name):
    idx = html.find(name)
    assert idx != -1, html
    match = re.search(r"intents/(\d+)/", html[idx:])
    assert match is not None, html[idx:]
    return int(match.group(1))


def _ai_profile_id_from_html(html, name):
    idx = html.find(name)
    assert idx != -1, html
    match = re.search(r"ai-profiles/(\d+)/", html[idx:])
    assert match is not None, html[idx:]
    return int(match.group(1))


def _openai_transport(payload: dict):
    """和 `app/pages/library/tests/test_library.py`、
    `app/pages/settings/tests/test_settings.py` 同名 helper 同一种构造
    方式——按项目约定各测试文件各自复制一份，不跨目录 import。"""
    content = json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _empty_sources_transport():
    """让 crossref/pubmed 的 search() 都返回"没有命中"——这里只关心
    `run_discovery` 整条路径能不能同步跑通并落一条成功的 `DiscoveryRun`，
    候选怎么解析/去重已经在 `features/discovery` 自己的测试里覆盖过，
    这里不重复。"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "esearch.fcgi" in url:
            return httpx.Response(200, json={"esearchresult": {"idlist": []}})
        if "esummary.fcgi" in url:
            return httpx.Response(200, json={"result": {"uids": []}})
        return httpx.Response(200, json={"status": "ok", "message": {"items": [], "total-results": 0}})

    return httpx.MockTransport(handler)


def _create_intent_with_ready_query(client, *, name, query="radar query"):
    client.post("/settings/intents/create", data={"name": name, "question_text": "q"})
    client.post(
        "/settings/ai-profiles/create",
        data={
            "provider": "openai_compat", "name": f"{name}-ai",
            "model": "gpt-4o-mini", "api_key": "sk-test",
        },
    )
    index = client.get("/settings")
    intent_id = _intent_id_from_html(index.text, name)
    connection_id = _ai_profile_id_from_html(index.text, f"{name}-ai")

    client.app.dependency_overrides[ai_llm_transport_dependency] = lambda: _openai_transport(
        {"query": query, "rationale": "r"}
    )
    client.post(f"/settings/intents/{intent_id}/compile", data={"connection_id": str(connection_id)})
    del client.app.dependency_overrides[ai_llm_transport_dependency]
    return intent_id


def test_home_shows_intent_list(client):
    _register(client, username="finn12", email="finn12@example.org")
    client.post("/settings/intents/create", data={"name": "visible-intent", "question_text": "q"})

    response = client.get("/")
    assert response.status_code == 200
    assert "visible-intent" in response.text
    assert "还没有运行过" in response.text


def test_toggle_intent_route_flips_enabled_state(client):
    _register(client, username="greta12", email="greta12@example.org")
    client.post("/settings/intents/create", data={"name": "toggle-home", "question_text": "q"})
    index = client.get("/")
    intent_id = _intent_id_from_html(index.text, "toggle-home")

    response = client.post(f"/intents/{intent_id}/toggle")
    assert response.status_code == 200
    assert "已停用" in response.text


def test_run_now_shows_error_when_no_ready_query(client):
    _register(client, username="hank12", email="hank12@example.org")
    client.post("/settings/intents/create", data={"name": "no-query", "question_text": "q"})
    index = client.get("/")
    intent_id = _intent_id_from_html(index.text, "no-query")

    response = client.post(f"/intents/{intent_id}/run-now")
    assert response.status_code == 200
    assert "还没有可用的检索式" in response.text


def test_run_now_shows_error_when_intent_disabled(client):
    _register(client, username="ivy12", email="ivy12@example.org")
    intent_id = _create_intent_with_ready_query(client, name="disabled-intent")
    client.post(f"/intents/{intent_id}/toggle")  # 现在是停用状态

    response = client.post(f"/intents/{intent_id}/run-now")
    assert response.status_code == 200
    assert "已停用，不能跑发现" in response.text


def test_run_now_succeeds_after_query_compiled(client):
    _register(client, username="jett12", email="jett12@example.org")
    intent_id = _create_intent_with_ready_query(client, name="run-me")

    client.app.dependency_overrides[metadata_lookup_transport_dependency] = lambda: _empty_sources_transport()

    response = client.post(f"/intents/{intent_id}/run-now")
    assert response.status_code == 200
    assert "还没有运行过" not in response.text
    assert "成功" in response.text


def test_intent_mutations_require_login(client):
    _register(client, username="kara12", email="kara12@example.org")
    client.post("/settings/intents/create", data={"name": "anon-home", "question_text": "q"})
    index = client.get("/")
    intent_id = _intent_id_from_html(index.text, "anon-home")
    client.post("/logout")

    for path in ("toggle", "run-now"):
        response = client.post(f"/intents/{intent_id}/{path}")
        assert str(response.url).endswith("/login"), path


def test_intent_mutations_reject_intent_id_from_another_account(client):
    _register(client, username="liam12", email="liam12@example.org")
    client.post("/settings/intents/create", data={"name": "liams-intent", "question_text": "q"})
    index = client.get("/")
    intent_id = _intent_id_from_html(index.text, "liams-intent")
    client.post("/logout")

    _register(client, username="mika13", email="mika13@example.org")
    for path in ("toggle", "run-now"):
        response = client.post(f"/intents/{intent_id}/{path}")
        assert response.status_code == 404, path
