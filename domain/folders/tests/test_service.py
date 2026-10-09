import pytest

from domain.folders.service import (
    FolderCycle,
    FolderNotFound,
    WorkFolderNotFound,
    add_work_to_folder,
    create_folder,
    delete_folder,
    get_folder,
    list_folders,
    list_folders_for_work,
    list_work_ids_in_folder,
    move_folder,
    remove_work_from_folder,
    rename_folder,
)

LIBRARY = 1
OTHER_LIBRARY = 2
WORK_A = 101
WORK_B = 102


# ── create_folder / get_folder ───────────────────────────────────────────


def test_create_folder_strips_name(db):
    folder = create_folder(db, library_id=LIBRARY, name="  Papers  ")
    assert folder.name == "Papers"


def test_create_folder_rejects_empty_name(db):
    with pytest.raises(ValueError):
        create_folder(db, library_id=LIBRARY, name="   ")


def test_create_folder_defaults_to_root(db):
    folder = create_folder(db, library_id=LIBRARY, name="Root Folder")
    assert folder.parent_folder_id is None


def test_create_nested_folder(db):
    parent = create_folder(db, library_id=LIBRARY, name="Parent")
    child = create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=parent.id)
    assert child.parent_folder_id == parent.id


def test_create_folder_missing_parent_raises(db):
    with pytest.raises(FolderNotFound):
        create_folder(db, library_id=LIBRARY, name="Orphan", parent_folder_id=999)


def test_create_folder_parent_in_other_library_raises(db):
    parent = create_folder(db, library_id=OTHER_LIBRARY, name="Parent")
    with pytest.raises(ValueError):
        create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=parent.id)


def test_get_folder_missing_raises(db):
    with pytest.raises(FolderNotFound):
        get_folder(db, 999)


# ── rename_folder ─────────────────────────────────────────────────────────


def test_rename_folder_ok(db):
    folder = create_folder(db, library_id=LIBRARY, name="Old Name")
    renamed = rename_folder(db, folder.id, "New Name")
    assert renamed.name == "New Name"


def test_rename_folder_rejects_empty_name(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    with pytest.raises(ValueError):
        rename_folder(db, folder.id, "   ")


def test_rename_folder_missing_raises(db):
    with pytest.raises(FolderNotFound):
        rename_folder(db, 999, "New Name")


# ── move_folder ───────────────────────────────────────────────────────────


def test_move_folder_to_root(db):
    parent = create_folder(db, library_id=LIBRARY, name="Parent")
    child = create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=parent.id)
    moved = move_folder(db, child.id, None)
    assert moved.parent_folder_id is None


def test_move_folder_to_another_parent(db):
    a = create_folder(db, library_id=LIBRARY, name="A")
    b = create_folder(db, library_id=LIBRARY, name="B")
    moved = move_folder(db, a.id, b.id)
    assert moved.parent_folder_id == b.id


def test_move_folder_rejects_self_as_parent(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    with pytest.raises(FolderCycle):
        move_folder(db, folder.id, folder.id)


def test_move_folder_rejects_moving_into_own_descendant(db):
    grandparent = create_folder(db, library_id=LIBRARY, name="Grandparent")
    parent = create_folder(db, library_id=LIBRARY, name="Parent", parent_folder_id=grandparent.id)
    child = create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=parent.id)

    with pytest.raises(FolderCycle):
        move_folder(db, grandparent.id, child.id)


def test_move_folder_missing_raises(db):
    with pytest.raises(FolderNotFound):
        move_folder(db, 999, None)


