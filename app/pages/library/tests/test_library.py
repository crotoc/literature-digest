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


# ── 打标签 / 去标签 ──────────────────────────────────────────────────────


def test_add_tag_then_remove_tag(client):
    _register(client, username="tina", email="tina@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    add_response = client.post(
        f"{library_url}works/{work_id}/tags/add",
        data={
            "name": "machine-learning",
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )
    assert add_response.status_code == 200
    assert "machine-learning" in add_response.text

    tag_id_match = re.search(r"tags/(\d+)/remove", add_response.text)
    assert tag_id_match is not None, add_response.text
    tag_id = int(tag_id_match.group(1))

    remove_response = client.post(
        f"{library_url}works/{work_id}/tags/{tag_id}/remove",
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    assert remove_response.status_code == 200
    assert "machine-learning" not in remove_response.text


def test_add_tag_with_blank_name_is_a_no_op(client):
    _register(client, username="uma", email="uma@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}works/{work_id}/tags/add",
        data={"name": "   ", "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )

    assert response.status_code == 200
    assert "tag-chip" not in response.text


# ── 笔记 ─────────────────────────────────────────────────────────────────


def test_save_note_then_update_it(client):
    _register(client, username="vince", email="vince@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    save_response = client.post(
        f"{library_url}works/{work_id}/note",
        data={
            "content": "第一版笔记",
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )
    assert save_response.status_code == 200
    assert "第一版笔记" in save_response.text
    assert "笔记（已有）" in save_response.text

    update_response = client.post(
        f"{library_url}works/{work_id}/note",
        data={
            "content": "改过的笔记",
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )
    assert "改过的笔记" in update_response.text
    assert "第一版笔记" not in update_response.text


def test_clearing_note_content_deletes_it(client):
    _register(client, username="wade", email="wade@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    client.post(
        f"{library_url}works/{work_id}/note",
        data={"content": "先写一条", "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )

    clear_response = client.post(
        f"{library_url}works/{work_id}/note",
        data={"content": "   ", "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )

    assert clear_response.status_code == 200
    assert "先写一条" not in clear_response.text
    assert "笔记（还没有）" in clear_response.text


def test_save_note_requires_login(client):
    _register(client, username="xena", email="xena@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.post(f"{library_url}works/{work_id}/note", data={"content": "不该成功"})

    assert str(response.url).endswith("/login")


def test_save_note_rejects_work_id_from_another_library(client):
    # features.annotating.set_work_note 自己不校验 work_id 是否真的属于
    # 传入的 library_id——这个检查必须在页面层做，否则账号 A 能对着自己的
    # 文库 URL、拿一个属于账号 B 的 work_id 去改 B 的笔记。
    _register(client, username="yuki", email="yuki@example.org")
    yuki_work_id = _import_sample(client)
    yuki_lib_response = client.get("/library")
    yuki_library_url = str(yuki_lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    _register(client, username="zane", email="zane@example.org")
    zane_lib_response = client.get("/library")
    zane_library_url = str(zane_lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{zane_library_url}works/{yuki_work_id}/note",
        data={"content": "越权写入", "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    assert response.status_code == 404

    # 确认真没写进去：用 yuki 自己的账号重新登录查看，笔记栏应该仍是空的。
    client.post("/logout")
    client.post("/login", data={"username_or_email": "yuki", "password": PASSWORD})
    check_response = client.get(yuki_library_url)
    assert "越权写入" not in check_response.text


# ── 编辑元数据 ───────────────────────────────────────────────────────────


def test_edit_metadata_updates_title_year_container_and_abstract(client):
    _register(client, username="abel", email="abel@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}works/{work_id}/edit",
        data={
            "title": "A Renamed Paper",
            "year": "1999",
            "container_title": "Journal of Testing",
            "abstract": "一段新的摘要",
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )

    assert response.status_code == 200
    assert "A Renamed Paper" in response.text
    assert "1999" in response.text
    assert "Journal of Testing" in response.text
    assert "一段新的摘要" in response.text
    assert "A Sample Paper" not in response.text


def test_edit_metadata_rejects_non_numeric_year(client):
    _register(client, username="brad", email="brad@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}works/{work_id}/edit",
        data={"title": "whatever", "year": "not-a-year", "view": "all"},
    )

    assert response.status_code == 400


def test_edit_metadata_requires_login(client):
    _register(client, username="cora", email="cora@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.post(f"{library_url}works/{work_id}/edit", data={"title": "不该成功"})

    assert str(response.url).endswith("/login")


def test_edit_metadata_rejects_work_id_from_another_library(client):
    _register(client, username="dina", email="dina@example.org")
    dina_work_id = _import_sample(client)
    dina_lib_response = client.get("/library")
    dina_library_url = str(dina_lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    _register(client, username="ezra", email="ezra@example.org")
    ezra_lib_response = client.get("/library")
    ezra_library_url = str(ezra_lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{ezra_library_url}works/{dina_work_id}/edit",
        data={"title": "越权改写标题", "view": "all"},
    )
    assert response.status_code == 404

    client.post("/logout")
    client.post("/login", data={"username_or_email": "dina", "password": PASSWORD})
    check_response = client.get(dina_library_url)
    assert "越权改写标题" not in check_response.text
    assert "A Sample Paper" in check_response.text


# ── 导出整库 ─────────────────────────────────────────────────────────────


def test_export_ris_contains_the_imported_work(client):
    _register(client, username="finn", email="finn@example.org")
    _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.get(f"{library_url}export", params={"format": "ris"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-research-info-systems")
    assert "attachment" in response.headers["content-disposition"]
    assert "A Sample Paper" in response.text


def test_export_bibtex_and_csljson_also_work(client):
    _register(client, username="greta", email="greta@example.org")
    _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    bibtex_response = client.get(f"{library_url}export", params={"format": "bibtex"})
    assert bibtex_response.status_code == 200
    assert "A Sample Paper" in bibtex_response.text

    csljson_response = client.get(f"{library_url}export", params={"format": "csljson"})
    assert csljson_response.status_code == 200
    assert "A Sample Paper" in csljson_response.text


def test_export_rejects_unsupported_format(client):
    _register(client, username="hugo", email="hugo@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.get(f"{library_url}export", params={"format": "docx"})

    assert response.status_code == 400


def test_export_excludes_trashed_work(client):
    _register(client, username="iris", email="iris@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    client.post(f"{library_url}works/{work_id}/delete", data={"view": "all"})

    response = client.get(f"{library_url}export", params={"format": "ris"})
    assert "A Sample Paper" not in response.text


def test_export_requires_login(client):
    _register(client, username="jace", email="jace@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.get(f"{library_url}export", params={"format": "ris"})

    assert str(response.url).endswith("/login")


# ── 单条引用 ─────────────────────────────────────────────────────────────


def test_library_view_shows_formatted_citation_per_card(client):
    _register(client, username="kara", email="kara@example.org")
    _import_sample(client)

    response = client.get("/library")

    assert response.status_code == 200
    assert "citation-text" in response.text


def test_cite_work_downloads_single_record_in_each_format(client):
    _register(client, username="liam2", email="liam2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    for fmt in ("ris", "bibtex", "csljson"):
        response = client.get(f"{library_url}works/{work_id}/cite", params={"format": fmt})
        assert response.status_code == 200, (fmt, response.text)
        assert "A Sample Paper" in response.text


def test_cite_work_rejects_unsupported_format(client):
    _register(client, username="mara", email="mara@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.get(f"{library_url}works/{work_id}/cite", params={"format": "docx"})

    assert response.status_code == 400


def test_cite_work_rejects_work_id_from_another_library(client):
    _register(client, username="nico", email="nico@example.org")
    nico_work_id = _import_sample(client)
    client.post("/logout")

    _register(client, username="opal", email="opal@example.org")
    opal_lib_response = client.get("/library")
    opal_library_url = str(opal_lib_response.url).replace("http://testserver", "")

    response = client.get(f"{opal_library_url}works/{nico_work_id}/cite", params={"format": "ris"})

    assert response.status_code == 404


def test_cite_work_requires_login(client):
    _register(client, username="priya", email="priya@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.get(f"{library_url}works/{work_id}/cite", params={"format": "ris"})

    assert str(response.url).endswith("/login")


# ── 文件夹 ───────────────────────────────────────────────────────────────


def test_add_work_to_folder_then_remove_it(client):
    _register(client, username="quinn2", email="quinn2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    create_response = client.post(
        f"{library_url}folders/create", data={"name": "Methods4", "parent_folder_id": ""}
    )
    folder_id_match = re.search(r"folders/(\d+)/rename", create_response.text)
    assert folder_id_match is not None, create_response.text
    folder_id = int(folder_id_match.group(1))

    add_response = client.post(
        f"{library_url}works/{work_id}/folders/add",
        data={
            "folder_id": str(folder_id),
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )
    assert add_response.status_code == 200
    assert "Methods4" in add_response.text

    remove_response = client.post(
        f"{library_url}works/{work_id}/folders/{folder_id}/remove",
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    assert remove_response.status_code == 200
    # "Methods4" 本身还会出现在"加入文件夹……"下拉里（文件夹没被删，只是这篇
    # 文献不再挂在它下面）——真正要确认的是这张卡片的文件夹 chip（带移出表单）
    # 消失了，不是整页再也不出现这个名字。
    assert f"folders/{folder_id}/remove" not in remove_response.text


def test_add_work_to_folder_with_blank_selection_is_a_no_op(client):
    _register(client, username="rory2", email="rory2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}works/{work_id}/folders/add",
        data={"folder_id": "", "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )

    assert response.status_code == 200


def test_add_work_to_folder_rejects_work_id_from_another_library(client):
    # 和笔记路由同一类问题：features.organizing 的 bulk_add_to_folder 内部
    # 校验 work_id 不通过时抛裸 ValueError（会变成未处理的 500），所以页面层
    # 用 _require_work_in_library 先挡一遍，统一成 404——这里验证的就是这层
    # 页面守卫，不是 organizing 自己的异常类型。
    _register(client, username="sage2", email="sage2@example.org")
    sage_work_id = _import_sample(client)
    client.post("/logout")

    _register(client, username="tara2", email="tara2@example.org")
    tara_lib_response = client.get("/library")
    tara_library_url = str(tara_lib_response.url).replace("http://testserver", "")
    create_response = client.post(
        f"{tara_library_url}folders/create", data={"name": "TaraFolder", "parent_folder_id": ""}
    )
    folder_id_match = re.search(r"folders/(\d+)/rename", create_response.text)
    assert folder_id_match is not None, create_response.text
    folder_id = int(folder_id_match.group(1))

    response = client.post(
        f"{tara_library_url}works/{sage_work_id}/folders/add",
        data={
            "folder_id": str(folder_id),
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )
    assert response.status_code == 404


def test_add_work_to_folder_requires_login(client):
    _register(client, username="ursa2", email="ursa2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.post(f"{library_url}works/{work_id}/folders/add", data={"folder_id": "1"})

    assert str(response.url).endswith("/login")


# ── 附件 ─────────────────────────────────────────────────────────────────


def test_upload_attachment_then_download_then_delete(client):
    _register(client, username="vance4", email="vance4@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    upload_response = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
        files={"file": ("notes.txt", b"hello attachment", "text/plain")},
    )
    assert upload_response.status_code == 200
    assert "notes.txt" in upload_response.text

    attachment_id_match = re.search(r"attachments/(\d+)/download", upload_response.text)
    assert attachment_id_match is not None, upload_response.text
    attachment_id = int(attachment_id_match.group(1))

    download_response = client.get(f"{library_url}works/{work_id}/attachments/{attachment_id}/download")
    assert download_response.status_code == 200
    assert download_response.content == b"hello attachment"

    delete_response = client.post(
        f"{library_url}works/{work_id}/attachments/{attachment_id}/delete",
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    assert delete_response.status_code == 200
    assert "notes.txt" not in delete_response.text


def test_upload_attachment_requires_login(client):
    _register(client, username="wendy4", email="wendy4@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert str(response.url).endswith("/login")


def test_download_attachment_rejects_attachment_id_from_another_library(client):
    # 和笔记/文件夹同一类问题：features.uploading.download_attachment 自己
    # 不校验 attachment_id 是不是真的属于传入的 library_id——页面层的
    # _require_attachment_in_library 挡这一层。
    _register(client, username="xavier4", email="xavier4@example.org")
    xavier_work_id = _import_sample(client)
    xavier_lib_response = client.get("/library")
    xavier_library_url = str(xavier_lib_response.url).replace("http://testserver", "")
    upload_response = client.post(
        f"{xavier_library_url}works/{xavier_work_id}/attachments/upload",
        files={"file": ("secret.txt", b"xavier secret", "text/plain")},
    )
    attachment_id_match = re.search(r"attachments/(\d+)/download", upload_response.text)
    assert attachment_id_match is not None, upload_response.text
    attachment_id = int(attachment_id_match.group(1))
    client.post("/logout")

    _register(client, username="yolanda4", email="yolanda4@example.org")
    yolanda_work_id = _import_sample(client)
    yolanda_lib_response = client.get("/library")
    yolanda_library_url = str(yolanda_lib_response.url).replace("http://testserver", "")

    response = client.get(
        f"{yolanda_library_url}works/{yolanda_work_id}/attachments/{attachment_id}/download"
    )
    assert response.status_code == 404


def test_delete_attachment_rejects_attachment_id_from_another_library(client):
    _register(client, username="zach4", email="zach4@example.org")
    zach_work_id = _import_sample(client)
    zach_lib_response = client.get("/library")
    zach_library_url = str(zach_lib_response.url).replace("http://testserver", "")
    upload_response = client.post(
        f"{zach_library_url}works/{zach_work_id}/attachments/upload",
        files={"file": ("zach.txt", b"zach secret", "text/plain")},
    )
    attachment_id_match = re.search(r"attachments/(\d+)/download", upload_response.text)
    assert attachment_id_match is not None, upload_response.text
    attachment_id = int(attachment_id_match.group(1))
    client.post("/logout")

    _register(client, username="amara4", email="amara4@example.org")
    amara_work_id = _import_sample(client)
    amara_lib_response = client.get("/library")
    amara_library_url = str(amara_lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{amara_library_url}works/{amara_work_id}/attachments/{attachment_id}/delete"
    )
    assert response.status_code == 404
