"""app factory。只做装配：发现页面、挂路由、注入 nav。

import 顺序有讲究（规避清单 #2）：registry.discover() 会 import 所有页面模块，
从而 import 到所有 domain 的 models；init_db() 必须排在它之后。
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.shell import registry
from app.shell.middleware import RequestContextMiddleware
from app.shell.templating import templates
from infra import logging as app_logging
from infra.config import REPO_ROOT, settings


def create_app(*, create_tables: bool | None = None) -> FastAPI:
    config = settings()
    app_logging.configure(config.log_level)

    application = FastAPI(title="literature-digest", docs_url=None, redoc_url=None)
    application.add_middleware(RequestContextMiddleware)

    pages = registry.discover()  # ← 这一步把所有 models import 进来
    for page in pages:
        application.include_router(page.router)

    nav = registry.nav_items(pages)
    templates.env.globals["nav_items"] = nav
    templates.env.globals["active_nav"] = None

    application.mount("/static", StaticFiles(directory=REPO_ROOT / "app" / "static"), name="static")

    @application.get("/api/frontend/features")
    def frontend_features():
        """给浏览器扩展读的安全子集（无 secret）。"""
        return {"features": registry.extension_manifest(pages)}

    should_create = config.app_env == "dev" if create_tables is None else create_tables
    if should_create:
        from infra.db import init_db  # noqa: PLC0415 — 必须晚于 discover()

        init_db()

    return application


app = create_app()
