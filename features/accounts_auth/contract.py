"""features/accounts_auth 的对外表面。外部只许 import 这里的东西。

除本模块自己的异常外，也重新导出 `register()` / `resume_session()` 等函数
可能原样往上抛的 `domain.accounts` 异常（`UsernameTaken` / `EmailTaken` /
`InvalidCredentials` / ...）和 `caps.authn` 的 `TokenExpired` / `TokenInvalid`
——调用方（`app/pages/auth`）只需要 import 这一个模块就能拿到完整的异常面，
不需要再去翻 `domain.accounts` 或 `caps.authn` 的契约。
"""

from caps.authn import TokenExpired, TokenInvalid
from domain.accounts import (
    AccountNotFound,
    EmailTaken,
    InvalidCredentials,
    PasswordResetExpired,
    PasswordResetInvalid,
    PasswordResetUsed,
    SessionNotFound,
    SessionRevoked,
    UsernameTaken,
)
from features.accounts_auth.service import (
    MIN_PASSWORD_LENGTH,
    MIN_USERNAME_LENGTH,
    EmailInvalid,
    LoginResult,
    PasswordResetUnavailable,
    PasswordTooShort,
    RegisteredAccount,
    RegistrationDisabled,
    UsernameTooShort,
    consume_password_reset,
    login,
    logout,
    register,
    request_password_reset,
    resume_session,
)

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "MIN_USERNAME_LENGTH",
    "AccountNotFound",
    "EmailInvalid",
    "EmailTaken",
    "InvalidCredentials",
    "LoginResult",
    "PasswordResetExpired",
    "PasswordResetInvalid",
    "PasswordResetUnavailable",
    "PasswordResetUsed",
    "PasswordTooShort",
    "RegisteredAccount",
    "RegistrationDisabled",
    "SessionNotFound",
    "SessionRevoked",
    "TokenExpired",
    "TokenInvalid",
    "UsernameTaken",
    "UsernameTooShort",
    "consume_password_reset",
    "login",
    "logout",
    "register",
    "request_password_reset",
    "resume_session",
]
