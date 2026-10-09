import pytest

from caps.bibformats import Person
from domain.works.service import (
    DuplicateCandidateNotFound,
    IdentifierBelongsToDeletedWork,
    IdentifierConflict,
    IdentifierNotFound,
    RelationNotFound,
    WorkNotFound,
    add_identifier,
    add_relation,
    count_works,
    create_work,
    find_by_identifier,
    find_candidates_by_title_year_key,
    get_work,
    list_duplicate_candidates,
    list_identifiers,
    list_provenance,
    list_relations,
    list_work_ids,
    list_works,
    purge_work,
    record_duplicate_candidate,
    record_provenance,
    remove_identifier,
    remove_relation,
    resolve_duplicate_candidate,
    restore_work,
    soft_delete_work,
    update_work,
)

LIBRARY = 1
OTHER_LIBRARY = 2


def _make_work(db, *, library_id=LIBRARY, title="Deep Learning for Genomics", year=2020, **kwargs):
    return create_work(db, library_id=library_id, title=title, year=year, **kwargs)


# ── create_work / get_work ───────────────────────────────────────────────


def test_create_work_computes_title_year_key(db):
    work = _make_work(db)
    assert work.title_year_key is not None
    assert work.title_year_key.endswith("_2020")


def test_create_work_with_authors_round_trips(db):
    authors = (Person(family="Doe", given="Jane"), Person(literal="World Health Organization"))
    work = create_work(db, library_id=LIBRARY, title="X", year=2020, authors=authors)
    fetched = get_work(db, work.id)
    assert fetched.authors == authors


def test_create_work_rejects_bad_item_type(db):
    with pytest.raises(ValueError):
        create_work(db, library_id=LIBRARY, item_type="not_a_real_type", title="X")


def test_create_work_without_title_or_year_has_no_fingerprint(db):
    work = create_work(db, library_id=LIBRARY, note="just a stub")
    assert work.title_year_key is None


def test_get_work_missing_raises(db):
    with pytest.raises(WorkNotFound):
        get_work(db, 999)


def test_get_work_hides_deleted_by_default(db):
    work = _make_work(db)
    soft_delete_work(db, work.id)
    with pytest.raises(WorkNotFound):
        get_work(db, work.id)
    assert get_work(db, work.id, include_deleted=True).id == work.id


# ── update_work ──────────────────────────────────────────────────────────


def test_update_work_only_touches_passed_fields(db):
    work = _make_work(db, abstract="original abstract")
    updated = update_work(db, work.id, title="New Title")
    assert updated.title == "New Title"
    assert updated.abstract == "original abstract"


def test_update_work_can_explicitly_clear_a_field(db):
    work = _make_work(db, abstract="has an abstract")
    updated = update_work(db, work.id, abstract=None)
    assert updated.abstract is None


def test_update_work_recomputes_title_year_key_on_title_change(db):
    work = _make_work(db, title="Old Title", year=2020)
    old_key = work.title_year_key
    updated = update_work(db, work.id, title="Brand New Title")
    assert updated.title_year_key != old_key
    assert updated.title_year_key.endswith("_2020")


def test_update_work_recomputes_title_year_key_on_year_change(db):
    work = _make_work(db, title="Same Title", year=2020)
    updated = update_work(db, work.id, year=2021)
    assert updated.title_year_key.endswith("_2021")


def test_update_work_leaves_title_year_key_untouched_when_neither_changes(db):
    work = _make_work(db)
    updated = update_work(db, work.id, abstract="unrelated change")
    assert updated.title_year_key == work.title_year_key


def test_update_work_missing_raises(db):
    with pytest.raises(WorkNotFound):
        update_work(db, 999, title="X")


def test_update_work_rejects_bad_item_type(db):
    work = _make_work(db)
    with pytest.raises(ValueError):
        update_work(db, work.id, item_type="bogus")


# ── soft delete / restore / purge ───────────────────────────────────────


def test_soft_delete_is_idempotent(db):
    work = _make_work(db)
    soft_delete_work(db, work.id)
    first_deleted_at = get_work(db, work.id, include_deleted=True).deleted_at
    soft_delete_work(db, work.id)
    second_deleted_at = get_work(db, work.id, include_deleted=True).deleted_at
    assert first_deleted_at == second_deleted_at


