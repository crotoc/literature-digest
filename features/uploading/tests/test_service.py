import pytest

from caps.bibformats import Person
from caps.template import UnknownPlaceholder
from domain.attachments import get_attachment, list_attachments_for_work
from domain.jobs import list_child_jobs
from domain.libraries import create_library
from domain.works import create_work
from features.uploading import (
    FilenameConflict,
    UploadInput,
    UploadRejected,
    download_attachment,
    remove_attachment,
    resolve_naming_template,
    set_naming_template,
    upload_batch,
    upload_file,
)

ACCOUNT = 1

PDF_BYTES = b"%PDF-1.4\n%fake pdf content for tests\n%%EOF"
TEXT_BYTES = b"hello, this is plain text, not a pdf"


# ── upload_file：基本成功路径 ────────────────────────────────────────────


def test_upload_file_creates_attachment(db, library_id, work_id, blob_store):
    attachment = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    assert attachment.filename == "paper.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.size == len(PDF_BYTES)
    assert blob_store.exists(attachment.digest)


def test_upload_file_rejects_oversized(db, library_id, work_id, blob_store):
    with pytest.raises(UploadRejected):
        upload_file(
            db, library_id=library_id, work_id=work_id, filename="paper.pdf",
            content=PDF_BYTES, blob_store=blob_store, max_bytes=5,
        )


def test_upload_file_rejects_disallowed_media_type(db, library_id, work_id, blob_store):
    with pytest.raises(UploadRejected):
        upload_file(
            db, library_id=library_id, work_id=work_id, filename="notes.txt",
            content=TEXT_BYTES, blob_store=blob_store,
            allowed_media_types=frozenset({"application/pdf"}),
        )


def test_upload_file_rejects_cross_library_work(db, library_id, work_id, blob_store):
    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    with pytest.raises(ValueError):
        upload_file(
            db, library_id=other_library_id, work_id=work_id, filename="paper.pdf",
            content=PDF_BYTES, blob_store=blob_store,
        )


# ── 重名策略 ─────────────────────────────────────────────────────────────


def test_upload_file_on_conflict_ask_raises(db, library_id, work_id, blob_store):
    upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    with pytest.raises(FilenameConflict):
        upload_file(
            db, library_id=library_id, work_id=work_id, filename="paper.pdf",
            content=PDF_BYTES, blob_store=blob_store, on_conflict="ask",
        )


def test_upload_file_on_conflict_rename_dedupes(db, library_id, work_id, blob_store):
    first = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    second = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES + b"more", blob_store=blob_store, on_conflict="rename",
    )
    assert first.filename == "paper.pdf"
    assert second.filename == "paper (1).pdf"


def test_upload_file_on_conflict_overwrite_replaces(db, library_id, work_id, blob_store):
    first = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    second = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES + b"more", blob_store=blob_store, on_conflict="overwrite",
    )
    remaining = list_attachments_for_work(db, work_id)
    assert len(remaining) == 1
    assert remaining[0].id == second.id
    assert not blob_store.exists(first.digest)  # 旧 blob 变孤儿后被删了
    assert blob_store.exists(second.digest)


def test_upload_file_on_conflict_overwrite_keeps_blob_if_identical_content(
    db, library_id, work_id, blob_store
):
    first = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store, on_conflict="overwrite",
    )
    # 新旧内容完全一样：同一个 digest 仍然被新行引用着，不该被当成孤儿删掉
    assert blob_store.exists(first.digest)


# ── 重命名模板 ───────────────────────────────────────────────────────────


def test_upload_file_naming_template_renders_from_work(db, library_id, blob_store):
    work = create_work(
        db, library_id=library_id, title="深度学习综述", year=2021,
        authors=(Person(family="张"),),
    )
    attachment = upload_file(
        db, library_id=library_id, work_id=work.id, filename="whatever-original-name.pdf",
        content=PDF_BYTES, blob_store=blob_store, naming_template="[firstauthor:1]_[year]",
    )
    assert attachment.filename == "张_2021.pdf"


