"""domain/accounts 的业务逻辑：账号 CRUD、密码校验（含登录时的哈希升级）、
会话发放与吊销、API token 发放与吊销、密码重置令牌的发放与消费。

本模块的函数全部接收调用方传入的 `Session`，自己不建 session、不 commit、
不 rollback——事务边界在 `app/shell/deps.py`（请求路径）或
`infra.db.session_scope`（后台任务），这是整个项目的约定（见 infra/db.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from caps.authn import (
    DEFAULT_PASSWORD_PARAMS,
    PasswordParams,
    check_password,
    hash_password,
    lookup_prefix_of,
    new_token,
    verify_token,
)
from domain.accounts.models import Account, AccountSession, ApiToken, PasswordReset
from infra.errors import AppError, Conflict, NotFound

API_TOKEN_PREFIX = "ld"
PASSWORD_RESET_PREFIX = "pwdrst"
DEFAULT_PASSWORD_RESET_TTL_SECONDS = 3600


def _utcnow() -> datetime:
    """朴素（无 tzinfo）UTC 时间，和 models.py 的同名函数保持一致——
    理由见那边的 docstring。"""
    return datetime.now(UTC).replace(tzinfo=None)


# ── 异常 ─────────────────────────────────────────────────────────────────


class AccountNotFound(NotFound):
    code = "account_not_found"


class UsernameTaken(Conflict):
    code = "username_taken"


class EmailTaken(Conflict):
    code = "email_taken"


class InvalidCredentials(AppError):
    """用户名/邮箱不存在和密码不对，用同一个异常——不向调用方泄露"是哪一种不对"。"""

    status_code = 401
    code = "invalid_credentials"


class SessionNotFound(NotFound):
    code = "session_not_found"


class SessionRevoked(AppError):
    status_code = 401
    code = "session_revoked"


class TokenNotFound(NotFound):
    code = "token_not_found"


class TokenRevoked(AppError):
    status_code = 401
    code = "token_revoked"


class PasswordResetInvalid(AppError):
    code = "password_reset_invalid"


class PasswordResetExpired(AppError):
    code = "password_reset_expired"


class PasswordResetUsed(AppError):
    code = "password_reset_used"


# ── DTO ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AccountDTO:
    id: int
    username: str
    email: str
    created_at: datetime


@dataclass(frozen=True)
class SessionDTO:
    id: int
    account_id: int
    created_at: datetime
    last_seen_at: datetime
    revoked: bool


@dataclass(frozen=True)
class ApiTokenDTO:
    id: int
    account_id: int
    name: str | None
    lookup_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


def _account_dto(row: Account) -> AccountDTO:
    return AccountDTO(id=row.id, username=row.username, email=row.email, created_at=row.created_at)


def _session_dto(row: AccountSession) -> SessionDTO:
    return SessionDTO(
        id=row.id,
        account_id=row.account_id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        revoked=row.revoked_at is not None,
    )


def _token_dto(row: ApiToken) -> ApiTokenDTO:
    return ApiTokenDTO(
        id=row.id,
        account_id=row.account_id,
        name=row.name,
        lookup_prefix=row.lookup_prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked=row.revoked_at is not None,
    )


# ── 账号 ─────────────────────────────────────────────────────────────────


def create_account(
    db: Session,
    *,
    username: str,
    email: str,
    password: str,
    password_params: PasswordParams = DEFAULT_PASSWORD_PARAMS,
) -> AccountDTO:
    """创建账号。

    用户名大小写敏感（"Alice" 和 "alice" 是两个账号——v1 不做大小写无关唯一性，
    见 README 裁剪范围）；邮箱统一转小写后再比较和入库，防止同一邮箱靠大小写
    开出多个账号。

    Raises:
        UsernameTaken / EmailTaken: 唯一性检查在插入前做；并发撞车的极小概率
            留给 DB 唯一索引兜底，那种情况下调用方会在 commit 时拿到一个通用
            的 IntegrityError 而不是这两个异常（见 README「刻意裁剪的范围」）。
    """
    username = username.strip()
    email = email.strip().lower()

    if db.scalar(select(Account).where(Account.username == username)) is not None:
        raise UsernameTaken(f"用户名已被占用：{username}")
    if db.scalar(select(Account).where(Account.email == email)) is not None:
        raise EmailTaken(f"邮箱已被注册：{email}")

    row = Account(
        username=username,
        email=email,
        password_hash=hash_password(password, params=password_params),
    )
    db.add(row)
    db.flush()
    return _account_dto(row)


def get_account(db: Session, account_id: int) -> AccountDTO:
    row = db.get(Account, account_id)
    if row is None:
        raise AccountNotFound(f"账号不存在：{account_id}")
    return _account_dto(row)


def verify_password(
    db: Session,
    *,
    username_or_email: str,
    password: str,
    password_params: PasswordParams = DEFAULT_PASSWORD_PARAMS,
) -> AccountDTO:
    """校验登录。用户名和邮箱走同一个入口：按原样先试用户名精确匹配，不命中
    再试邮箱（邮箱比较前转小写，和 create_account 对齐）。

    找不到账号和密码错误抛同一个 `InvalidCredentials`——不告诉调用方是哪一种，
    否则等于告诉攻击者"这个用户名存在，继续试密码就好"。

    登录成功且 `check_password` 判断该升级哈希参数时，顺手把 `password_hash`
    改成新参数——这是全流程里唯一还拿得到明文的时刻（见 caps/authn 的
    `needs_rehash` 设计）。
    """
    row = db.scalar(select(Account).where(Account.username == username_or_email))
    if row is None:
        row = db.scalar(select(Account).where(Account.email == username_or_email.strip().lower()))
    if row is None:
        raise InvalidCredentials("用户名/邮箱或密码不对")

    result = check_password(row.password_hash, password, params=password_params)
    if not result.ok:
        raise InvalidCredentials("用户名/邮箱或密码不对")
    if result.needs_rehash:
        row.password_hash = hash_password(password, params=password_params)
        db.flush()
    return _account_dto(row)


# ── 会话 ─────────────────────────────────────────────────────────────────


def create_session(db: Session, account_id: int) -> SessionDTO:
    """开一个新会话行。调用方拿到 `SessionDTO.id` 后自己用 `caps.authn.sign()`
    签成 cookie——签名密钥、cookie 时长都不是本模块的知识。
    """
    row = AccountSession(account_id=account_id)
    db.add(row)
    db.flush()
    return _session_dto(row)


def get_session(db: Session, session_id: int) -> SessionDTO:
    """校验一个从已验证签名的 cookie 里解出来的 session_id 还活着，顺手刷新
    `last_seen_at`。

    Raises:
        SessionNotFound: 没这一行（比如数据被清过）。
        SessionRevoked: 这一行存在但已经被吊销。
    """
    row = db.get(AccountSession, session_id)
    if row is None:
        raise SessionNotFound(f"会话不存在：{session_id}")
    if row.revoked_at is not None:
        raise SessionRevoked(f"会话已吊销：{session_id}")
    row.last_seen_at = _utcnow()
    db.flush()
    return _session_dto(row)


def revoke_session(db: Session, session_id: int) -> None:
    """吊销会话。幂等——已经吊销过的再调一次不报错。"""
    row = db.get(AccountSession, session_id)
    if row is None:
        raise SessionNotFound(f"会话不存在：{session_id}")
    if row.revoked_at is None:
        row.revoked_at = _utcnow()
        db.flush()


def list_sessions(db: Session, account_id: int) -> list[SessionDTO]:
    rows = db.scalars(
        select(AccountSession)
        .where(AccountSession.account_id == account_id)
        .order_by(AccountSession.created_at.desc())
    )
    return [_session_dto(row) for row in rows]


# ── API token ──────────────────────────────────────────────────────────────


def create_api_token(db: Session, account_id: int, *, name: str | None = None) -> tuple[ApiTokenDTO, str]:
    """发一个新 API token。返回 `(DTO, 明文)`——明文只在这一次能拿到，调用方
    必须当场展示给用户，之后数据库里只剩哈希（见 caps.authn.new_token）。
    """
    token = new_token(prefix=API_TOKEN_PREFIX)
    row = ApiToken(
        account_id=account_id,
        name=name,
        lookup_prefix=token.lookup_prefix,
        token_hash=token.hashed,
    )
    db.add(row)
    db.flush()
    return _token_dto(row), token.plaintext


def verify_api_token(db: Session, plaintext: str) -> ApiTokenDTO:
    """按明文校验 API token，顺手刷新 `last_used_at`。

    Raises:
        TokenNotFound: 前缀都没查到匹配行。
        TokenRevoked: 查到了但已被吊销。
        InvalidCredentials: 前缀命中但哈希比对不通过（明文被篡改或拼错）。
    """
    prefix = lookup_prefix_of(plaintext)
    row = db.scalar(select(ApiToken).where(ApiToken.lookup_prefix == prefix))
    if row is None:
        raise TokenNotFound("token 不存在")
    if row.revoked_at is not None:
        raise TokenRevoked("token 已吊销")
    if not verify_token(plaintext, row.token_hash):
        raise InvalidCredentials("token 不对")
    row.last_used_at = _utcnow()
    db.flush()
    return _token_dto(row)


def revoke_api_token(db: Session, token_id: int, *, account_id: int) -> None:
    """吊销一个 token。必须传 `account_id` 核对归属——防止传错一个 id 就吊销了
    别的账号的 token；不属于该账号时报 `TokenNotFound` 而不是
    `PermissionDenied`，不向调用方泄露"这个 id 其实存在，只是不是你的"。
    """
    row = db.get(ApiToken, token_id)
    if row is None or row.account_id != account_id:
        raise TokenNotFound(f"token 不存在：{token_id}")
    if row.revoked_at is None:
        row.revoked_at = _utcnow()
        db.flush()


def list_api_tokens(db: Session, account_id: int) -> list[ApiTokenDTO]:
    rows = db.scalars(
        select(ApiToken).where(ApiToken.account_id == account_id).order_by(ApiToken.created_at.desc())
    )
    return [_token_dto(row) for row in rows]


# ── 密码重置 ───────────────────────────────────────────────────────────────


def request_password_reset(
    db: Session,
    *,
    email: str,
    ttl_seconds: int = DEFAULT_PASSWORD_RESET_TTL_SECONDS,
) -> tuple[AccountDTO, str] | None:
    """发一个密码重置令牌。

    邮箱不存在时返回 `None` 而不是抛异常——调用方（`features/accounts_auth`）
    应该对"邮箱不存在"和"邮箱存在"展示同一句提示文案，不然等于告诉外部
    "这个邮箱注册过"。返回值里的明文同 API token，只在这一次能拿到，调用方
    负责发邮件，本模块不知道怎么发邮件。
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds 必须为正")

    row = db.scalar(select(Account).where(Account.email == email.strip().lower()))
    if row is None:
        return None

    token = new_token(prefix=PASSWORD_RESET_PREFIX)
    reset = PasswordReset(
        account_id=row.id,
        lookup_prefix=token.lookup_prefix,
        token_hash=token.hashed,
        expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
    )
    db.add(reset)
    db.flush()
    return _account_dto(row), token.plaintext


