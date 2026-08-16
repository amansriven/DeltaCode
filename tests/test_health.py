from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api_version": "2026-08-15",
        "capabilities": [
            "migrations",
            "providers",
            "workspace_intelligence",
            "dashboard_chat",
            "pull_requests",
        ],
    }
