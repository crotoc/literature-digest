PASSWORD = "correct-horse-battery-staple"


def _register(client, *, username="alice", email="alice@example.org", password=PASSWORD):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": password, "confirm_password": password},
    )


# ── GET /login ────────────────────────────────────────────────────────────


def test_login_form_renders_for_anonymous(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "登录" in response.text


def test_login_form_redirects_when_already_authenticated(client):
    _register(client)

    response = client.get("/login")

    assert response.status_code == 200  # TestClient 默认跟随重定向
    assert str(response.url).endswith("/")


# ── GET /register ────────────────────────────────────────────────────────


def test_register_form_renders_when_signup_allowed(client):
    response = client.get("/register")
    assert response.status_code == 200
    assert "注册" in response.text
    assert 'name="confirm_password"' in response.text


def test_register_form_shows_disabled_message_when_signup_not_allowed(client, monkeypatch):
    class _FakeSettings:
        allow_self_signup = False

    monkeypatch.setattr("app.pages.auth.page.settings", lambda: _FakeSettings())

    response = client.get("/register")

    assert response.status_code == 200
    assert "未开放自助注册" in response.text
    assert 'name="username"' not in response.text


# ── POST /register ───────────────────────────────────────────────────────


def test_register_then_auto_login_and_redirect_home(client):
    response = _register(client)

    assert response.status_code == 200  # 跟随了 303 -> "/"
    assert str(response.url).endswith("/")
    assert "欢迎，alice" in response.text
    assert client.cookies.get("ld2_session") is not None


def test_register_rejects_mismatched_passwords(client):
    response = client.post(
        "/register",
        data={
            "username": "bob",
            "email": "bob@example.org",
            "password": PASSWORD,
            "confirm_password": "something-else-entirely",
        },
    )

    assert response.status_code == 422
    assert "两次输入的密码不一致" in response.text
    assert 'value="bob"' in response.text  # 已填的用户名/邮箱原样保留


def test_register_rejects_duplicate_username(client):
    _register(client, username="carol", email="carol@example.org")
    client.post("/logout")

    response = client.post(
        "/register",
        data={
            "username": "carol",
            "email": "someone-else@example.org",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )

    assert response.status_code == 409
    assert "carol" in response.text


def test_register_rejects_short_password(client):
    response = client.post(
        "/register",
        data={
            "username": "dave",
            "email": "dave@example.org",
            "password": "short",
            "confirm_password": "short",
        },
    )

    assert response.status_code == 422
    assert "密码至少" in response.text


# ── POST /login ───────────────────────────────────────────────────────────


def test_login_with_correct_credentials_sets_cookie_and_redirects(client):
    _register(client, username="erin", email="erin@example.org")
    client.post("/logout")

    response = client.post("/login", data={"username_or_email": "erin", "password": PASSWORD})

    assert response.status_code == 200
    assert str(response.url).endswith("/")
    assert "欢迎，erin" in response.text


def test_login_with_wrong_password_shows_generic_error(client):
    _register(client, username="frank", email="frank@example.org")
    client.post("/logout")

    response = client.post("/login", data={"username_or_email": "frank", "password": "totally-wrong"})

    assert response.status_code == 401
    assert "用户名/邮箱或密码不对" in response.text
    assert 'value="frank"' in response.text


# ── POST /logout ──────────────────────────────────────────────────────────


def test_logout_clears_cookie_and_redirects_to_login(client):
    _register(client, username="grace", email="grace@example.org")

    response = client.post("/logout")

    assert response.status_code == 200
    assert str(response.url).endswith("/login")
    assert client.cookies.get("ld2_session") is None

    # 登出后再访问首页应该被弹回登录页，确认 cookie 真的失效了。
    home_response = client.get("/")
    assert str(home_response.url).endswith("/login")
