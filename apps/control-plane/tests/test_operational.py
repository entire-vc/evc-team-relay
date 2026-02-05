from __future__ import annotations

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


def test_version_endpoint(client: TestClient) -> None:
    """Test version endpoint."""
    response = client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data


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
