import pytest

from domain.tags.service import (
    TagNameTaken,
    TagNotFound,
    WorkTagNotFound,
    add_tag_to_work,
    aggregate_states,
    create_tag,
    delete_tag,
    get_tag,
    list_tags,
    list_tags_for_work,
    list_work_ids_for_tag,
    remove_tag_from_work,
    rename_tag,
    reorder_tags,
)

LIBRARY = 1
OTHER_LIBRARY = 2
WORK_A = 101
WORK_B = 102
WORK_C = 103


# ── create_tag / get_tag ─────────────────────────────────────────────────


def test_create_tag_strips_name(db):
    tag = create_tag(db, library_id=LIBRARY, name="  Deep Learning  ")
    assert tag.name == "Deep Learning"


def test_create_tag_rejects_empty_name(db):
    with pytest.raises(ValueError):
        create_tag(db, library_id=LIBRARY, name="   ")


def test_create_tag_is_idempotent_for_same_name(db):
    first = create_tag(db, library_id=LIBRARY, name="Genomics")
    second = create_tag(db, library_id=LIBRARY, name="Genomics")
    assert first.id == second.id
    assert len(list_tags(db, library_id=LIBRARY)) == 1


def test_create_tag_same_name_different_library_is_separate(db):
    a = create_tag(db, library_id=LIBRARY, name="Genomics")
    b = create_tag(db, library_id=OTHER_LIBRARY, name="Genomics")
    assert a.id != b.id


def test_create_tag_appends_to_end_of_sort_order(db):
    first = create_tag(db, library_id=LIBRARY, name="First")
    second = create_tag(db, library_id=LIBRARY, name="Second")
    assert second.sort_order > first.sort_order


def test_get_tag_missing_raises(db):
    with pytest.raises(TagNotFound):
        get_tag(db, 999)


# ── rename_tag ───────────────────────────────────────────────────────────


def test_rename_tag_ok(db):
    tag = create_tag(db, library_id=LIBRARY, name="Old")
    renamed = rename_tag(db, tag.id, "New")
    assert renamed.name == "New"


def test_rename_tag_to_same_name_is_a_noop(db):
    tag = create_tag(db, library_id=LIBRARY, name="Same")
    renamed = rename_tag(db, tag.id, "Same")
    assert renamed.name == "Same"


def test_rename_tag_rejects_empty_name(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    with pytest.raises(ValueError):
        rename_tag(db, tag.id, "   ")


def test_rename_tag_collision_raises(db):
    create_tag(db, library_id=LIBRARY, name="Taken")
    other = create_tag(db, library_id=LIBRARY, name="Other")
    with pytest.raises(TagNameTaken):
        rename_tag(db, other.id, "Taken")


def test_rename_tag_missing_raises(db):
    with pytest.raises(TagNotFound):
        rename_tag(db, 999, "New")


# ── delete_tag ───────────────────────────────────────────────────────────


def test_delete_tag_ok(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    delete_tag(db, tag.id)
    with pytest.raises(TagNotFound):
        get_tag(db, tag.id)


def test_delete_tag_removes_work_associations(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)
    delete_tag(db, tag.id)
    assert list_tags_for_work(db, WORK_A) == []


def test_delete_tag_missing_raises(db):
    with pytest.raises(TagNotFound):
        delete_tag(db, 999)


def test_delete_tag_does_not_affect_other_tags(db):
    kept = create_tag(db, library_id=LIBRARY, name="Kept")
    gone = create_tag(db, library_id=LIBRARY, name="Gone")
    delete_tag(db, gone.id)
    assert get_tag(db, kept.id).id == kept.id


# ── list_tags / reorder_tags ─────────────────────────────────────────────


def test_list_tags_ordered_by_sort_order(db):
    a = create_tag(db, library_id=LIBRARY, name="A")
    b = create_tag(db, library_id=LIBRARY, name="B")
    tags = list_tags(db, library_id=LIBRARY)
    assert [t.id for t in tags] == [a.id, b.id]


def test_list_tags_scoped_to_library(db):
    create_tag(db, library_id=LIBRARY, name="Mine")
    create_tag(db, library_id=OTHER_LIBRARY, name="Not mine")
    tags = list_tags(db, library_id=LIBRARY)
    assert len(tags) == 1
    assert tags[0].name == "Mine"


def test_reorder_tags_ok(db):
    a = create_tag(db, library_id=LIBRARY, name="A")
    b = create_tag(db, library_id=LIBRARY, name="B")
    c = create_tag(db, library_id=LIBRARY, name="C")

    reordered = reorder_tags(db, library_id=LIBRARY, tag_ids_in_order=[c.id, a.id, b.id])
    assert [t.id for t in reordered] == [c.id, a.id, b.id]
    assert [t.id for t in list_tags(db, library_id=LIBRARY)] == [c.id, a.id, b.id]


def test_reorder_tags_rejects_partial_set(db):
    a = create_tag(db, library_id=LIBRARY, name="A")
    create_tag(db, library_id=LIBRARY, name="B")
    with pytest.raises(ValueError):
        reorder_tags(db, library_id=LIBRARY, tag_ids_in_order=[a.id])


def test_reorder_tags_rejects_foreign_tag_id(db):
    a = create_tag(db, library_id=LIBRARY, name="A")
    other = create_tag(db, library_id=OTHER_LIBRARY, name="Other")
    with pytest.raises(ValueError):
        reorder_tags(db, library_id=LIBRARY, tag_ids_in_order=[a.id, other.id])


# ── work ↔ tag 关联 ──────────────────────────────────────────────────────


def test_add_tag_to_work_then_list_both_directions(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)

    assert [t.id for t in list_tags_for_work(db, WORK_A)] == [tag.id]
    assert list_work_ids_for_tag(db, tag.id) == [WORK_A]


def test_add_tag_to_work_is_idempotent(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    first = add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)
    second = add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)
    assert first.id == second.id
    assert list_work_ids_for_tag(db, tag.id) == [WORK_A]


def test_add_tag_to_work_missing_tag_raises(db):
    with pytest.raises(TagNotFound):
        add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=999)