def consume_password_reset(
    db: Session,
    *,
    plaintext: str,
    new_password: str,
    password_params: PasswordParams = DEFAULT_PASSWORD_PARAMS,
) -> AccountDTO:
    """用明文令牌消费一次密码重置：校验 → 改密码 → 标记令牌已用。

    三种"令牌不对"情形分开报：没查到/哈希不对 → `PasswordResetInvalid`；查到
    但已用过 → `PasswordResetUsed`；查到未用过但过期 → `PasswordResetExpired`。
    和登录故意合并成一个异常（`InvalidCredentials`）正好相反——这里不存在
    "泄露账号是否存在"的问题（令牌本身已经证明了"出示者有权操作某个账号"，
    分开报不会泄露新信息），分开报对用户更友好（"链接过期"和"链接已经用过"
    是用户能理解、会采取不同下一步动作的两种情况）。
    """
    prefix = lookup_prefix_of(plaintext)
    row = db.scalar(select(PasswordReset).where(PasswordReset.lookup_prefix == prefix))
    if row is None or not verify_token(plaintext, row.token_hash):
        raise PasswordResetInvalid("重置链接不对")
    if row.used_at is not None:
        raise PasswordResetUsed("重置链接已经用过")
    if _utcnow() > row.expires_at:
        raise PasswordResetExpired("重置链接已过期")

    account = db.get(Account, row.account_id)
    assert account is not None  # noqa: S101 — FK 保证；真丢了说明数据被破坏，让它炸比吞掉更安全
    account.password_hash = hash_password(new_password, params=password_params)
    row.used_at = _utcnow()
    db.flush()
    return _account_dto(account)
