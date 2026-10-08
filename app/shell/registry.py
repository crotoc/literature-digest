"""页面注册表：加页面 = 新建 app/pages/<x>/ 目录，不改 shell。

每个 app/pages/<x>/__init__.py 需导出：
    router : APIRouter
    nav    : NavItem | None   —— None 表示该页不进导航（如纯 API 页）

另外导出 extension_manifest()：给浏览器扩展读的安全子集（只有导航元数据，
没有任何 secret）。旧实现的 frontend/manifest.py + /api/frontend/features 就是这个形状。
"""

import importlib
import pkgutil
from dataclasses import asdict, dataclass

from fastapi import APIRouter

import app.pages


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    path: str
    icon: str = "circle"
    order: int = 100
    requires: str | None = None  # 需要的权限；None = 登录即可


@dataclass(frozen=True)
class Page:
    key: str
    router: APIRouter
    nav: NavItem | None


def discover() -> list[Page]:
    """扫 app/pages/ 下每个子包，取出 router + nav。"""
    pages: list[Page] = []
    for info in pkgutil.iter_modules(app.pages.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"app.pages.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            raise RuntimeError(f"app/pages/{info.name} 没有导出 router")
        pages.append(Page(key=info.name, router=router, nav=getattr(module, "nav", None)))
    pages.sort(key=lambda p: (p.nav.order if p.nav else 999, p.key))
    return pages


def nav_items(pages: list[Page]) -> list[NavItem]:
    return [p.nav for p in pages if p.nav is not None]


def extension_manifest(pages: list[Page]) -> list[dict]:
    """给扩展读的安全子集：只有导航元数据。"""
    return [asdict(item) for item in nav_items(pages)]
