PASSWORD = "correct-horse-battery-staple"


def test_home_redirects_anonymous_to_login(client):
    response = client.get("/")

    assert response.status_code == 200  # 跟随了 303 -> /login
    assert str(response.url).endswith("/login")


def test_home_shows_username_when_authenticated(client):
    client.post(
        "/register",
        data={
            "username": "heidi",
            "email": "heidi@example.org",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "欢迎，heidi" in response.text


def test_home_is_in_nav_items(client):
    response = client.get("/healthz")  # 任意一个真实页面即可，复用它看 nav_items

    assert '<code>home</code>' in response.text
