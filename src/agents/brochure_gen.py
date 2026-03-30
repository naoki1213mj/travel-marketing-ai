"""Agent4: ブローシャ＆画像生成エージェント。HTML ブローシャとバナー画像を生成する。"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

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
_images_lock = threading.Lock()
_pending_images: dict[str, str] = {}


def pop_pending_images() -> dict[str, str]:
    """保存済み画像を取得しクリアする（スレッドセーフ）。"""
    with _images_lock:
        images = _pending_images.copy()
        _pending_images.clear()
        return images


# --- Side-channel 動画ジョブストア ---
# Photo Avatar バッチ合成は非同期ジョブのため、ジョブ情報を side-channel で保存する
_video_lock = threading.Lock()
_pending_video_job: dict[str, str] | None = None


def pop_pending_video_job() -> dict[str, str] | None:
    """保留中の動画生成ジョブ情報を取得してクリアする（スレッドセーフ）。"""
    global _pending_video_job
    with _video_lock:
        job = _pending_video_job
        _pending_video_job = None
        return job


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
    except (ValueError, OSError, Exception) as exc:
        logger.warning("Photo Avatar ポーリング: トークン取得失敗: %s", exc)
        return None

    poll_url = f"{speech_endpoint.rstrip('/')}/avatar/batchsyntheses/{job_id}?api-version=2024-08-01"
    headers = {"Authorization": f"Bearer {token.token}"}

    start = time.time()
    while time.time() - start < max_wait:
        try:
            req = urllib.request.Request(poll_url, headers=headers, method="GET")
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
            data = json.loads(resp.read().decode())
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
        except urllib.error.URLError as exc:
            logger.warning("Photo Avatar ポーリング通信エラー: %s", exc)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Photo Avatar ポーリングエラー: %s", exc)

        await asyncio.sleep(10)

    logger.warning("Photo Avatar ポーリングタイムアウト (job_id=%s)", job_id)
    return None


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
    with _images_lock:
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
    with _images_lock:
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
    # パストラバーサル防止: data/ ディレクトリ内のみアクセスを許可
    allowed_dir = Path(__file__).resolve().parent.parent.parent / "data"
    resolved = Path(pdf_path).resolve()
    if not str(resolved).startswith(str(allowed_dir)):
        return json.dumps({"error": "指定されたパスはアクセスが許可されていません"}, ensure_ascii=False)
    if not resolved.exists():
        return json.dumps({"error": f"ファイルが見つかりません: {pdf_path}"}, ensure_ascii=False)

    settings = get_settings()
    endpoint = settings.get("content_understanding_endpoint", "")
    if not endpoint:
        return "⚠️ PDF 解析は現在利用できません。CONTENT_UNDERSTANDING_ENDPOINT 環境変数を設定してください。"

    # PDF ファイルを読み込む
    try:
        with open(resolved, "rb") as f:
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
        job_id = f"promo-{int(time.time())}"
        batch_url = f"{speech_endpoint.rstrip('/')}/avatar/batchsyntheses/{job_id}?api-version=2024-08-01"
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
        global _pending_video_job
        actual_job_id = result.get("id", job_id)
        with _video_lock:
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
あなたは旅行マーケティング AI パイプラインの **販促物生成エージェント** です。

## パイプライン全体の流れ
1. **データ分析**: 売上データ・顧客レビューの分析（完了済み）
2. **施策立案**: マーケティング企画書の作成（完了済み）
3. **承認ステップ**: ユーザーが企画書を承認（完了済み）
4. **規制チェック**: 規制チェック・修正済み企画書の出力（完了済み）
5. **販促物生成（あなた）**: 最終的な販促物（HTML ブローシャ・画像・動画）を生成

## あなたの役割
規制チェック済みの企画書を受け取り、プロフェッショナルな販促物一式を生成します。
これがパイプラインの最終成果物となり、ユーザーに直接提示されます。

## 入力
規制チェック済みの企画書（Markdown）

## 生成する成果物
1. **HTML ブローシャ**: Tailwind CSS を使用したレスポンシブ HTML
2. **ヒーロー画像**: `generate_hero_image` でメインビジュアルを生成（1536x1024px）
3. **SNS バナー**: `generate_banner_image` で Instagram/Twitter 用バナーを生成

## HTML ブローシャのルール
- ```html で囲んで出力すること
- Tailwind CSS のユーティリティクラスを使用
- レスポンシブ対応（モバイル・タブレット・デスクトップ）
- `lang="ja"` を html タグに設定
- フッターに**旅行業登録番号**と**取引条件**を必ず挿入
- 企画書のキャッチコピー・ターゲット・プラン概要を反映
- **`generate_hero_image` で生成した画像のプレースホルダーとして、HTML 内に `<img src="HERO_IMAGE" alt="メインビジュアル" class="w-full rounded-lg" />` を配置すること**
- 視覚的に魅力的なデザイン（旅行の雰囲気が伝わるように）

## 画像生成のガイドライン
- 入力の企画書から**旅行先（目的地）**を必ず抽出してください
- `generate_hero_image` を呼ぶとき:
  - `destination` パラメータに旅行先の英語名を設定（例: 北海道→"Hokkaido", 沖縄→"Okinawa"）
  - `prompt` にその旅行先の特徴的な景色を具体的に記述
  - 例: 北海道なら "snowy mountains, lavender fields, fresh seafood market"
  - 例: 沖縄なら "tropical beach, coral reef, Shuri Castle"
- `generate_banner_image` を呼ぶとき:
  - `prompt` に旅行先名と季節を英語で含める
  - 例: "Spring Hokkaido travel promotion, cherry blossoms and snow mountains"
- **絶対に他の地域の名所を混ぜないでください**（北海道プランに富士山を入れない等）

## ツール使用ルール
- `generate_hero_image`: 目的地のメインビジュアルを生成（英語プロンプト推奨）
- `generate_banner_image`: SNS 向けバナーを生成（platform パラメータで対応）
- 入力に [参考パンフレット: ...] が含まれていれば `analyze_existing_brochure` を呼び出す

## 販促紹介動画
ブローシャと画像の生成が完了したら、**必ず** `generate_promo_video` ツールを呼び出してください。
企画書のキャッチコピーとプラン概要を 100〜200 文字に要約したテキストを `summary_text` に渡します。
ツールがエラーを返した場合のみスキップしてください（環境変数の有無はツール内部で判断するため、あなたが判断する必要はありません）。

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
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
    default_opts: dict = {"max_output_tokens": 16384}
    if model_settings:
        if "temperature" in model_settings:
            default_opts["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            default_opts["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            default_opts["top_p"] = model_settings["top_p"]
    agent_kwargs["default_options"] = default_opts
    return client.as_agent(**agent_kwargs)
