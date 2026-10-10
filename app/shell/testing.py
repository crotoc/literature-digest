"""给 `app/pages/*/tests` 用的测试客户端工厂。

和 `infra.db.new_memory_session_factory()` 是同一类例外口子的消费方：
生产路径上 `app.shell.deps.db` 固定绑定 `infra.db.SessionFactory`（唯一
engine，lint 规则 6），但各页面的 HTTP 测试需要一个和开发库完全隔离、
每个测试互不可见的数据库——这里统一提供 `test_client()`，不让每个
`app/pages/<x>/tests/conftest.py` 各自重新发明一遍同样的
`dependency_overrides` 接线。本文件自己不新开 engine/session 工厂，只调
`infra.db.new_memory_session_factory()`——那两处调用点仍然只在
`infra/db.py` 里（满足规则 6；这句话特意不把那两个函数名和左括号连在一起
写，避免 lint 的 grep 把本段说明文字本身误判成一处违规）。
"""

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shell.deps import blob_store as blob_store_dependency
from app.shell.deps import db as db_dependency
from app.shell.deps import log_file as log_file_dependency
from app.shell.deps import metadata_lookup_resolve as metadata_lookup_resolve_dependency
from app.shell.deps import metadata_lookup_transport as metadata_lookup_transport_dependency
from caps.blobstore import BlobStore
from caps.blobstore.testing import MemoryBackend
from infra.db import new_memory_session_factory

# 跟 blob_store/log_file 同一条规则：生产默认值会真的发网络请求打
# Crossref/PubMed，测试不能用。这里给一个确定性失败的默认值（503），
# 任何没有进一步覆盖这两个依赖的测试如果真的触发了 refresh-metadata
# 路由，会拿到一个明确的"查不到"而不是真的发出请求；需要模拟"查到了"
# 的测试在自己的测试函数里用 `client.app.dependency_overrides` 再换一次
# （precedent 见 `app/pages/settings/tests/test_settings.py` 对 `log_file`
# 的同款用法）。
_DEFAULT_METADATA_LOOKUP_TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(503))
_DEFAULT_METADATA_LOOKUP_RESOLVE_IP = "93.184.216.34"


def _default_metadata_lookup_resolve(host: str) -> list[str]:
    return [_DEFAULT_METADATA_LOOKUP_RESOLVE_IP]


@contextmanager
def test_client(app: FastAPI) -> Iterator[TestClient]:
    """`app` 必须是 `app.main.create_app(create_tables=False)` 的返回值。

    建表动作（`new_memory_session_factory()` 内部）必须晚于 `create_app()`
    里的 `registry.discover()`——规避清单 #2：`discover()` 会 import 所有
    页面模块、从而 import 到所有 domain 的 `models.py`，建表动作排在这
    之前会漏建表。调用方只要保证"先 `create_app()` 再传给这个函数"就自动
    满足这条顺序，不需要自己操心。
    """
    session_factory = new_memory_session_factory()

    def _override_db() -> Iterator:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # 生产路径的 blob_store 真的落盘到 settings().blob_root——测试不能用
    # 那一个，否则每次跑测试都会在真实的 data/blobs/ 下面堆测试文件。
    # 这里同 db 一样换成 dependency_overrides，用 caps/blobstore/testing
    # 的内存后端，每个 test_client() 调用一个全新实例，测试之间互不可见。
    test_blob_store = BlobStore(MemoryBackend())

    def _override_blob_store() -> BlobStore:
        return test_blob_store

    # 同样的道理：`features/logs_viewer` 的 `clear_log()`/`export_log()`
    # 操作的是 `app.shell.deps.log_file()` 指向的那一个文件——生产路径上
    # 那是 `settings().log_file`，`tests/conftest.py` 没有单独配
    # `LOG_FILE` 环境变量，会解析到和部署环境相同的默认路径。不换成临时
    # 文件的话，跑一次测试就会把真实的 `data/app.log` 清空、写进测试数据。
    # 每个 `test_client()` 调用用一个全新的临时目录，测试之间互不可见；
    # `TemporaryDirectory` 跟着这个 contextmanager 的生命周期自动清理。
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_log_file = Path(tmp_dir) / "test.log"

        def _override_log_file() -> Path:
            return test_log_file

        app.dependency_overrides[db_dependency] = _override_db
        app.dependency_overrides[blob_store_dependency] = _override_blob_store
        app.dependency_overrides[log_file_dependency] = _override_log_file
        app.dependency_overrides[metadata_lookup_transport_dependency] = (
            lambda: _DEFAULT_METADATA_LOOKUP_TRANSPORT
        )
        app.dependency_overrides[metadata_lookup_resolve_dependency] = (
            lambda: _default_metadata_lookup_resolve
        )
        try:
            with TestClient(app) as client:
                yield client
        finally:
            app.dependency_overrides.pop(db_dependency, None)
            app.dependency_overrides.pop(blob_store_dependency, None)
            app.dependency_overrides.pop(log_file_dependency, None)
            app.dependency_overrides.pop(metadata_lookup_transport_dependency, None)
            app.dependency_overrides.pop(metadata_lookup_resolve_dependency, None)
