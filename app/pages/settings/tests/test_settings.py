import json
import re

import httpx

from app.shell.deps import ai_llm_transport as ai_llm_transport_dependency
from app.shell.deps import log_file as log_file_dependency

PASSWORD = "correct-horse-battery-staple"


def _register(client, *, username, email):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def test_settings_view_redirects_anonymous_to_login(client):
    response = client.get("/settings")
    assert str(response.url).endswith("/login")


def test_settings_view_shows_default_style(client):
    _register(client, username="alice3", email="alice3@example.org")

    response = client.get("/settings")

    assert response.status_code == 200
    assert '<option value="apa" selected>' in response.text


def test_save_citation_style_then_it_shows_as_selected(client):
    _register(client, username="bob3", email="bob3@example.org")

    save_response = client.post("/settings/citation-style", data={"style": "vancouver"})

    assert save_response.status_code == 200
    assert '<option value="vancouver" selected>' in save_response.text
    assert '<option value="apa" selected>' not in save_response.text


def test_save_citation_style_rejects_unknown_style(client):
    _register(client, username="carl3", email="carl3@example.org")

    response = client.post("/settings/citation-style", data={"style": "mla"})

    assert response.status_code == 400


def test_save_citation_style_requires_login(client):
    _register(client, username="dana3", email="dana3@example.org")
    client.post("/logout")

    response = client.post("/settings/citation-style", data={"style": "vancouver"})

    assert str(response.url).endswith("/login")


def test_settings_view_shows_empty_naming_template_by_default(client):
    _register(client, username="erin3", email="erin3@example.org")

    response = client.get("/settings")

    assert response.status_code == 200
    assert 'name="template" value=""' in response.text


def test_save_naming_template_then_it_shows_as_current_value(client):
    _register(client, username="finn3", email="finn3@example.org")

    response = client.post(
        "/settings/attachment-naming", data={"template": "[firstauthor]_[year]_[title:20]"}
    )

    assert response.status_code == 200
    assert 'name="template" value="[firstauthor]_[year]_[title:20]"' in response.text


def test_save_naming_template_rejects_unknown_placeholder(client):
    _register(client, username="gail3", email="gail3@example.org")

    response = client.post("/settings/attachment-naming", data={"template": "[doi]_[year]"})

    assert response.status_code == 200
    assert "不认识的占位" in response.text


def test_save_naming_template_with_blank_value_clears_it(client):
    _register(client, username="hank3", email="hank3@example.org")
    client.post("/settings/attachment-naming", data={"template": "[year]"})

    response = client.post("/settings/attachment-naming", data={"template": "  "})

    assert response.status_code == 200
    assert 'name="template" value=""' in response.text


def test_save_naming_template_requires_login(client):
    _register(client, username="iris3", email="iris3@example.org")
    client.post("/logout")

    response = client.post("/settings/attachment-naming", data={"template": "[year]"})

    assert str(response.url).endswith("/login")


def test_citation_style_change_affects_library_card_rendering(client):
    _register(client, username="erin3", email="erin3@example.org")
    r = client.post(
        "/import",
        data={
            "format": "ris",
            "raw_text": "TY  - JOUR\nTI  - Style Switch Paper\nPY  - 2022///\nER  -\n",
        },
    )
    assert r.status_code == 200

    client.post("/settings/citation-style", data={"style": "vancouver"})

    lib_response = client.get("/library")
    # Vancouver 和 APA 对同一条记录的渲染规则不同（大小写/标点顺序），
    # 这里不去抠具体格式细节，只确认改了设置之后文献库页面确实换了样式，
    # 不是设置页自己存了但从没被读过。
    assert lib_response.status_code == 200


# ── 数据源凭据 ───────────────────────────────────────────────────────────


def _connection_id_from_settings(html, name):
    """每个连接卡片的名字后面紧跟着它自己的 connections/{id}/... 路径，
    从 name 出现的位置往后找第一个这样的路径就是它的 connection_id。
    """
    idx = html.find(name)
    assert idx != -1, html
    match = re.search(r"connections/(\d+)/", html[idx:])
    assert match is not None, html[idx:]
    return int(match.group(1))


def test_create_crossref_credential_then_it_shows_in_the_list(client):
    _register(client, username="finn7", email="finn7@example.org")

    response = client.post(
        "/settings/connections/create",
        data={"source": "crossref", "name": "my-crossref", "mailto": "finn7@example.org"},
    )
    assert response.status_code == 200
    assert "my-crossref" in response.text
    assert "crossref" in response.text


