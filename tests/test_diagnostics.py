"""`/api/ready/deep` deep readiness probe の単体テスト。

実 Azure dependency を mock して probe の集約ロジック・503 判定を検証する。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@patch("src.diagnostics._probe_fabric_data_agent", new_callable=AsyncMock)
@patch("src.diagnostics._probe_foundry_iq_search", new_callable=AsyncMock)
@patch("src.diagnostics._probe_foundry_project", new_callable=AsyncMock)
@patch("src.diagnostics._probe_cosmos", new_callable=AsyncMock)
def test_ready_deep_returns_200_when_all_probes_ok(
    mock_cosmos, mock_foundry, mock_search, mock_fabric, client: TestClient
) -> None:
    """全 probe が ok なら 200 + status=ok"""
    mock_cosmos.return_value = {"name": "cosmos", "ok": True, "latency_ms": 50.0}
    mock_foundry.return_value = {"name": "foundry_project", "ok": True, "latency_ms": 50.0}
    mock_search.return_value = {"name": "foundry_iq_search", "ok": True, "latency_ms": 50.0}
    mock_fabric.return_value = {"name": "fabric_data_agent", "ok": True, "latency_ms": 50.0}

    response = client.get("/api/ready/deep")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["failure_count"] == 0
    assert len(body["probes"]) == 4


@patch("src.diagnostics._probe_fabric_data_agent", new_callable=AsyncMock)
@patch("src.diagnostics._probe_foundry_iq_search", new_callable=AsyncMock)
@patch("src.diagnostics._probe_foundry_project", new_callable=AsyncMock)
@patch("src.diagnostics._probe_cosmos", new_callable=AsyncMock)
def test_ready_deep_returns_503_when_any_probe_fails(
    mock_cosmos, mock_foundry, mock_search, mock_fabric, client: TestClient
) -> None:
    """1 つでも probe が fail (skipped でない) なら 503 + status=degraded"""
    mock_cosmos.return_value = {"name": "cosmos", "ok": True, "latency_ms": 50.0}
    mock_foundry.return_value = {"name": "foundry_project", "ok": True, "latency_ms": 50.0}
    mock_search.return_value = {"name": "foundry_iq_search", "ok": True, "latency_ms": 50.0}
    mock_fabric.return_value = {"name": "fabric_data_agent", "ok": False, "reason": "http_401"}

    response = client.get("/api/ready/deep")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["failure_count"] == 1
    failed_probe = next(p for p in body["probes"] if p["name"] == "fabric_data_agent")
    assert failed_probe["ok"] is False
    assert failed_probe["reason"] == "http_401"


@patch("src.diagnostics._probe_fabric_data_agent", new_callable=AsyncMock)
@patch("src.diagnostics._probe_foundry_iq_search", new_callable=AsyncMock)
@patch("src.diagnostics._probe_foundry_project", new_callable=AsyncMock)
@patch("src.diagnostics._probe_cosmos", new_callable=AsyncMock)
def test_ready_deep_skipped_probes_do_not_count_as_failures(
    mock_cosmos, mock_foundry, mock_search, mock_fabric, client: TestClient
) -> None:
    """`skipped=True` の probe は failure に数えない (env 未設定で disabled な機能)"""
    mock_cosmos.return_value = {"name": "cosmos", "ok": True, "latency_ms": 50.0}
    mock_foundry.return_value = {"name": "foundry_project", "ok": True, "latency_ms": 50.0}
    mock_search.return_value = {"name": "foundry_iq_search", "ok": True, "skipped": True, "reason": "not_configured"}
    mock_fabric.return_value = {"name": "fabric_data_agent", "ok": True, "skipped": True, "reason": "not_configured"}

    response = client.get("/api/ready/deep")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["failure_count"] == 0


def test_health_endpoint_unchanged_remains_cheap(client: TestClient) -> None:
    """`/api/health` は依然として固定 200 を返し、外部 probe を実行しない (rubber-duck #2)"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_endpoint_remains_shallow(client: TestClient) -> None:
    """`/api/ready` は env var presence のみ。外部 probe しない。"""
    response = client.get("/api/ready")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "status" in body
    assert "missing" in body

