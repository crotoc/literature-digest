import re

PASSWORD = "correct-horse-battery-staple"

SAME_TITLE_RIS = """TY  - JOUR
TI  - Duplicate Candidate Paper
PY  - 2021///
ER  -
"""


def _register(client, *, username, email):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def _import(client, raw_text=SAME_TITLE_RIS) -> int:
    r = client.post("/import", data={"format": "ris", "raw_text": raw_text})
    match = re.search(r"work_id=(\d+)", r.text)
    assert match is not None, r.text
    return int(match.group(1))


def _library_url(client) -> str:
    r = client.get("/library")
    return str(r.url).replace("http://testserver", "")


def _make_pending_pair(client):
    """导入两篇标题/年份完全一样的记录，触发 title_year_key 命中、自动
    建一条 pending 候选，返回 (library_url, work_id, dup_work_id)。
    """
    work_id = _import(client)
    dup_work_id = _import(client)
    library_url = _library_url(client)
    return library_url, work_id, dup_work_id


def test_dedupe_entry_redirects_anonymous_to_login(client):
    response = client.get("/dedupe")
    assert str(response.url).endswith("/login")


def test_importing_same_title_year_creates_a_pending_pair(client):
    _register(client, username="ada6", email="ada6@example.org")
    library_url, work_id, dup_work_id = _make_pending_pair(client)

    response = client.get(f"{library_url}dedupe/")
    assert response.status_code == 200
    assert "Duplicate Candidate Paper" in response.text
    assert response.text.count("Duplicate Candidate Paper") == 2
    assert "title_year_key_match" in response.text


def test_dismiss_candidate_removes_it_from_the_pending_list(client):
    _register(client, username="brecht6", email="brecht6@example.org")
    library_url, work_id, dup_work_id = _make_pending_pair(client)
    index = client.get(f"{library_url}dedupe/")
    candidate_id = int(re.search(r"dedupe/(\d+)/dismiss", index.text).group(1))

    response = client.post(f"{library_url}dedupe/{candidate_id}/dismiss")
    assert response.status_code == 200
    assert "Duplicate Candidate Paper" not in response.text
    assert "没有待复核" in response.text


def test_dismiss_rejects_candidate_id_from_another_library(client):
    _register(client, username="celia6", email="celia6@example.org")
    celia_library_url, _, _ = _make_pending_pair(client)
    celia_index = client.get(f"{celia_library_url}dedupe/")
    candidate_id = int(re.search(r"dedupe/(\d+)/dismiss", celia_index.text).group(1))
    client.post("/logout")

    _register(client, username="deshawn6", email="deshawn6@example.org")
    deshawn_library_url = _library_url(client)

    response = client.post(f"{deshawn_library_url}dedupe/{candidate_id}/dismiss")
    assert response.status_code == 200
    assert "不属于这个库，或已经被处理过" in response.text


def test_merge_keeping_one_side_deletes_the_other(client):
    # record_duplicate_candidate 的 work_id 是刚导入的那条（第二次 import 的
    # dup_work_id）、candidate_work_id 是命中的既有记录（第一次 import 的
    # work_id）——所以 keep="work" 实际保留的是 dup_work_id，删掉 work_id。
    # 标题完全相同不能用来区分两条记录，这里改用各自的 /edit 路由地址
    # （带着唯一的 work_id）当标记。
    _register(client, username="elan6", email="elan6@example.org")
    library_url, work_id, dup_work_id = _make_pending_pair(client)
    index = client.get(f"{library_url}dedupe/")
    candidate_id = int(re.search(r"dedupe/(\d+)/dismiss", index.text).group(1))

    response = client.post(f"{library_url}dedupe/{candidate_id}/merge", data={"keep": "work"})
    assert response.status_code == 200
    assert "没有待复核" in response.text

    # 保留的那条（dup_work_id）还在文库列表里；被合并掉的那条（work_id）
    # 已经彻底消失（不是回收站，trash 视图里也看不到）。
    all_view = client.get(f"{library_url}?view=all")
    assert f"works/{dup_work_id}/edit" in all_view.text
    assert f"works/{work_id}/edit" not in all_view.text
    trash_view = client.get(f"{library_url}?view=trash")
    assert f"works/{work_id}" not in trash_view.text


def test_merge_rejects_invalid_keep_value(client):
    _register(client, username="farrah6", email="farrah6@example.org")
    library_url, work_id, dup_work_id = _make_pending_pair(client)
    index = client.get(f"{library_url}dedupe/")
    candidate_id = int(re.search(r"dedupe/(\d+)/dismiss", index.text).group(1))

    response = client.post(f"{library_url}dedupe/{candidate_id}/merge", data={"keep": "nonsense"})
    assert response.status_code == 400


def test_merge_requires_login(client):
    _register(client, username="gable6", email="gable6@example.org")
    library_url, work_id, dup_work_id = _make_pending_pair(client)
    index = client.get(f"{library_url}dedupe/")
    candidate_id = int(re.search(r"dedupe/(\d+)/dismiss", index.text).group(1))
    client.post("/logout")

    response = client.post(f"{library_url}dedupe/{candidate_id}/merge", data={"keep": "work"})
    assert str(response.url).endswith("/login")