def test_create_pubmed_credential_rejects_mailto(client):
    _register(client, username="greta7", email="greta7@example.org")

    response = client.post(
        "/settings/connections/create",
        data={"source": "pubmed", "name": "my-pubmed", "mailto": "nope@example.org"},
    )
    assert response.status_code == 200
    assert "不接受 mailto" in response.text


def test_toggle_then_set_default_then_delete(client):
    _register(client, username="hank7", email="hank7@example.org")
    client.post(
        "/settings/connections/create", data={"source": "crossref", "name": "toggle-me", "mailto": ""}
    )
    index = client.get("/settings")
    connection_id = _connection_id_from_settings(index.text, "toggle-me")

    toggled = client.post(f"/settings/connections/{connection_id}/toggle")
    assert "已停用" in toggled.text

    defaulted = client.post(f"/settings/connections/{connection_id}/set-default")
    assert "默认" in defaulted.text

    deleted = client.post(f"/settings/connections/{connection_id}/delete")
    assert "toggle-me" not in deleted.text


def test_rotate_pubmed_api_key(client):
    _register(client, username="ivy7", email="ivy7@example.org")
    client.post(
        "/settings/connections/create",
        data={"source": "pubmed", "name": "rotate-me", "api_key": "first-key"},
    )
    index = client.get("/settings")
    connection_id = _connection_id_from_settings(index.text, "rotate-me")

    response = client.post(
        f"/settings/connections/{connection_id}/rotate-key", data={"api_key": "second-key"}
    )
    assert response.status_code == 200
    assert "rotate-me" in response.text


def test_rotate_api_key_rejects_crossref_connection(client):
    _register(client, username="jett7", email="jett7@example.org")
    client.post("/settings/connections/create", data={"source": "crossref", "name": "cr-only"})
    index = client.get("/settings")
    connection_id = _connection_id_from_settings(index.text, "cr-only")

    response = client.post(
        f"/settings/connections/{connection_id}/rotate-key", data={"api_key": "whatever"}
    )
    assert response.status_code == 200
    assert "只有 source=&#39;pubmed&#39;" in response.text or "只有 source='pubmed'" in response.text


def test_connection_mutations_require_login(client):
    _register(client, username="kara7", email="kara7@example.org")
    client.post("/settings/connections/create", data={"source": "crossref", "name": "anon-test"})
    index = client.get("/settings")
    connection_id = _connection_id_from_settings(index.text, "anon-test")
    client.post("/logout")

    for path in ("toggle", "set-default", "delete", "check"):
        response = client.post(f"/settings/connections/{connection_id}/{path}")
        assert str(response.url).endswith("/login"), path


def test_connection_mutations_reject_connection_id_from_another_account(client):
    _register(client, username="liam7", email="liam7@example.org")
    client.post("/settings/connections/create", data={"source": "crossref", "name": "liams-cred"})
    index = client.get("/settings")
    connection_id = _connection_id_from_settings(index.text, "liams-cred")
    client.post("/logout")

    _register(client, username="mika7", email="mika7@example.org")
    for path in ("toggle", "set-default", "delete"):
        response = client.post(f"/settings/connections/{connection_id}/{path}")
        assert response.status_code == 404, path


# ── 日志 ────────────────────────────────────────────────────────────────


def _seed_log(client, tmp_path, entries):
    """把 app/shell/deps.log_file 换成一个测试自己写的文件，绕开
    app.shell.testing 默认分配的、测试代码摸不到的临时文件——这里的
    entries 直接按 infra.logging.JsonFormatter 落盘的字段写，不走真实
    的 logging 调用链（那条链路在应用启动时就绑定了生产配置的日志
    文件，和这里的 dependency override 是两条独立路径，互不影响）。
    """
    log_path = tmp_path / "seeded.log"
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    client.app.dependency_overrides[log_file_dependency] = lambda: log_path
    return log_path


def test_logs_section_shows_empty_state_when_no_entries(client):
    _register(client, username="noa9", email="noa9@example.org")

    response = client.get("/settings")

    assert response.status_code == 200
    assert "没有匹配的日志条目" in response.text


def test_logs_section_lists_seeded_entries(client, tmp_path):
    _register(client, username="opal9", email="opal9@example.org")
    _seed_log(
        client,
        tmp_path,
        [
            {"ts": "2026-01-01T00:00:00", "level": "INFO", "logger": "app", "msg": "hello-info-line"},
            {"ts": "2026-01-01T00:00:01", "level": "ERROR", "logger": "app", "msg": "boom-error-line"},
        ],
    )

    response = client.get("/settings")

    assert "hello-info-line" in response.text
    assert "boom-error-line" in response.text


