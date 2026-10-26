from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from test_production_asgi import _valid_production_env

from marketingiq.api.app import create_app
from marketingiq.api.production import create_production_app
from marketingiq.domain.models import Base

SECRET = "a-secure-test-secret-that-is-long-enough"


def _client(tmp_path) -> TestClient:
    url = f"sqlite+pysqlite:///{tmp_path / 'web.db'}"
    Base.metadata.create_all(create_engine(url))
    return TestClient(create_app(url, SECRET))


def test_root_serves_the_operator_interface(tmp_path):
    client = _client(tmp_path)

    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "/static/app.js" in page.text

    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "innerHTML" not in script.text.replace("never innerHTML", "")

    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/missing.js").status_code == 404


def test_unsubscribe_without_public_url_is_not_available(tmp_path):
    client = _client(tmp_path)

    assert client.get("/unsubscribe", params={"t": "anything.here"}).status_code == 404
    assert client.post("/unsubscribe", params={"t": "anything.here"}).status_code == 404


def test_production_interface_has_content_security_policy(monkeypatch):
    _valid_production_env(monkeypatch)
    client = TestClient(create_production_app())

    response = client.get("/")
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert client.get("/docs").status_code == 404
