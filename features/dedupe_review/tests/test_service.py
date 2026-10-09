import pytest

from domain.attachments import create_attachment, list_attachments_for_work
from domain.folders import create_folder, list_folders_for_work
from domain.notes import get_note, set_note
from domain.tags import create_tag, list_tags_for_work
from domain.works import (
    WorkNotFound,
    add_identifier,
    create_work,
    get_work,
    list_duplicate_candidates,
    list_identifiers,
    record_duplicate_candidate,
)
from features.dedupe_review import bulk_dismiss_candidates, list_pending_candidates, merge_works

ACCOUNT = 1


# ── list_pending_candidates ──────────────────────────────────────────────


def test_list_pending_candidates_expands_both_sides(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    record_duplicate_candidate(
        db, library_id=library_id, work_id=w1, candidate_work_id=w2, reason="title_year_key"
    )

    pairs = list_pending_candidates(db, library_id=library_id)

    assert len(pairs) == 1
    assert pairs[0].work.id == w1
    assert pairs[0].candidate_work.id == w2
    assert pairs[0].candidate.status == "pending"


def test_list_pending_candidates_skips_pair_with_a_trashed_side(db, library_id):
    from domain.works import soft_delete_work

    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    record_duplicate_candidate(
        db, library_id=library_id, work_id=w1, candidate_work_id=w2, reason="title_year_key"
    )

    soft_delete_work(db, w2)  # 走别的路径（比如 organizing 的批量软删）被挪进回收站

    assert list_pending_candidates(db, library_id=library_id) == []


# ── bulk_dismiss_candidates ──────────────────────────────────────────────


def test_bulk_dismiss_marks_candidates_dismissed_without_touching_works(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    candidate = record_duplicate_candidate(
        db, library_id=library_id, work_id=w1, candidate_work_id=w2, reason="title_year_key"
    )

    result = bulk_dismiss_candidates(
        db, account_id=ACCOUNT, library_id=library_id, candidate_ids=[candidate.id]
    )

    assert result.job.status == "succeeded"
    assert list_pending_candidates(db, library_id=library_id) == []
    assert get_work(db, w1).id == w1  # 两条都还在，谁也没被删
    assert get_work(db, w2).id == w2


def test_bulk_dismiss_isolates_already_resolved_candidate_as_failure(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    w3 = create_work(db, library_id=library_id, title="丙").id
    c1 = record_duplicate_candidate(db, library_id=library_id, work_id=w1, candidate_work_id=w2, reason="r")
    c2 = record_duplicate_candidate(db, library_id=library_id, work_id=w1, candidate_work_id=w3, reason="r")

    # c2 已经被处理过（比如另一个标签页先提交了）
    bulk_dismiss_candidates(db, account_id=ACCOUNT, library_id=library_id, candidate_ids=[c2.id])

    result = bulk_dismiss_candidates(
        db, account_id=ACCOUNT, library_id=library_id, candidate_ids=[c1.id, c2.id]
    )

    assert result.job.status == "failed"
    assert result.job.counts == {"total": 2, "done": 1, "failed": 1}
    statuses = {o.item_id: o.status for o in result.outcomes}
    assert statuses == {c1.id: "done", c2.id: "failed"}


# ── merge_works ──────────────────────────────────────────────────────────


def test_merge_works_rejects_same_id(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    with pytest.raises(ValueError):
        merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w1)


def test_merge_works_rejects_cross_library(db, library_id):
    from domain.libraries import create_library

    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=other_library_id, title="别的库").id
    with pytest.raises(ValueError):
        merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)


def test_merge_works_purges_merge_work_and_keeps_keep_work(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id

    kept = merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    assert kept.id == w1
    with pytest.raises(WorkNotFound):
        get_work(db, w2, include_deleted=True)


def test_merge_works_migrates_tags_and_folders(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    tag = create_tag(db, library_id=library_id, name="重要")
    folder = create_folder(db, library_id=library_id, name="收藏")
    from domain.folders import add_work_to_folder
    from domain.tags import add_tag_to_work

    add_tag_to_work(db, library_id=library_id, work_id=w2, tag_id=tag.id)
    add_work_to_folder(db, library_id=library_id, work_id=w2, folder_id=folder.id)

    merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    assert {t.id for t in list_tags_for_work(db, w1)} == {tag.id}
    assert {f.id for f in list_folders_for_work(db, w1)} == {folder.id}


def test_merge_works_moves_note_when_keep_has_none(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    set_note(db, library_id=library_id, work_id=w2, content="merge 的笔记")

    merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    assert get_note(db, w1).content == "merge 的笔记"


def test_merge_works_concatenates_notes_when_both_have_one(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    set_note(db, library_id=library_id, work_id=w1, content="keep 的笔记")
    set_note(db, library_id=library_id, work_id=w2, content="merge 的笔记")

    merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    assert get_note(db, w1).content == "keep 的笔记\n\n---\n\nmerge 的笔记"


def test_merge_works_migrates_identifier_without_conflict(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    add_identifier(db, library_id=library_id, work_id=w2, scheme="doi", value="10.1/abc")

    merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    identifiers = list_identifiers(db, w1)
    assert [(i.scheme, i.value_norm) for i in identifiers] == [("doi", "10.1/abc")]


def test_merge_works_migrates_attachment_as_other_role_and_keeps_shared_blob(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    create_attachment(
        db, library_id=library_id, work_id=w2, filename="main.pdf",
        digest="a" * 64, size=100, role="main",
    )

    merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    kept_attachments = list_attachments_for_work(db, w1)
    assert len(kept_attachments) == 1
    assert kept_attachments[0].role == "other"  # 不抢占 keep_work 的 main
    assert kept_attachments[0].digest == "a" * 64


def test_merge_works_clears_dangling_duplicate_candidates_about_merge_work(db, library_id):
    w1 = create_work(db, library_id=library_id, title="甲").id
    w2 = create_work(db, library_id=library_id, title="乙").id
    w3 = create_work(db, library_id=library_id, title="丙").id
    record_duplicate_candidate(db, library_id=library_id, work_id=w1, candidate_work_id=w2, reason="r")
    # w2 同时也被怀疑和 w3 重复——这条候选跟这次合并无关，但 w2 没了之后
    # 它自然失去意义，应该随 purge_work 的级联一起消失
    record_duplicate_candidate(db, library_id=library_id, work_id=w2, candidate_work_id=w3, reason="r")

    merge_works(db, library_id=library_id, keep_work_id=w1, merge_work_id=w2)

    remaining = list_duplicate_candidates(db, library_id=library_id)
    assert remaining == []
