"""app/pages/auth：登录 / 注册 / 登出。

只调 features/accounts_auth 的 contract，不碰任何 ORM（lint 规则 5）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.shell.deps import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    current_account,
    current_session_id,
    db,
)
from app.shell.templating import templates
from domain.accounts import AccountDTO
from features.accounts_auth import login, logout, register
from infra.config import settings
from infra.errors import AppError

router = APIRouter()
# "/" 由 app/pages/home 负责（未登录时它自己转 /login），本页不进常驻导航。
nav = None


def _redirect_if_authenticated(account: AccountDTO | None) -> RedirectResponse | None:
    if account is not None:
        return RedirectResponse("/", status_code=303)
    return None


def _set_session_cookie(response: RedirectResponse, cookie_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


# ── 登录 ─────────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, account: AccountDTO | None = Depends(current_account)):
    if (redirect := _redirect_if_authenticated(account)) is not None:
        return redirect
    return templates.TemplateResponse(request, "auth/login.html", {"active_nav": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username_or_email: str = Form(...),
    password: str = Form(...),
    session=Depends(db),
):
    try:
        result = login(
            session,
            username_or_email=username_or_email,
            password=password,
            session_secret=settings().app_secret_key,
        )
    except AppError as error:
        # 不区分"用户名不存在"和"密码错了"——两种情况 InvalidCredentials
        # 的报错已经是同一句话，这里原样显示，不额外拆分。
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"active_nav": None, "error": error.message, "username_or_email": username_or_email},
            status_code=error.status_code,
        )

    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, result.cookie_token)
    return response


# ── 注册 ─────────────────────────────────────────────────────────────────


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, account: AccountDTO | None = Depends(current_account)):
    if (redirect := _redirect_if_authenticated(account)) is not None:
        return redirect
    return templates.TemplateResponse(
        request,
        "auth/register.html",
        {"active_nav": None, "allow_self_signup": settings().allow_self_signup},
    )


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    session=Depends(db),
):
    form_state = {
        "active_nav": None,
        "allow_self_signup": settings().allow_self_signup,
        "username": username,
        "email": email,
    }
    if password != confirm_password:
        # 两次密码是否一致是纯页面层的校验——features/accounts_auth 的
        # register() 不知道"确认密码"这个字段，它只认一个 password 参数。
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {**form_state, "error": "两次输入的密码不一致"},
            status_code=422,
        )

    try:
        register(
            session,
            username=username,
            email=email,
            password=password,
            allow_self_signup=settings().allow_self_signup,
        )
    except AppError as error:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {**form_state, "error": error.message},
            status_code=error.status_code,
        )

    # 注册成功后自动登录——和旧单体一致的体验，用户不需要再填一遍表单。
    result = login(
        session, username_or_email=username, password=password, session_secret=settings().app_secret_key
    )
    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, result.cookie_token)
    return response


# ── 登出 ─────────────────────────────────────────────────────────────────


@router.post("/logout")
def logout_submit(session=Depends(db), session_id: int | None = Depends(current_session_id)):
    if session_id is not None:
        logout(session, session_id=session_id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
