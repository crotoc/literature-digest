import json

import pytest

from domain.jobs import list_jobs
from domain.libraries import LibraryNotFound
from domain.works import (
    add_identifier,
    create_work,
    find_by_identifier,
    get_work,
    list_duplicate_candidates,
    list_works,
)
from features.importing.service import JOB_KIND_ITEM, ImportParseFailed, import_records

ACCOUNT = 1
OTHER_ACCOUNT = 2


def _csl(**overrides):
    item = {
        "id": "rec1",
        "type": "article-journal",
        "title": "Gene expression in X",
        "author": [{"family": "Smith", "given": "Jane"}],
        "issued": {"date-parts": [[2020, 3, 1]]},
    }
    item.update(overrides)
    return item


def _raw(*items):
    return json.dumps(list(items))


def test_import_creates_new_work_and_attaches_identifier(db, library_id):
    raw = _raw(_csl(DOI="10.1000/abc"))

    result = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=raw)

    assert [o.status for o in result.outcomes] == ["created"]
    work = find_by_identifier(db, library_id=library_id, scheme="doi", value="10.1000/abc")
    assert work is not None and work.id == result.outcomes[0].work_id
    assert result.job.status == "succeeded"
    assert result.job.counts == {"total": 1, "created": 1, "merged": 0, "flagged_duplicate": 0, "failed": 0}


def test_import_same_doi_second_time_merges_and_fills_missing_fields_only(db, library_id):
    first = _raw(_csl(DOI="10.1000/abc", title="Original Title"))
    import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=first)

    second = _raw(_csl(DOI="10.1000/abc", title="Different Title", abstract="Filled in abstract"))
    result = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=second)

    assert [o.status for o in result.outcomes] == ["merged"]
    work = get_work(db, result.outcomes[0].work_id)
    assert work.title == "Original Title"  # 已有字段不被覆盖
    assert work.abstract == "Filled in abstract"  # 原来是空，补上了
    assert len(list_works(db, library_id=library_id)) == 1  # 没有多建一条


def test_import_without_identifier_flags_title_year_duplicate(db, library_id):
    first = _raw(_csl(id="rec-a"))
    r1 = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=first)
    first_work_id = r1.outcomes[0].work_id
    assert r1.outcomes[0].status == "created"

    second = _raw(_csl(id="rec-b"))  # 相同标题+年份，同样没有标识符
    r2 = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=second)

    outcome = r2.outcomes[0]
    assert outcome.status == "flagged_duplicate"
    assert outcome.work_id != first_work_id
    assert len(outcome.duplicate_candidate_ids) == 1

    candidates = list_duplicate_candidates(db, library_id=library_id)
    match = [c for c in candidates if c.id == outcome.duplicate_candidate_ids[0]][0]
    assert match.work_id == outcome.work_id
    assert match.candidate_work_id == first_work_id
    assert match.reason == "title_year_key_match"


def test_import_unsupported_format_raises_without_creating_job(db, library_id):
    with pytest.raises(ImportParseFailed):
        import_records(db, account_id=ACCOUNT, library_id=library_id, format="unknown", raw_text="[]")

    assert list_jobs(db, account_id=ACCOUNT) == []


def test_import_malformed_json_raises_parse_failed(db, library_id):
    with pytest.raises(ImportParseFailed):
        import_records(
            db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text="{not valid"
        )


def test_import_rejects_account_without_library_access(db, library_id):
    with pytest.raises(LibraryNotFound):
        import_records(
            db, account_id=OTHER_ACCOUNT, library_id=library_id, format="csljson", raw_text=_raw(_csl())
        )


def test_import_item_failure_is_isolated_from_other_items(db, library_id):
    work_a = create_work(db, library_id=library_id, title="Existing A")
    add_identifier(db, library_id=library_id, work_id=work_a.id, scheme="doi", value="10.1000/conflict")
    work_b = create_work(db, library_id=library_id, title="Existing B")
    add_identifier(db, library_id=library_id, work_id=work_b.id, scheme="pmid", value="999")

    # 这条记录的 DOI 命中 work_a（触发合并），但它的 PMID 已经属于另一条
    # 完全不同的 work_b——这是真实的标识符冲突，应该让这一条失败，而不是
    # 静默吞掉或者打断整批导入。
    conflicting = _csl(id="bad", DOI="10.1000/conflict", PMID="999")
    clean = _csl(id="good", title="Brand New", DOI="10.1000/clean")
    raw = _raw(conflicting, clean)

    result = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=raw)

    assert [o.status for o in result.outcomes] == ["failed", "created"]
    assert result.outcomes[0].work_id is None
    assert result.outcomes[0].reason is not None
    assert result.job.status == "failed"
    assert result.job.counts["failed"] == 1
    assert result.job.counts["created"] == 1


def test_import_creates_one_child_job_per_record(db, library_id):
    raw = _raw(_csl(id="a", DOI="10.1000/a"), _csl(id="b", DOI="10.1000/b"))

    result = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text=raw)

    children = list_jobs(db, account_id=ACCOUNT, parent_job_id=result.job.id)
    assert len(children) == 2
    assert {c.kind for c in children} == {JOB_KIND_ITEM}
    assert {c.status for c in children} == {"succeeded"}


def test_import_empty_list_produces_zero_item_successful_job(db, library_id):
    result = import_records(db, account_id=ACCOUNT, library_id=library_id, format="csljson", raw_text="[]")

    assert result.outcomes == ()
    assert result.job.status == "succeeded"
    assert result.job.counts["total"] == 0
