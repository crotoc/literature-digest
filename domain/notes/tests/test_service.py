from domain.notes.service import get_note, list_notes_for_works, set_note

LIBRARY = 1
WORK_A = 101
WORK_B = 102
WORK_C = 103


def test_get_note_returns_none_when_absent(db):
    assert get_note(db, WORK_A) is None


def test_set_note_then_get_note(db):
    note = set_note(db, library_id=LIBRARY, work_id=WORK_A, content="interesting methodology")
    assert note.content == "interesting methodology"
    fetched = get_note(db, WORK_A)
    assert fetched.content == "interesting methodology"


def test_set_note_overwrites_existing_content(db):
    set_note(db, library_id=LIBRARY, work_id=WORK_A, content="first draft")
    updated = set_note(db, library_id=LIBRARY, work_id=WORK_A, content="revised draft")
    assert updated.content == "revised draft"
    # 同一个 work 永远只有一条笔记行，不是追加历史
    assert get_note(db, WORK_A).id == updated.id


def test_set_note_with_empty_string_clears_it(db):
    set_note(db, library_id=LIBRARY, work_id=WORK_A, content="something")
    cleared = set_note(db, library_id=LIBRARY, work_id=WORK_A, content="")
    assert cleared is None
    assert get_note(db, WORK_A) is None


def test_set_note_with_none_clears_it(db):
    set_note(db, library_id=LIBRARY, work_id=WORK_A, content="something")
    cleared = set_note(db, library_id=LIBRARY, work_id=WORK_A, content=None)
    assert cleared is None
    assert get_note(db, WORK_A) is None


def test_set_note_empty_on_work_with_no_note_is_a_noop(db):
    result = set_note(db, library_id=LIBRARY, work_id=WORK_A, content="")
    assert result is None
    assert get_note(db, WORK_A) is None


def test_set_note_updates_updated_at_on_change(db):
    first = set_note(db, library_id=LIBRARY, work_id=WORK_A, content="v1")
    second = set_note(db, library_id=LIBRARY, work_id=WORK_A, content="v2")
    assert second.updated_at >= first.updated_at


def test_notes_are_independent_per_work(db):
    set_note(db, library_id=LIBRARY, work_id=WORK_A, content="note for A")
    assert get_note(db, WORK_B) is None


def test_list_notes_for_works_returns_only_existing(db):
    set_note(db, library_id=LIBRARY, work_id=WORK_A, content="note A")
    set_note(db, library_id=LIBRARY, work_id=WORK_B, content="note B")
    # WORK_C 没有笔记

    notes = list_notes_for_works(db, [WORK_A, WORK_B, WORK_C])
    assert set(notes.keys()) == {WORK_A, WORK_B}
    assert notes[WORK_A].content == "note A"
    assert notes[WORK_B].content == "note B"


def test_list_notes_for_works_empty_input_returns_empty_dict(db):
    assert list_notes_for_works(db, []) == {}


def test_list_notes_for_works_all_missing_returns_empty_dict(db):
    assert list_notes_for_works(db, [WORK_A, WORK_B]) == {}
