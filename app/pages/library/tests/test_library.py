PASSWORD = "correct-horse-battery-staple"


def _register(client, *, username, email):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def _library_id_from_url(url) -> int:
    tail = str(url).rstrip("/").rpartition("-")[2]
    return int(tail)


# ── /library 入口 ────────────────────────────────────────────────────────


def test_library_entry_redirects_anonymous_to_login(client):
    response = client.get("/library")
    assert str(response.url).endswith("/login")


def test_library_entry_redirects_to_personal_library(client):
    _register(client, username="ivy", email="ivy@example.org")

    response = client.get("/library")

    assert response.status_code == 200  # 跟随了 303
    assert "/l/" in str(response.url)
    assert "共 0 篇" in response.text
    assert "这里还没有文献" in response.text


# ── /l/<slug>-<id>/ ──────────────────────────────────────────────────────


def test_library_view_redirects_anonymous_to_login(client):
    response = client.get("/l/whatever-1/")
    assert str(response.url).endswith("/login")


def test_library_view_rejects_slug_without_trailing_digits(client):
    _register(client, username="jack", email="jack@example.org")

    response = client.get("/l/not-a-number/")

    assert response.status_code == 404


def test_library_view_rejects_other_accounts_library(client):
    _register(client, username="kim", email="kim@example.org")
    kim_response = client.get("/library")
    kim_library_id = _library_id_from_url(kim_response.url)
    client.post("/logout")

    _register(client, username="liam", email="liam@example.org")

    response = client.get(f"/l/someone-elses-library-{kim_library_id}/")

    assert response.status_code == 404


def test_library_view_rejects_invalid_sort_params(client):
    _register(client, username="mia", email="mia@example.org")
    response = client.get("/library")
    library_id = _library_id_from_url(response.url)

    bad = client.get(f"/l/mia-{library_id}/", params={"sort_by": "not_a_real_field"})

    assert bad.status_code == 400


def test_library_view_renders_tabs_and_sort_controls(client):
    _register(client, username="noah", email="noah@example.org")

    response = client.get("/library")

    assert response.status_code == 200
    assert "全部" in response.text
    assert "回收站" in response.text
    assert 'value="updated_at" selected' in response.text
