"""モデル deployment の安全な選択と可用性判定。"""

from typing import TypedDict

from src.config import AppSettings, get_settings

DEFAULT_MODEL_DEPLOYMENT = "gpt-5-4-mini"
MODEL_ROUTER_DEPLOYMENT = "model-router"

_TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled"}
_BASE_MODEL_DEPLOYMENTS = (DEFAULT_MODEL_DEPLOYMENT, "gpt-5.4", "gpt-4-1-mini", "gpt-4.1")
_GPT_55_MODEL_NAMES = {"gpt-5.5", "gpt-5-5", "gpt-55"}
_MODEL_ROUTER_NAMES = {"model-router", "model_router", "router"}


class ModelAvailability(TypedDict):
    """機能別モデル可用性。"""

    configured: bool
    available: bool


class ModelDeploymentUnavailableError(ValueError):
    """選択された model/deployment がこの環境では使えない。"""

    code = "MODEL_DEPLOYMENT_UNAVAILABLE"

    def __init__(self, selected_model: str, allowed_models: list[str]) -> None:
        allowed = ", ".join(allowed_models) if allowed_models else DEFAULT_MODEL_DEPLOYMENT
        super().__init__(
            f"MODEL_DEPLOYMENT_UNAVAILABLE: 選択されたモデル deployment '{selected_model}' はこの環境で利用できません。"
            f" 利用可能な deployment: {allowed}"
        )
        self.selected_model = selected_model
        self.allowed_models = allowed_models


def parse_bool_setting(value: str | None) -> bool:
    """環境変数由来の文字列を安全に bool へ変換する。"""
    return (value or "").strip().lower() in _TRUE_VALUES


def split_model_allowlist(value: str | None) -> list[str]:
    """カンマ区切り allowlist を順序維持で正規化する。"""
    models: list[str] = []
    for raw_item in (value or "").replace("\n", ",").split(","):
        item = raw_item.strip()
        if item and item not in models:
            models.append(item)
    return models


def _has_value(value: str | None) -> bool:
    return bool((value or "").strip())


def _setting(settings: AppSettings, key: str) -> str:
    """テスト用の部分 dict も許容して設定値を読む。"""
    return str(settings.get(key, ""))


def _add_model(models: list[str], model: str | None) -> None:
    normalized = (model or "").strip()
    if normalized and normalized not in models:
        models.append(normalized)


def gpt_55_availability(settings: AppSettings | None = None) -> ModelAvailability:
    """GPT-5.5 deployment の設定・利用可否を返す。"""
    resolved = settings or get_settings()
    configured = (
        parse_bool_setting(_setting(resolved, "enable_gpt_55"))
        or _has_value(_setting(resolved, "gpt_55_deployment_name"))
        or _setting(resolved, "model_name").strip().lower() in _GPT_55_MODEL_NAMES
    )
    return {"configured": configured, "available": configured and _has_value(_setting(resolved, "project_endpoint"))}


def model_router_availability(settings: AppSettings | None = None) -> ModelAvailability:
    """Model Router deployment の設定・利用可否を返す。"""
    resolved = settings or get_settings()
    configured = (
        parse_bool_setting(_setting(resolved, "enable_model_router"))
        or _has_value(_setting(resolved, "model_router_endpoint"))
        or _has_value(_setting(resolved, "model_router_deployment_name"))
    )
    return {"configured": configured, "available": configured and _has_value(_setting(resolved, "project_endpoint"))}


def get_allowed_model_deployments(settings: AppSettings | None = None) -> list[str]:
    """この環境で選択を許可する deployment 名を返す。"""
    resolved = settings or get_settings()
    models: list[str] = []
    for model in _BASE_MODEL_DEPLOYMENTS:
        _add_model(models, model)

    _add_model(models, _setting(resolved, "model_name") or DEFAULT_MODEL_DEPLOYMENT)
    for model in split_model_allowlist(_setting(resolved, "model_deployment_allowlist")):
        _add_model(models, model)

    if gpt_55_availability(resolved)["available"]:
        _add_model(models, _setting(resolved, "gpt_55_deployment_name") or "gpt-5.5")

    if model_router_availability(resolved)["available"]:
        _add_model(models, _setting(resolved, "model_router_deployment_name") or MODEL_ROUTER_DEPLOYMENT)

    return models


def resolve_model_deployment(
    selected_model: str | None,
    *,
    settings: AppSettings | None = None,
) -> str:
    """UI/リクエストのモデル選択を実 deployment 名へ解決し allowlist 検証する。"""
    resolved = settings or get_settings()
    selected = (selected_model or "").strip() or (_setting(resolved, "model_name").strip() or DEFAULT_MODEL_DEPLOYMENT)
    selected_key = selected.lower()

    if selected_key in _GPT_55_MODEL_NAMES:
        if not gpt_55_availability(resolved)["available"]:
            raise ModelDeploymentUnavailableError(selected, get_allowed_model_deployments(resolved))
        return _setting(resolved, "gpt_55_deployment_name").strip() or "gpt-5.5"

    if selected_key in _MODEL_ROUTER_NAMES:
        if not model_router_availability(resolved)["available"]:
            raise ModelDeploymentUnavailableError(selected, get_allowed_model_deployments(resolved))
        return _setting(resolved, "model_router_deployment_name").strip() or MODEL_ROUTER_DEPLOYMENT

    allowed = get_allowed_model_deployments(resolved)
    if selected not in allowed:
        raise ModelDeploymentUnavailableError(selected, allowed)
    return selected