def test_naming_template_setting_resolves_and_validates(db):
    assert resolve_naming_template(db, account_id=ACCOUNT) is None
    set_naming_template(db, account_id=ACCOUNT, template="[firstauthor:1]_[year]_[title:30]")
    assert resolve_naming_template(db, account_id=ACCOUNT) == "[firstauthor:1]_[year]_[title:30]"
    with pytest.raises(UnknownPlaceholder):
        set_naming_template(db, account_id=ACCOUNT, template="[not_a_real_field]")


# ── download / remove ───────────────────────────────────────────────────


def test_download_attachment_returns_bytes(db, library_id, work_id, blob_store):
    attachment = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    dto, stream = download_attachment(db, attachment.id, blob_store=blob_store)
    assert dto.id == attachment.id
    assert stream.read() == PDF_BYTES


def test_remove_attachment_deletes_blob_when_orphaned(db, library_id, work_id, blob_store):
    attachment = upload_file(
        db, library_id=library_id, work_id=work_id, filename="paper.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    remove_attachment(db, attachment.id, blob_store=blob_store)
    assert not blob_store.exists(attachment.digest)
    assert list_attachments_for_work(db, work_id) == []


def test_remove_attachment_keeps_blob_when_still_referenced(db, library_id, work_id, blob_store):
    work2_id = create_work(db, library_id=library_id, title="另一篇").id
    a1 = upload_file(
        db, library_id=library_id, work_id=work_id, filename="shared.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    a2 = upload_file(
        db, library_id=library_id, work_id=work2_id, filename="shared.pdf",
        content=PDF_BYTES, blob_store=blob_store,
    )
    assert a1.digest == a2.digest  # 内容寻址，同内容共享一个 digest

    remove_attachment(db, a1.id, blob_store=blob_store)
    assert blob_store.exists(a2.digest)  # 另一条还引用着，blob 不能删


# ── upload_batch：瞬时批量，只有失败项建子 job ──────────────────────────


def test_upload_batch_all_succeed(db, library_id, work_id, blob_store):
    files = [
        UploadInput(filename="a.pdf", content=PDF_BYTES),
        UploadInput(filename="b.pdf", content=PDF_BYTES + b"x"),
    ]
    result = upload_batch(
        db, account_id=ACCOUNT, library_id=library_id, work_id=work_id,
        files=files, blob_store=blob_store,
    )
    assert result.job.status == "succeeded"
    assert result.job.counts == {"total": 2, "created": 2, "failed": 0}
    assert [o.status for o in result.outcomes] == ["created", "created"]
    assert list_child_jobs(db, result.job.id) == []  # 全成功，不建子行


def test_upload_batch_isolates_per_file_failure_and_records_child_job(db, library_id, work_id, blob_store):
    files = [
        UploadInput(filename="good.pdf", content=PDF_BYTES),
        UploadInput(filename="bad.txt", content=TEXT_BYTES),
    ]
    result = upload_batch(
        db, account_id=ACCOUNT, library_id=library_id, work_id=work_id, files=files, blob_store=blob_store,
        allowed_media_types=frozenset({"application/pdf"}),
    )
    assert result.job.status == "failed"
    assert result.job.counts == {"total": 2, "created": 1, "failed": 1}
    statuses = {o.filename: o.status for o in result.outcomes}
    assert statuses == {"good.pdf": "created", "bad.txt": "failed"}

    children = list_child_jobs(db, result.job.id)
    assert len(children) == 1
    assert children[0].status == "failed"
    assert children[0].reason is not None


def test_upload_batch_rejects_cross_library_work_before_creating_job(db, library_id, work_id, blob_store):
    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    with pytest.raises(ValueError):
        upload_batch(
            db, account_id=ACCOUNT, library_id=other_library_id, work_id=work_id,
            files=[UploadInput(filename="a.pdf", content=PDF_BYTES)], blob_store=blob_store,
        )


def test_upload_batch_preserves_rel_path_per_file(db, library_id, work_id, blob_store):
    files = [UploadInput(filename="a.pdf", content=PDF_BYTES, rel_path="subdir/a.pdf")]
    result = upload_batch(
        db, account_id=ACCOUNT, library_id=library_id, work_id=work_id,
        files=files, blob_store=blob_store,
    )
    attachment_id = result.outcomes[0].attachment_id
    assert get_attachment(db, attachment_id).rel_path == "subdir/a.pdf"
