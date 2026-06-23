"""capabilities エンドポイントと判定ロジックのテスト。"""

import json

from fastapi.testclient import TestClient

from src import config as config_module
from src.capabilities import build_capability_snapshot, parse_bool_setting
from src.main import app

client = TestClient(app)


def _disable_azd_env(monkeypatch) -> None:
    """テスト中は実マシンの azd env を参照しない。"""
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})


def test_parse_bool_setting_accepts_common_true_values():
    """文字列の真偽値を安全に解釈する。"""
    assert parse_bool_setting("true") is True
    assert parse_bool_setting("1") is True
    assert parse_bool_setting("enabled") is True
    assert parse_bool_setting("false") is False
    assert parse_bool_setting("") is False


def test_capabilities_default_to_unavailable(monkeypatch):
    """未設定時は新規機能を利用可能として公開しない。"""
    _disable_azd_env(monkeypatch)
    for key in [
        "AZURE_AI_PROJECT_ENDPOINT",
        "ENTRA_CLIENT_ID",
        "ENABLE_MODEL_ROUTER",
        "MODEL_ROUTER_DEPLOYMENT_NAME",
        "ENABLE_GPT_55",
        "GPT_55_DEPLOYMENT_NAME",
        "ENABLE_FOUNDRY_TRACING",
        "ENABLE_CONTINUOUS_MONITORING",
        "CONTINUOUS_MONITORING_SAMPLE_RATE",
        "ENABLE_EVALUATION_LOGGING",
        "ENABLE_COST_METRICS",
        "MCP_REGISTRY_ENDPOINT",
        "ENABLE_SOURCE_INGESTION",
        "SOURCE_INGESTION_ENDPOINT",
        "ENABLE_VOICE_TALK_TO_START",
        "SPEECH_SERVICE_ENDPOINT",
        "SPEECH_SERVICE_REGION",
        "ENABLE_MAI_TRANSCRIBE_1",
        "MAI_TRANSCRIBE_1_ENDPOINT",
        "MAI_TRANSCRIBE_1_DEPLOYMENT_NAME",
        "MAI_TRANSCRIBE_1_API_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)

    snapshot = build_capability_snapshot()

    assert snapshot["features"]["model_router"]["available"] is False
    assert snapshot["features"]["gpt_55"]["available"] is False
    assert snapshot["features"]["foundry_tracing"]["available"] is False
    assert snapshot["features"]["evaluation_logging"]["available"] is False
    assert snapshot["features"]["source_ingestion"]["available"] is False
    assert snapshot["features"]["work_iq"]["available"] is False
    assert snapshot["features"]["voice_live"]["available"] is False
    assert snapshot["features"]["voice_talk_to_start"]["available"] is False


def test_capabilities_reflect_safe_configuration(monkeypatch):
    """設定済み機能を bool のみで公開する。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client-id")
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=placeholder")
    monkeypatch.setenv("ENABLE_MODEL_ROUTER", "true")
    monkeypatch.setenv("MODEL_ROUTER_DEPLOYMENT_NAME", "model-router")
    monkeypatch.setenv("ENABLE_GPT_55", "true")
    monkeypatch.setenv("ENABLE_FOUNDRY_TRACING", "true")
    monkeypatch.setenv("ENABLE_CONTINUOUS_MONITORING", "true")
    monkeypatch.setenv("CONTINUOUS_MONITORING_SAMPLE_RATE", "1")
    monkeypatch.setenv("ENABLE_EVALUATION_LOGGING", "true")
    monkeypatch.setenv("ENABLE_VOICE_TALK_TO_START", "true")
    monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://example.cognitiveservices.azure.com/")
    monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
    monkeypatch.setenv("ENABLE_MAI_TRANSCRIBE_1", "true")
    monkeypatch.setenv("MAI_TRANSCRIBE_1_ENDPOINT", "https://transcribe.example")
    monkeypatch.setenv("MAI_TRANSCRIBE_1_DEPLOYMENT_NAME", "mai-transcribe-1")
    monkeypatch.setenv("MAI_TRANSCRIBE_1_API_PATH", "/mai/v1/audio/transcriptions")
    monkeypatch.setenv("MCP_REGISTRY_ENDPOINT", "https://registry.example/mcp")
    monkeypatch.setenv("ENABLE_SOURCE_INGESTION", "true")
    monkeypatch.setenv("SOURCE_INGESTION_ENDPOINT", "https://source.example/ingest")

    snapshot = build_capability_snapshot()

    assert snapshot["features"]["model_router"]["available"] is True
    assert snapshot["features"]["gpt_55"]["available"] is True
    assert snapshot["features"]["foundry_tracing"]["available"] is True
    assert snapshot["features"]["evaluation_logging"]["available"] is True
    assert snapshot["features"]["continuous_monitoring"]["available"] is True
    assert snapshot["features"]["voice_live"]["available"] is True
    assert snapshot["features"]["voice_talk_to_start"]["available"] is True
    assert snapshot["features"]["mai_transcribe_1"]["available"] is True
    assert snapshot["features"]["mcp_registry"]["available"] is True
    assert snapshot["features"]["source_ingestion"]["available"] is True


def test_mai_transcribe_requires_feature_endpoint_deployment_and_api_path(monkeypatch):
    """MAI Transcribe は flag / endpoint / deployment / API path が揃うまで available にしない。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("ENABLE_MAI_TRANSCRIBE_1", "true")
    monkeypatch.setenv("MAI_TRANSCRIBE_1_DEPLOYMENT_NAME", "mai-transcribe-1")

    snapshot = build_capability_snapshot()

    assert snapshot["features"]["mai_transcribe_1"] == {"available": False, "configured": True}


def test_continuous_monitoring_requires_evaluation_logging_opt_in(monkeypatch):
    """継続監視は評価ログ opt-in がない限り available にしない。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("ENABLE_CONTINUOUS_MONITORING", "true")
    monkeypatch.delenv("ENABLE_EVALUATION_LOGGING", raising=False)

    snapshot = build_capability_snapshot()

    assert snapshot["features"]["continuous_monitoring"] == {"available": False, "configured": True}
    assert snapshot["features"]["evaluation_logging"] == {"available": False, "configured": False}


def test_continuous_monitoring_sample_rate_zero_is_unavailable(monkeypatch):
    """sampling off の場合は configured でも available にはしない。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("ENABLE_EVALUATION_LOGGING", "true")
    monkeypatch.setenv("ENABLE_CONTINUOUS_MONITORING", "true")
    monkeypatch.setenv("CONTINUOUS_MONITORING_SAMPLE_RATE", "0")

    snapshot = build_capability_snapshot()

    assert snapshot["features"]["continuous_monitoring"] == {"available": False, "configured": True}


def test_capabilities_keep_optional_models_unavailable_without_project_endpoint(monkeypatch):
    """Model Router / GPT-5.5 は設定済みでも Project endpoint がないと利用可能にしない。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setenv("ENABLE_MODEL_ROUTER", "true")
    monkeypatch.setenv("ENABLE_GPT_55", "true")

    snapshot = build_capability_snapshot()

    assert snapshot["features"]["model_router"] == {"available": False, "configured": True}
    assert snapshot["features"]["gpt_55"] == {"available": False, "configured": True}


def test_capabilities_endpoint_does_not_expose_sensitive_values(monkeypatch):
    """エンドポイントは raw endpoint / connection string を返さない。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("ENTRA_CLIENT_ID", "client-id")
    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=secret;Endpoint=example")
    monkeypatch.setenv("ENABLE_COST_METRICS", "true")
    monkeypatch.setenv("MODEL_ROUTER_ENDPOINT", "https://router.example/models")
    monkeypatch.setenv("ENABLE_MODEL_ROUTER", "true")

    response = client.get("/api/capabilities")

    assert response.status_code == 200
    payload = response.json()
    serialized = json.dumps(payload)
    assert payload["features"]["cost_metrics"]["available"] is True
    assert payload["features"]["model_router"]["available"] is True
    assert "router.example" not in serialized
    assert "InstrumentationKey" not in serialized
    assert "services.ai.azure.com" not in serialized
