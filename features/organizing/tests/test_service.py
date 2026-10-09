import pytest

from caps.bibformats import Person
from domain.attachments import create_attachment
from domain.folders import create_folder, list_folders_for_work
from domain.jobs import list_child_jobs
from domain.libraries import create_library
from domain.notes import get_note, set_note
from domain.tags import WorkTagNotFound, create_tag, get_tag, list_tags_for_work, remove_tag_from_work
from domain.works import WorkNotFound, create_work, get_work
from features.organizing import (
    bulk_add_tag,
    bulk_add_to_folder,
    bulk_remove_from_folder,
    bulk_remove_tag,
    bulk_restore,
    bulk_soft_delete,
    create_tag_and_apply,
    purge_works,
)

ACCOUNT = 1


# ── 批量打标签 ───────────────────────────────────────────────────────────


def test_bulk_add_tag_all_succeed(db, library_id):
    w1 = create_work(db, library_id=library_id, title="A").id
    w2 = create_work(db, library_id=library_id, title="B").id
    tag = create_tag(db, library_id=library_id, name="重要")

    result = bulk_add_tag(db, account_id=ACCOUNT, library_id=library_id, work_ids=[w1, w2], tag_id=tag.id)

    assert result.job.status == "succeeded"
    assert result.job.counts == {"total": 2, "done": 2, "failed": 0}
    assert {t.id for t in list_tags_for_work(db, w1)} == {tag.id}
    assert {t.id for t in list_tags_for_work(db, w2)} == {tag.id}


def test_bulk_add_tag_isolates_cross_library_failure(db, library_id, work_id):
    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    other_work_id = create_work(db, library_id=other_library_id, title="别的库里的文献").id
    tag = create_tag(db, library_id=library_id, name="重要")

    result = bulk_add_tag(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id, other_work_id], tag_id=tag.id
    )

    assert result.job.status == "failed"
    assert result.job.counts == {"total": 2, "done": 1, "failed": 1}
    statuses = {o.work_id: o.status for o in result.outcomes}
    assert statuses == {work_id: "done", other_work_id: "failed"}
    children = list_child_jobs(db, result.job.id)
    assert len(children) == 1
    assert "不属于这个库" in children[0].reason


def test_bulk_add_tag_rejects_tag_from_other_library_before_creating_job(db, library_id, work_id):
    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    other_tag = create_tag(db, library_id=other_library_id, name="别的库的标签")

    with pytest.raises(ValueError):
        bulk_add_tag(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], tag_id=other_tag.id)


def test_bulk_remove_tag_treats_missing_pairing_as_success_not_failure(db, library_id):
    w1 = create_work(db, library_id=library_id, title="A").id
    w2 = create_work(db, library_id=library_id, title="B").id
    tag = create_tag(db, library_id=library_id, name="重要")
    bulk_add_tag(db, account_id=ACCOUNT, library_id=library_id, work_ids=[w1], tag_id=tag.id)
    # w2 从来没打过这个标签——混合选区批量去标签时的常态

    result = bulk_remove_tag(db, account_id=ACCOUNT, library_id=library_id, work_ids=[w1, w2], tag_id=tag.id)

    assert result.job.status == "succeeded"
    assert result.job.counts == {"total": 2, "done": 2, "failed": 0}
    assert list_tags_for_work(db, w1) == []
    with pytest.raises(WorkTagNotFound):
        remove_tag_from_work(db, work_id=w2, tag_id=tag.id)  # 确认本来就没有


def test_create_tag_and_apply_normalizes_name_then_applies(db, library_id, work_id):
    tag, result = create_tag_and_apply(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], name="  ＡＩ　综述  "
    )
    assert tag.name == "AI 综述"  # NFKC 全角转半角 + 折叠空白 + 去首尾
    assert result.job.status == "succeeded"
    assert {t.id for t in list_tags_for_work(db, work_id)} == {tag.id}


# ── 批量加移文件夹 ───────────────────────────────────────────────────────