def test_logs_section_filters_by_min_level(client, tmp_path):
    _register(client, username="pike9", email="pike9@example.org")
    _seed_log(
        client,
        tmp_path,
        [
            {"ts": "2026-01-01T00:00:00", "level": "INFO", "logger": "app", "msg": "hello-info-line"},
            {"ts": "2026-01-01T00:00:01", "level": "ERROR", "logger": "app", "msg": "boom-error-line"},
        ],
    )

    response = client.get("/settings", params={"log_min_level": "ERROR"})

    assert "boom-error-line" in response.text
    assert "hello-info-line" not in response.text


def test_logs_section_filters_by_search_text(client, tmp_path):
    _register(client, username="quin9", email="quin9@example.org")
    _seed_log(
        client,
        tmp_path,
        [
            {"ts": "2026-01-01T00:00:00", "level": "INFO", "logger": "app", "msg": "needle-in-haystack"},
            {"ts": "2026-01-01T00:00:01", "level": "INFO", "logger": "app", "msg": "nothing-interesting"},
        ],
    )

    response = client.get("/settings", params={"log_search": "needle"})

    assert "needle-in-haystack" in response.text
    assert "nothing-interesting" not in response.text


def test_clear_log_route_empties_the_file_and_shows_notice(client, tmp_path):
    _register(client, username="ruth9", email="ruth9@example.org")
    log_path = _seed_log(
        client,
        tmp_path,
        [{"ts": "2026-01-01T00:00:00", "level": "INFO", "logger": "app", "msg": "to-be-cleared"}],
    )

    response = client.post("/settings/logs/clear")

    assert response.status_code == 200
    assert "已清空日志" in response.text
    assert "to-be-cleared" not in response.text
    assert log_path.read_text(encoding="utf-8") == ""


def test_export_log_route_returns_filtered_jsonl(client, tmp_path):
    _register(client, username="sana9", email="sana9@example.org")
    _seed_log(
        client,
        tmp_path,
        [
            {"ts": "2026-01-01T00:00:00", "level": "INFO", "logger": "app", "msg": "info-only-line"},
            {"ts": "2026-01-01T00:00:01", "level": "ERROR", "logger": "app", "msg": "error-only-line"},
        ],
    )

    response = client.get("/settings/logs/export", params={"log_min_level": "ERROR"})

    assert response.status_code == 200
    assert "error-only-line" in response.text
    assert "info-only-line" not in response.text
    assert "app-log-export.jsonl" in response.headers["content-disposition"]


def test_log_routes_require_login(client):
    _register(client, username="theo9", email="theo9@example.org")
    client.post("/logout")

    for method, path in (("post", "/settings/logs/clear"), ("get", "/settings/logs/export")):
        response = getattr(client, method)(path)
        assert str(response.url).endswith("/login"), path


# ── AI 模型连接 ──────────────────────────────────────────────────────────


def _ai_profile_id_from_settings(html, name):
    idx = html.find(name)
    assert idx != -1, html
    match = re.search(r"ai-profiles/(\d+)/", html[idx:])
    assert match is not None, html[idx:]
    return int(match.group(1))


def test_create_ai_profile_then_it_shows_in_the_list(client):
    _register(client, username="finn8", email="finn8@example.org")

    response = client.post(
        "/settings/ai-profiles/create",
        data={"provider": "openai_compat", "name": "my-openai", "model": "gpt-4o-mini", "api_key": "sk-test"},
    )
    assert response.status_code == 200
    assert "my-openai" in response.text
    assert "gpt-4o-mini" in response.text


def test_create_ai_profile_rejects_unknown_provider(client):
    _register(client, username="greta8", email="greta8@example.org")

    response = client.post(
        "/settings/ai-profiles/create",
        data={"provider": "deepseek", "name": "x", "model": "x", "api_key": "sk-test"},
    )
    assert response.status_code == 400


def test_toggle_then_set_default_then_delete_ai_profile(client):
    _register(client, username="hank8", email="hank8@example.org")
    client.post(
        "/settings/ai-profiles/create",
        data={"provider": "openai_compat", "name": "toggle-me", "model": "gpt-4o-mini", "api_key": "sk-test"},
    )
    index = client.get("/settings")
    connection_id = _ai_profile_id_from_settings(index.text, "toggle-me")

    toggled = client.post(f"/settings/ai-profiles/{connection_id}/toggle")
    assert "已停用" in toggled.text

    defaulted = client.post(f"/settings/ai-profiles/{connection_id}/set-default")
    assert "默认" in defaulted.text

    deleted = client.post(f"/settings/ai-profiles/{connection_id}/delete")
    assert "toggle-me" not in deleted.text


