import pytest

from domain.attachments.service import (
    AttachmentNotFound,
    count_references,
    create_attachment,
    delete_attachment,
    get_attachment,
    list_attachments_for_work,
    set_main_attachment,
)

LIBRARY = 1
WORK_A = 101
WORK_B = 102
DIGEST_PDF = "a" * 64
DIGEST_SUPPLEMENT = "b" * 64


def _make_attachment(db, *, work_id=WORK_A, filename="paper.pdf", digest=DIGEST_PDF, **kwargs):
    return create_attachment(
        db, library_id=LIBRARY, work_id=work_id, filename=filename, digest=digest, size=1024, **kwargs
    )


# ── create_attachment / get_attachment ───────────────────────────────────


def test_create_attachment_defaults_to_other(db):
    attachment = _make_attachment(db)
    assert attachment.role == "other"


def test_create_attachment_rejects_bad_role(db):
    with pytest.raises(ValueError):
        _make_attachment(db, role="primary")


def test_create_main_attachment_demotes_previous_main(db):
    first = _make_attachment(db, filename="v1.pdf", digest=DIGEST_PDF, role="main")
    second = _make_attachment(db, filename="v2.pdf", digest=DIGEST_SUPPLEMENT, role="main")

    assert get_attachment(db, first.id).role == "other"
    assert get_attachment(db, second.id).role == "main"


def test_create_other_attachment_does_not_touch_existing_main(db):
    main = _make_attachment(db, filename="main.pdf", digest=DIGEST_PDF, role="main")
    _make_attachment(db, filename="supplement.pdf", digest=DIGEST_SUPPLEMENT, role="other")

    assert get_attachment(db, main.id).role == "main"


def test_main_role_is_per_work_not_global(db):
    """work A 的 main 不影响 work B 能不能也有自己的 main。"""
    a_main = _make_attachment(db, work_id=WORK_A, filename="a.pdf", digest=DIGEST_PDF, role="main")
    b_main = _make_attachment(db, work_id=WORK_B, filename="b.pdf", digest=DIGEST_SUPPLEMENT, role="main")

    assert get_attachment(db, a_main.id).role == "main"
    assert get_attachment(db, b_main.id).role == "main"


def test_get_attachment_missing_raises(db):
    with pytest.raises(AttachmentNotFound):
        get_attachment(db, 999)


def test_create_attachment_preserves_rel_path(db):
    attachment = _make_attachment(db, rel_path="supplementary/figure_s1.png")
    assert attachment.rel_path == "supplementary/figure_s1.png"


# ── set_main_attachment ───────────────────────────────────────────────────


def test_set_main_attachment_promotes_and_demotes(db):
    old_main = _make_attachment(db, filename="old.pdf", digest=DIGEST_PDF, role="main")
    other = _make_attachment(db, filename="other.pdf", digest=DIGEST_SUPPLEMENT, role="other")

    promoted = set_main_attachment(db, other.id)
    assert promoted.role == "main"
    assert get_attachment(db, old_main.id).role == "other"


def test_set_main_attachment_already_main_is_a_noop(db):
    main = _make_attachment(db, role="main")
    result = set_main_attachment(db, main.id)
    assert result.role == "main"


def test_set_main_attachment_missing_raises(db):
    with pytest.raises(AttachmentNotFound):
        set_main_attachment(db, 999)


# ── list_attachments_for_work ────────────────────────────────────────────


def test_list_attachments_for_work_orders_main_first(db):
    other = _make_attachment(db, filename="other.pdf", digest=DIGEST_SUPPLEMENT, role="other")
    main = _make_attachment(db, filename="main.pdf", digest=DIGEST_PDF, role="main")

    attachments = list_attachments_for_work(db, WORK_A)
    assert [a.id for a in attachments] == [main.id, other.id]


def test_list_attachments_for_work_scoped_to_work(db):
    _make_attachment(db, work_id=WORK_A, digest=DIGEST_PDF)
    _make_attachment(db, work_id=WORK_B, digest=DIGEST_SUPPLEMENT)

    assert len(list_attachments_for_work(db, WORK_A)) == 1


def test_list_attachments_for_work_empty_when_none(db):
    assert list_attachments_for_work(db, WORK_A) == []


# ── 引用计数 / delete_attachment ──────────────────────────────────────────


def test_count_references_zero_for_unknown_digest(db):
    assert count_references(db, "unknown" * 8) == 0


def test_count_references_counts_across_works(db):
    """同一个 digest 可能被不同 work 的附件同时引用——blobstore 按内容去重，
    两个人上传同一份 PDF 应该落到同一个 digest 上。
    """
    _make_attachment(db, work_id=WORK_A, digest=DIGEST_PDF)
    _make_attachment(db, work_id=WORK_B, digest=DIGEST_PDF)

    assert count_references(db, DIGEST_PDF) == 2


def test_delete_attachment_not_orphaned_when_others_reference_same_digest(db):
    a = _make_attachment(db, work_id=WORK_A, digest=DIGEST_PDF)
    _make_attachment(db, work_id=WORK_B, digest=DIGEST_PDF)

    orphaned = delete_attachment(db, a.id)
    assert orphaned is False
    assert count_references(db, DIGEST_PDF) == 1


def test_delete_attachment_orphaned_when_last_reference(db):
    a = _make_attachment(db, digest=DIGEST_PDF)

    orphaned = delete_attachment(db, a.id)
    assert orphaned is True
    assert count_references(db, DIGEST_PDF) == 0


def test_delete_attachment_missing_raises(db):
    with pytest.raises(AttachmentNotFound):
        delete_attachment(db, 999)


def test_delete_attachment_removes_it_from_listing(db):
    a = _make_attachment(db)
    delete_attachment(db, a.id)
    assert list_attachments_for_work(db, WORK_A) == []