def test_bulk_add_and_remove_folder(db, library_id):
    w1 = create_work(db, library_id=library_id, title="A").id
    w2 = create_work(db, library_id=library_id, title="B").id
    folder = create_folder(db, library_id=library_id, name="收藏夹")

    add_result = bulk_add_to_folder(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[w1, w2], folder_id=folder.id
    )
    assert add_result.job.status == "succeeded"
    assert {f.id for f in list_folders_for_work(db, w1)} == {folder.id}

    remove_result = bulk_remove_from_folder(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[w1], folder_id=folder.id
    )
    assert remove_result.job.status == "succeeded"
    assert list_folders_for_work(db, w1) == []


def test_bulk_remove_from_folder_treats_not_in_folder_as_success(db, library_id, work_id):
    folder = create_folder(db, library_id=library_id, name="收藏夹")
    result = bulk_remove_from_folder(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], folder_id=folder.id
    )
    assert result.job.status == "succeeded"
    assert result.job.counts == {"total": 1, "done": 1, "failed": 0}


# ── 回收站：软删/恢复 ────────────────────────────────────────────────────


def test_bulk_soft_delete_and_restore_roundtrip(db, library_id, work_id):
    delete_result = bulk_soft_delete(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id])
    assert delete_result.job.status == "succeeded"
    with pytest.raises(WorkNotFound):
        get_work(db, work_id)  # 默认视图看不到已删
    assert get_work(db, work_id, include_deleted=True).deleted_at is not None

    restore_result = bulk_restore(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id])
    assert restore_result.job.status == "succeeded"
    assert get_work(db, work_id).deleted_at is None


def test_bulk_soft_delete_is_idempotent(db, library_id, work_id):
    bulk_soft_delete(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id])
    second = bulk_soft_delete(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id])
    assert second.job.status == "succeeded"  # 再删一次不报错


# ── 彻底删除 ─────────────────────────────────────────────────────────────


def test_purge_rejects_work_not_in_trash(db, library_id, work_id):
    result = purge_works(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], blob_store=None
    )
    assert result.job.status == "failed"
    assert result.outcomes[0].status == "failed"
    assert "还没有移入回收站" in result.outcomes[0].reason


def test_purge_cascades_tags_folders_notes_attachments_and_blob(db, library_id, blob_store):
    work_id = create_work(db, library_id=library_id, title="要删的文献", authors=(Person(family="X"),)).id
    tag = create_tag(db, library_id=library_id, name="临时标签")
    folder = create_folder(db, library_id=library_id, name="临时文件夹")
    bulk_add_tag(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], tag_id=tag.id)
    bulk_add_to_folder(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], folder_id=folder.id)
    set_note(db, library_id=library_id, work_id=work_id, content="一些笔记")
    put_result = blob_store.put(b"pdf bytes")
    create_attachment(
        db, library_id=library_id, work_id=work_id, filename="a.pdf",
        digest=put_result.digest, size=put_result.blob.size,
    )

    bulk_soft_delete(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id])
    result = purge_works(
        db, account_id=ACCOUNT, library_id=library_id, work_ids=[work_id], blob_store=blob_store
    )

    assert result.job.status == "succeeded"
    assert list_tags_for_work(db, work_id) == []
    assert list_folders_for_work(db, work_id) == []
    assert get_note(db, work_id) is None
    assert not blob_store.exists(put_result.digest)
    with pytest.raises(WorkNotFound):
        get_work(db, work_id, include_deleted=True)
    # 标签/文件夹本身没被删，只是解除了和这篇文献的关联
    assert get_tag(db, tag.id).id == tag.id


def test_purge_keeps_shared_blob_when_another_attachment_still_references_it(db, library_id, blob_store):
    work1_id = create_work(db, library_id=library_id, title="文献一").id
    work2_id = create_work(db, library_id=library_id, title="文献二").id
    put_result = blob_store.put(b"shared pdf bytes")
    create_attachment(
        db, library_id=library_id, work_id=work1_id, filename="shared.pdf",
        digest=put_result.digest, size=put_result.blob.size,
    )
    create_attachment(
        db, library_id=library_id, work_id=work2_id, filename="shared.pdf",
        digest=put_result.digest, size=put_result.blob.size,
    )

    bulk_soft_delete(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work1_id])
    purge_works(db, account_id=ACCOUNT, library_id=library_id, work_ids=[work1_id], blob_store=blob_store)

    assert blob_store.exists(put_result.digest)  # work2 还引用着
