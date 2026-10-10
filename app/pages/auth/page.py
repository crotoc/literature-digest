"""app/pages/auth：登录 / 注册 / 登出 / 忘记密码。

只调 features/accounts_auth 的 contract，不碰任何 ORM（lint 规则 5）。

忘记密码只接了"入口显示不可用"这一半（见 `/forgot-password` 路由自己的
注释和 `SMTP_CONFIGURED` 常量）——`features.accounts_auth` 的
`request_password_reset`/`consume_password_reset` 两个函数本身已经完整
可用，缺的是 v1 根本没有邮件发送能力（`adapters/delivery/mailer` 是 E4
阶段才做），所以页面层这次只做"让用户知道这个功能存在、但现在打不开"，
不建一条在 v1 永远走不通的提交表单/POST 处理器。
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

SMTP_CONFIGURED = False
"""v1 没有 adapters/delivery/mailer（计划里是 E4 阶段才做），infra/config.py
现在也没有任何 SMTP 字段——所以这里先硬编码一个常量，而不是去读一个还不
存在的配置项。等 E4 真的接上 mailer、infra/config.py 有了 smtp_* 字段，
把这里改成从 settings() 读出来即可，/forgot-password 这个路由本身不需要
跟着改签名（和 features.accounts_auth 的 request_password_reset 把
smtp_configured 设计成参数是同一个理由）。"""


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


# ── 忘记密码 ─────────────────────────────────────────────────────────────
#
# v1 范围的刻意裁剪：这里只做计划原话要求的那一半——"入口显示不可用,不要
# 让用户点进去才看到异常"（features/accounts_auth/README.md 的说法）。
# SMTP_CONFIGURED 在 v1 恒为 False，request_password_reset() 一定会在
# 生成令牌之前就抛 PasswordResetUnavailable，所以不建一个"提交邮箱"的
# POST 表单——那会是一条永远只能走到同一句"不可用"提示的死代码路径，不如
# GET 页面直接把这句话显示出来。等 E4 真的接上 mailer，再把表单和 POST
# 处理器一起加上（到时候 SMTP_CONFIGURED 也会变成从 settings() 读出来）。


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_form(request: Request, account: AccountDTO | None = Depends(current_account)):
    if (redirect := _redirect_if_authenticated(account)) is not None:
        return redirect
    return templates.TemplateResponse(
        request,
        "auth/forgot_password.html",
        {"active_nav": None, "smtp_configured": SMTP_CONFIGURED},
    )


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