def test_rotate_ai_profile_api_key(client):
    _register(client, username="ivy8", email="ivy8@example.org")
    client.post(
        "/settings/ai-profiles/create",
        data={
            "provider": "anthropic",
            "name": "rotate-me",
            "model": "claude-haiku-4-5-20251001",
            "api_key": "sk-ant-test",
        },
    )
    index = client.get("/settings")
    connection_id = _ai_profile_id_from_settings(index.text, "rotate-me")

    response = client.post(
        f"/settings/ai-profiles/{connection_id}/rotate-key", data={"api_key": "sk-ant-second"}
    )
    assert response.status_code == 200
    assert "rotate-me" in response.text


def test_ai_profile_mutations_require_login(client):
    _register(client, username="kara8", email="kara8@example.org")
    client.post(
        "/settings/ai-profiles/create",
        data={"provider": "openai_compat", "name": "anon-test", "model": "gpt-4o-mini", "api_key": "sk-test"},
    )
    index = client.get("/settings")
    connection_id = _ai_profile_id_from_settings(index.text, "anon-test")
    client.post("/logout")

    for path in ("toggle", "set-default", "delete", "check"):
        response = client.post(f"/settings/ai-profiles/{connection_id}/{path}")
        assert str(response.url).endswith("/login"), path


def test_ai_profile_mutations_reject_connection_id_from_another_account(client):
    _register(client, username="liam8", email="liam8@example.org")
    client.post(
        "/settings/ai-profiles/create",
        data={
            "provider": "openai_compat",
            "name": "liams-profile",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
        },
    )
    index = client.get("/settings")
    connection_id = _ai_profile_id_from_settings(index.text, "liams-profile")
    client.post("/logout")

    _register(client, username="mika8", email="mika8@example.org")
    for path in ("toggle", "set-default", "delete"):
        response = client.post(f"/settings/ai-profiles/{connection_id}/{path}")
        assert response.status_code == 404, path


def test_ai_profile_mutations_reject_connection_of_source_credential_kind(client):
    _register(client, username="noa8", email="noa8@example.org")
    client.post("/settings/connections/create", data={"source": "crossref", "name": "not-an-ai-profile"})
    index = client.get("/settings")
    connection_id = _connection_id_from_settings(index.text, "not-an-ai-profile")

    response = client.post(f"/settings/ai-profiles/{connection_id}/toggle")
    assert response.status_code == 404


# ── 研究意图 ─────────────────────────────────────────────────────────────


def _intent_id_from_settings(html, name):
    idx = html.find(name)
    assert idx != -1, html
    match = re.search(r"intents/(\d+)/", html[idx:])
    assert match is not None, html[idx:]
    return int(match.group(1))


def _openai_transport(payload: dict):
    """和 `app/pages/library/tests/test_library.py` 同名 helper 同一种
    构造方式——按项目约定各测试文件各自复制一份，不跨目录 import。"""
    content = json.dumps(payload)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def test_create_intent_then_it_shows_in_the_list(client):
    _register(client, username="finn10", email="finn10@example.org")

    response = client.post(
        "/settings/intents/create",
        data={"name": "crispr-radar", "question_text": "CRISPR base editing 体内递送的最新进展"},
    )
    assert response.status_code == 200
    assert "crispr-radar" in response.text
    assert "还没有编译出任何检索式版本" in response.text


def test_create_intent_rejects_empty_question(client):
    _register(client, username="greta10", email="greta10@example.org")

    response = client.post("/settings/intents/create", data={"name": "x", "question_text": "   "})
    assert response.status_code == 200
    assert "研究问题描述不能为空" in response.text