def test_soft_delete_missing_raises(db):
    with pytest.raises(WorkNotFound):
        soft_delete_work(db, 999)


def test_restore_clears_deleted_at(db):
    work = _make_work(db)
    soft_delete_work(db, work.id)
    restored = restore_work(db, work.id)
    assert restored.deleted_at is None


def test_restore_missing_raises(db):
    with pytest.raises(WorkNotFound):
        restore_work(db, 999)


def test_restore_does_not_disturb_identifiers(db):
    """软删期间挂的标识符恢复后原样还在，不需要任何额外处理。"""
    work = _make_work(db)
    add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1/abc")
    soft_delete_work(db, work.id)
    restore_work(db, work.id)
    identifiers = list_identifiers(db, work.id)
    assert len(identifiers) == 1
    assert identifiers[0].value_norm == "10.1/abc"


def test_purge_removes_work_and_its_own_domain_rows(db):
    a = _make_work(db, title="A", year=2020)
    b = _make_work(db, title="B", year=2021)
    add_identifier(db, library_id=LIBRARY, work_id=a.id, scheme="doi", value="10.1/a")
    add_relation(db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="related_to")
    record_provenance(db, work_id=a.id, source="crossref")
    record_duplicate_candidate(
        db, library_id=LIBRARY, work_id=a.id, candidate_work_id=b.id, reason="same_title_year"
    )

    purge_work(db, a.id)

    with pytest.raises(WorkNotFound):
        get_work(db, a.id, include_deleted=True)
    assert list_identifiers(db, a.id) == []
    assert list_relations(db, b.id) == []
    assert list_duplicate_candidates(db, library_id=LIBRARY) == []
    # b 本身毫发无伤
    assert get_work(db, b.id).id == b.id


def test_purge_missing_work_is_a_noop(db):
    """purge 一个不存在的 id 不报错——它本质是"确保它不存在"，已经满足了。"""
    purge_work(db, 999)


# ── list_works ───────────────────────────────────────────────────────────


def test_list_works_excludes_deleted_by_default(db):
    kept = _make_work(db, title="Kept")
    gone = _make_work(db, title="Gone")
    soft_delete_work(db, gone.id)

    works = list_works(db, library_id=LIBRARY)
    assert [w.id for w in works] == [kept.id]


def test_list_works_include_deleted_shows_both(db):
    kept = _make_work(db, title="Kept")
    gone = _make_work(db, title="Gone")
    soft_delete_work(db, gone.id)

    works = list_works(db, library_id=LIBRARY, include_deleted=True)
    assert {w.id for w in works} == {kept.id, gone.id}


def test_list_works_scoped_to_library(db):
    _make_work(db, library_id=LIBRARY, title="Mine")
    _make_work(db, library_id=OTHER_LIBRARY, title="Not mine")

    works = list_works(db, library_id=LIBRARY)
    assert len(works) == 1
    assert works[0].title == "Mine"


def test_list_works_orders_by_updated_at_desc(db):
    first = _make_work(db, title="First")
    second = _make_work(db, title="Second")
    update_work(db, first.id, note="bump updated_at")

    works = list_works(db, library_id=LIBRARY)
    assert [w.id for w in works] == [first.id, second.id]


def test_list_works_limit_and_offset(db):
    for i in range(5):
        _make_work(db, title=f"Work {i}", year=2000 + i)

    page = list_works(db, library_id=LIBRARY, limit=2, offset=1)
    assert len(page) == 2


def test_list_works_work_ids_filters_to_explicit_set(db):
    a = _make_work(db, title="A")
    _make_work(db, title="B")
    c = _make_work(db, title="C")

    works = list_works(db, library_id=LIBRARY, work_ids=[a.id, c.id])
    assert {w.id for w in works} == {a.id, c.id}


def test_list_works_work_ids_empty_list_means_no_candidates(db):
    _make_work(db, title="A")

    works = list_works(db, library_id=LIBRARY, work_ids=[])
    assert works == []


