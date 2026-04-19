"""Agent5: 販促動画生成エージェント。Photo Avatar で紹介動画を生成する。"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import TypedDict
from xml.sax.saxutils import escape

import httpx
from agent_framework import tool
from azure.identity import DefaultAzureCredential

from src.config import get_settings
from src.tool_telemetry import trace_tool_invocation

logger = logging.getLogger(__name__)

_DEFAULT_PROMO_VOICE = "ja-JP-Nanami:DragonHDLatestNeural"
_DEFAULT_AVATAR_STYLE = "casual-sitting"
_DEFAULT_BACKGROUND_COLOR = "#FFFFFFFF"
_DEFAULT_BITRATE_KBPS = 4000
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_MULTISPACE_RE = re.compile(r"\s+")
_VIDEO_SECTION_LABELS = {
    "タイトル",
    "キャッチコピー",
    "ターゲットペルソナ",
    "プラン概要",
    "差別化ポイント",
    "改善ポイント",
    "販促チャネル",
    "kpi",
}
_AVATAR_PROFILES: dict[str, tuple[str, str]] = {
    "concierge": ("lisa", "casual-sitting"),
    "guide": ("lori", "casual"),
    "presenter": ("lisa", "graceful-sitting"),
}
_SUPPORTED_GESTURES: dict[tuple[str, str], list[str]] = {
    ("lisa", "casual-sitting"): ["show-front-1", "show-front-2", "thumbsup-left-1"],
    ("lisa", "graceful-sitting"): ["wave-left-1", "show-left-1", "show-right-1"],
    ("lori", "casual"): ["hello", "open", "thanks"],
}

# --- Side-channel 動画ジョブストア ---
# Photo Avatar バッチ合成は非同期ジョブのため、ジョブ情報を side-channel で保存する
_video_lock = threading.Lock()
_pending_video_jobs: dict[str, dict[str, str]] = {}
_conversation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "video_conversation_id",
    default="",
)


class VideoPollResult(TypedDict):
    """動画ポーリング結果。"""

    status: str
    video_url: str | None
    message: str


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


def _read_positive_int_env(name: str, default_value: int) -> int:
    """正の整数環境変数を取得する。未設定や不正値は既定値にフォールバックする。"""
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default_value
    try:
        parsed_value = int(raw_value)
    except ValueError:
        logger.warning("%s が整数ではないため既定値を使用します: %s", name, raw_value)
        return default_value
    if parsed_value <= 0:
        logger.warning("%s が正の整数ではないため既定値を使用します: %s", name, raw_value)
        return default_value
    return parsed_value


def _resolve_avatar_profile(
    avatar_style: str,
    configured_character: str,
    configured_style: str,
) -> tuple[str, str]:
    """アバター種別から互換性のある character/style を解決する。"""
    default_character, default_style = _AVATAR_PROFILES.get(avatar_style, ("lisa", _DEFAULT_AVATAR_STYLE))
    character = configured_character or default_character
    style = configured_style or default_style
    return character, style


def _select_avatar_gestures(character: str, avatar_pose: str) -> list[str]:
    """標準アバターの組み合わせに対応したジェスチャー列を返す。"""
    return list(_SUPPORTED_GESTURES.get((character.lower(), avatar_pose.lower()), []))


def _stringify_poll_detail(value: object) -> str:
    """ポーリング応答内の詳細情報を UI 向けに文字列化する。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return _MULTISPACE_RE.sub(" ", value).strip()
    if isinstance(value, dict):
        preferred_parts: list[str] = []
        for key in ("message", "details", "reason", "error", "code"):
            nested_value = value.get(key)
            if isinstance(nested_value, str) and nested_value.strip():
                preferred_parts.append(nested_value.strip())
        if preferred_parts:
            return _MULTISPACE_RE.sub(" ", " / ".join(preferred_parts)).strip()
    try:
        return _MULTISPACE_RE.sub(" ", json.dumps(value, ensure_ascii=False)).strip()
    except TypeError:
        return _MULTISPACE_RE.sub(" ", str(value)).strip()