def test_move_folder_into_missing_parent_raises(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    with pytest.raises(FolderNotFound):
        move_folder(db, folder.id, 999)


def test_move_folder_across_library_raises(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    other = create_folder(db, library_id=OTHER_LIBRARY, name="Y")
    with pytest.raises(ValueError):
        move_folder(db, folder.id, other.id)


# ── delete_folder ───────────────────────────────────────────────────────────


def test_delete_leaf_folder(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    delete_folder(db, folder.id)
    with pytest.raises(FolderNotFound):
        get_folder(db, folder.id)


def test_delete_folder_cascades_to_children(db):
    parent = create_folder(db, library_id=LIBRARY, name="Parent")
    child = create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=parent.id)
    grandchild = create_folder(db, library_id=LIBRARY, name="Grandchild", parent_folder_id=child.id)

    delete_folder(db, parent.id)

    with pytest.raises(FolderNotFound):
        get_folder(db, child.id)
    with pytest.raises(FolderNotFound):
        get_folder(db, grandchild.id)


def test_delete_folder_removes_work_associations_but_not_the_idea_of_the_work(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)

    delete_folder(db, folder.id)

    # 关联没了，但这只是本模块自己的表——works 本身是不是还活着不归这里管
    assert list_folders_for_work(db, WORK_A) == []


def test_delete_folder_missing_raises(db):
    with pytest.raises(FolderNotFound):
        delete_folder(db, 999)


def test_delete_folder_does_not_affect_siblings(db):
    kept = create_folder(db, library_id=LIBRARY, name="Kept")
    gone = create_folder(db, library_id=LIBRARY, name="Gone")
    delete_folder(db, gone.id)
    assert get_folder(db, kept.id).id == kept.id


# ── list_folders ───────────────────────────────────────────────────────────


def test_list_folders_without_parent_filter_returns_all_flat(db):
    a = create_folder(db, library_id=LIBRARY, name="A")
    b = create_folder(db, library_id=LIBRARY, name="B", parent_folder_id=a.id)

    folders = list_folders(db, library_id=LIBRARY)
    assert {f.id for f in folders} == {a.id, b.id}


def test_list_folders_filtered_by_root_parent(db):
    root = create_folder(db, library_id=LIBRARY, name="Root")
    create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=root.id)

    roots = list_folders(db, library_id=LIBRARY, parent_folder_id=None)
    assert [f.id for f in roots] == [root.id]


def test_list_folders_filtered_by_specific_parent(db):
    root = create_folder(db, library_id=LIBRARY, name="Root")
    child = create_folder(db, library_id=LIBRARY, name="Child", parent_folder_id=root.id)
    create_folder(db, library_id=LIBRARY, name="Grandchild", parent_folder_id=child.id)

    children = list_folders(db, library_id=LIBRARY, parent_folder_id=root.id)
    assert [f.id for f in children] == [child.id]


def test_list_folders_scoped_to_library(db):
    create_folder(db, library_id=LIBRARY, name="Mine")
    create_folder(db, library_id=OTHER_LIBRARY, name="Not mine")

    folders = list_folders(db, library_id=LIBRARY)
    assert len(folders) == 1
    assert folders[0].name == "Mine"


# ── work ↔ folder 关联 ───────────────────────────────────────────────────


def test_add_work_to_folder_then_list_both_directions(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)

    assert [f.id for f in list_folders_for_work(db, WORK_A)] == [folder.id]
    assert list_work_ids_in_folder(db, folder.id) == [WORK_A]


def test_add_work_to_folder_is_idempotent(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    first = add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)
    second = add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)
    assert first.id == second.id
    assert list_work_ids_in_folder(db, folder.id) == [WORK_A]


def test_add_work_to_folder_missing_folder_raises(db):
    with pytest.raises(FolderNotFound):
        add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=999)


def test_add_work_to_folder_wrong_library_raises(db):
    folder = create_folder(db, library_id=OTHER_LIBRARY, name="X")
    with pytest.raises(ValueError):
        add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)


def test_work_can_be_in_multiple_folders(db):
    folder_a = create_folder(db, library_id=LIBRARY, name="A")
    folder_b = create_folder(db, library_id=LIBRARY, name="B")
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder_a.id)
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder_b.id)

    assert {f.id for f in list_folders_for_work(db, WORK_A)} == {folder_a.id, folder_b.id}


def test_folder_can_contain_multiple_works(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_B, folder_id=folder.id)

    assert set(list_work_ids_in_folder(db, folder.id)) == {WORK_A, WORK_B}


def test_remove_work_from_folder_ok(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    add_work_to_folder(db, library_id=LIBRARY, work_id=WORK_A, folder_id=folder.id)
    remove_work_from_folder(db, work_id=WORK_A, folder_id=folder.id)
    assert list_work_ids_in_folder(db, folder.id) == []


def test_remove_work_from_folder_missing_raises(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    with pytest.raises(WorkFolderNotFound):
        remove_work_from_folder(db, work_id=WORK_A, folder_id=folder.id)


def test_list_folders_for_work_empty_when_none(db):
    assert list_folders_for_work(db, WORK_A) == []


def test_list_work_ids_in_folder_empty_when_none(db):
    folder = create_folder(db, library_id=LIBRARY, name="X")
    assert list_work_ids_in_folder(db, folder.id) == []
