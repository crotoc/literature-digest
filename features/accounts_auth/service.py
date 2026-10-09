"""账号注册 / 登录 / 密码重置的编排逻辑。

本模块不开表——它组合 `caps/authn`（密码哈希、会话签名）和
`domain/{accounts,libraries}`（账号/会话/库的持久化）。策略性的常量（用户名
最短几位、密码最短几位、注册是否开放、重置链接有效期）**归这一层决定**，
不是 `caps/authn` 或 `domain/accounts` 的知识——`caps/authn` 的 docstring
明确写了"密码长度、令牌有效期、会话时长都由调用方作为参数传进来"，
这个"调用方"就是本模块。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——约定见 `domain/accounts/service.py` 的同一段说明；`register()`
内部连续调用 `create_account` 和 `create_library` 两个 domain 函数，
原子性由调用方的事务边界保证（两次调用之间不 commit，第二次失败时
第一次的插入也还没落盘）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from caps.authn import SESSION_PURPOSE, TokenInvalid, sign, unsign
from domain.accounts import (
    DEFAULT_PASSWORD_RESET_TTL_SECONDS,
    AccountDTO,
    SessionDTO,
)
from domain.accounts import consume_password_reset as _domain_consume_password_reset
from domain.accounts import create_account as _domain_create_account
from domain.accounts import create_session as _domain_create_session
from domain.accounts import get_account as _domain_get_account
from domain.accounts import get_session as _domain_get_session
from domain.accounts import request_password_reset as _domain_request_password_reset
from domain.accounts import revoke_session as _domain_revoke_session
from domain.accounts import verify_password as _domain_verify_password
from domain.libraries import LibraryDTO
from domain.libraries import create_library as _domain_create_library
from infra.errors import AppError, ValidationFailed

MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 12

# ── 异常 ─────────────────────────────────────────────────────────────────


class RegistrationDisabled(AppError):
    """`ALLOW_SELF_SIGNUP` 关闭时拒绝注册。"""

    status_code = 403
    code = "registration_disabled"


class UsernameTooShort(ValidationFailed):
    code = "username_too_short"


class PasswordTooShort(ValidationFailed):
    code = "password_too_short"


class EmailInvalid(ValidationFailed):
    code = "email_invalid"


class PasswordResetUnavailable(AppError):
    """SMTP 没配时密码重置入口显示不可用——v1 没有 `adapters/delivery/mailer`
    （计划里是 E4 阶段才做），所以这个分支在 v1 几乎总是会被触发；调用方
    (`app/pages/auth`) 应该同时用同一个 `smtp_configured` 标志隐藏/禁用
    "忘记密码"链接，不要让用户点进去才看到这个异常。保留这条路径是为了
    E4 接上真的 mailer 之后，这个函数不用改签名。
    """

    status_code = 503
    code = "password_reset_unavailable"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegisteredAccount:
    account: AccountDTO
    library: LibraryDTO


@dataclass(frozen=True)
class LoginResult:
    account: AccountDTO
    session: SessionDTO
    cookie_token: str


# ── 注册 ─────────────────────────────────────────────────────────────────


def register(
    db: Session,
    *,
    username: str,
    email: str,
    password: str,
    allow_self_signup: bool,
) -> RegisteredAccount:
    """注册一个账号，并自动给它建一个同名个人库（自己当 owner）。

    校验顺序：先看注册开不开放（不开放就不该浪费一次校验），再按
    用户名 → 密码 → 邮箱的顺序挨个检查格式；第一个不满足的就报，不汇总成
    一个列表——这几条规则简单到用户看到第一条就能连带猜到其余几条。

    用户名/邮箱唯一性冲突（`UsernameTaken` / `EmailTaken`）直接从
    `domain.accounts.create_account` 原样往上抛，本模块不重新包装。

    Raises:
        RegistrationDisabled: `allow_self_signup` 为 False。
        UsernameTooShort / PasswordTooShort / EmailInvalid: 格式不满足。
        UsernameTaken / EmailTaken: 唯一性冲突（来自 `domain.accounts`）。
    """
    if not allow_self_signup:
        raise RegistrationDisabled("本站未开放自助注册")
    if len(username) < MIN_USERNAME_LENGTH:
        raise UsernameTooShort(f"用户名至少 {MIN_USERNAME_LENGTH} 个字符")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort(f"密码至少 {MIN_PASSWORD_LENGTH} 个字符")
    if "@" not in email:
        raise EmailInvalid("邮箱格式不对：必须包含 @")

    account = _domain_create_account(db, username=username, email=email, password=password)
    library = _domain_create_library(db, owner_account_id=account.id, name=f"{username} 的文库")
    return RegisteredAccount(account=account, library=library)


# ── 登录 / 会话 ────────────────────────────────────────────────────────────


def login(
    db: Session,
    *,
    username_or_email: str,
    password: str,
    session_secret: str | Sequence[str],
) -> LoginResult:
    """校验密码、开一个会话行、把会话 id 签成 cookie-safe 的 token。

    `session_secret` 对应 `.env` 的 `APP_SECRET_KEY`（或轮换时的密钥列表），
    本模块不知道、也不保存它——每次调用都由调用方传入。`InvalidCredentials`
    直接从 `domain.accounts.verify_password` 原样往上抛。
    """
    account = _domain_verify_password(db, username_or_email=username_or_email, password=password)
    session = _domain_create_session(db, account.id)
    cookie_token = sign(str(session.id), secret=session_secret, purpose=SESSION_PURPOSE)
    return LoginResult(account=account, session=session, cookie_token=cookie_token)


def resume_session(
    db: Session,
    *,
    cookie_token: str,
    session_secret: str | Sequence[str],
    max_age_seconds: int,
) -> AccountDTO:
    """从请求带来的 cookie token 恢复出当前账号；顺手校验会话还活着。

    `max_age_seconds`（cookie 多久过期）是调用方的策略，本模块不内置默认值
    ——和 `caps.authn.unsign` 本身"调用方必须显式给 max_age"的设计一致。

    Raises:
        caps.authn.TokenExpired / TokenInvalid: 签名过期 / 不符 / 被改过。
        domain.accounts.SessionNotFound / SessionRevoked: 签名是对的，但
            会话行已经不存在或被吊销了（比如用户在别处点了登出）。
    """
    session_id_str = unsign(
        cookie_token, secret=session_secret, max_age_seconds=max_age_seconds, purpose=SESSION_PURPOSE
    )
    try:
        session_id = int(session_id_str)
    except ValueError as exc:
        # 签名已经验证过 payload 没被改过，理论上不会走到这——除非拿一个
        # 别的 purpose 签出来的 token 硬塞进来（见 caps/authn 的 purpose 隔离
        # 说明，正常情况下那种 token 在上面 unsign 这一步就该被拒）。
        # 防御性地当成"token 不对"而不是让一个 ValueError 裸着往上抛。
        raise TokenInvalid(f"会话 cookie 的 payload 不是合法 id：{session_id_str!r}") from exc
    session = _domain_get_session(db, session_id)
    return _domain_get_account(db, session.account_id)


def logout(db: Session, *, session_id: int) -> None:
    """吊销一个会话。幂等，直接转发给 `domain.accounts.revoke_session`。"""
    _domain_revoke_session(db, session_id)


# ── 密码重置 ───────────────────────────────────────────────────────────────


def request_password_reset(
    db: Session,
    *,
    email: str,
    smtp_configured: bool,
    ttl_seconds: int = DEFAULT_PASSWORD_RESET_TTL_SECONDS,
) -> tuple[AccountDTO, str] | None:
    """发一个密码重置令牌。

    `smtp_configured` 来自调用方（最终是 `.env` 有没有配 SMTP，v1 目前
    `infra/config.py` 根本没有这几个字段，所以这个参数在 v1 实际总是
    `False`）——这不是本模块关心邮件怎么发，而是在"入口要不要可用"这件事上
    和 `caps/authn` 的"策略由调用方决定"原则保持一致：真要发邮件是
    `adapters/delivery/mailer`（E4）的事，本模块只负责在没有它时诚实地
    报不可用，而不是悄悄发出一个永远没人去用的令牌。

    邮箱不存在时返回 `None`（而不是异常）的反枚举设计，原样继承自
    `domain.accounts.request_password_reset`。

    Raises:
        PasswordResetUnavailable: `smtp_configured` 为 False。
    """
    if not smtp_configured:
        raise PasswordResetUnavailable("密码重置邮件未配置，暂不可用")
    return _domain_request_password_reset(db, email=email, ttl_seconds=ttl_seconds)


def consume_password_reset(db: Session, *, plaintext: str, new_password: str) -> AccountDTO:
    """用明文令牌消费一次密码重置，顺带把新密码长度校验收在这一层——
    和注册时的 `MIN_PASSWORD_LENGTH` 保持同一条策略，不让"通过重置链接绕开
    密码强度要求"成为一个口子。

    Raises:
        PasswordTooShort: 新密码不满足长度要求。
        domain.accounts.PasswordResetInvalid / PasswordResetUsed /
            PasswordResetExpired: 原样转发。
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort(f"密码至少 {MIN_PASSWORD_LENGTH} 个字符")
    return _domain_consume_password_reset(db, plaintext=plaintext, new_password=new_password)
