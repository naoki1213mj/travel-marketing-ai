"""improvement-mcp 用 Azure Functions を配備し、APIM 登録まで同期する。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.postprovision import (
    _cli_available,
    _get_azd_env,
    _merge_env,
    _normalize_resource_token,
    setup_improvement_mcp,
)

logger = logging.getLogger(__name__)


def _load_env() -> dict[str, str]:
    """CI ではプロセス環境を優先し、azd がある場合のみ azd env を取り込む。"""
    env = _merge_env()
    if not _cli_available("azd"):
        return env
    return _merge_env(_get_azd_env())


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    env = _load_env()
    subscription_id = env.get("AZURE_SUBSCRIPTION_ID", "").strip()
    resource_group = env.get("AZURE_RESOURCE_GROUP", "").strip()
    container_app_name = env.get("AZURE_CONTAINER_APP_NAME", "").strip()
    apim_name = env.get("AZURE_APIM_NAME", "").strip()
    if not apim_name and container_app_name:
        resource_token = _normalize_resource_token(container_app_name)
        if resource_token:
            apim_name = f"apim-{resource_token}"

    if not all([subscription_id, resource_group, apim_name]):
        logger.error(
            "必要な環境変数が不足しています (subscription=%s, resource_group=%s, apim=%s)",
            bool(subscription_id),
            bool(resource_group),
            bool(apim_name),
        )
        return 1

    if not setup_improvement_mcp(subscription_id, resource_group, apim_name, env):
        logger.error("improvement-mcp の配備または APIM 登録に失敗しました")
        return 1

    logger.info("improvement-mcp の配備と APIM 登録が完了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
