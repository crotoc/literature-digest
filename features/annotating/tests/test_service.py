from domain.works import get_work
from features.annotating import get_work_note, set_work_note, update_metadata


def test_update_metadata_updates_only_given_fields(db, library_id, work_id):
    result = update_metadata(db, library_id=library_id, work_id=work_id, abstract="新摘要")

    assert result.work.abstract == "新摘要"
    assert result.work.title == "原标题"  # 没传 title，原样保留
    assert result.work.year == 2020


def test_update_metadata_without_title_or_year_change_does_not_scan(db, library_id, work_id):
    from domain.works import create_work

    create_work(db, library_id=library_id, title="原标题", year=2020)  # 本来就有个重复

    result = update_metadata(db, library_id=library_id, work_id=work_id, abstract="改了摘要")

    assert result.new_duplicate_candidates == ()


def test_update_metadata_title_change_triggers_duplicate_scan(db, library_id, work_id):
    from domain.works import create_work

    twin = create_work(db, library_id=library_id, title="改名后撞上的标题", year=2020).id

    result = update_metadata(db, library_id=library_id, work_id=work_id, title="改名后撞上的标题")

    assert [c.candidate_work_id for c in result.new_duplicate_candidates] == [twin]
    assert result.new_duplicate_candidates[0].reason == "title_year_key"


def test_update_metadata_year_change_triggers_duplicate_scan(db, library_id, work_id):
    from domain.works import create_work

    twin = create_work(db, library_id=library_id, title="原标题", year=1999).id

    result = update_metadata(db, library_id=library_id, work_id=work_id, year=1999)

    assert [c.candidate_work_id for c in result.new_duplicate_candidates] == [twin]


def test_update_metadata_title_change_with_no_hits_returns_empty(db, library_id, work_id):
    result = update_metadata(db, library_id=library_id, work_id=work_id, title="独一无二的新标题")

    assert result.new_duplicate_candidates == ()


def test_update_metadata_note_field_is_the_works_table_note_not_domain_notes(db, library_id, work_id):
    result = update_metadata(db, library_id=library_id, work_id=work_id, note="来自 RIS N1 字段的导入备注")

    assert result.work.note == "来自 RIS N1 字段的导入备注"
    assert get_work_note(db, work_id) is None  # 两个"笔记"互不影响


def test_set_and_get_work_note_roundtrip(db, library_id, work_id):
    set_work_note(db, library_id=library_id, work_id=work_id, content="这篇讲的是……")

    note = get_work_note(db, work_id)
    assert note.content == "这篇讲的是……"
    # works 自己的 note 字段没被动过
    assert get_work(db, work_id).note is None


def test_set_work_note_with_none_deletes_it(db, library_id, work_id):
    set_work_note(db, library_id=library_id, work_id=work_id, content="先写一条")
    assert get_work_note(db, work_id) is not None

    set_work_note(db, library_id=library_id, work_id=work_id, content=None)

    assert get_work_note(db, work_id) is None