def _extract_poll_failure_detail(data: dict[str, object]) -> str:
    """Speech Avatar の失敗詳細を応答 JSON から抽出する。"""
    candidates: list[object] = [
        data.get("statusMessage"),
        data.get("message"),
        data.get("error"),
    ]

    outputs = data.get("outputs")
    if isinstance(outputs, dict):
        candidates.append(outputs.get("summary"))

    properties = data.get("properties")
    if isinstance(properties, dict):
        candidates.extend(
            [
                properties.get("statusDetails"),
                properties.get("error"),
                properties.get("errorMessage"),
                properties.get("reason"),
            ]
        )

    for candidate in candidates:
        detail = _stringify_poll_detail(candidate)
        if detail:
            return detail[:280]
    return ""


def _normalize_summary_text(summary_text: str) -> str:
    """動画ナレーション用に企画書サマリを整形する。"""
    cleaned_lines: list[str] = []
    for raw_line in summary_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("|"):
            continue
        if line.startswith("[参考パンフレット:"):
            continue

        normalized = line.lstrip("#").strip()
        normalized = normalized.lstrip("-*・ ").strip()
        normalized = _MARKDOWN_LINK_RE.sub(r"\1", normalized)
        normalized = normalized.replace("**", "").replace("__", "").replace("`", "")
        normalized = re.sub(
            r"^(タイトル|キャッチコピー|ターゲットペルソナ|プラン概要|差別化ポイント|改善ポイント|販促チャネル|KPI)\s*[:：]\s*",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip()
        if not normalized:
            continue
        if normalized.lower().rstrip(":：") in _VIDEO_SECTION_LABELS:
            continue

        cleaned_lines.append(normalized)

    normalized_text = _MULTISPACE_RE.sub(" ", " ".join(cleaned_lines)).strip(" 。")
    return normalized_text[:320]


def _split_sentences(summary_text: str) -> list[str]:
    """テキストをナレーション向けの短い文に分割する。"""
    normalized = _normalize_summary_text(summary_text)
    if not normalized:
        return []

    raw_parts = re.split(r"[。！？!?]+", normalized)
    sentences: list[str] = []
    for raw_part in raw_parts:
        part = raw_part.strip(" 、")
        if not part:
            continue
        sentences.append(f"{part}。")
    return sentences[:4]


def _build_avatar_ssml(summary_text: str, voice_name: str, gestures: list[str]) -> str:
    """Photo Avatar 用の互換性重視 SSML を構築する。"""
    source_sentences = _split_sentences(summary_text)
    if not source_sentences:
        source_sentences = ["おすすめの旅行プランをご紹介します。"]

    intro = "こんにちは。今回ご紹介するのは、こちらのおすすめ旅行プランです。"
    headline = source_sentences[0]
    detail_sentences = source_sentences[1:3]
    closing = "詳しくはブローシャをご確認のうえ、ぜひお問い合わせください。"
    intro_gesture = gestures[0] if len(gestures) > 0 else ""
    headline_gesture = gestures[1] if len(gestures) > 1 else ""
    detail_gestures = gestures[1:] if len(gestures) > 1 else []
    closing_gesture = gestures[-1] if gestures else ""

    ssml_parts = [
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' ",
        "xmlns:mstts='https://www.w3.org/2001/mstts' xml:lang='ja-JP'>",
        f"<voice name='{escape(voice_name)}' parameters='temperature=0.72'>",
    ]

    if intro_gesture:
        ssml_parts.append(f"<bookmark mark='gesture.{escape(intro_gesture)}'/>")
    ssml_parts.extend(
        [
            escape(intro),
            "<break time='500ms'/>",
        ]
    )

    if headline_gesture:
        ssml_parts.append(f"<bookmark mark='gesture.{escape(headline_gesture)}'/>")
    ssml_parts.append(escape(headline))

    for index, sentence in enumerate(detail_sentences):
        gesture = detail_gestures[index] if index < len(detail_gestures) else ""
        ssml_parts.append("<break time='400ms'/>")
        if gesture:
            ssml_parts.append(f"<bookmark mark='gesture.{escape(gesture)}'/>")
        ssml_parts.append(escape(sentence))

    ssml_parts.append("<break time='500ms'/>")
    if closing_gesture:
        ssml_parts.append(f"<bookmark mark='gesture.{escape(closing_gesture)}'/>")
    ssml_parts.extend(
        [
            escape(closing),
            "</voice>",
            "</speak>",
        ]
    )
    return "".join(ssml_parts)


async def poll_video_job(job_id: str, max_wait: int = 180) -> VideoPollResult | None:
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
    last_error_detail = ""
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
                    return {"status": "succeeded", "video_url": video_url, "message": ""}
                logger.warning("Photo Avatar: Succeeded だが result URL なし")
                return {
                    "status": "failed",
                    "video_url": None,
                    "message": "Photo Avatar ジョブは成功扱いですが動画 URL を返しませんでした。",
                }

            if status in ("Failed", "Cancelled"):
                detail = _extract_poll_failure_detail(data)
                logger.warning("Photo Avatar ジョブ失敗: status=%s detail=%s", status, detail or "<empty>")
                return {
                    "status": status.lower(),
                    "video_url": None,
                    "message": detail or f"Photo Avatar ジョブが {status} で終了しました。",
                }

            logger.debug("Photo Avatar ポーリング中: status=%s", status)
        except httpx.HTTPStatusError as exc:
            last_error_detail = f"HTTP {exc.response.status_code}"
            logger.warning("Photo Avatar ポーリング HTTP エラー: %s", exc)
        except (httpx.RequestError, json.JSONDecodeError) as exc:
            last_error_detail = str(exc)
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
    timeout_message = "Photo Avatar ジョブの完了待機がタイムアウトしました。"
    if last_error_detail:
        timeout_message = f"{timeout_message} 最後のエラー: {last_error_detail}"
    return {"status": "timeout", "video_url": None, "message": timeout_message}


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
    async with trace_tool_invocation("generate_promo_video", agent_name="video-gen-agent"):
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

        configured_character = os.environ.get("VIDEO_GEN_AVATAR_CHARACTER", "").strip()
        configured_style = os.environ.get("VIDEO_GEN_AVATAR_STYLE", "").strip()
        configured_voice = os.environ.get("VIDEO_GEN_VOICE", _DEFAULT_PROMO_VOICE).strip()
        background_color = os.environ.get("VIDEO_GEN_BACKGROUND_COLOR", _DEFAULT_BACKGROUND_COLOR).strip()
        bitrate_kbps = _read_positive_int_env("VIDEO_GEN_BITRATE_KBPS", _DEFAULT_BITRATE_KBPS)

        character, avatar_pose = _resolve_avatar_profile(avatar_style, configured_character, configured_style)
        voice_name = configured_voice or _DEFAULT_PROMO_VOICE
        gesture_sequence = _select_avatar_gestures(character, avatar_pose)
        ssml_content = _build_avatar_ssml(summary_text, voice_name, gesture_sequence)

        try:
            credential = DefaultAzureCredential()
            token = credential.get_token("https://cognitiveservices.azure.com/.default")

            # バッチ合成ジョブを作成する
            job_id = f"promo-{int(time.time())}"
            batch_url = f"{speech_endpoint.rstrip('/')}/avatar/batchsyntheses/{job_id}?api-version=2024-08-01"
            payload = json.dumps(
                {
                    "inputKind": "SSML",
                    "inputs": [{"content": ssml_content}],
                    "avatarConfig": {
                        "talkingAvatarCharacter": character,
                        "talkingAvatarStyle": avatar_pose,
                        "videoFormat": "Mp4",
                        "videoCodec": "h264",
                        "subtitleType": "soft_embedded",
                        "backgroundColor": background_color,
                        "bitrateKbps": bitrate_kbps,
                    },
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
                        f"🎬 動画生成ジョブを送信しました（ID: {job_id}）。"
                        f"アバター: {character}, スタイル: {avatar_pose}, 音声: {voice_name}"
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
企画書のサマリテキスト（100〜240文字程度）

## ツール使用ルール
- `generate_promo_video` を必ず呼び出してください
- `summary_text` には顧客向けの短いナレーション台本を渡してください
- ナレーションは 3〜4 文で、冒頭の導入 → 主な魅力 → 締めの案内、の流れにしてください
- KPI、売上目標、セグメント分析、競合分析などの社内情報は含めないでください
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