def test_add_tag_to_work_wrong_library_raises(db):
    tag = create_tag(db, library_id=OTHER_LIBRARY, name="X")
    with pytest.raises(ValueError):
        add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)


def test_remove_tag_from_work_ok(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)
    remove_tag_from_work(db, work_id=WORK_A, tag_id=tag.id)
    assert list_work_ids_for_tag(db, tag.id) == []


def test_remove_tag_from_work_missing_raises(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    with pytest.raises(WorkTagNotFound):
        remove_tag_from_work(db, work_id=WORK_A, tag_id=tag.id)


# ── 三态聚合 ───────────────────────────────────────────────────────────────


def test_aggregate_states_all(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_B, tag_id=tag.id)

    states = aggregate_states(db, library_id=LIBRARY, work_ids=[WORK_A, WORK_B])
    assert {s.tag.id: s.state for s in states} == {tag.id: "all"}


def test_aggregate_states_some(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)

    states = aggregate_states(db, library_id=LIBRARY, work_ids=[WORK_A, WORK_B])
    assert {s.tag.id: s.state for s in states} == {tag.id: "some"}


def test_aggregate_states_none(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")

    states = aggregate_states(db, library_id=LIBRARY, work_ids=[WORK_A, WORK_B])
    assert {s.tag.id: s.state for s in states} == {tag.id: "none"}


def test_aggregate_states_covers_every_tag_in_library(db):
    tag_a = create_tag(db, library_id=LIBRARY, name="A")
    tag_b = create_tag(db, library_id=LIBRARY, name="B")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag_a.id)

    states = aggregate_states(db, library_id=LIBRARY, work_ids=[WORK_A])
    assert {s.tag.id: s.state for s in states} == {tag_a.id: "all", tag_b.id: "none"}


def test_aggregate_states_empty_work_ids_is_all_none(db):
    tag = create_tag(db, library_id=LIBRARY, name="X")
    states = aggregate_states(db, library_id=LIBRARY, work_ids=[])
    assert {s.tag.id: s.state for s in states} == {tag.id: "none"}


def test_aggregate_states_mixed_three_works(db):
    """三篇文献，一个标签只打在两篇上 -> 整体是 some，不是 all。"""
    tag = create_tag(db, library_id=LIBRARY, name="X")
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_A, tag_id=tag.id)
    add_tag_to_work(db, library_id=LIBRARY, work_id=WORK_B, tag_id=tag.id)

    states = aggregate_states(db, library_id=LIBRARY, work_ids=[WORK_A, WORK_B, WORK_C])
    assert {s.tag.id: s.state for s in states} == {tag.id: "some"}
