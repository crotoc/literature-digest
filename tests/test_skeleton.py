"""骨架验收：装配机制 + 规避清单里几条真实踩过的坑。"""

from infra.errors import AppError


def test_health_page_renders(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert "骨架就位" in response.text


def test_container_class_is_overridable(client):
    """规避清单 #4：base.html 的 container_class 必须真能被子模板覆盖。"""
    response = client.get("/healthz")
    assert 'class="container-wide"' in response.text
    assert 'class="container"' not in response.text


def test_request_id_on_success(client):
    assert client.get("/healthz.json").headers["X-Request-Id"]


def test_request_id_survives_500(client):
    """规避清单 #3：500 响应也必须带 X-Request-Id。

    用 @app.exception_handler(Exception) 的写法会让这条测试失败，
    因为那个 handler 会被提到本项目中间件之上。
    """
    app = client.app

    @app.get("/__boom")
    def boom():
        raise RuntimeError("boom")

    response = client.get("/__boom", headers={"X-Request-Id": "probe-1"})
    assert response.status_code == 500
    assert response.headers["X-Request-Id"] == "probe-1"
    assert response.json()["request_id"] == "probe-1"


def test_app_error_maps_to_status(client):
    app = client.app

    @app.get("/__missing")
    def missing():
        raise AppError("没了", code="not_found")

    response = client.get("/__missing")
    assert response.status_code == 400
    assert response.json()["error"] == "not_found"


def test_registry_discovers_pages(client):
    payload = client.get("/api/frontend/features").json()
    keys = {item["key"] for item in payload["features"]}
    assert "health" in keys
    # 安全子集：不许漏任何 secret 形状的字段
    for item in payload["features"]:
        assert set(item) <= {"key", "label", "path", "icon", "order", "requires"}


def test_single_engine_factory():
    """规则 6 的运行时版本：全项目只有一个 engine。"""
    from infra.db import ENGINE, SessionFactory

    assert SessionFactory.kw["bind"] is ENGINE
