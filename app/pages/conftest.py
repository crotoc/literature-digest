"""给 app/pages/*/tests 共用的 `client` fixture。

放在这一层（而不是各页面自己的 tests/conftest.py 里各写一遍）是因为
pytest 的 conftest 发现机制按目录祖先链走——这里是所有 app/pages/<x>/tests/
的共同祖先，写一份大家都能用。`app` 对象本身只建一次（注册表扫描的开销
不需要每个测试都重付），每个测试函数拿到的是全新的内存数据库（见
`app.shell.testing.test_client`），测试之间互不可见。
"""

import pytest

from app.main import create_app
from app.shell.testing import test_client as _test_client

_app = create_app(create_tables=False)


@pytest.fixture()
def client():
    with _test_client(_app) as c:
        yield c
