"""lint.sh 的自测：逐条种一个违规，确认对应规则真的报红。

在干净的树上全绿说明不了什么——这些测试保证规则不是空跑。
rule 7 会递归调 pytest，所以这里一律 SKIP_RULE7=1。
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LINT = REPO / "scripts" / "lint.sh"

# (规则号, 违规文件相对路径, 文件内容)
VIOLATIONS = [
    (1, "caps/_probe_bad/__init__.py", "from domain.works import service\n"),
    (1, "caps/_probe_bad/models.py", "# caps 不许有 models.py\n"),
    (2, "adapters/_probe_bad/x.py", "from domain.works import contract\n"),
    (3, "domain/_probe_a/service.py", "from domain._probe_b.models import Thing\n"),
    (4, "features/_probe_a/service.py", "from features._probe_b.contract import Thing\n"),
    (5, "app/pages/_probe_bad/page.py", "import sqlalchemy\n"),
    (6, "features/_probe_bad/service.py", "from sqlalchemy import create_engine\ne = create_engine('x')\n"),
]


def _run_lint():
    return subprocess.run(
        ["bash", str(LINT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "SKIP_RULE7": "1"},
        cwd=REPO,
    )


def test_lint_passes_on_clean_tree():
    result = _run_lint()
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("rule", "relpath", "content"), VIOLATIONS, ids=lambda v: str(v)[:40])
def test_rule_catches_violation(rule, relpath, content):
    target = REPO / relpath
    created_dirs = []
    parent = target.parent
    while not parent.exists():
        created_dirs.append(parent)
        parent = parent.parent
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    try:
        result = _run_lint()
        assert result.returncode != 0, f"rule {rule} 没抓到 {relpath}\n{result.stdout}"
        assert f"rule {rule}:" in result.stdout, f"报红的不是 rule {rule}\n{result.stdout}"
    finally:
        target.unlink(missing_ok=True)
        for directory in [target.parent, *created_dirs]:
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
