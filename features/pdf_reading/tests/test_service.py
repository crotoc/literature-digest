import pytest

from domain.attachments import AttachmentNotFound, create_attachment
from features.pdf_reading import NotViewable, get_main_attachment_for_view, open_for_view


def test_get_main_attachment_for_view_returns_none_without_one(db, work_id):
    assert get_main_attachment_for_view(db, work_id) is None


def test_get_main_attachment_for_view_skips_non_main_roles(db, library_id, work_id):
    create_attachment(
        db, library_id=library_id, work_id=work_id, filename="supp.pdf",
        digest="a" * 64, size=10, role="other", content_type="application/pdf",
    )

    assert get_main_attachment_for_view(db, work_id) is None


def test_get_main_attachment_for_view_returns_the_main_one(db, library_id, work_id):
    create_attachment(
        db, library_id=library_id, work_id=work_id, filename="other.pdf",
        digest="a" * 64, size=10, role="other", content_type="application/pdf",
    )
    main = create_attachment(
        db, library_id=library_id, work_id=work_id, filename="main.pdf",
        digest="b" * 64, size=20, role="main", content_type="application/pdf",
    )

    found = get_main_attachment_for_view(db, work_id)
    assert found.id == main.id


def test_open_for_view_returns_metadata_and_stream(db, library_id, work_id, blob_store):
    digest = blob_store.put(b"%PDF-1.4 fake pdf bytes").digest
    attachment = create_attachment(
        db, library_id=library_id, work_id=work_id, filename="main.pdf",
        digest=digest, size=23, role="main", content_type="application/pdf",
    )

    found, stream = open_for_view(db, attachment.id, blob_store=blob_store)

    assert found.id == attachment.id
    assert stream.read() == b"%PDF-1.4 fake pdf bytes"


def test_open_for_view_rejects_non_viewable_content_type(db, library_id, work_id, blob_store):
    digest = blob_store.put(b"PK\x03\x04 fake docx bytes").digest
    attachment = create_attachment(
        db, library_id=library_id, work_id=work_id, filename="notes.docx",
        digest=digest, size=20, role="main",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    with pytest.raises(NotViewable) as excinfo:
        open_for_view(db, attachment.id, blob_store=blob_store)

    assert excinfo.value.attachment_id == attachment.id


def test_open_for_view_rejects_missing_content_type(db, library_id, work_id, blob_store):
    digest = blob_store.put(b"unknown bytes").digest
    attachment = create_attachment(
        db, library_id=library_id, work_id=work_id, filename="mystery",
        digest=digest, size=13, role="main",
    )

    with pytest.raises(NotViewable):
        open_for_view(db, attachment.id, blob_store=blob_store)


def test_open_for_view_propagates_attachment_not_found(db, blob_store):
    with pytest.raises(AttachmentNotFound):
        open_for_view(db, 999999, blob_store=blob_store)
