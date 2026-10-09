PASSWORD = "correct-horse-battery-staple"

SAMPLE_RIS = """TY  - JOUR
AU  - Smith, John
AU  - Doe, Jane
TI  - A Great Paper
T2  - Nature
PY  - 2019/05/12/
VL  - 567
IS  - 7748
SP  - 100
EP  - 110
PB  - Nature Publishing Group
DO  - 10.1038/s41586-019-1234-5
SN  - 1476-4687
UR  - https://example.com/paper
AB  - This is the abstract.
LA  - en
N1  - A note.
ER  -
"""


def _register(client, *, username="pat", email="pat@example.org"):
    return client.post(
        "/register",
        data={"username": username, "email": email, "password": PASSWORD, "confirm_password": PASSWORD},
    )


def test_import_form_redirects_anonymous_to_login(client):
    response = client.get("/import")
    assert str(response.url).endswith("/login")


def test_import_form_renders_format_options(client):
    _register(client)

    response = client.get("/import")

    assert response.status_code == 200
    assert "ris" in response.text
    assert "bibtex" in response.text
    assert "csljson" in response.text


def test_import_submit_creates_work_and_shows_counts(client):
    _register(client)

    response = client.post("/import", data={"format": "ris", "raw_text": SAMPLE_RIS})

    assert response.status_code == 200
    assert "新建" in response.text
    assert "work_id=1" in response.text
    assert "created" in response.text


def test_import_submit_then_appears_in_library(client):
    _register(client)
    client.post("/import", data={"format": "ris", "raw_text": SAMPLE_RIS})

    response = client.get("/library")

    assert "A Great Paper" in response.text
    assert "共 1 篇" in response.text


def test_import_submit_rejects_malformed_text(client):
    _register(client)

    response = client.post("/import", data={"format": "ris", "raw_text": "this is not RIS at all"})

    assert response.status_code == 422


def test_import_submit_rejects_unsupported_format(client):
    _register(client)

    response = client.post("/import", data={"format": "endnote-xml", "raw_text": SAMPLE_RIS})

    assert response.status_code == 422
