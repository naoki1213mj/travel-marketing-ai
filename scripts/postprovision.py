"""postprovision.py — azd provision 後の APIM AI Gateway + Foundry 統合セットアップ

azd の postprovision フックとして実行される。
1. Foundry に AI Gateway 接続（APIM → Foundry）を作成
2. Foundry が自動生成する foundry-* API に AI Gateway ポリシーを適用

冪等: 何度実行しても安全。失敗時はログ出力のみで中断しない。
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request

from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _get_azd_env() -> dict[str, str]:
    """azd env get-values から環境変数を読み込む"""
    result = subprocess.run(
        ["azd", "env", "get-values"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning("azd env get-values に失敗: %s", result.stderr.strip())
        return {}

    env: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"')
    return env


def _get_token(scope: str = "https://management.azure.com/.default") -> str:
    """DefaultAzureCredential でアクセストークンを取得"""
    credential = DefaultAzureCredential()
    return credential.get_token(scope).token


def _rest_call(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    scope: str = "https://management.azure.com/.default",
    timeout: int = 30,
) -> dict | None:
    """Azure REST API を呼び出すヘルパー"""
    if token is None:
        token = _get_token(scope)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()
        except Exception:
            pass
        logger.warning("REST %s %s → HTTP %s: %s", method, url, e.code, error_body[:500])
        return None
    except Exception as e:
        logger.warning("REST %s %s → %s", method, url, e)
        return None


# ---------------------------------------------------------------------------
# Step 1: Foundry AI Gateway 接続を作成
# ---------------------------------------------------------------------------


def create_ai_gateway_connection(
    project_endpoint: str,
    apim_name: str,
    apim_resource_id: str,
) -> bool:
    """Foundry に AI Gateway 接続を作成する

    APIM を経由して LLM 推論をルーティングするための接続を登録する。
    成功すると Foundry が APIM 上に foundry-* API を自動生成する。
    """
    connection_name = "travel-ai-gateway"
    url = (
        f"{project_endpoint}/connections/{connection_name}"
        "?api-version=2025-05-01-preview"
    )

    body = {
        "properties": {
            "authType": "ProjectManagedIdentity",
            "category": "ApiManagement",
            "target": f"https://{apim_name}.azure-api.net",
            "isSharedToAll": True,
            "metadata": {
                "ResourceId": apim_resource_id,
                "ApiType": "Azure",
            },
        },
    }

    logger.info("AI Gateway 接続を作成中: %s → %s", connection_name, apim_name)
    result = _rest_call(url, method="PUT", body=body)
    if result is not None:
        logger.info("AI Gateway 接続を作成しました: %s", connection_name)
        return True

    logger.warning("AI Gateway 接続の作成に失敗しました")
    return False


# ---------------------------------------------------------------------------
# Step 2: foundry-* API に AI Gateway ポリシーを適用
# ---------------------------------------------------------------------------

_AI_GATEWAY_POLICY_XML = """\
<policies>
  <inbound>
    <base />
    <llm-token-limit
      tokens-per-minute="80000"
      counter-key="@(context.Subscription.Id)"
      estimate-prompt-tokens="true"
      remaining-tokens-header-name="x-ratelimit-remaining-tokens" />
    <llm-content-safety backend-id="content-safety" />
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
    <llm-emit-token-metric>
      <dimension name="API ID" />
    </llm-emit-token-metric>
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>"""


def apply_ai_gateway_policy(
    subscription_id: str,
    rg: str,
    apim_name: str,
) -> None:
    """APIM の foundry-* API に AI Gateway ポリシーを適用する"""
    token = _get_token()

    # foundry-* API を列挙
    list_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        "/apis?api-version=2024-05-01"
    )
    resp = _rest_call(list_url, token=token)
    if resp is None:
        logger.warning("APIM API 一覧の取得に失敗しました")
        return

    foundry_apis = [
        api for api in resp.get("value", []) if api["name"].startswith("foundry-")
    ]

    if not foundry_apis:
        logger.info("foundry-* API はまだ作成されていません（後で再実行してください）")
        return

    for api in foundry_apis:
        api_name = api["name"]
        policy_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{rg}"
            f"/providers/Microsoft.ApiManagement/service/{apim_name}"
            f"/apis/{api_name}/policies/policy?api-version=2024-05-01"
        )
        body = {
            "properties": {
                "format": "rawxml",
                "value": _AI_GATEWAY_POLICY_XML,
            },
        }
        result = _rest_call(policy_url, method="PUT", body=body, token=token)
        if result is not None:
            logger.info("AI Gateway ポリシーを適用しました: %s", api_name)
        else:
            logger.warning("AI Gateway ポリシー適用に失敗: %s", api_name)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

_FOUNDRY_API_CREATION_WAIT_SECONDS = 30


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    logger.info("postprovision を開始します")

    # azd 環境変数を読み込み
    env = _get_azd_env()
    subscription_id = env.get("AZURE_SUBSCRIPTION_ID", "")
    rg = env.get("AZURE_RESOURCE_GROUP", "")
    apim_name = env.get("AZURE_APIM_NAME", "")
    project_endpoint = env.get("AZURE_AI_PROJECT_ENDPOINT", "")

    if not all([subscription_id, rg, project_endpoint]):
        logger.error(
            "必要な環境変数が不足しています "
            "(AZURE_SUBSCRIPTION_ID=%s, AZURE_RESOURCE_GROUP=%s, AZURE_AI_PROJECT_ENDPOINT=%s)",
            bool(subscription_id),
            bool(rg),
            bool(project_endpoint),
        )
        return

    if not apim_name:
        logger.warning("AZURE_APIM_NAME が未設定のため AI Gateway セットアップをスキップします")
        logger.info("postprovision 完了（スキップ）")
        return

    # APIM リソース ID を構築
    apim_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
    )

    # Step 1: Foundry に AI Gateway 接続を作成
    created = create_ai_gateway_connection(project_endpoint, apim_name, apim_resource_id)

    # Step 2: Foundry が APIM 上に foundry-* API を自動生成するのを待機
    if created:
        logger.info(
            "Foundry が APIM に API を作成するのを待機中 (%d秒)...",
            _FOUNDRY_API_CREATION_WAIT_SECONDS,
        )
        time.sleep(_FOUNDRY_API_CREATION_WAIT_SECONDS)

    # Step 3: AI Gateway ポリシーを適用
    apply_ai_gateway_policy(subscription_id, rg, apim_name)

    logger.info("postprovision 完了")


if __name__ == "__main__":
    main()
