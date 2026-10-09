import pytest

from domain.libraries.service import (
    DuplicateMembership,
    LastOwnerRequired,
    LibraryNotFound,
    MembershipNotFound,
    NotLibraryOwner,
    add_member,
    create_library,
    get_library,
    list_libraries_for_account,
    list_members,
    remove_member,
    rename_library,
    resolve_scope,
    update_member_role,
)

OWNER = 1
OTHER = 2
STRANGER = 3


def _make_library(db, *, owner_account_id=OWNER, name="Alice's library"):
    return create_library(db, owner_account_id=owner_account_id, name=name)


# ── create_library / get_library ──────────────────────────────────────────


def test_create_library_makes_owner_member(db):
    library = _make_library(db)
    membership = resolve_scope(db, account_id=OWNER, library_id=library.id)
    assert membership.role == "owner"


def test_create_library_strips_name(db):
    library = create_library(db, owner_account_id=OWNER, name="  Padded  ")
    assert library.name == "Padded"


def test_create_library_rejects_empty_name(db):
    with pytest.raises(ValueError):
        create_library(db, owner_account_id=OWNER, name="   ")


def test_get_library_returns_dto(db):
    created = _make_library(db)
    fetched = get_library(db, created.id)
    assert fetched == created


def test_get_library_missing_raises(db):
    with pytest.raises(LibraryNotFound):
        get_library(db, 999)


# ── rename_library ─────────────────────────────────────────────────────────


def test_rename_library_by_member_ok(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")
    renamed = rename_library(db, library_id=library.id, name="New Name", acting_account_id=OTHER)
    assert renamed.name == "New Name"


def test_rename_library_by_non_member_raises(db):
    library = _make_library(db)
    with pytest.raises(LibraryNotFound):
        rename_library(db, library_id=library.id, name="Hijacked", acting_account_id=STRANGER)


def test_rename_library_rejects_empty_name(db):
    library = _make_library(db)
    with pytest.raises(ValueError):
        rename_library(db, library_id=library.id, name="  ", acting_account_id=OWNER)


# ── list_libraries_for_account ─────────────────────────────────────────────


def test_list_libraries_for_account_only_shows_member_libraries(db):
    mine = _make_library(db, owner_account_id=OWNER, name="mine")
    _make_library(db, owner_account_id=OTHER, name="not mine")

    libraries = list_libraries_for_account(db, OWNER)
    assert [library.id for library in libraries] == [mine.id]


# ── resolve_scope ────────────────────────────────────────────────────────


def test_resolve_scope_returns_membership(db):
    library = _make_library(db)
    membership = resolve_scope(db, account_id=OWNER, library_id=library.id)
    assert membership.library_id == library.id
    assert membership.account_id == OWNER
    assert membership.role == "owner"


def test_resolve_scope_missing_library_raises(db):
    with pytest.raises(LibraryNotFound):
        resolve_scope(db, account_id=OWNER, library_id=999)


def test_resolve_scope_non_member_raises_same_error_as_missing_library(db):
    """库存在但不是成员，和库根本不存在，必须报同一个异常类型——
    不然等于向非成员泄露"这个 id 是真的"。"""
    library = _make_library(db)
    with pytest.raises(LibraryNotFound):
        resolve_scope(db, account_id=STRANGER, library_id=library.id)


# ── list_members ───────────────────────────────────────────────────────────


def test_list_members_requires_membership(db):
    library = _make_library(db)
    with pytest.raises(LibraryNotFound):
        list_members(db, library_id=library.id, acting_account_id=STRANGER)


def test_list_members_returns_all_including_non_owner(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")

    members = list_members(db, library_id=library.id, acting_account_id=OTHER)
    assert {m.account_id for m in members} == {OWNER, OTHER}


# ── add_member ───────────────────────────────────────────────────────────


def test_add_member_by_owner_ok(db):
    library = _make_library(db)
    membership = add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER)
    assert membership.role == "member"


def test_add_member_by_non_owner_raises(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")
    with pytest.raises(NotLibraryOwner):
        add_member(db, library_id=library.id, account_id=STRANGER, acting_account_id=OTHER)


def test_add_member_duplicate_raises(db):
    library = _make_library(db)
    with pytest.raises(DuplicateMembership):
        add_member(db, library_id=library.id, account_id=OWNER, acting_account_id=OWNER)


def test_add_member_invalid_role_raises(db):
    library = _make_library(db)
    with pytest.raises(ValueError):
        add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="admin")


# ── remove_member ──────────────────────────────────────────────────────────


def test_remove_member_by_owner_ok(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER)
    remove_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER)
    with pytest.raises(LibraryNotFound):
        resolve_scope(db, account_id=OTHER, library_id=library.id)


def test_remove_member_last_owner_raises(db):
    library = _make_library(db)
    with pytest.raises(LastOwnerRequired):
        remove_member(db, library_id=library.id, account_id=OWNER, acting_account_id=OWNER)


def test_remove_member_by_non_owner_raises(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")
    with pytest.raises(NotLibraryOwner):
        remove_member(db, library_id=library.id, account_id=OWNER, acting_account_id=OTHER)


def test_remove_member_missing_raises(db):
    library = _make_library(db)
    with pytest.raises(MembershipNotFound):
        remove_member(db, library_id=library.id, account_id=STRANGER, acting_account_id=OWNER)


# ── update_member_role ─────────────────────────────────────────────────────


def test_update_member_role_ok(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")
    updated = update_member_role(
        db, library_id=library.id, account_id=OTHER, new_role="owner", acting_account_id=OWNER
    )
    assert updated.role == "owner"


def test_update_member_role_demote_last_owner_raises(db):
    library = _make_library(db)
    with pytest.raises(LastOwnerRequired):
        update_member_role(
            db, library_id=library.id, account_id=OWNER, new_role="member", acting_account_id=OWNER
        )


def test_update_member_role_demote_one_of_two_owners_ok(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="owner")
    updated = update_member_role(
        db, library_id=library.id, account_id=OWNER, new_role="member", acting_account_id=OTHER
    )
    assert updated.role == "member"


def test_update_member_role_invalid_role_raises(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")
    with pytest.raises(ValueError):
        update_member_role(
            db, library_id=library.id, account_id=OTHER, new_role="admin", acting_account_id=OWNER
        )


def test_update_member_role_by_non_owner_raises(db):
    library = _make_library(db)
    add_member(db, library_id=library.id, account_id=OTHER, acting_account_id=OWNER, role="member")
    with pytest.raises(NotLibraryOwner):
        update_member_role(
            db, library_id=library.id, account_id=OWNER, new_role="member", acting_account_id=OTHER
        )


def test_update_member_role_missing_member_raises(db):
    library = _make_library(db)
    with pytest.raises(MembershipNotFound):
        update_member_role(
            db, library_id=library.id, account_id=STRANGER, new_role="owner", acting_account_id=OWNER
        )