def test_list_works_sort_by_title_asc(db):
    _make_work(db, title="Zebra")
    _make_work(db, title="Apple")

    works = list_works(db, library_id=LIBRARY, sort_by="title", sort_dir="asc")
    assert [w.title for w in works] == ["Apple", "Zebra"]


def test_list_works_sort_by_year_desc(db):
    _make_work(db, title="Older", year=2010)
    _make_work(db, title="Newer", year=2022)

    works = list_works(db, library_id=LIBRARY, sort_by="year", sort_dir="desc")
    assert [w.title for w in works] == ["Newer", "Older"]


def test_list_works_deleted_only_returns_just_trashed(db):
    kept = _make_work(db, title="Kept")
    gone = _make_work(db, title="Gone")
    soft_delete_work(db, gone.id)

    works = list_works(db, library_id=LIBRARY, deleted_only=True)
    assert [w.id for w in works] == [gone.id]
    assert kept.id not in [w.id for w in works]


def test_list_works_deleted_only_overrides_include_deleted(db):
    _make_work(db, title="Kept")
    gone = _make_work(db, title="Gone")
    soft_delete_work(db, gone.id)

    # deleted_only=True 时 include_deleted 的默认值不该让回收站视图漏看任何东西，
    # 也不该被它意外放宽成"全都要"。
    works = list_works(db, library_id=LIBRARY, include_deleted=False, deleted_only=True)
    assert [w.id for w in works] == [gone.id]


def test_list_works_rejects_unknown_sort_by(db):
    with pytest.raises(ValueError, match="sort_by"):
        list_works(db, library_id=LIBRARY, sort_by="first_author")


def test_list_works_rejects_unknown_sort_dir(db):
    with pytest.raises(ValueError, match="sort_dir"):
        list_works(db, library_id=LIBRARY, sort_dir="sideways")


def test_list_work_ids_scoped_and_excludes_deleted_by_default(db):
    kept = _make_work(db, library_id=LIBRARY, title="Kept")
    gone = _make_work(db, library_id=LIBRARY, title="Gone")
    soft_delete_work(db, gone.id)
    _make_work(db, library_id=OTHER_LIBRARY, title="Not mine")

    assert list_work_ids(db, library_id=LIBRARY) == [kept.id]


def test_list_work_ids_honors_work_ids_filter(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")

    assert set(list_work_ids(db, library_id=LIBRARY, work_ids=[a.id])) == {a.id}
    assert set(list_work_ids(db, library_id=LIBRARY, work_ids=[a.id, b.id])) == {a.id, b.id}


def test_list_work_ids_deleted_only(db):
    kept = _make_work(db, title="Kept")
    gone = _make_work(db, title="Gone")
    soft_delete_work(db, gone.id)

    assert list_work_ids(db, library_id=LIBRARY, deleted_only=True) == [gone.id]
    assert kept.id not in list_work_ids(db, library_id=LIBRARY, deleted_only=True)


def test_count_works_matches_list_works_filters(db):
    a = _make_work(db, title="A")
    _make_work(db, title="B")
    gone = _make_work(db, title="Gone")
    soft_delete_work(db, gone.id)

    assert count_works(db, library_id=LIBRARY) == 2
    assert count_works(db, library_id=LIBRARY, include_deleted=True) == 3
    assert count_works(db, library_id=LIBRARY, deleted_only=True) == 1
    assert count_works(db, library_id=LIBRARY, work_ids=[a.id]) == 1
    assert count_works(db, library_id=LIBRARY, work_ids=[]) == 0


# ── 标识符 ────────────────────────────────────────────────────────────────


def test_add_identifier_then_find_by_identifier(db):
    work = _make_work(db)
    add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1/abc")

    found = find_by_identifier(db, library_id=LIBRARY, scheme="doi", value="10.1/ABC")
    assert found.id == work.id


def test_add_identifier_normalizes_doi_prefix(db):
    work = _make_work(db)
    add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="https://doi.org/10.1/abc")

    found = find_by_identifier(db, library_id=LIBRARY, scheme="doi", value="10.1/abc")
    assert found.id == work.id


def test_add_identifier_is_idempotent_for_same_work(db):
    work = _make_work(db)
    first = add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1/abc")
    second = add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1/ABC")
    assert first.id == second.id
    assert len(list_identifiers(db, work.id)) == 1


