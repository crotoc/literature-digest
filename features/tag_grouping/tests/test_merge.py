import pytest

from domain.tags import TagNotFound, create_tag, list_tags, list_tags_for_work, list_work_ids_for_tag
from domain.works import create_work
from features.tag_grouping import merge_tags

ACCOUNT = 1


def test_merge_tags_moves_works_and_deletes_source_tags(db, library_id):
    w1 = create_work(db, library_id=library_id, title="A").id
    w2 = create_work(db, library_id=library_id, title="B").id
    neuro = create_tag(db, library_id=library_id, name="neuro")
    neuroscience = create_tag(db, library_id=library_id, name="Neuroscience")

    from domain.tags import add_tag_to_work

    add_tag_to_work(db, library_id=library_id, work_id=w1, tag_id=neuro.id)
    add_tag_to_work(db, library_id=library_id, work_id=w2, tag_id=neuroscience.id)

    result = merge_tags(
        db, account_id=ACCOUNT, library_id=library_id,
        tag_ids=[neuro.id, neuroscience.id], target_name="neuroscience",
    )

    assert result.job.status == "succeeded"
    assert result.job.counts == {"tags_merged": 2, "works_retagged": 2}
    assert result.works_retagged == 2
    assert set(result.merged_tag_ids) == {neuro.id, neuroscience.id}

    remaining = {tag.name for tag in list_tags(db, library_id=library_id)}
    assert remaining == {"neuroscience"}
    assert {t.name for t in list_tags_for_work(db, w1)} == {"neuroscience"}
    assert {t.name for t in list_tags_for_work(db, w2)} == {"neuroscience"}

    with pytest.raises(TagNotFound):
        from domain.tags import get_tag

        get_tag(db, neuro.id)


def test_merge_tags_reuses_existing_tag_when_target_name_matches_one_of_the_sources(db, library_id):
    a = create_tag(db, library_id=library_id, name="A")
    b = create_tag(db, library_id=library_id, name="B")

    result = merge_tags(db, account_id=ACCOUNT, library_id=library_id, tag_ids=[a.id, b.id], target_name="A")

    assert result.target_tag.id == a.id
    assert result.merged_tag_ids == (b.id,)


def test_merge_tags_rejects_fewer_than_two_tags(db, library_id):
    a = create_tag(db, library_id=library_id, name="A")

    with pytest.raises(ValueError):
        merge_tags(db, account_id=ACCOUNT, library_id=library_id, tag_ids=[a.id], target_name="A")


def test_merge_tags_rejects_tag_from_another_library(db, library_id):
    from domain.libraries import create_library

    other_library_id = create_library(db, owner_account_id=ACCOUNT, name="另一个库").id
    a = create_tag(db, library_id=library_id, name="A")
    other = create_tag(db, library_id=other_library_id, name="other")

    with pytest.raises(ValueError):
        merge_tags(db, account_id=ACCOUNT, library_id=library_id, tag_ids=[a.id, other.id], target_name="A")


def test_merge_tags_rejects_unknown_tag_id(db, library_id):
    a = create_tag(db, library_id=library_id, name="A")

    with pytest.raises(TagNotFound):
        merge_tags(db, account_id=ACCOUNT, library_id=library_id, tag_ids=[a.id, 999999], target_name="A")


def test_merge_tags_deduplicates_tag_that_already_has_the_target_tag(db, library_id):
    """work 同时打了源标签和目标标签时，`add_tag_to_work` 的幂等性保证
    合并不会报错、也不会重复计数成两条 work_tag。"""
    w = create_work(db, library_id=library_id, title="A").id
    a = create_tag(db, library_id=library_id, name="A")
    b = create_tag(db, library_id=library_id, name="B")

    from domain.tags import add_tag_to_work

    add_tag_to_work(db, library_id=library_id, work_id=w, tag_id=a.id)
    add_tag_to_work(db, library_id=library_id, work_id=w, tag_id=b.id)

    result = merge_tags(db, account_id=ACCOUNT, library_id=library_id, tag_ids=[a.id, b.id], target_name="A")

    assert result.works_retagged == 1
    assert [t.id for t in list_tags_for_work(db, w)] == [a.id]
    assert list_work_ids_for_tag(db, a.id) == [w]