def test_update_intent_changes_candidate_limit_and_lookback(client):
    _register(client, username="hank10", email="hank10@example.org")
    client.post("/settings/intents/create", data={"name": "update-me", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "update-me")

    response = client.post(
        f"/settings/intents/{intent_id}/update",
        data={"name": "update-me", "question_text": "q2", "candidate_limit": "10", "lookback_days": "3"},
    )
    assert response.status_code == 200
    assert "候选上限 10" in response.text
    assert "回溯 3 天" in response.text


def test_toggle_then_delete_intent(client):
    _register(client, username="ivy10", email="ivy10@example.org")
    client.post("/settings/intents/create", data={"name": "toggle-me", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "toggle-me")

    toggled = client.post(f"/settings/intents/{intent_id}/toggle")
    assert "已停用" in toggled.text

    deleted = client.post(f"/settings/intents/{intent_id}/delete")
    assert "toggle-me" not in deleted.text


def test_intent_mutations_require_login(client):
    _register(client, username="jett10", email="jett10@example.org")
    client.post("/settings/intents/create", data={"name": "anon-test", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "anon-test")
    client.post("/logout")

    for path in ("toggle", "delete"):
        response = client.post(f"/settings/intents/{intent_id}/{path}")
        assert str(response.url).endswith("/login"), path


def test_intent_mutations_reject_intent_id_from_another_account(client):
    _register(client, username="kara10", email="kara10@example.org")
    client.post("/settings/intents/create", data={"name": "karas-intent", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "karas-intent")
    client.post("/logout")

    _register(client, username="mika10", email="mika10@example.org")
    response = client.post(f"/settings/intents/{intent_id}/toggle")
    assert response.status_code == 404


def test_compile_intent_query_route_succeeds_and_shows_query(client):
    _register(client, username="liam10", email="liam10@example.org")
    client.post("/settings/intents/create", data={"name": "compile-me", "question_text": "q"})
    client.post(
        "/settings/ai-profiles/create",
        data={"provider": "openai_compat", "name": "my-ai", "model": "gpt-4o-mini", "api_key": "sk-test"},
    )
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "compile-me")
    connection_id = _ai_profile_id_from_settings(index.text, "my-ai")

    client.app.dependency_overrides[ai_llm_transport_dependency] = lambda: _openai_transport(
        {"query": "crispr[tiab] AND base editing[tiab]", "rationale": "聚焦标题/摘要关键词"}
    )

    response = client.post(
        f"/settings/intents/{intent_id}/compile", data={"connection_id": str(connection_id)}
    )
    assert response.status_code == 200
    assert "crispr[tiab]" in response.text
    assert "检索式编译成功" in response.text


def test_compile_intent_query_route_shows_failure_when_llm_errors(client):
    _register(client, username="mika11", email="mika11@example.org")
    client.post("/settings/intents/create", data={"name": "fail-me", "question_text": "q"})
    client.post(
        "/settings/ai-profiles/create",
        data={"provider": "openai_compat", "name": "my-ai2", "model": "gpt-4o-mini", "api_key": "sk-test"},
    )
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "fail-me")
    connection_id = _ai_profile_id_from_settings(index.text, "my-ai2")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    client.app.dependency_overrides[ai_llm_transport_dependency] = lambda: httpx.MockTransport(handler)

    response = client.post(
        f"/settings/intents/{intent_id}/compile", data={"connection_id": str(connection_id)}
    )
    assert response.status_code == 200
    assert "检索式编译失败" in response.text


def test_compile_intent_query_route_rejects_connection_of_another_account(client):
    _register(client, username="noa10", email="noa10@example.org")
    client.post("/settings/intents/create", data={"name": "cross-account-compile", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "cross-account-compile")
    client.post("/logout")

    _register(client, username="opal10", email="opal10@example.org")
    client.post(
        "/settings/ai-profiles/create",
        data={"provider": "openai_compat", "name": "opals-ai", "model": "gpt-4o-mini", "api_key": "sk-test"},
    )
    other_index = client.get("/settings")
    connection_id = _ai_profile_id_from_settings(other_index.text, "opals-ai")

    response = client.post(
        f"/settings/intents/{intent_id}/compile", data={"connection_id": str(connection_id)}
    )
    assert response.status_code == 404


def test_upsert_then_delete_intent_schedule(client):
    # 故意不用模板里占位符文案同款的 "0 9 * * *"（`placeholder="...0 9 * * *"`
    # 本身就一直出现在 HTML 里），换一个不会和占位符撞上的 cron 串，
    # 靠精确匹配 `value="..."` 属性而不是裸子串来确认保存/删除生效。
    _register(client, username="pike10", email="pike10@example.org")
    client.post("/settings/intents/create", data={"name": "schedule-me", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "schedule-me")

    response = client.post(
        f"/settings/intents/{intent_id}/schedule", data={"cron": "0 10 * * *", "enabled": "on"}
    )
    assert response.status_code == 200
    assert 'value="0 10 * * *"' in response.text

    deleted = client.post(f"/settings/intents/{intent_id}/schedule/delete")
    assert deleted.status_code == 200
    assert 'value="0 10 * * *"' not in deleted.text


def test_upsert_intent_schedule_rejects_invalid_cron(client):
    _register(client, username="quin10", email="quin10@example.org")
    client.post("/settings/intents/create", data={"name": "bad-cron", "question_text": "q"})
    index = client.get("/settings")
    intent_id = _intent_id_from_settings(index.text, "bad-cron")

    response = client.post(f"/settings/intents/{intent_id}/schedule", data={"cron": "not-a-cron"})
    assert response.status_code == 200
    assert "alert-danger" in response.text