def test_add_identifier_conflicts_with_another_non_deleted_work(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")
    add_identifier(db, library_id=LIBRARY, work_id=a.id, scheme="doi", value="10.1/abc")

    with pytest.raises(IdentifierConflict) as exc_info:
        add_identifier(db, library_id=LIBRARY, work_id=b.id, scheme="doi", value="10.1/abc")
    assert exc_info.value.existing_work_id == a.id


def test_add_identifier_signals_deleted_work_instead_of_conflict(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")
    add_identifier(db, library_id=LIBRARY, work_id=a.id, scheme="doi", value="10.1/abc")
    soft_delete_work(db, a.id)

    with pytest.raises(IdentifierBelongsToDeletedWork) as exc_info:
        add_identifier(db, library_id=LIBRARY, work_id=b.id, scheme="doi", value="10.1/abc")
    assert exc_info.value.deleted_work_id == a.id


def test_add_identifier_scoped_to_library(db):
    a = _make_work(db, library_id=LIBRARY, title="A")
    b = _make_work(db, library_id=OTHER_LIBRARY, title="B")
    add_identifier(db, library_id=LIBRARY, work_id=a.id, scheme="doi", value="10.1/abc")

    # 不同库，同一个 DOI 字面值不冲突
    identifier = add_identifier(db, library_id=OTHER_LIBRARY, work_id=b.id, scheme="doi", value="10.1/abc")
    assert identifier.work_id == b.id


def test_list_identifiers_empty_for_work_with_none(db):
    work = _make_work(db)
    assert list_identifiers(db, work.id) == []


def test_remove_identifier_ok(db):
    work = _make_work(db)
    identifier = add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1/abc")
    remove_identifier(db, identifier.id)
    assert list_identifiers(db, work.id) == []


def test_remove_identifier_missing_raises(db):
    with pytest.raises(IdentifierNotFound):
        remove_identifier(db, 999)


def test_find_by_identifier_missing_returns_none(db):
    assert find_by_identifier(db, library_id=LIBRARY, scheme="doi", value="nope") is None


def test_find_by_identifier_hides_deleted_work_by_default(db):
    work = _make_work(db)
    add_identifier(db, library_id=LIBRARY, work_id=work.id, scheme="doi", value="10.1/abc")
    soft_delete_work(db, work.id)

    assert find_by_identifier(db, library_id=LIBRARY, scheme="doi", value="10.1/abc") is None
    found = find_by_identifier(db, library_id=LIBRARY, scheme="doi", value="10.1/abc", include_deleted=True)
    assert found.id == work.id


# ── 疑似重复候选 ──────────────────────────────────────────────────────────


def test_find_candidates_by_title_year_key_matches_same_key(db):
    a = _make_work(db, title="Same Title", year=2020)
    b = _make_work(db, title="same title", year=2020)
    _make_work(db, title="Different", year=2020)

    candidates = find_candidates_by_title_year_key(db, library_id=LIBRARY, title_year_key=a.title_year_key)
    assert {c.id for c in candidates} == {a.id, b.id}


def test_find_candidates_excludes_given_work_id(db):
    a = _make_work(db, title="Same Title", year=2020)
    b = _make_work(db, title="same title", year=2020)

    candidates = find_candidates_by_title_year_key(
        db, library_id=LIBRARY, title_year_key=a.title_year_key, exclude_work_id=a.id
    )
    assert [c.id for c in candidates] == [b.id]


def test_record_duplicate_candidate_is_idempotent_per_pair(db):
    a = _make_work(db, title="A", year=2020)
    b = _make_work(db, title="a", year=2020)

    first = record_duplicate_candidate(
        db, library_id=LIBRARY, work_id=a.id, candidate_work_id=b.id, reason="same_title_year"
    )
    second = record_duplicate_candidate(
        db, library_id=LIBRARY, work_id=a.id, candidate_work_id=b.id, reason="same_title_year"
    )
    assert first.id == second.id
    assert len(list_duplicate_candidates(db, library_id=LIBRARY)) == 1


def test_list_duplicate_candidates_filters_by_status(db):
    a = _make_work(db, title="A", year=2020)
    b = _make_work(db, title="a", year=2020)
    candidate = record_duplicate_candidate(
        db, library_id=LIBRARY, work_id=a.id, candidate_work_id=b.id, reason="same_title_year"
    )
    resolve_duplicate_candidate(db, candidate.id, status="confirmed")

    assert len(list_duplicate_candidates(db, library_id=LIBRARY, status="confirmed")) == 1
    assert list_duplicate_candidates(db, library_id=LIBRARY, status="dismissed") == []


def test_resolve_duplicate_candidate_sets_resolved_at(db):
    a = _make_work(db, title="A", year=2020)
    b = _make_work(db, title="a", year=2020)
    candidate = record_duplicate_candidate(
        db, library_id=LIBRARY, work_id=a.id, candidate_work_id=b.id, reason="same_title_year"
    )
    resolved = resolve_duplicate_candidate(db, candidate.id, status="dismissed")
    assert resolved.status == "dismissed"
    assert resolved.resolved_at is not None


def test_resolve_duplicate_candidate_rejects_bad_status(db):
    a = _make_work(db, title="A", year=2020)
    b = _make_work(db, title="a", year=2020)
    candidate = record_duplicate_candidate(
        db, library_id=LIBRARY, work_id=a.id, candidate_work_id=b.id, reason="same_title_year"
    )
    with pytest.raises(ValueError):
        resolve_duplicate_candidate(db, candidate.id, status="pending")


def test_resolve_duplicate_candidate_missing_raises(db):
    with pytest.raises(DuplicateCandidateNotFound):
        resolve_duplicate_candidate(db, 999, status="confirmed")


# ── 文献间关系 ─────────────────────────────────────────────────────────────


def test_add_relation_then_list_from_either_side(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")
    add_relation(db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="preprint_of")

    assert len(list_relations(db, a.id)) == 1
    assert len(list_relations(db, b.id)) == 1


def test_add_relation_rejects_self_loop(db):
    a = _make_work(db)
    with pytest.raises(ValueError):
        add_relation(db, library_id=LIBRARY, from_work_id=a.id, to_work_id=a.id, relation_type="preprint_of")


def test_add_relation_is_idempotent_for_exact_triple(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")
    first = add_relation(
        db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="preprint_of"
    )
    second = add_relation(
        db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="preprint_of"
    )
    assert first.id == second.id
    assert len(list_relations(db, a.id)) == 1


def test_add_relation_different_type_is_a_separate_edge(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")
    add_relation(db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="preprint_of")
    add_relation(
        db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="confirmed_not_duplicate"
    )

    assert len(list_relations(db, a.id)) == 2


def test_remove_relation_ok(db):
    a = _make_work(db, title="A")
    b = _make_work(db, title="B")
    relation = add_relation(
        db, library_id=LIBRARY, from_work_id=a.id, to_work_id=b.id, relation_type="preprint_of"
    )
    remove_relation(db, relation.id)
    assert list_relations(db, a.id) == []


def test_remove_relation_missing_raises(db):
    with pytest.raises(RelationNotFound):
        remove_relation(db, 999)


# ── 来源溯源 ───────────────────────────────────────────────────────────────


def test_record_provenance_then_list_newest_first(db):
    work = _make_work(db)
    record_provenance(db, work_id=work.id, source="crossref", source_id="10.1/abc")
    record_provenance(db, work_id=work.id, source="pubmed", source_id="12345", payload={"raw": "x"})

    rows = list_provenance(db, work.id)
    assert [r.source for r in rows] == ["pubmed", "crossref"]
    assert rows[0].payload == {"raw": "x"}


def test_record_provenance_does_not_dedupe_repeated_pulls(db):
    """同一来源多次回抓是多条独立历史，不是一条记录——这是溯源表存在的意义。"""
    work = _make_work(db)
    record_provenance(db, work_id=work.id, source="crossref")
    record_provenance(db, work_id=work.id, source="crossref")

    assert len(list_provenance(db, work.id)) == 2


def test_list_provenance_empty_for_work_with_none(db):
    work = _make_work(db)
    assert list_provenance(db, work.id) == []
