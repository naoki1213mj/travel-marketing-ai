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
    # Foundry connections は ARM API 経由で管理する
    # project_endpoint（AI Services 形式）ではなく ARM パスを使用
    ai_services_name = project_endpoint.split("//")[1].split(".")[0]  # ais5gg4m4g72lrdo
    project_name = project_endpoint.rstrip("/").split("/")[-1]  # aip-5gg4m4g72lrdo
    url = (
        f"https://management.azure.com{apim_resource_id.rsplit('/providers/Microsoft.ApiManagement', 1)[0]}"
        f"/providers/Microsoft.CognitiveServices/accounts/{ai_services_name}"
        f"/projects/{project_name}/connections/{connection_name}"
        "?api-version=2025-04-01-preview"
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
    # Foundry connections API は ARM 経由でアクセスする
    # project_endpoint ではなく management.azure.com を使用
    result = _rest_call(url, method="PUT", body=body, scope="https://management.azure.com/.default")
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
    <llm-emit-token-metric>
      <dimension name="API ID" />
    </llm-emit-token-metric>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
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
# Step 3: Entra ID SPA アプリ登録（Voice Live ブラウザ認証用）
# ---------------------------------------------------------------------------


def create_entra_app(app_name: str = "travel-voice-spa") -> str | None:
    """Voice Live 用の Entra ID SPA アプリ登録を作成する。"""
    import os as _os

    # 既存アプリの確認
    result = subprocess.run(
        ["az", "ad", "app", "list", "--display-name", app_name, "--query", "[0].appId", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=False, shell=True,
    )
    existing_id = result.stdout.strip()
    if existing_id:
        logger.info("Entra App 既存: %s (%s)", app_name, existing_id)
        return existing_id

    # 新規 SPA アプリ作成
    result = subprocess.run(
        [
            "az", "ad", "app", "create",
            "--display-name", app_name,
            "--sign-in-audience", "AzureADMyOrg",
            "--enable-id-token-issuance", "true",
            "--web-redirect-uris", "",
            "--query", "appId", "-o", "tsv",
        ],
        capture_output=True,
        text=True,
        check=False,
        shell=True,
    )
    if result.returncode != 0:
        logger.warning("Entra App 作成失敗: %s", result.stderr)
        return None

    app_id = result.stdout.strip()

    # SPA リダイレクト URI を設定
    container_app_url = _os.environ.get("SERVICE_WEB_ENDPOINTS", "").strip("[]\"' ")
    redirect_uris = [
        "http://localhost:5173",
        "http://localhost:8000",
    ]
    if container_app_url:
        redirect_uris.append(container_app_url)

    subprocess.run(
        ["az", "ad", "app", "update", "--id", app_id, "--spa-redirect-uris", *redirect_uris],
        capture_output=True,
        text=True,
        check=False,
        shell=True,
    )

    # Microsoft Graph User.Read 権限を追加
    subprocess.run(
        [
            "az", "ad", "app", "permission", "add",
            "--id", app_id,
            "--api", "00000003-0000-0000-c000-000000000000",
            "--api-permissions", "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope",
        ],
        capture_output=True,
        text=True,
        check=False,
        shell=True,
    )

    logger.info("Entra App 作成完了: %s (%s)", app_name, app_id)
    return app_id


# ---------------------------------------------------------------------------
# Step 4: Voice Live 用 Foundry Prompt Agent を作成
# ---------------------------------------------------------------------------


def create_voice_agent(
    project_endpoint: str,
    subscription_id: str,
    rg: str,
) -> None:
    """Voice Live 用の Foundry Prompt Agent を作成する。"""
    ai_services_name = project_endpoint.split("//")[1].split(".")[0]
    project_name = project_endpoint.rstrip("/").split("/")[-1]

    token = _get_token()

    agent_name = "travel-voice-orchestrator"

    # Voice Live 設定
    voice_live_config = json.dumps({
        "session": {
            "voice": {
                "name": "ja-JP-NanamiNeural",
                "type": "azure-standard",
                "temperature": 0.8,
            },
            "input_audio_transcription": {
                "model": "azure-speech",
            },
            "turn_detection": {
                "type": "azure_semantic_vad",
                "silence_duration_ms": 500,
            },
            "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
            "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
        }
    })

    # メタデータに Voice Live 設定を格納（512文字制限のためチャンク化）
    metadata: dict[str, str] = {}
    limit = 512
    metadata["microsoft.voice-live.configuration"] = voice_live_config[:limit]
    remaining = voice_live_config[limit:]
    chunk_num = 1
    while remaining:
        metadata[f"microsoft.voice-live.configuration.{chunk_num}"] = remaining[:limit]
        remaining = remaining[limit:]
        chunk_num += 1

    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{ai_services_name}"
        f"/projects/{project_name}/agents/{agent_name}/versions/1.0"
        "?api-version=2025-04-01-preview"
    )

    body = {
        "properties": {
            "definition": {
                "type": "PromptAgent",
                "model": "gpt-5-4-mini",
                "instructions": (
                    "あなたは旅行マーケティングのアシスタントです。\n"
                    "ユーザーの音声指示を聞き取り、旅行プランの企画を支援します。\n"
                    "ユーザーが旅行プランの企画を依頼したら、具体的な旅行先・季節・ターゲット・予算を確認し、\n"
                    "企画の方向性を提案してください。\n"
                    "日本語で応答してください。"
                ),
            },
            "metadata": metadata,
        }
    }

    logger.info("Voice Agent を作成中: %s", agent_name)
    result = _rest_call(url, method="PUT", body=body, token=token)
    if result is not None:
        logger.info("Voice Agent を作成しました: %s", agent_name)
    else:
        logger.warning("Voice Agent の作成に失敗しました")


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

    # Step 4: Voice Agent 作成
    create_voice_agent(project_endpoint, subscription_id, rg)

    # Step 5: Entra App 登録（Voice Live SPA 認証用）
    tenant_result = subprocess.run(
        ["az", "account", "show", "--query", "tenantId", "-o", "tsv"],
        capture_output=True,
        text=True,
        check=False, shell=True,
    )
    tenant_id = tenant_result.stdout.strip()
    app_id = create_entra_app()
    if app_id:
        subprocess.run(["azd", "env", "set", "VOICE_SPA_CLIENT_ID", app_id], check=False, shell=True)
        logger.info("Voice SPA Client ID を azd env に保存: %s", app_id)
    if tenant_id:
        subprocess.run(["azd", "env", "set", "AZURE_TENANT_ID", tenant_id], check=False, shell=True)
        logger.info("Azure Tenant ID を azd env に保存: %s", tenant_id)

    logger.info("postprovision 完了")


if __name__ == "__main__":
    main()
