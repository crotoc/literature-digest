import pytest

from domain.folders import add_work_to_folder, create_folder
from domain.notes import set_note
from domain.tags import add_tag_to_work, create_tag
from domain.works import add_identifier, create_work, soft_delete_work
from features.library_browse.service import list_library_page, resolve_selection

LIBRARY = 1
OTHER_LIBRARY = 2


def _work(db, *, title, library_id=LIBRARY, year=2020):
    return create_work(db, library_id=library_id, title=title, year=year)


# ── list_library_page：基本列表/分页/排序 ────────────────────────────────────


def test_list_library_page_lists_scoped_to_library(db):
    _work(db, title="Mine")
    _work(db, library_id=OTHER_LIBRARY, title="Not mine")

    page = list_library_page(db, library_id=LIBRARY)
    assert page.total == 1
    assert [c.work.title for c in page.items] == ["Mine"]


def test_list_library_page_excludes_deleted_by_default(db):
    kept = _work(db, title="Kept")
    gone = _work(db, title="Gone")
    soft_delete_work(db, gone.id)

    page = list_library_page(db, library_id=LIBRARY)
    assert [c.work.id for c in page.items] == [kept.id]
    assert page.total == 1


def test_list_library_page_trash_view_shows_only_deleted(db):
    _work(db, title="Kept")
    gone = _work(db, title="Gone")
    soft_delete_work(db, gone.id)

    page = list_library_page(db, library_id=LIBRARY, view="trash")
    assert [c.work.id for c in page.items] == [gone.id]
    assert page.total == 1


def test_list_library_page_sort_by_title_asc(db):
    _work(db, title="Zebra")
    _work(db, title="Apple")

    page = list_library_page(db, library_id=LIBRARY, sort_by="title", sort_dir="asc")
    assert [c.work.title for c in page.items] == ["Apple", "Zebra"]


def test_list_library_page_pagination(db):
    for i in range(5):
        _work(db, title=f"Work {i}", year=2000 + i)

    page = list_library_page(db, library_id=LIBRARY, limit=2, offset=1)
    assert len(page.items) == 2
    assert page.total == 5  # total 是筛选总数，不是这一页的条数


def test_list_library_page_rejects_unknown_view(db):
    with pytest.raises(ValueError, match="view"):
        list_library_page(db, library_id=LIBRARY, view="archived")


def test_list_library_page_rejects_unknown_sort_by(db):
    with pytest.raises(ValueError, match="sort_by"):
        list_library_page(db, library_id=LIBRARY, sort_by="first_author")


# ── list_library_page：卡片展开（tags/folders/attachments/notes） ────────────


def test_list_library_page_expands_card_attachments(db):
    work = _work(db, title="Has note and tag")
    tag = create_tag(db, library_id=LIBRARY, name="genomics")
    add_tag_to_work(db, library_id=LIBRARY, work_id=work.id, tag_id=tag.id)
    folder = create_folder(db, library_id=LIBRARY, name="2026 reading list")
    add_work_to_folder(db, library_id=LIBRARY, work_id=work.id, folder_id=folder.id)
    set_note(db, library_id=LIBRARY, work_id=work.id, content="重要")

    page = list_library_page(db, library_id=LIBRARY)
    card = page.items[0]
    assert [t.name for t in card.tags] == ["genomics"]
    assert [f.name for f in card.folders] == ["2026 reading list"]
    assert card.note is not None and card.note.content == "重要"
    assert card.attachments == ()
    assert card.identifiers == ()


def test_list_library_page_card_note_is_none_when_absent(db):
    _work(db, title="No note")

    page = list_library_page(db, library_id=LIBRARY)
    assert page.items[0].note is None


def test_list_library_page_expands_card_identifiers(db):
    work = _work(db, title="Has a DOI")
    add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1000/xyz123")
    add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="pmid", value="12345678")

    page = list_library_page(db, library_id=LIBRARY)
    card = page.items[0]
    assert {(i.scheme, i.value) for i in card.identifiers} == {
        ("doi", "10.1000/xyz123"),
        ("pmid", "12345678"),
    }


# ── list_library_page：按标签 / 文件夹筛选 ───────────────────────────────────


def test_list_library_page_filters_by_single_tag(db):
    tag = create_tag(db, library_id=LIBRARY, name="genomics")
    tagged = _work(db, title="Tagged")
    add_tag_to_work(db, library_id=LIBRARY, work_id=tagged.id, tag_id=tag.id)
    _work(db, title="Untagged")

    page = list_library_page(db, library_id=LIBRARY, tag_ids=[tag.id])
    assert [c.work.title for c in page.items] == ["Tagged"]


