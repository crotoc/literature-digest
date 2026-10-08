"""Jinja 环境。

模板约定：每个页面把模板放在 `app/pages/<x>/templates/<x>/` 下，
引用时写 `<x>/index.html`。多一层同名目录是为了防止跨页重名
（两个页面都有 index.html 时不会互相覆盖）。

搜索路径 = shell/templates + 每个页面的 templates/，
所以加页面只要新建目录，不用改这里。
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

SHELL_DIR = Path(__file__).resolve().parent / "templates"
PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"


def _search_paths() -> list[str]:
    paths = [str(SHELL_DIR)]
    paths += [str(p) for p in sorted(PAGES_DIR.glob("*/templates")) if p.is_dir()]
    return paths


templates = Jinja2Templates(directory=_search_paths())
