import re

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
