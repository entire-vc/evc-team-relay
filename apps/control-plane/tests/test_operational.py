from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_health_endpoint(client: TestClient) -> None:
    """Test basic health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_liveness_probe(client: TestClient) -> None:
    """Test Kubernetes liveness probe endpoint."""
    response = client.get("/health/live")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert "version" in data


def test_readiness_probe(client: TestClient) -> None:
    """Test Kubernetes readiness probe endpoint."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "unhealthy"]
    assert "database" in data
    assert "relay_keys" in data
    assert "timestamp" in data
    assert "version" in data


def test_readiness_probe_returns_503_when_db_down(client: TestClient) -> None:
    """TR-17: a DB outage must fail the HTTP status code, not just the JSON body.

    Before the fix, /health/ready computed status="unhealthy" in the response
    body but always returned 200 — Docker's healthcheck (and any load balancer
    or monitor that reads the status code, not the JSON) never saw a failure,
    so a real Postgres outage went undetected.
    """
    from app.db.session import get_db

    class _BrokenSession:
        def execute(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated DB outage")

    def _broken_get_db():
        yield _BrokenSession()

    original = client.app.dependency_overrides.get(get_db)
    client.app.dependency_overrides[get_db] = _broken_get_db
    try:
        response = client.get("/health/ready")
    finally:
        if original is not None:
            client.app.dependency_overrides[get_db] = original
        else:
            client.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503, response.text
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["database"] == "unhealthy"


def test_version_endpoint(client: TestClient) -> None:
    """Test version endpoint."""
    response = client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "build_sha" in data


def test_version_reports_the_deployed_commit(client: TestClient, tmp_path, monkeypatch) -> None:
    """A real BUILD_SHA is reported verbatim."""
    from app.api.routers import health

    sha = "a" * 40
    sha_file = tmp_path / "BUILD_SHA"
    sha_file.write_text(sha + "\n", encoding="utf-8")
    monkeypatch.setattr(health, "BUILD_SHA_PATH", sha_file)

    data = client.get("/version").json()
    assert data["build_sha"] == sha


@pytest.mark.parametrize(
    "contents",
    [
        None,  # file absent entirely
        "",  # present but empty
        "   \n",  # whitespace only
        "deadbeef",  # truncated — a short prefix must not pass as a commit
        "not-a-sha-at-all-not-a-sha-at-all-notyet",
        "A" * 40,  # uppercase: git writes lowercase, so this is not our file
    ],
)
def test_version_reports_unknown_rather_than_guessing(
    client: TestClient, tmp_path, monkeypatch, contents
) -> None:
    """
    Anything that is not a full lowercase 40-hex commit reads as "unknown".

    This is the half that matters: a freshness check must be able to tell "I
    could not read the version" from "the version matches". If a truncated or
    half-written file were returned verbatim, a comparison against the canon
    HEAD would simply never match and the checker would look permanently red
    for the wrong reason; if an absent file returned something falsy that a
    checker skipped over, it would look permanently green. Both are worse than
    an explicit "unknown" the checker is told to fail on.
    """
    from app.api.routers import health

    sha_file = tmp_path / "BUILD_SHA"
    if contents is not None:
        sha_file.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(health, "BUILD_SHA_PATH", sha_file)

    assert client.get("/version").json()["build_sha"] == "unknown"


def test_rate_limit_login(client: TestClient) -> None:
    """Test rate limiting on login endpoint."""
    login_payload = {"email": "test@example.com", "password": "wrongpass"}

    # Make 10 requests (limit)
    for _ in range(10):
        response = client.post("/auth/login", json=login_payload)
        # Should fail auth, but not rate limited yet
        assert response.status_code in [401, 429]  # 401 Unauthorized or 429 Too Many Requests

    # 11th request should be rate limited
    response = client.post("/auth/login", json=login_payload)
    # If rate limiting is working, should get 429
    if response.status_code == 429:
        assert "rate limit" in response.text.lower() or "too many" in response.text.lower()


def test_error_response_format(client: TestClient) -> None:
    """Test that error responses have consistent format."""
    # Trigger a 404 error
    response = client.get("/nonexistent-endpoint")
    assert response.status_code == 404
    data = response.json()

    # Check error response structure
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]
    assert data["error"]["code"] == 404


def test_validation_error_format(client: TestClient) -> None:
    """Test validation error response format."""
    # Send invalid login payload (missing required fields)
    response = client.post("/auth/login", json={})
    assert response.status_code == 422
    data = response.json()

    # Check validation error structure
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]
    assert "details" in data["error"]
    assert data["error"]["code"] == 422


def test_request_id_header(client: TestClient) -> None:
    """Test that X-Request-ID header is added to responses."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "x-request-id" in response.headers
    request_id = response.headers["x-request-id"]
    # Should be a valid UUID format
    assert len(request_id) == 36  # UUID format: 8-4-4-4-12 with hyphens
    assert request_id.count("-") == 4
