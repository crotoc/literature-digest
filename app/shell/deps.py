"""FastAPI 依赖。

service 层接收 Session、自己不造 session（四件套约定）；
造 session 的唯一职责在这里和 infra.db.session_scope。
"""

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from caps.authn import SESSION_PURPOSE, TokenExpired, TokenInvalid, unsign
from domain.accounts import AccountDTO, AccountNotFound, SessionNotFound, SessionRevoked
from features.accounts_auth import resume_session
from infra.config import settings
from infra.db import SessionFactory

SESSION_COOKIE_NAME = "ld2_session"
# 30 天——登录 cookie 的 Max-Age 和 resume_session() 校验签名时的
# max_age_seconds 必须用同一个值，两处都从这个常量读，不各自写一遍。
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def db() -> Iterator[Session]:
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_account(request: Request, session: Session = Depends(db)) -> AccountDTO | None:
    """从请求的会话 cookie 恢复当前账号。

    没带 cookie、签名过期/不符、或会话行已经不存在/被吊销，统一返回
    None，不抛异常——"要不要登录"是各页面自己的事（重定向到 /login 还是
    允许匿名访问），这一层只负责交出"现在有没有一个有效会话"这个事实。
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        return resume_session(
            session,
            cookie_token=token,
            session_secret=settings().app_secret_key,
            max_age_seconds=SESSION_MAX_AGE_SECONDS,
        )
    except (TokenExpired, TokenInvalid, SessionNotFound, SessionRevoked, AccountNotFound):
        return None


def current_session_id(request: Request) -> int | None:
    """解出当前会话 cookie 对应的会话行 id，只校验签名、不查库。

    专给 /logout 用——注销要吊销的是"这个 cookie 现在指向的会话"，不关心
    它是否已经失效（失效了就什么都不用做，和 logout() 本身的幂等性
    一致）。大多数页面应该用 current_account，不要用这个。
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        session_id_str = unsign(
            token,
            secret=settings().app_secret_key,
            max_age_seconds=SESSION_MAX_AGE_SECONDS,
            purpose=SESSION_PURPOSE,
        )
        return int(session_id_str)
    except (TokenExpired, TokenInvalid, ValueError):
        return None