def test_list_library_page_filters_by_multiple_tags_is_and(db):
    tag_a = create_tag(db, library_id=LIBRARY, name="a")
    tag_b = create_tag(db, library_id=LIBRARY, name="b")
    both = _work(db, title="Both")
    add_tag_to_work(db, library_id=LIBRARY, work_id=both.id, tag_id=tag_a.id)
    add_tag_to_work(db, library_id=LIBRARY, work_id=both.id, tag_id=tag_b.id)
    only_a = _work(db, title="OnlyA")
    add_tag_to_work(db, library_id=LIBRARY, work_id=only_a.id, tag_id=tag_a.id)

    page = list_library_page(db, library_id=LIBRARY, tag_ids=[tag_a.id, tag_b.id])
    assert [c.work.title for c in page.items] == ["Both"]


def test_list_library_page_filters_by_folder(db):
    folder = create_folder(db, library_id=LIBRARY, name="reading list")
    inside = _work(db, title="Inside")
    add_work_to_folder(db, library_id=LIBRARY, work_id=inside.id, folder_id=folder.id)
    _work(db, title="Outside")

    page = list_library_page(db, library_id=LIBRARY, folder_id=folder.id)
    assert [c.work.title for c in page.items] == ["Inside"]


def test_list_library_page_tag_and_folder_combine_as_and(db):
    tag = create_tag(db, library_id=LIBRARY, name="genomics")
    folder = create_folder(db, library_id=LIBRARY, name="reading list")
    both = _work(db, title="Both")
    add_tag_to_work(db, library_id=LIBRARY, work_id=both.id, tag_id=tag.id)
    add_work_to_folder(db, library_id=LIBRARY, work_id=both.id, folder_id=folder.id)
    tag_only = _work(db, title="TagOnly")
    add_tag_to_work(db, library_id=LIBRARY, work_id=tag_only.id, tag_id=tag.id)

    page = list_library_page(db, library_id=LIBRARY, tag_ids=[tag.id], folder_id=folder.id)
    assert [c.work.title for c in page.items] == ["Both"]


def test_list_library_page_rejects_tag_from_other_library(db):
    foreign_tag = create_tag(db, library_id=OTHER_LIBRARY, name="foreign")
    with pytest.raises(ValueError, match="tag_id"):
        list_library_page(db, library_id=LIBRARY, tag_ids=[foreign_tag.id])


def test_list_library_page_rejects_folder_from_other_library(db):
    foreign_folder = create_folder(db, library_id=OTHER_LIBRARY, name="foreign")
    with pytest.raises(ValueError, match="folder_id"):
        list_library_page(db, library_id=LIBRARY, folder_id=foreign_folder.id)


# ── resolve_selection ───────────────────────────────────────────────────────


def test_resolve_selection_explicit_mode_returns_given_ids(db):
    a = _work(db, title="A")
    b = _work(db, title="B")

    result = resolve_selection(db, library_id=LIBRARY, mode="explicit", work_ids=[a.id, b.id])
    assert set(result) == {a.id, b.id}


def test_resolve_selection_explicit_mode_drops_ids_from_other_library(db):
    mine = _work(db, title="Mine")
    foreign = _work(db, library_id=OTHER_LIBRARY, title="Foreign")

    result = resolve_selection(db, library_id=LIBRARY, mode="explicit", work_ids=[mine.id, foreign.id])
    assert result == [mine.id]


def test_resolve_selection_explicit_mode_empty_input_returns_empty(db):
    assert resolve_selection(db, library_id=LIBRARY, mode="explicit", work_ids=[]) == []


def test_resolve_selection_all_filtered_expands_to_full_candidate_set(db):
    for i in range(5):
        _work(db, title=f"Work {i}", year=2000 + i)

    result = resolve_selection(db, library_id=LIBRARY, mode="all_filtered")
    assert len(result) == 5


def test_resolve_selection_all_filtered_honors_tag_filter(db):
    tag = create_tag(db, library_id=LIBRARY, name="genomics")
    tagged = _work(db, title="Tagged")
    add_tag_to_work(db, library_id=LIBRARY, work_id=tagged.id, tag_id=tag.id)
    _work(db, title="Untagged")

    result = resolve_selection(db, library_id=LIBRARY, mode="all_filtered", tag_ids=[tag.id])
    assert result == [tagged.id]


def test_resolve_selection_all_filtered_trash_view(db):
    _work(db, title="Kept")
    gone = _work(db, title="Gone")
    soft_delete_work(db, gone.id)

    result = resolve_selection(db, library_id=LIBRARY, mode="all_filtered", view="trash")
    assert result == [gone.id]


def test_resolve_selection_rejects_unknown_mode(db):
    with pytest.raises(ValueError, match="mode"):
        resolve_selection(db, library_id=LIBRARY, mode="everything")
