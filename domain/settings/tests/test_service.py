from domain.settings.service import (
    delete_account_setting,
    delete_site_setting,
    get_account_setting,
    get_site_setting,
    list_account_settings,
    list_site_settings,
    resolve_setting,
    set_account_setting,
    set_site_setting,
)

ACCOUNT = 1
OTHER_ACCOUNT = 2


# ── resolve_setting 三级回退 ───────────────────────────────────────────────


def test_resolve_setting_falls_back_to_default_when_nothing_set(db):
    resolution = resolve_setting(db, module="exporting", key="default_csl_style", default="apa")
    assert resolution.value == "apa"
    assert resolution.source == "default"


def test_resolve_setting_uses_site_level_when_set(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    resolution = resolve_setting(db, module="exporting", key="default_csl_style", default="apa")
    assert resolution.value == "chicago"
    assert resolution.source == "site"


def test_resolve_setting_account_overrides_site(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")

    resolution = resolve_setting(
        db, module="exporting", key="default_csl_style", account_id=ACCOUNT, default="apa"
    )
    assert resolution.value == "mla"
    assert resolution.source == "account"


def test_resolve_setting_without_account_id_skips_account_level(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")

    resolution = resolve_setting(db, module="exporting", key="default_csl_style", default="apa")
    assert resolution.value == "chicago"
    assert resolution.source == "site"


def test_resolve_setting_account_override_is_scoped_per_account(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")

    resolution = resolve_setting(
        db, module="exporting", key="default_csl_style", account_id=OTHER_ACCOUNT, default="apa"
    )
    assert resolution.value == "apa"
    assert resolution.source == "default"


def test_resolve_setting_scoped_per_module_and_key(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    resolution = resolve_setting(db, module="tags", key="default_csl_style", default="apa")
    assert resolution.value == "apa"
    assert resolution.source == "default"


def test_resolve_setting_supports_non_string_values(db):
    set_site_setting(db, module="logs_viewer", key="retention_days", value=30)
    set_account_setting(
        db, account_id=ACCOUNT, module="ai_enrichment", key="batch_config", value={"max_workers": 4}
    )

    numeric = resolve_setting(db, module="logs_viewer", key="retention_days", default=7)
    structured = resolve_setting(
        db, module="ai_enrichment", key="batch_config", account_id=ACCOUNT, default={}
    )
    assert numeric.value == 30
    assert structured.value == {"max_workers": 4}


# ── 站点级 CRUD ────────────────────────────────────────────────────────────


def test_get_site_setting_none_when_absent(db):
    assert get_site_setting(db, module="exporting", key="default_csl_style") is None


def test_set_site_setting_then_get(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    assert get_site_setting(db, module="exporting", key="default_csl_style") == "chicago"


def test_set_site_setting_is_upsert(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    updated = set_site_setting(db, module="exporting", key="default_csl_style", value="mla")
    assert updated.value == "mla"
    assert get_site_setting(db, module="exporting", key="default_csl_style") == "mla"


def test_delete_site_setting_removes_it(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    delete_site_setting(db, module="exporting", key="default_csl_style")
    assert get_site_setting(db, module="exporting", key="default_csl_style") is None


def test_delete_site_setting_is_idempotent_when_absent(db):
    delete_site_setting(db, module="exporting", key="never_set")  # 不应报错


def test_list_site_settings_filtered_by_module(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    set_site_setting(db, module="logs_viewer", key="retention_days", value=30)

    exporting_only = list_site_settings(db, module="exporting")
    assert [s.key for s in exporting_only] == ["default_csl_style"]


def test_list_site_settings_without_module_returns_all(db):
    set_site_setting(db, module="exporting", key="default_csl_style", value="chicago")
    set_site_setting(db, module="logs_viewer", key="retention_days", value=30)

    assert len(list_site_settings(db)) == 2


# ── 账号级 CRUD ────────────────────────────────────────────────────────────


def test_get_account_setting_none_when_absent(db):
    assert get_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style") is None


def test_set_account_setting_then_get(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")
    assert (
        get_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style")
        == "mla"
    )


def test_set_account_setting_is_upsert(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")
    updated = set_account_setting(
        db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="apa"
    )
    assert updated.value == "apa"


def test_set_account_setting_scoped_per_account(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")
    assert (
        get_account_setting(db, account_id=OTHER_ACCOUNT, module="exporting", key="default_csl_style")
        is None
    )


def test_delete_account_setting_removes_it(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")
    delete_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style")
    assert get_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style") is None


def test_delete_account_setting_is_idempotent_when_absent(db):
    delete_account_setting(db, account_id=ACCOUNT, module="exporting", key="never_set")  # 不应报错


def test_list_account_settings_filtered_by_module(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")
    set_account_setting(db, account_id=ACCOUNT, module="tags", key="ai_autogroup", value=True)

    exporting_only = list_account_settings(db, account_id=ACCOUNT, module="exporting")
    assert [s.key for s in exporting_only] == ["default_csl_style"]


def test_list_account_settings_scoped_per_account(db):
    set_account_setting(db, account_id=ACCOUNT, module="exporting", key="default_csl_style", value="mla")
    set_account_setting(
        db, account_id=OTHER_ACCOUNT, module="exporting", key="default_csl_style", value="apa"
    )

    assert len(list_account_settings(db, account_id=ACCOUNT)) == 1
