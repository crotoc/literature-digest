import re

PASSWORD = "correct-horse-battery-staple"


def _register(client, *, username, email):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def _folders_url(client) -> str:
    response = client.get("/folders")
    return str(response.url).replace("http://testserver", "")


def _create_folder(client, folders_url, *, name, parent_folder_id=""):
    response = client.post(
        f"{folders_url}create", data={"name": name, "parent_folder_id": str(parent_folder_id)}
    )
    return response


def _folder_id_by_name(html: str, name: str) -> int:
    match = re.search(rf'value="(\d+)"[^>]*>\s*{re.escape(name)}\s*<', html)
    assert match is not None, html
    return int(match.group(1))


def test_folders_entry_redirects_anonymous_to_login(client):
    response = client.get("/folders")
    assert str(response.url).endswith("/login")


def test_folders_entry_redirects_to_personal_library_folders(client):
    _register(client, username="anna", email="anna@example.org")

    response = client.get("/folders")

    assert response.status_code == 200
    assert "/folders/" in str(response.url)
    assert "还没有文件夹" in response.text


def test_create_folder_then_rename_it(client):
    _register(client, username="ben", email="ben@example.org")
    folders_url = _folders_url(client)

    create_response = _create_folder(client, folders_url, name="Methods")
    assert create_response.status_code == 200  # 跟随了 303
    assert "Methods" in create_response.text

    folder_id = _folder_id_by_name(create_response.text, "Methods")

    rename_response = client.post(f"{folders_url}{folder_id}/rename", data={"name": "Renamed Methods"})
    assert rename_response.status_code == 200
    assert "Renamed Methods" in rename_response.text
    assert ">Methods<" not in rename_response.text


def test_create_folder_rejects_blank_name(client):
    _register(client, username="carl", email="carl@example.org")
    folders_url = _folders_url(client)

    response = _create_folder(client, folders_url, name="   ")

    assert response.status_code == 200  # 跟随了 303
    assert "文件夹名不能为空" in response.text


def test_create_nested_folder_and_delete_cascades(client):
    _register(client, username="dana", email="dana@example.org")
    folders_url = _folders_url(client)

    parent_response = _create_folder(client, folders_url, name="Parent")
    parent_id = _folder_id_by_name(parent_response.text, "Parent")

    child_response = _create_folder(client, folders_url, name="Child", parent_folder_id=parent_id)
    assert "Child" in child_response.text
    child_id = _folder_id_by_name(child_response.text, "Child")

    delete_response = client.post(f"{folders_url}{parent_id}/delete")
    assert delete_response.status_code == 200
    assert "Parent" not in delete_response.text
    assert "Child" not in delete_response.text

    # 子文件夹也被级联删了——再对它改名应该 404。
    stale_response = client.post(f"{folders_url}{child_id}/rename", data={"name": "whatever"})
    assert stale_response.status_code == 404


def test_move_folder_rejects_cycle(client):
    _register(client, username="erin", email="erin@example.org")
    folders_url = _folders_url(client)

    parent_response = _create_folder(client, folders_url, name="Parent2")
    parent_id = _folder_id_by_name(parent_response.text, "Parent2")

    child_response = _create_folder(client, folders_url, name="Child2", parent_folder_id=parent_id)
    child_id = _folder_id_by_name(child_response.text, "Child2")

    # 把 Parent2 挪到它自己的子节点 Child2 下面——应该被拒绝，树结构不变。
    response = client.post(
        f"{folders_url}{parent_id}/move", data={"new_parent_folder_id": str(child_id)}
    )
    assert response.status_code == 200  # 跟随了 303 回到列表
    assert "不能把文件夹" in response.text


def test_folders_view_rejects_other_accounts_library(client):
    _register(client, username="finn2", email="finn2@example.org")
    finn_response = client.get("/folders")
    finn_folders_url = str(finn_response.url).replace("http://testserver", "")
    client.post("/logout")

    _register(client, username="gail", email="gail@example.org")

    response = client.get(finn_folders_url)

    assert response.status_code == 404


def test_rename_folder_rejects_work_id_from_another_library(client):
    _register(client, username="hank", email="hank@example.org")
    hank_folders_url = _folders_url(client)
    create_response = _create_folder(client, hank_folders_url, name="HanksFolder")
    folder_id = _folder_id_by_name(create_response.text, "HanksFolder")
    client.post("/logout")

    _register(client, username="iris2", email="iris2@example.org")
    iris_folders_url = _folders_url(client)

    response = client.post(f"{iris_folders_url}{folder_id}/rename", data={"name": "越权改名"})

    assert response.status_code == 404


def test_create_folder_requires_login(client):
    _register(client, username="jun", email="jun@example.org")
    folders_url = _folders_url(client)
    client.post("/logout")

    response = client.post(f"{folders_url}create", data={"name": "whatever"})

    assert str(response.url).endswith("/login")
