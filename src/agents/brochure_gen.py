"""Agent4: ブローシャ＆画像生成エージェント。HTML ブローシャとバナー画像を生成する。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from src.config import get_settings

logger = logging.getLogger(__name__)

# 1x1 透明 PNG（フォールバック用）
_FALLBACK_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

# 画像生成モデル名（Foundry にデプロイ済み）
_IMAGE_MODEL = "gpt-image-1.5"

# --- ブランドテンプレートプリセット ---
# 旧 functions/function_app.py (Azure Functions MCP サーバー) から移植。
# APIM が REST→MCP 変換を行うため Functions は廃止。
# regulations/brand_guidelines.md 準拠のカラー定数。
# TODO: ブランドテンプレート適用機能を FastAPI エンドポイントとして実装する際に使う
# _BRAND_PRIMARY = "#0066CC"
# _BRAND_SECONDARY = "#00A86B"
# _BRAND_ACCENT = "#FF6B35"
# _BRAND_TEXT = "#333333"
# _BRAND_BG = "#FFFFFF"
# _BRAND_BG_ALT = "#F5F5F5"
# _TEMPLATE_PRESETS = {
#     "default":   {"header_bg": "linear-gradient(135deg, #0066CC, #00A86B)", "accent": "#FF6B35"},
#     "luxury":    {"header_bg": "linear-gradient(135deg, #1a1a2e, #0066CC)", "accent": "#D4AF37"},
#     "nature":    {"header_bg": "linear-gradient(135deg, #00A86B, #2d6a4f)", "accent": "#FF6B35"},
#     "adventure": {"header_bg": "linear-gradient(135deg, #FF6B35, #e63946)", "accent": "#0066CC"},
# }

# Responses API ベースの画像生成クライアント（シングルトン）
_image_openai_client: object | None = None
_image_client_initialized: bool = False

# Azure AD token provider — caching + auto-refresh
_AZURE_AI_SCOPE = "https://ai.azure.com/.default"


def _get_image_openai_client():
    """Responses API 経由で画像生成する OpenAI クライアントを返す。

    Foundry project endpoint は legacy images.generate() をサポートしないため、
    Responses API + image_generation ツールを使う。
    x-ms-oai-image-generation-deployment ヘッダでデプロイ先を指定する。
    """
    global _image_openai_client, _image_client_initialized
    if not _image_client_initialized:
        _image_client_initialized = True
        settings = get_settings()
        endpoint = settings["project_endpoint"]
        if not endpoint:
            logger.info("project_endpoint 未設定、画像生成は無効")
            _image_openai_client = None
            return None
        try:
            from openai import OpenAI

            # Responses API の base URL を構築
            base_url = endpoint.rstrip("/")
            if not base_url.endswith("/openai/v1"):
                base_url = f"{base_url}/openai/v1"

            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(credential, _AZURE_AI_SCOPE)

            _image_openai_client = OpenAI(
                base_url=base_url,
                api_key=token_provider,
                default_headers={
                    "x-ms-oai-image-generation-deployment": _IMAGE_MODEL,
                },
            )
            logger.info("Responses API 画像クライアント作成: base_url=%s, deployment=%s", base_url, _IMAGE_MODEL)
        except (ImportError, ValueError, OSError) as exc:
            logger.warning("画像クライアント初期化失敗: %s", exc)
            _image_openai_client = None
        except Exception as exc:
            logger.exception("画像クライアント初期化で予期しないエラー: %s", exc)
            _image_openai_client = None
    return _image_openai_client


async def _generate_image(prompt: str, size: str = "1024x1024") -> str:
    """Responses API の image_generation ツールで画像を生成し、data URI を返す。"""
    try:
        client = _get_image_openai_client()
        if client is None:
            logger.info("OpenAI クライアント未初期化。フォールバック画像を返します")
            return _FALLBACK_IMAGE

        settings = get_settings()
        model_name = settings["model_name"]

        def _sync_generate():
            return client.responses.create(
                model=model_name,
                input=prompt,
                tools=[{"type": "image_generation", "size": size, "quality": "medium"}],
            )

        response = await asyncio.to_thread(_sync_generate)

        # Responses API の出力から画像データを抽出
        image_items = [item for item in (response.output or []) if item.type == "image_generation_call"]
        if not image_items or not getattr(image_items[0], "result", None):
            out_types = [i.type for i in (response.output or [])]
            logger.warning("画像データなし。出力タイプ: %s", out_types)
            return _FALLBACK_IMAGE

        b64_data = image_items[0].result
        return f"data:image/png;base64,{b64_data}"
    except (ValueError, OSError) as exc:
        logger.warning("画像生成に失敗。フォールバック画像を返します: %s", exc)
        return _FALLBACK_IMAGE
    except Exception as exc:
        logger.exception("画像生成で予期しないエラー。フォールバック画像を返します: %s", exc)
        return _FALLBACK_IMAGE


# --- Side-channel 画像ストア ---
# 画像の base64 をツール出力に含めるとコンテキストウインドウを超過するため、
# side-channel で保存し、パイプライン完了後に別途取得する（social-ai-studio パターン）
_pending_images: dict[str, str] = {}


def pop_pending_images() -> dict[str, str]:
    """保存済み画像を取得しクリアする。"""
    global _pending_images
    images = _pending_images.copy()
    _pending_images = {}
    return images


# --- Side-channel 動画ジョブストア ---
# Photo Avatar バッチ合成は非同期ジョブのため、ジョブ情報を side-channel で保存する
_pending_video_job: dict[str, str] | None = None


def pop_pending_video_job() -> dict[str, str] | None:
    """保留中の動画生成ジョブ情報を取得してクリアする。"""
    global _pending_video_job
    job = _pending_video_job
    _pending_video_job = None
    return job


# --- ツール定義 ---


@tool
async def generate_hero_image(
    prompt: str,
    destination: str,
    style: str = "photorealistic",
) -> str:
    """旅行先のヒーロー画像を生成する。

    Args:
        prompt: 画像生成プロンプト（英語推奨）
        destination: 旅行先の地名
        style: 画像スタイル（photorealistic/illustration/watercolor）
    """
    full_prompt = f"{style} travel photo of {destination}. {prompt}"
    data_uri = await _generate_image(full_prompt, "1536x1024")
    _pending_images["hero"] = data_uri
    return json.dumps(
        {"status": "generated", "type": "hero", "size": "1536x1024", "message": "ヒーロー画像を生成しました。"}
    )


@tool
async def generate_banner_image(
    prompt: str,
    platform: str = "instagram",
) -> str:
    """SNS バナー画像を生成する。

    Args:
        prompt: 画像生成プロンプト（英語推奨）
        platform: SNS プラットフォーム（instagram/twitter/facebook）
    """
    size = "1024x1024" if platform == "instagram" else "1536x1024"
    data_uri = await _generate_image(prompt, size)
    _pending_images[f"banner_{platform}"] = data_uri
    return json.dumps(
        {
            "status": "generated",
            "type": "banner",
            "platform": platform,
            "size": size,
            "message": f"{platform}用バナーを生成しました。",
        }
    )


@tool
async def analyze_existing_brochure(pdf_path: str) -> str:
    """既存のパンフレット PDF を解析し、レイアウト・トーンを参考情報として取得する。

    Content Understanding API（prebuilt-document-rag アナライザー）を使用して
    PDF の構造・テキスト・レイアウト情報を抽出する。

    Args:
        pdf_path: 解析対象の PDF ファイルパス
    """
    settings = get_settings()
    endpoint = settings.get("content_understanding_endpoint", "")
    if not endpoint:
        return "⚠️ PDF 解析は現在利用できません。CONTENT_UNDERSTANDING_ENDPOINT 環境変数を設定してください。"

    # PDF ファイルを読み込む
    try:
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
    except FileNotFoundError:
        return f"❌ ファイルが見つかりません: {pdf_path}"
    except OSError as exc:
        return f"❌ ファイル読み込みエラー: {exc}"

    # Content Understanding API でドキュメント解析
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        analyze_url = (
            f"{endpoint.rstrip('/')}/contentunderstanding/analyzers/"
            f"prebuilt-document-rag:analyze?api-version=2025-05-01-preview"
        )

        request = urllib.request.Request(
            analyze_url,
            data=pdf_bytes,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/pdf",
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # 解析結果からレイアウト・テキスト情報を構造化して返す
        pages = result.get("pages", [])
        paragraphs = result.get("paragraphs", [])

        summary_parts: list[str] = [
            f"📄 PDF 解析結果: {pdf_path}",
            f"  ページ数: {len(pages)}",
            "",
            "--- 抽出テキスト ---",
        ]

        for i, para in enumerate(paragraphs[:30]):
            role = para.get("role", "text")
            content = para.get("content", "").strip()
            if content:
                summary_parts.append(f"[{role}] {content}")

        if len(paragraphs) > 30:
            summary_parts.append(f"... 他 {len(paragraphs) - 30} 段落省略")

        return "\n".join(summary_parts)

    except urllib.error.URLError as exc:
        logger.exception("Content Understanding API 呼び出しに失敗しました")
        return f"❌ PDF 解析 API エラー: {exc}"
    except Exception as exc:
        logger.exception("PDF 解析中に予期しないエラーが発生しました")
        return f"❌ PDF 解析エラー: {exc}"


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
        "guide": "harry",
        "presenter": "lisa",
    }
    character = avatar_characters.get(avatar_style, "lisa")

    try:
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        # バッチ合成ジョブを作成する
        batch_url = f"{speech_endpoint.rstrip('/')}/avatar/batchsyntheses?api-version=2024-08-01"

        job_id = f"promo-{int(time.time())}"
        payload = json.dumps(
            {
                "inputKind": "PlainText",
                "inputs": [{"content": summary_text}],
                "avatarConfig": {
                    "talkingAvatarCharacter": character,
                    "talkingAvatarStyle": "graceful",
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
            f"{batch_url}&id={job_id}",
            data=payload,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )

        with urllib.request.urlopen(request, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        # Side-channel にジョブ情報を保存
        global _pending_video_job
        actual_job_id = result.get("id", job_id)
        _pending_video_job = {"job_id": actual_job_id, "status": "submitted"}

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
あなたは旅行販促物の制作エージェントです。企画書を受け取り、HTML ブローシャ・ヒーロー画像・SNS バナーを生成してください。

## 出力ルール
- HTML ブローシャは ```html で囲んで出力。Tailwind CSS 使用。レスポンシブ対応
- `generate_hero_image` でメインビジュアル、`generate_banner_image` で SNS バナーを生成
- フッターに旅行業登録番号と取引条件を挿入

## 既存パンフレット参照
`analyze_existing_brochure` ツールが利用可能な場合、既存のパンフレット PDF を解析して
レイアウト構成・キャッチコピーのトーン・写真配置を参考にしてください。
参考情報として取り込み、新しいプラン内容に合わせたブローシャを生成してください。
入力に [参考パンフレット: ...] が含まれていれば、そのパスで `analyze_existing_brochure` を呼び出してください。

## 販促紹介動画
ブローシャと画像の生成後、`generate_promo_video` ツールを使って販促紹介動画を生成してください。
企画書のサマリテキスト（100〜200文字）をアバターに読み上げさせます。
SPEECH_SERVICE_ENDPOINT が設定されていない場合はスキップしてください。
"""


def create_brochure_gen_agent(model_settings: dict | None = None):
    """ブローシャ＆画像生成エージェントを作成する"""
    settings = get_settings()
    deployment = settings["model_name"]
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
        deployment_name=deployment,
    )

    agent_tools: list = [
        generate_hero_image,
        generate_banner_image,
        analyze_existing_brochure,
        generate_promo_video,
    ]

    agent_kwargs: dict = {
        "name": "brochure-gen-agent",
        "instructions": INSTRUCTIONS,
        "tools": agent_tools,
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
