import io
import re
import zipfile

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


def _import_with_title(client, *, title, year) -> int:
    raw_text = f"TY  - JOUR\nTI  - {title}\nPY  - {year}///\nER  -\n"
    r = client.post("/import", data={"format": "ris", "raw_text": raw_text})
    match = re.search(r"work_id=(\d+)", r.text)
    assert match is not None, r.text
    return int(match.group(1))


def _add_tag(client, library_url, work_id, name) -> int:
    """既锚定 work_id 又锚定标签名本身——同一篇文献打了不止一个标签时，
    裸的 `works/{work_id}/tags/(\\d+)/remove` 正则只会抓到页面上第一个
    标签 chip 的 id（和之前 attachment 下载链接正则踩过的同一种坑）。
    页面上同一个标签名还会在侧栏"按标签筛选"的 checkbox 里再出现一次
    （那里只是个 input，后面没有 remove 链接），所以不能只取 `name`
    第一次出现的位置——这里把 `name` 的每一处出现都试一遍，取第一个
    紧跟着这个 work_id 自己的 remove 链接的那个。
    """
    response = client.post(
        f"{library_url}works/{work_id}/tags/add",
        data={"name": name, "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )
    text = response.text
    for name_match in re.finditer(re.escape(name), text):
        window = text[name_match.start() : name_match.start() + 300]
        tag_id_match = re.search(rf"works/{work_id}/tags/(\d+)/remove", window)
        if tag_id_match is not None:
            return int(tag_id_match.group(1))
    raise AssertionError(f"没找到 {name} 对应 work {work_id} 的 remove 链接：{text}")


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
    # 不能断言 "machine-learning" 整页消失——这个标签还在库里的标签池中
    # （remove 只摘掉这篇文献和它的关联，不删标签本身），侧栏"按标签筛选"
    # 的 checkbox 列表会继续显示它。断言缩小到"这篇文献卡片上的 remove
    # 链接消失了"才是这个测试真正要测的事。
    assert f"works/{work_id}/tags/{tag_id}/remove" not in remove_response.text


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


# ── 侧栏筛选 ─────────────────────────────────────────────────────────────


def _create_folder(client, slug_and_id, name) -> int:
    client.post(f"/l/{slug_and_id}/folders/create", data={"name": name})
    folders_response = client.get(f"/l/{slug_and_id}/folders/")
    name_idx = folders_response.text.find(name)
    assert name_idx != -1, folders_response.text
    folder_id_match = re.search(r"folders/(\d+)/rename", folders_response.text[name_idx:])
    assert folder_id_match is not None, folders_response.text[name_idx:]
    return int(folder_id_match.group(1))


def test_filter_by_tag_id_shows_only_the_tagged_work(client):
    _register(client, username="viv", email="viv@example.org")
    kept_id = _import_with_title(client, title="Keep Me Paper", year=2021)
    _import_with_title(client, title="Other Paper", year=2022)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    tag_id = _add_tag(client, library_url, kept_id, "filter-me")

    response = client.get(library_url, params={"tag_ids": tag_id})

    assert "Keep Me Paper" in response.text
    assert "Other Paper" not in response.text


def test_filter_by_multiple_tag_ids_is_and_semantics(client):
    _register(client, username="wade", email="wade@example.org")
    both_id = _import_with_title(client, title="Has Both Tags", year=2021)
    only_one_id = _import_with_title(client, title="Has Only One Tag", year=2022)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    tag_a = _add_tag(client, library_url, both_id, "tag-a")
    tag_b = _add_tag(client, library_url, both_id, "tag-b")
    _add_tag(client, library_url, only_one_id, "tag-a")

    response = client.get(library_url, params={"tag_ids": [tag_a, tag_b]})

    assert "Has Both Tags" in response.text
    assert "Has Only One Tag" not in response.text


def test_filter_by_folder_id_shows_only_the_filed_work(client):
    _register(client, username="xena", email="xena@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    slug_and_id = library_url.strip("/").removeprefix("l/")
    filed_id = _import_with_title(client, title="Filed Paper", year=2021)
    _import_with_title(client, title="Unfiled Paper", year=2022)
    folder_id = _create_folder(client, slug_and_id, "my-folder")
    client.post(f"{library_url}works/{filed_id}/folders/add", data={"folder_id": str(folder_id)})

    response = client.get(library_url, params={"folder_id": folder_id})

    assert "Filed Paper" in response.text
    assert "Unfiled Paper" not in response.text


def test_filter_rejects_tag_id_from_another_library(client):
    _register(client, username="yana", email="yana@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Yana Paper", year=2021)
    other_tag_id = _add_tag(client, library_url, work_id, "yana-tag")
    client.post("/logout")

    _register(client, username="zack", email="zack@example.org")
    other_lib_response = client.get("/library")
    other_library_url = str(other_lib_response.url).replace("http://testserver", "")

    response = client.get(other_library_url, params={"tag_ids": other_tag_id})

    assert response.status_code == 400


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
    assert '<p class="muted">一段新的摘要</p>' in response.text
    assert "A Sample Paper" not in response.text


def test_work_card_shows_abstract_when_present_and_hides_block_when_absent(client):
    _register(client, username="abstractreader", email="abstractreader@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    before_response = client.get(library_url)
    assert "abstract-details" not in before_response.text

    client.post(
        f"{library_url}works/{work_id}/edit",
        data={
            "title": "",
            "year": "",
            "container_title": "",
            "abstract": "可展开阅读的摘要正文",
            "view": "all",
            "sort_by": "updated_at",
            "sort_dir": "desc",
            "page": "1",
        },
    )

    after_response = client.get(library_url)
    assert "abstract-details" in after_response.text
    assert "可展开阅读的摘要正文" in after_response.text


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


def test_export_with_attachments_returns_zip_with_bibliography_and_file(client):
    _register(client, username="greta2", email="greta2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("notes.txt", b"hello attachment", "text/plain")},
    )

    response = client.get(
        f"{library_url}export", params={"format": "ris", "with_attachments": "true"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "library.zip" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "bibliography.ris" in names
        assert any(name.startswith("attachments/") for name in names)
        assert "A Sample Paper" in archive.read("bibliography.ris").decode("utf-8")


def test_export_without_attachments_flag_returns_plain_text_not_zip(client):
    _register(client, username="greta3", email="greta3@example.org")
    _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.get(f"{library_url}export", params={"format": "ris"})

    assert response.headers["content-type"].startswith("application/x-research-info-systems")


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


# ── 设为 Main PDF / 在线查看 ─────────────────────────────────────────────


def test_upload_defaults_to_other_role_then_set_main_promotes_and_demotes(client):
    _register(client, username="bianca5", email="bianca5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    first = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("first.txt", b"first", "text/plain")},
    )
    first_id = int(re.search(r"attachments/(\d+)/download\">\s*first\.txt", first.text).group(1))
    # 默认 role 是 "other"，列表里不该出现"主"标记，应该有"设为主"按钮。
    assert "设为主" in first.text

    second = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("second.txt", b"second", "text/plain")},
    )
    second_id = int(re.search(r"attachments/(\d+)/download\">\s*second\.txt", second.text).group(1))

    set_main_response = client.post(f"{library_url}works/{work_id}/attachments/{second_id}/set-main")
    assert set_main_response.status_code == 200
    # second 升级成 main 之后不再有"设为主"按钮；first 仍然是 other、按钮还在。
    second_row = re.search(rf"attachments/{second_id}/download.*?</li>", set_main_response.text, re.S)
    first_row = re.search(rf"attachments/{first_id}/download.*?</li>", set_main_response.text, re.S)
    assert "设为主" not in second_row.group(0)
    assert "设为主" in first_row.group(0)


def test_set_main_attachment_requires_login(client):
    _register(client, username="carlos5", email="carlos5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    upload_response = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("a.txt", b"a", "text/plain")},
    )
    attachment_id = int(re.search(r"attachments/(\d+)/download", upload_response.text).group(1))
    client.post("/logout")

    response = client.post(f"{library_url}works/{work_id}/attachments/{attachment_id}/set-main")
    assert str(response.url).endswith("/login")


def test_set_main_attachment_rejects_attachment_id_from_another_library(client):
    _register(client, username="dahlia5", email="dahlia5@example.org")
    dahlia_work_id = _import_sample(client)
    dahlia_lib_response = client.get("/library")
    dahlia_library_url = str(dahlia_lib_response.url).replace("http://testserver", "")
    upload_response = client.post(
        f"{dahlia_library_url}works/{dahlia_work_id}/attachments/upload",
        files={"file": ("dahlia.txt", b"dahlia secret", "text/plain")},
    )
    attachment_id = int(re.search(r"attachments/(\d+)/download", upload_response.text).group(1))
    client.post("/logout")

    _register(client, username="emmett5", email="emmett5@example.org")
    emmett_work_id = _import_sample(client)
    emmett_lib_response = client.get("/library")
    emmett_library_url = str(emmett_lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{emmett_library_url}works/{emmett_work_id}/attachments/{attachment_id}/set-main"
    )
    assert response.status_code == 404


def test_view_attachment_renders_pdf_inline(client):
    _register(client, username="felix5", email="felix5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    pdf_bytes = b"%PDF-1.4 fake pdf content for test"
    upload_response = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
    )
    assert "在线查看" in upload_response.text
    attachment_id = int(re.search(r"attachments/(\d+)/download", upload_response.text).group(1))

    view_response = client.get(f"{library_url}works/{work_id}/attachments/{attachment_id}/view")
    assert view_response.status_code == 200
    assert view_response.content == pdf_bytes
    assert "inline" in view_response.headers["content-disposition"]


def test_view_attachment_rejects_non_viewable_content_type(client):
    _register(client, username="grace5", email="grace5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    upload_response = client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert "在线查看" not in upload_response.text
    attachment_id = int(re.search(r"attachments/(\d+)/download", upload_response.text).group(1))

    view_response = client.get(f"{library_url}works/{work_id}/attachments/{attachment_id}/view")
    assert view_response.status_code == 415


def test_view_attachment_rejects_attachment_id_from_another_library(client):
    _register(client, username="harlow5", email="harlow5@example.org")
    harlow_work_id = _import_sample(client)
    harlow_lib_response = client.get("/library")
    harlow_library_url = str(harlow_lib_response.url).replace("http://testserver", "")
    upload_response = client.post(
        f"{harlow_library_url}works/{harlow_work_id}/attachments/upload",
        files={"file": ("harlow.pdf", b"%PDF-1.4 harlow secret", "application/pdf")},
    )
    attachment_id = int(re.search(r"attachments/(\d+)/download", upload_response.text).group(1))
    client.post("/logout")

    _register(client, username="iris5", email="iris5@example.org")
    iris_work_id = _import_sample(client)
    iris_lib_response = client.get("/library")
    iris_library_url = str(iris_lib_response.url).replace("http://testserver", "")

    response = client.get(f"{iris_library_url}works/{iris_work_id}/attachments/{attachment_id}/view")
    assert response.status_code == 404


# ── 批量操作 ─────────────────────────────────────────────────────────────


def _batch_post(client, library_url, *, action, work_ids, **extra):
    data = {
        "action": action,
        "work_ids": [str(w) for w in work_ids],
        "view": "all",
        "sort_by": "updated_at",
        "sort_dir": "desc",
        "page": "1",
    }
    data.update(extra)
    return client.post(f"{library_url}batch", data=data)


def _batch_post_all_filtered(client, library_url, *, action, view="all", tag_ids=(), folder_id="", **extra):
    data = {
        "action": action,
        "selection_mode": "all_filtered",
        "tag_ids": [str(t) for t in tag_ids],
        "folder_id": str(folder_id),
        "view": view,
        "sort_by": "updated_at",
        "sort_dir": "desc",
        "page": "1",
    }
    data.update(extra)
    return client.post(f"{library_url}batch", data=data)


def test_batch_add_tag_then_remove_tag_applies_to_both_selected_works(client):
    _register(client, username="batch1", email="batch1@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_a = _import_with_title(client, title="Batch Tag A", year=2021)
    work_b = _import_with_title(client, title="Batch Tag B", year=2022)
    tag_id = _add_tag(client, library_url, work_a, "batch-tag")

    add_response = _batch_post(
        client, library_url, action="add_tag", work_ids=[work_a, work_b], target_tag_id=str(tag_id)
    )
    assert add_response.status_code == 200
    assert f"works/{work_b}/tags/{tag_id}/remove" in add_response.text
    assert f"works/{work_a}/tags/{tag_id}/remove" in add_response.text

    remove_response = _batch_post(
        client, library_url, action="remove_tag", work_ids=[work_a, work_b], target_tag_id=str(tag_id)
    )
    assert remove_response.status_code == 200
    assert f"works/{work_a}/tags/{tag_id}/remove" not in remove_response.text
    assert f"works/{work_b}/tags/{tag_id}/remove" not in remove_response.text


def test_batch_add_to_folder_then_remove_from_folder(client):
    _register(client, username="batch2", email="batch2@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    slug_and_id = library_url.strip("/").removeprefix("l/")
    work_a = _import_with_title(client, title="Batch Folder A", year=2021)
    work_b = _import_with_title(client, title="Batch Folder B", year=2022)
    folder_id = _create_folder(client, slug_and_id, "batch-folder")

    add_response = _batch_post(
        client, library_url, action="add_folder", work_ids=[work_a, work_b], target_folder_id=str(folder_id)
    )
    assert add_response.status_code == 200
    filtered = client.get(library_url, params={"folder_id": folder_id})
    assert "Batch Folder A" in filtered.text
    assert "Batch Folder B" in filtered.text

    remove_response = _batch_post(
        client, library_url, action="remove_folder", work_ids=[work_a], target_folder_id=str(folder_id)
    )
    assert remove_response.status_code == 200
    filtered_again = client.get(library_url, params={"folder_id": folder_id})
    assert "Batch Folder A" not in filtered_again.text
    assert "Batch Folder B" in filtered_again.text


def test_batch_soft_delete_then_restore(client):
    _register(client, username="batch3", email="batch3@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_a = _import_with_title(client, title="Batch Delete A", year=2021)
    work_b = _import_with_title(client, title="Batch Delete B", year=2022)

    delete_response = _batch_post(client, library_url, action="delete", work_ids=[work_a, work_b])
    assert delete_response.status_code == 200
    all_response = client.get(library_url, params={"view": "all"})
    assert "共 0 篇" in all_response.text
    trash_response = client.get(library_url, params={"view": "trash"})
    assert "共 2 篇" in trash_response.text

    restore_response = _batch_post(client, library_url, action="restore", work_ids=[work_a, work_b])
    assert restore_response.status_code == 200
    all_response_again = client.get(library_url, params={"view": "all"})
    assert "共 2 篇" in all_response_again.text


def test_batch_purge_requires_confirm_checkbox(client):
    _register(client, username="batch4", email="batch4@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Batch Purge Unconfirmed", year=2021)
    _batch_post(client, library_url, action="delete", work_ids=[work_id])

    response = _batch_post(client, library_url, action="purge", work_ids=[work_id])

    assert response.status_code == 200
    assert "确认" in response.text
    trash_response = client.get(library_url, params={"view": "trash"})
    assert "Batch Purge Unconfirmed" in trash_response.text


def test_batch_purge_with_confirm_permanently_removes_trashed_work(client):
    _register(client, username="batch5", email="batch5@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Batch Purge Confirmed", year=2021)
    _batch_post(client, library_url, action="delete", work_ids=[work_id])

    response = _batch_post(
        client, library_url, action="purge", work_ids=[work_id], confirm_purge="true"
    )

    assert response.status_code == 200
    trash_response = client.get(library_url, params={"view": "trash"})
    assert "共 0 篇" in trash_response.text


def test_batch_purge_on_non_trashed_work_reports_failure_but_does_not_raise(client):
    _register(client, username="batch6", email="batch6@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Batch Purge Not In Trash", year=2021)

    response = _batch_post(
        client, library_url, action="purge", work_ids=[work_id], confirm_purge="true"
    )

    assert response.status_code == 200
    assert "失败" in response.text
    all_response = client.get(library_url, params={"view": "all"})
    assert "Batch Purge Not In Trash" in all_response.text


def test_batch_action_with_no_work_ids_shows_error(client):
    _register(client, username="batch7", email="batch7@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = _batch_post(client, library_url, action="delete", work_ids=[])

    assert response.status_code == 200
    assert "没有选中" in response.text


def test_batch_add_tag_with_unselected_target_tag_shows_error(client):
    _register(client, username="batch8", email="batch8@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Batch No Target Tag", year=2021)

    response = _batch_post(client, library_url, action="add_tag", work_ids=[work_id], target_tag_id="")

    # _require_target_id 直接抛 HTTPException(400)，和 _parse_library_id/
    # sort_by 校验同一个风格——客户端传了不合法的参数，不是走友好重定向。
    assert response.status_code == 400


def test_batch_add_tag_rejects_tag_id_from_another_library(client):
    _register(client, username="batch9", email="batch9@example.org")
    batch9_lib_response = client.get("/library")
    batch9_library_url = str(batch9_lib_response.url).replace("http://testserver", "")
    batch9_work_id = _import_with_title(client, title="Batch9 Paper", year=2021)
    other_tag_id = _add_tag(client, batch9_library_url, batch9_work_id, "batch9-tag")
    client.post("/logout")

    _register(client, username="batch10", email="batch10@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Batch10 Paper", year=2021)

    response = _batch_post(
        client, library_url, action="add_tag", work_ids=[work_id], target_tag_id=str(other_tag_id)
    )

    assert response.status_code == 200
    assert "不属于这个库" in response.text


def test_batch_add_tag_with_work_id_from_another_library_fails_only_that_item(client):
    _register(client, username="batch11", email="batch11@example.org")
    batch11_lib_response = client.get("/library")
    batch11_library_url = str(batch11_lib_response.url).replace("http://testserver", "")
    foreign_work_id = _import_with_title(client, title="Batch11 Foreign Paper", year=2021)
    client.post("/logout")

    _register(client, username="batch12", email="batch12@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    own_work_id = _import_with_title(client, title="Batch12 Own Paper", year=2021)
    tag_id = _add_tag(client, library_url, own_work_id, "batch12-tag")
    client.post(
        f"{library_url}works/{own_work_id}/tags/{tag_id}/remove",
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )

    response = _batch_post(
        client, library_url, action="add_tag",
        work_ids=[own_work_id, foreign_work_id], target_tag_id=str(tag_id),
    )

    assert response.status_code == 200
    assert "失败" in response.text
    own_filtered = client.get(library_url, params={"tag_ids": tag_id})
    assert "Batch12 Own Paper" in own_filtered.text


def test_batch_action_requires_login(client):
    _register(client, username="batch13", email="batch13@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = _batch_post(client, library_url, action="delete", work_ids=[work_id])

    assert str(response.url).endswith("/login")


# ── 整目录批量上传 ───────────────────────────────────────────────────────


def test_upload_batch_directory_preserves_relative_paths_and_uploads_all_files(client):
    _register(client, username="dir1", email="dir1@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}works/{work_id}/attachments/upload-batch",
        files=[
            ("files", ("a.pdf", b"%PDF-1.4 file a", "application/pdf")),
            ("files", ("b.pdf", b"%PDF-1.4 file b", "application/pdf")),
        ],
        data={
            "rel_paths": ["mydir/a.pdf", "mydir/sub/b.pdf"],
            "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1",
        },
    )

    assert response.status_code == 200
    assert "a.pdf" in response.text
    assert "b.pdf" in response.text
    assert response.text.count("attachments/") >= 2


def test_upload_batch_directory_isolates_failed_file_from_succeeded_ones(client):
    _register(client, username="dir2", email="dir2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    # upload_file 默认不限制 media type、不开 reject_damaged，唯一默认就会
    # 拒绝的情形是超过 DEFAULT_MAX_BYTES（50MB）——用一个超限文件触发真实
    # 的 UploadRejected，而不是假装某个扩展名/content-type 会被拒绝
    # （这个项目目前没有按扩展名/类型的默认黑名单）。
    oversized = b"x" * (50 * 1024 * 1024 + 1024)
    response = client.post(
        f"{library_url}works/{work_id}/attachments/upload-batch",
        files=[
            ("files", ("good.pdf", b"%PDF-1.4 ok", "application/pdf")),
            ("files", ("too-big.bin", oversized, "application/octet-stream")),
        ],
        data={
            "rel_paths": ["mydir/good.pdf", "mydir/too-big.bin"],
            "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1",
        },
    )

    assert response.status_code == 200
    assert "good.pdf" in response.text
    assert "失败" in response.text


def test_upload_batch_directory_with_no_files_shows_error(client):
    _register(client, username="dir3", email="dir3@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    # 真实浏览器在 <input webkitdirectory multiple> 没选任何文件时根本不会
    # 带上 "files" 这个字段——这里用一个不相关的字段名强制 httpx 走
    # multipart 编码，同时让路由收到的 files 参数保持默认空列表。
    response = client.post(
        f"{library_url}works/{work_id}/attachments/upload-batch",
        files=[("_force_multipart", ("x.txt", b"x", "text/plain"))],
        data={"view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )

    assert response.status_code == 200
    assert "没有选择任何文件" in response.text


def test_upload_batch_directory_requires_login(client):
    _register(client, username="dir4", email="dir4@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = client.post(
        f"{library_url}works/{work_id}/attachments/upload-batch",
        files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))],
        data={"view": "all"},
    )

    assert str(response.url).endswith("/login")


def test_upload_batch_directory_rejects_work_id_from_another_library(client):
    _register(client, username="dir5", email="dir5@example.org")
    dir5_work_id = _import_sample(client)
    client.post("/logout")

    _register(client, username="dir6", email="dir6@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}works/{dir5_work_id}/attachments/upload-batch",
        files=[("files", ("a.pdf", b"%PDF-1.4", "application/pdf"))],
        data={"view": "all"},
    )

    assert response.status_code == 404


# ── 批量操作：全选所有筛选结果 ────────────────────────────────────────────


def test_batch_all_filtered_with_tag_filter_applies_to_every_matching_work_not_just_checked_ones(client):
    _register(client, username="allf1", email="allf1@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    tagged_a = _import_with_title(client, title="AllFiltered Tagged A", year=2021)
    tagged_b = _import_with_title(client, title="AllFiltered Tagged B", year=2022)
    untagged = _import_with_title(client, title="AllFiltered Untagged", year=2023)
    tag_id = _add_tag(client, library_url, tagged_a, "allf-tag")
    _add_tag(client, library_url, tagged_b, "allf-tag")

    # 不传任何 work_ids（模拟"一个都没勾"，纯靠 all_filtered 展开）。
    response = _batch_post_all_filtered(
        client, library_url, action="delete", tag_ids=[tag_id],
    )

    assert response.status_code == 200
    trash_response = client.get(library_url, params={"view": "trash"})
    assert "AllFiltered Tagged A" in trash_response.text
    assert "AllFiltered Tagged B" in trash_response.text
    all_response = client.get(library_url, params={"view": "all"})
    assert "AllFiltered Untagged" in all_response.text


def test_batch_all_filtered_with_folder_filter_restores_every_matching_trashed_work(client):
    _register(client, username="allf2", email="allf2@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    slug_and_id = library_url.strip("/").removeprefix("l/")
    filed = _import_with_title(client, title="AllFiltered Filed", year=2021)
    not_filed = _import_with_title(client, title="AllFiltered Not Filed", year=2022)
    folder_id = _create_folder(client, slug_and_id, "allf-folder")
    client.post(f"{library_url}works/{filed}/folders/add", data={"folder_id": str(folder_id)})
    _batch_post(client, library_url, action="delete", work_ids=[filed, not_filed])

    response = _batch_post_all_filtered(
        client, library_url, action="restore", view="trash", folder_id=folder_id,
    )

    assert response.status_code == 200
    all_response = client.get(library_url, params={"view": "all"})
    assert "AllFiltered Filed" in all_response.text
    assert "AllFiltered Not Filed" not in all_response.text
    trash_response = client.get(library_url, params={"view": "trash"})
    assert "AllFiltered Not Filed" in trash_response.text


def test_batch_all_filtered_rejects_tag_id_from_another_library(client):
    _register(client, username="allf3", email="allf3@example.org")
    allf3_lib_response = client.get("/library")
    allf3_library_url = str(allf3_lib_response.url).replace("http://testserver", "")
    allf3_work_id = _import_with_title(client, title="AllFiltered3 Paper", year=2021)
    other_tag_id = _add_tag(client, allf3_library_url, allf3_work_id, "allf3-tag")
    client.post("/logout")

    _register(client, username="allf4", email="allf4@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = _batch_post_all_filtered(
        client, library_url, action="delete", tag_ids=[other_tag_id],
    )

    assert response.status_code == 200
    assert "不属于这个库" in response.text


def test_batch_unrecognized_selection_mode_is_rejected(client):
    _register(client, username="allf5", email="allf5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = client.post(
        f"{library_url}batch",
        data={
            "action": "delete", "selection_mode": "bogus", "work_ids": [str(work_id)],
            "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1",
        },
    )

    assert response.status_code == 400


# ── 批量导出选中项 / 批量引用复制 ───────────────────────────────────────


def _export_selection_post(client, library_url, *, work_ids=(), selection_mode="explicit", **extra):
    data = {
        "work_ids": [str(w) for w in work_ids],
        "selection_mode": selection_mode,
        "format": "ris",
        "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1",
    }
    data.update(extra)
    return client.post(f"{library_url}export-selection", data=data)


def _cite_selection_post(client, library_url, *, work_ids=(), selection_mode="explicit", **extra):
    data = {
        "work_ids": [str(w) for w in work_ids],
        "selection_mode": selection_mode,
        "cite_format": "keys",
        "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1",
    }
    data.update(extra)
    return client.post(f"{library_url}cite-selection", data=data)


def test_export_selection_explicit_only_includes_checked_work_ids(client):
    _register(client, username="sel1", email="sel1@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    kept_id = _import_with_title(client, title="Selection Keep", year=2021)
    _import_with_title(client, title="Selection Skip", year=2022)

    response = _export_selection_post(client, library_url, work_ids=[kept_id])

    assert response.status_code == 200
    assert "Selection Keep" in response.text
    assert "Selection Skip" not in response.text


def test_export_selection_with_attachments_returns_zip(client):
    _register(client, username="sel2", email="sel2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post(
        f"{library_url}works/{work_id}/attachments/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    response = _export_selection_post(
        client, library_url, work_ids=[work_id], with_attachments="true"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "bibliography.ris" in archive.namelist()


def test_export_selection_all_filtered_uses_tag_filter(client):
    _register(client, username="sel3", email="sel3@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    tagged = _import_with_title(client, title="Export Tagged", year=2021)
    untagged = _import_with_title(client, title="Export Untagged", year=2022)
    tag_id = _add_tag(client, library_url, tagged, "export-sel-tag")

    response = _export_selection_post(
        client, library_url, selection_mode="all_filtered", tag_ids=[str(tag_id)]
    )

    assert response.status_code == 200
    assert "Export Tagged" in response.text
    assert "Export Untagged" not in response.text


def test_export_selection_with_no_selection_shows_error(client):
    _register(client, username="sel4", email="sel4@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    _import_sample(client)

    response = _export_selection_post(client, library_url, work_ids=[])

    assert response.status_code == 200
    assert "没有选中任何文献" in response.text


def test_export_selection_requires_login(client):
    _register(client, username="sel5", email="sel5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = _export_selection_post(client, library_url, work_ids=[work_id])

    assert str(response.url).endswith("/login")


def test_cite_selection_keys_returns_citekeys_for_selected_works_only(client):
    _register(client, username="sel6", email="sel6@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    kept_id = _import_with_title(client, title="Cite Keep", year=2021)
    _import_with_title(client, title="Cite Skip", year=2022)

    response = _cite_selection_post(client, library_url, work_ids=[kept_id], cite_format="keys")

    assert response.status_code == 200
    assert response.text.strip() != ""


def test_cite_selection_latex_wraps_keys_in_cite_command(client):
    _register(client, username="sel7", email="sel7@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_with_title(client, title="Cite Latex", year=2021)

    response = _cite_selection_post(
        client, library_url, work_ids=[work_id], cite_format="latex", latex_command="citep"
    )

    assert response.status_code == 200
    assert "\\citep{" in response.text


def test_cite_selection_all_filtered_uses_folder_filter(client):
    _register(client, username="sel8", email="sel8@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    slug_and_id = library_url.strip("/").removeprefix("l/")
    filed_id = _import_with_title(client, title="Cite Filed", year=2021)
    _import_with_title(client, title="Cite Not Filed", year=2022)
    folder_id = _create_folder(client, slug_and_id, "cite-sel-folder")
    client.post(f"{library_url}works/{filed_id}/folders/add", data={"folder_id": str(folder_id)})

    response = _cite_selection_post(
        client, library_url, selection_mode="all_filtered", folder_id=str(folder_id), cite_format="keys"
    )

    assert response.status_code == 200
    assert response.text.strip() != ""


def test_cite_selection_rejects_unrecognized_cite_format(client):
    _register(client, username="sel9", email="sel9@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    work_id = _import_sample(client)

    response = _cite_selection_post(client, library_url, work_ids=[work_id], cite_format="bogus")

    assert response.status_code == 400


def test_cite_selection_filters_out_work_id_from_another_library(client):
    _register(client, username="sel10", email="sel10@example.org")
    sel10_work_id = _import_sample(client)
    client.post("/logout")

    _register(client, username="sel11", email="sel11@example.org")
    own_work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    # 混进一个属于 sel10 的 work_id——cite_keys/cite_latex 内部不核对
    # library_id，必须靠这条路由自己先过 resolve_selection 把它滤掉。
    response = _cite_selection_post(
        client, library_url, work_ids=[own_work_id, sel10_work_id], cite_format="keys"
    )

    assert response.status_code == 200
    # 只有一个 key（own_work_id 的），不是两个——如果外库的 id 没被滤掉，
    # 这里会多出一条不该出现的 citekey。
    assert len(response.text.strip().splitlines()) == 1


def test_cite_selection_requires_login(client):
    _register(client, username="sel12", email="sel12@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    client.post("/logout")

    response = _cite_selection_post(client, library_url, work_ids=[work_id])

    assert str(response.url).endswith("/login")


# ── 侧栏标签排序 ─────────────────────────────────────────────────────────


def _move_tag_post(client, library_url, tag_id, direction):
    return client.post(
        f"{library_url}tags/{tag_id}/move",
        data={"direction": direction, "view": "all", "sort_by": "updated_at", "sort_dir": "desc", "page": "1"},
    )


def test_move_tag_up_swaps_with_previous_tag(client):
    _register(client, username="move1", email="move1@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    _add_tag(client, library_url, work_id, "move-first")
    second_id = _add_tag(client, library_url, work_id, "move-second")

    response = _move_tag_post(client, library_url, second_id, "up")

    assert response.status_code == 200
    assert response.text.index("move-second") < response.text.index("move-first")


def test_move_tag_down_swaps_with_next_tag(client):
    _register(client, username="move2", email="move2@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    first_id = _add_tag(client, library_url, work_id, "move-first2")
    _add_tag(client, library_url, work_id, "move-second2")

    response = _move_tag_post(client, library_url, first_id, "down")

    assert response.status_code == 200
    assert response.text.index("move-second2") < response.text.index("move-first2")


def test_move_tag_up_at_top_is_a_no_op(client):
    _register(client, username="move3", email="move3@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    first_id = _add_tag(client, library_url, work_id, "move-top")
    _add_tag(client, library_url, work_id, "move-bottom")

    response = _move_tag_post(client, library_url, first_id, "up")

    assert response.status_code == 200
    assert response.text.index("move-top") < response.text.index("move-bottom")


def test_move_tag_down_at_bottom_is_a_no_op(client):
    _register(client, username="move4", email="move4@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    _add_tag(client, library_url, work_id, "move-top2")
    last_id = _add_tag(client, library_url, work_id, "move-bottom2")

    response = _move_tag_post(client, library_url, last_id, "down")

    assert response.status_code == 200
    assert response.text.index("move-top2") < response.text.index("move-bottom2")


def test_move_tag_rejects_invalid_direction(client):
    _register(client, username="move5", email="move5@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    tag_id = _add_tag(client, library_url, work_id, "move-dir")

    response = _move_tag_post(client, library_url, tag_id, "sideways")

    assert response.status_code == 400


def test_move_tag_rejects_tag_id_from_another_library(client):
    _register(client, username="move6", email="move6@example.org")
    move6_work_id = _import_sample(client)
    move6_lib_response = client.get("/library")
    move6_library_url = str(move6_lib_response.url).replace("http://testserver", "")
    other_tag_id = _add_tag(client, move6_library_url, move6_work_id, "move6-tag")
    client.post("/logout")

    _register(client, username="move7", email="move7@example.org")
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")

    response = _move_tag_post(client, library_url, other_tag_id, "up")

    assert response.status_code == 404


def test_move_tag_requires_login(client):
    _register(client, username="move8", email="move8@example.org")
    work_id = _import_sample(client)
    lib_response = client.get("/library")
    library_url = str(lib_response.url).replace("http://testserver", "")
    tag_id = _add_tag(client, library_url, work_id, "move8-tag")
    client.post("/logout")

    response = _move_tag_post(client, library_url, tag_id, "up")

    assert str(response.url).endswith("/login")
