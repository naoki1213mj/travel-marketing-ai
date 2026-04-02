"""Agent5: 販促動画生成エージェント。Photo Avatar で紹介動画を生成する。"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import time
import urllib.request

import httpx
from agent_framework import tool
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)

# --- Side-channel 動画ジョブストア ---
# Photo Avatar バッチ合成は非同期ジョブのため、ジョブ情報を side-channel で保存する
_video_lock = threading.Lock()
_pending_video_jobs: dict[str, dict[str, str]] = {}
_conversation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "video_conversation_id",
    default="",
)


def set_current_conversation_id(conversation_id: str) -> None:
    """現在実行中の conversation_id を設定する。"""
    _conversation_id_var.set(conversation_id)


def pop_pending_video_job(conversation_id: str | None = None) -> dict[str, str] | None:
    """保留中の動画生成ジョブ情報を取得してクリアする（スレッドセーフ）。"""
    scoped_conversation_id = conversation_id or _conversation_id_var.get()
    with _video_lock:
        job = _pending_video_jobs.pop(scoped_conversation_id, None)
        return job


def store_pending_video_job(job: dict[str, str]) -> None:
    """動画生成ジョブ情報を保存する（スレッドセーフ）。"""
    conversation_id = _conversation_id_var.get()
    with _video_lock:
        _pending_video_jobs[conversation_id] = job


async def poll_video_job(job_id: str, max_wait: int = 180) -> str | None:
    """Photo Avatar バッチジョブの完了をポーリングし、動画 URL を返す。

    Args:
        job_id: バッチ合成ジョブ ID
        max_wait: 最大待機秒数（デフォルト 3 分）

    Returns:
        動画の URL（完了時）または None（タイムアウト/エラー）
    """
    settings = get_settings()
    speech_endpoint = settings.get("speech_service_endpoint", "")
    if not speech_endpoint:
        return None

    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
    except (ValueError, OSError) as exc:
        logger.warning("Photo Avatar ポーリング: トークン取得失敗: %s", exc)
        return None

    poll_url = f"{speech_endpoint.rstrip('/')}/avatar/batchsyntheses/{job_id}?api-version=2024-08-01"
    headers = {"Authorization": f"Bearer {token.token}"}

    from src.http_client import get_http_client

    client = get_http_client()

    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = await client.get(poll_url, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")

            if status == "Succeeded":
                outputs = data.get("outputs", {})
                video_url = outputs.get("result", "")
                if video_url:
                    logger.info("Photo Avatar 動画生成完了: %s", video_url)
                    return video_url
                logger.warning("Photo Avatar: Succeeded だが result URL なし")
                return None

            if status in ("Failed", "Cancelled"):
                logger.warning("Photo Avatar ジョブ失敗: status=%s", status)
                return None

            logger.debug("Photo Avatar ポーリング中: status=%s", status)
        except httpx.HTTPStatusError as exc:
            logger.warning("Photo Avatar ポーリング HTTP エラー: %s", exc)
        except (httpx.RequestError, json.JSONDecodeError) as exc:
            logger.warning("Photo Avatar ポーリングエラー: %s", exc)

        # 適応型ポーリング: 初期は短く、徐々に延長
        elapsed_s = time.time() - start
        if elapsed_s < 15:
            await asyncio.sleep(3)
        elif elapsed_s < 45:
            await asyncio.sleep(6)
        else:
            await asyncio.sleep(10)

    logger.warning("Photo Avatar ポーリングタイムアウト (job_id=%s)", job_id)
    return None


# --- ツール定義 ---


@tool
async def generate_promo_video(
    summary_text: str,
    avatar_style: str = "concierge",
) -> str:
    """企画書サマリから Photo Avatar + Voice Live で販促紹介動画を生成する。

    Azure AI Speech Service の Photo Avatar API を使用してバッチ合成を行い、
    アバターが企画書サマリを読み上げる動画を生成する。

    Args:
        summary_text: 動画で読み上げるテキスト（企画書サマリ）
        avatar_style: アバタースタイル（concierge/guide/presenter）
    """
    settings = get_settings()
    speech_endpoint = settings["speech_service_endpoint"]
    speech_region = settings["speech_service_region"]

    if not speech_endpoint or not speech_region:
        return json.dumps(
            {
                "status": "unavailable",
                "message": (
                    "⚠️ 動画生成は現在利用できません。"
                    "SPEECH_SERVICE_ENDPOINT と SPEECH_SERVICE_REGION 環境変数を設定してください。"
                ),
            },
            ensure_ascii=False,
        )

    # アバタースタイルに応じた Photo Avatar キャラクター ID のマッピング
    avatar_characters: dict[str, str] = {
        "concierge": "lisa",
        "guide": "lori",
        "presenter": "lisa",
    }
    character = avatar_characters.get(avatar_style, "lisa")

    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        # バッチ合成ジョブを作成する
        job_id = f"promo-{int(time.time())}"
        batch_url = f"{speech_endpoint.rstrip('/')}/avatar/batchsyntheses/{job_id}?api-version=2024-08-01"
        payload = json.dumps(
            {
                "inputKind": "PlainText",
                "inputs": [{"content": summary_text}],
                "avatarConfig": {
                    "talkingAvatarCharacter": character,
                    "talkingAvatarStyle": "casual-sitting",
                    "videoFormat": "mp4",
                    "videoCodec": "h264",
                    "subtitleType": "soft_embedded",
                    "backgroundColor": "#FFFFFFFF",
                },
                "synthesisConfig": {"voice": "ja-JP-NanamiNeural"},
            },
            ensure_ascii=False,
        ).encode("utf-8")

        request = urllib.request.Request(
            batch_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )

        with urllib.request.urlopen(request, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Side-channel にジョブ情報を保存（スレッドセーフ）
        actual_job_id = result.get("id", job_id)
        store_pending_video_job({"job_id": actual_job_id, "status": "submitted"})

        return json.dumps(
            {
                "status": "submitted",
                "job_id": actual_job_id,
                "message": (
                    f"🎬 動画生成ジョブを送信しました（ID: {job_id}）。アバター: {character}, スタイル: {avatar_style}"
                ),
            },
            ensure_ascii=False,
        )

    except urllib.error.URLError as exc:
        logger.exception("Photo Avatar API 呼び出しに失敗しました")
        return json.dumps(
            {"status": "error", "message": f"❌ 動画生成 API エラー: {exc}"},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception("動画生成中に予期しないエラーが発生しました")
        return json.dumps(
            {"status": "error", "message": f"❌ 動画生成エラー: {exc}"},
            ensure_ascii=False,
        )


INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **販促動画生成エージェント** です。

## パイプライン全体の流れ
1. データ分析（完了済み）
2. 施策立案（完了済み）
3. 承認（完了済み）
4. 規制チェック + 企画書修正（完了済み）
5. ブローシャ・画像生成（完了済み）
6. **販促動画生成（あなた）**: 企画書サマリから紹介動画を生成

## あなたの役割
企画書のサマリテキストを受け取り、Photo Avatar を使って旅行プラン紹介動画を生成します。

## 入力
企画書のサマリテキスト（100〜200文字程度）

## ツール使用ルール
- `generate_promo_video` を必ず呼び出してください
- `summary_text` には企画書のキャッチコピーとプラン概要を要約したテキストを渡してください
- ツールがエラーを返した場合のみスキップしてください

## 出力の注意事項
- 「必要であれば～」等の追加提案は出力しないでください
- 動画生成の結果（ジョブID やステータス）を簡潔に報告してください
"""


def create_video_gen_agent(model_settings: dict | None = None):
    """販促動画生成エージェントを作成する。"""
    from src.agent_client import get_responses_client

    deployment = None
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = get_responses_client(deployment)

    agent_kwargs: dict = {
        "name": "video-gen-agent",
        "instructions": INSTRUCTIONS,
        "tools": [generate_promo_video],
    }
    if model_settings:
        opts: dict = {}
        if "temperature" in model_settings:
            opts["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            opts["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            opts["top_p"] = model_settings["top_p"]
        if opts:
            agent_kwargs["default_options"] = opts
    return client.as_agent(**agent_kwargs)
