"""ロードマップ機能の安全な可用性サマリーを構築する。"""

from typing import TypedDict

from src.config import AppSettings, get_settings
from src.continuous_monitoring import parse_sample_rate
from src.foundry_tracing import is_foundry_tracing_enabled
from src.mai_transcribe import get_mai_transcribe_availability
from src.model_deployments import gpt_55_availability, model_router_availability, parse_bool_setting


class CapabilityFeature(TypedDict):
    """個別機能の公開可能な状態。"""

    available: bool
    configured: bool


class CapabilitySnapshot(TypedDict):
    """クライアントへ返す安全な capabilities レスポンス。"""

    version: int
    features: dict[str, CapabilityFeature]


def _has_value(value: str | None) -> bool:
    return bool((value or "").strip())


def _feature(available: bool, configured: bool) -> CapabilityFeature:
    return {"available": available, "configured": configured}


def build_capability_snapshot(settings: AppSettings | None = None) -> CapabilitySnapshot:
    """機密値を含まない機能可用性のみを返す。"""
    resolved = settings or get_settings()
    has_project_endpoint = _has_value(resolved["project_endpoint"])
    has_entra_client = _has_value(resolved["entra_client_id"])
    work_iq_runtime = resolved["work_iq_runtime"].strip()
    marketing_runtime = resolved["marketing_plan_runtime"].strip()

    gpt_55 = gpt_55_availability(resolved)
    model_router = model_router_availability(resolved)
    mai_transcribe = get_mai_transcribe_availability(resolved)
    evaluation_logging_enabled = parse_bool_setting(resolved["enable_evaluation_logging"])
    # 音声入力は Azure Speech STT を keyless（Managed Identity）で使うため、custom-domain
    # endpoint と region だけが必要。MSAL ユーザーログイン（entra_client）には依存しない。
    speech_stt_configured = _has_value(resolved["speech_service_endpoint"]) and _has_value(
        resolved["speech_service_region"]
    )
    work_iq_configured = (
        has_entra_client
        and marketing_runtime == "foundry_preprovisioned"
        and (
            work_iq_runtime == "graph_prefetch"
            or (work_iq_runtime == "foundry_tool" and has_project_endpoint)
        )
    )

    features: dict[str, CapabilityFeature] = {
        "model_router": _feature(model_router["available"], model_router["configured"]),
        "gpt_55": _feature(gpt_55["available"], gpt_55["configured"]),
        "foundry_tracing": _feature(
            is_foundry_tracing_enabled(resolved),
            parse_bool_setting(resolved["enable_foundry_tracing"]),
        ),
        "continuous_monitoring": _feature(
            parse_bool_setting(resolved["enable_continuous_monitoring"])
            and evaluation_logging_enabled
            and has_project_endpoint
            and parse_sample_rate(resolved["continuous_monitoring_sample_rate"]) > 0,
            parse_bool_setting(resolved["enable_continuous_monitoring"]),
        ),
        "evaluation_logging": _feature(
            evaluation_logging_enabled and has_project_endpoint,
            evaluation_logging_enabled,
        ),
        "cost_metrics": _feature(
            parse_bool_setting(resolved["enable_cost_metrics"])
            and _has_value(resolved["applicationinsights_connection_string"]),
            parse_bool_setting(resolved["enable_cost_metrics"]),
        ),
        "mcp_registry": _feature(
            _has_value(resolved["mcp_registry_endpoint"]) or _has_value(resolved["improvement_mcp_endpoint"]),
            _has_value(resolved["mcp_registry_endpoint"]) or _has_value(resolved["improvement_mcp_endpoint"]),
        ),
        "source_ingestion": _feature(
            parse_bool_setting(resolved["enable_source_ingestion"]),
            parse_bool_setting(resolved["enable_source_ingestion"]) or _has_value(resolved["source_ingestion_endpoint"]),
        ),
        "voice_live": _feature(speech_stt_configured, speech_stt_configured),
        "voice_talk_to_start": _feature(
            parse_bool_setting(resolved["enable_voice_talk_to_start"]) and speech_stt_configured,
            parse_bool_setting(resolved["enable_voice_talk_to_start"]),
        ),
        "mai_transcribe_1": _feature(
            mai_transcribe["available"],
            mai_transcribe["configured"],
        ),
        "work_iq": _feature(work_iq_configured, work_iq_configured),
    }
    return {"version": 1, "features": features}
