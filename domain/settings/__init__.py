"""domain/settings。详见 contract.py。"""

from domain.settings.contract import (
    AccountSettingDTO,
    SettingResolution,
    SiteSettingDTO,
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

__all__ = [
    "AccountSettingDTO",
    "SettingResolution",
    "SiteSettingDTO",
    "delete_account_setting",
    "delete_site_setting",
    "get_account_setting",
    "get_site_setting",
    "list_account_settings",
    "list_site_settings",
    "resolve_setting",
    "set_account_setting",
    "set_site_setting",
]
