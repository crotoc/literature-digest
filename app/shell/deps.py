"""FastAPI 依赖。

service 层接收 Session、自己不造 session（四件套约定）；
造 session 的唯一职责在这里和 infra.db.session_scope。
"""

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from caps.authn import SESSION_PURPOSE, TokenExpired, TokenInvalid, unsign
from caps.blobstore import BlobStore
from caps.httpfetch import default_resolve
from domain.accounts import AccountDTO, AccountNotFound, SessionNotFound, SessionRevoked
from features.accounts_auth import resume_session
from infra.config import settings
from infra.db import SessionFactory

SESSION_COOKIE_NAME = "ld2_session"
# 30 天——登录 cookie 的 Max-Age 和 resume_session() 校验签名时的
# max_age_seconds 必须用同一个值，两处都从这个常量读，不各自写一遍。
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

# BlobStore(backend) 的装配点——caps/blobstore 自己不认识 adapters/storage
# （caps 禁止向上依赖），所以具体后端只能在这里注入，见
# adapters/storage/local_fs.py 模块 docstring 里写的那句话。
#
# `LocalFsBackend` 的 import 故意留在函数体里、不提到模块顶层：
# app/shell/deps.py 是整个 app 层的公共管线，每一个页面——包括完全不碰
# 附件的 health/folders——都经这个模块的 db()/current_account()。如果在
# 模块顶层 import 具体的存储后端，`scripts/lint.sh` 规则 7（"删掉任一
# adapter，其余 pytest 仍须全绿"）删掉 local_fs.py 时，deps.py 本身就会
# import 失败，拖垒*所有*页面——这不是规则 7 想测的"某个 adapter 的直接
# 依赖方崩了"，而是把一个本该可替换的存储后端做成了和 domain/accounts
# 同级的地基依赖，正好违反了 adapters/storage 自己 README 里"接口留着
# 将来接对象存储"的承诺。延迟到第一次真正调用才 import + 建单例，这个
# 单例只建一次（`LocalFsBackend.__init__` 会建目录，没必要每次请求都重新
# mkdir）。测试走 app.shell.testing 里的 dependency_overrides，根本不会
# 调到这个函数体，所以测试环境也不会依赖这次 import。
_blob_store: BlobStore | None = None


def blob_store() -> BlobStore:
    global _blob_store
    if _blob_store is None:
        from adapters.storage.local_fs import LocalFsBackend

        _blob_store = BlobStore(LocalFsBackend(root=settings().blob_root))
    return _blob_store


def metadata_lookup_transport():
    """`features.metadata_lookup.lookup_record`/`refresh_work_metadata` 的
    `transport` 参数——生产路径返回 `None`（httpx 用默认传输，真的发网络
    请求打 Crossref/PubMed）。和 `blob_store()`/`log_file()` 同一条安全
    规则，只是这次的风险方向不同：不是"测试默认值会撞到真实部署资源"，
    而是"不走依赖注入的话，测试会真的发出网络请求"（慢、不确定、可能被
    外部 API 限流）。测试必须用 `client.app.dependency_overrides` 换成
    `httpx.MockTransport`，`app/shell/testing.py` 的 `test_client()` 已经
    给了一个不发真实请求的安全默认值，单条测试需要特定响应内容时再在
    自己的测试函数里进一步覆盖（precedent 见
    `app/pages/settings/tests/test_settings.py` 对 `log_file` 的同款用法）。
    """
    return None


def metadata_lookup_resolve():
    """配套 `metadata_lookup_transport()` 的 `resolve` 参数——生产路径用
    `caps.httpfetch.default_resolve`（真实 DNS + SSRF 防护）。`caps/` 不在
    `scripts/lint.sh` 规则 7 的可删目标里（目标只有 `adapters/*/*.py` 和
    `features/*/`），所以这个 import 不需要像 `blob_store()` 里的
    `LocalFsBackend` 那样延迟到函数体内，放模块顶层就行。
    """
    return default_resolve


def log_file() -> Path:
    """`features/logs_viewer` 操作的文件路径——走依赖注入而不是在页面层
    直接调 `settings().log_file`，理由和 `blob_store()` 一样：测试要能
    用 `app.dependency_overrides` 换成一个临时文件，否则 `clear_log()`
    在跑测试时会把部署环境真实的 `data/app.log` 清空（`tests/conftest.py`
    和生产 `.env` 都没有单独配 `LOG_FILE`，两者解析到的是同一个默认路径）。
    """
    return settings().log_file


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
