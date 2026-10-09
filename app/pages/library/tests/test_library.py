import re

PASSWORD = "correct-horse-battery-staple"

SAMPLE_RIS = """TY  - JOUR
TI  - A Sample Paper
PY  - 2020///
ER  -
"""


def _register(client, *, username, email):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def _library_id_from_url(url) -> int:
    tail = str(url).rstrip("/").rpartition("-")[2]
    return int(tail)


def _import_sample(client) -> int:
    r = client.post("/import", data={"format": "ris", "raw_text": SAMPLE_RIS})
    match = re.search(r"work_id=(\d+)", r.text)
    assert match is not None, r.text
    return int(match.group(1))


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


# ── 删除 / 恢复 ──────────────────────────────────────────────────────────


def test_delete_moves_work_to_trash_and_restore_brings_it_back(client):
    _register(client, username="ruth", email="ruth@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    delete_response = client.post(
        f"{library_url}works/{work_id}/delete",
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    assert delete_response.status_code == 200  # 跟随了 303 回到 all 视图
    assert "共 0 篇" in delete_response.text

    trash_response = client.get(library_url, params={"view": "trash"})
    assert "共 1 篇" in trash_response.text
    assert "A Sample Paper" in trash_response.text

    restore_response = client.post(
        f"{library_url}works/{work_id}/restore",
        data={"view": "trash", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    assert restore_response.status_code == 200
    assert "共 0 篇" in restore_response.text  # 回收站空了

    all_response = client.get(library_url, params={"view": "all"})
    assert "共 1 篇" in all_response.text
    assert "A Sample Paper" in all_response.text


def test_delete_requires_login(client):
    _register(client, username="sam", email="sam@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.post(f"{library_url}works/{work_id}/delete", data={"view": "all"})

    assert str(response.url).endswith("/login")
