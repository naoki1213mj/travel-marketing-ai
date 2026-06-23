"""main モジュールのミドルウェアテスト"""

from pathlib import Path

from fastapi.testclient import TestClient

import src.main as main_module
from src.main import app

client = TestClient(app)


def test_health_includes_request_id_header(monkeypatch):
    """すべてのレスポンスに X-Request-Id を付与する"""
    monkeypatch.setattr(main_module, "_API_KEY", "")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Request-Id"]


def test_api_key_middleware_blocks_protected_api(monkeypatch):
    """API_KEY 設定時は保護対象 API を x-api-key なしで拒否する"""
    monkeypatch.setattr(main_module, "_API_KEY", "test-secret")

    response = client.get("/api/voice-config")

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized — invalid or missing API key"}


def test_api_key_middleware_allows_authorized_request(monkeypatch):
    """正しい x-api-key があれば保護対象 API にアクセスできる"""
    monkeypatch.setattr(main_module, "_API_KEY", "test-secret")

    response = client.get("/api/voice-config", headers={"x-api-key": "test-secret"})

    assert response.status_code == 200
    assert "client_id" in response.json()
    assert "tenant_id" in response.json()


def test_api_key_middleware_keeps_health_exempt(monkeypatch):
    """health/readiness は API_KEY 設定時でも認証不要のままにする"""
    monkeypatch.setattr(main_module, "_API_KEY", "test-secret")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_redirect_bridge_serves_no_store_html(monkeypatch, tmp_path: Path):
    """auth redirect bridge は no-store で静的 HTML を返す"""
    redirect_file = tmp_path / "auth-redirect.html"
    redirect_file.write_text("<html>redirect</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIR", str(tmp_path))

    response = client.get("/auth-redirect.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "redirect" in response.text


def test_root_serves_index_when_public_app_base_url_unset(monkeypatch, tmp_path: Path):
    """PUBLIC_APP_BASE_URL 未設定なら従来通り index.html を返す (rollback / cutover 前の動作)"""
    index_file = tmp_path / "index.html"
    index_file.write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(main_module, "_PUBLIC_APP_BASE_URL", "")

    response = client.get("/", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "spa" in response.text


def test_root_redirects_to_apim_when_cutover_active(monkeypatch, tmp_path: Path):
    """PUBLIC_APP_BASE_URL 設定済 + ブラウザ流入 + APIM 経由でない → 302 redirect"""
    index_file = tmp_path / "index.html"
    index_file.write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(main_module, "_PUBLIC_APP_BASE_URL", "https://apim.example.net/app")
    monkeypatch.setattr(main_module, "_TRUSTED_AUTH_HEADER_NAME", "X-Apim-Trusted")

    response = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://apim.example.net/app/"


def test_root_does_not_redirect_when_request_came_via_apim(monkeypatch, tmp_path: Path):
    """APIM が trusted header を inject した場合は redirect せず SPA を返す (loop 回避)"""
    index_file = tmp_path / "index.html"
    index_file.write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(main_module, "_PUBLIC_APP_BASE_URL", "https://apim.example.net/app")
    monkeypatch.setattr(main_module, "_TRUSTED_AUTH_HEADER_NAME", "X-Apim-Trusted")

    response = client.get(
        "/",
        headers={"accept": "text/html", "X-Apim-Trusted": "any-non-empty-value"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "spa" in response.text


def test_root_does_not_redirect_non_browser_clients(monkeypatch, tmp_path: Path):
    """Accept ヘッダが text/html を含まない API クライアントは redirect しない"""
    index_file = tmp_path / "index.html"
    index_file.write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(main_module, "_PUBLIC_APP_BASE_URL", "https://apim.example.net/app")

    response = client.get("/", headers={"accept": "application/json"}, follow_redirects=False)

    assert response.status_code == 200
    assert "spa" in response.text
