import pytest

from domain.connections import create_connection
from features.ai_enrichment import (
    UnknownProvider,
    WrongKind,
    create_ai_profile,
    delete_ai_profile,
    list_ai_profiles,
    rotate_ai_profile_key,
    set_default_ai_profile,
    update_ai_profile,
)

ACCOUNT = 1


def test_create_ai_profile_then_it_shows_in_the_list(db):
    create_ai_profile(
        db, account_id=ACCOUNT, provider="openai_compat", name="my-openai", model="gpt-4o-mini",
        api_key="sk-test",
    )

    profiles = list_ai_profiles(db, account_id=ACCOUNT)

    assert len(profiles) == 1
    assert profiles[0].name == "my-openai"
    assert profiles[0].config == {"provider": "openai_compat", "model": "gpt-4o-mini"}
    assert profiles[0].has_secret is True


def test_create_ai_profile_rejects_unknown_provider(db):
    with pytest.raises(UnknownProvider):
        create_ai_profile(
            db, account_id=ACCOUNT, provider="deepseek", name="x", model="x", api_key="sk-test"
        )


def test_create_ai_profile_rejects_empty_api_key(db):
    with pytest.raises(ValueError):
        create_ai_profile(db, account_id=ACCOUNT, provider="openai_compat", name="x", model="x", api_key="  ")


def test_create_ai_profile_with_base_url_keeps_it_in_config(db):
    profile = create_ai_profile(
        db, account_id=ACCOUNT, provider="openai_compat", name="local-vllm", model="llama",
        api_key="sk-test", base_url="http://localhost:8000/v1",
    )

    assert profile.config["base_url"] == "http://localhost:8000/v1"


def test_update_ai_profile_changes_model_and_base_url(db):
    profile = create_ai_profile(
        db, account_id=ACCOUNT, provider="anthropic", name="claude", model="claude-haiku-4-5-20251001",
        api_key="sk-ant-test",
    )

    updated = update_ai_profile(db, profile.id, model="claude-opus-5", base_url="https://proxy.example/anthropic")

    assert updated.config["model"] == "claude-opus-5"
    assert updated.config["base_url"] == "https://proxy.example/anthropic"


def test_rotate_then_delete_then_set_default(db):
    a = create_ai_profile(db, account_id=ACCOUNT, provider="openai_compat", name="a", model="m", api_key="k1")
    b = create_ai_profile(db, account_id=ACCOUNT, provider="openai_compat", name="b", model="m", api_key="k2")

    rotated = rotate_ai_profile_key(db, a.id, api_key="k1-new")
    assert rotated.has_secret is True

    defaulted = set_default_ai_profile(db, b.id)
    assert defaulted.is_default is True

    delete_ai_profile(db, a.id)
    remaining = {p.id for p in list_ai_profiles(db, account_id=ACCOUNT)}
    assert remaining == {b.id}


def test_ai_profile_operations_reject_connection_of_another_kind(db):
    """不借 features/connection_setup 造一个 source_credential——features
    之间禁止互相 import（lint 规则 4）。直接用 domain.connections 造一个
    别的 kind 的连接即可，本模块对 domain/connections 本来就是合法依赖。"""
    source_credential = create_connection(
        db, account_id=ACCOUNT, kind="source_credential", name="not-an-ai-profile",
        config={"source": "crossref"},
    )

    for fn, kwargs in (
        (update_ai_profile, {}),
        (rotate_ai_profile_key, {"api_key": "x"}),
        (delete_ai_profile, {}),
        (set_default_ai_profile, {}),
    ):
        with pytest.raises(WrongKind):
            fn(db, source_credential.id, **kwargs)
