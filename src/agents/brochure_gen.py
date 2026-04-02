"""Agent4: ブローシャ＆画像生成エージェント。HTML ブローシャとバナー画像を生成する。"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import urllib.request
from pathlib import Path

from agent_framework import tool
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

        response = await asyncio.wait_for(asyncio.to_thread(_sync_generate), timeout=30)

        # Responses API の出力から画像データを抽出
        image_items = [item for item in (response.output or []) if item.type == "image_generation_call"]
        if not image_items or not getattr(image_items[0], "result", None):
            out_types = [i.type for i in (response.output or [])]
            logger.warning("画像データなし。出力タイプ: %s", out_types)
            return _FALLBACK_IMAGE

        b64_data = image_items[0].result
        return f"data:image/png;base64,{b64_data}"
    except TimeoutError:
        logger.warning("画像生成タイムアウト（30秒）。フォールバック画像を返します")
        return _FALLBACK_IMAGE
    except (ValueError, OSError) as exc:
        logger.warning("画像生成に失敗。フォールバック画像を返します: %s", exc)
        return _FALLBACK_IMAGE
    except Exception as exc:
        logger.exception("画像生成で予期しないエラー。フォールバック画像を返します: %s", exc)
        return _FALLBACK_IMAGE


# --- Side-channel 画像ストア ---
# 画像の base64 をツール出力に含めるとコンテキストウインドウを超過するため、
# side-channel で保存し、パイプライン完了後に別途取得する（social-ai-studio パターン）
# conversation_id でスコープし、並行リクエスト間の競合を防止する
_images_lock = threading.Lock()
_pending_images: dict[str, dict[str, str]] = {}
_conversation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "brochure_conversation_id",
    default="",
)


def set_current_conversation_id(conversation_id: str) -> None:
    """現在実行中の conversation_id を設定する。"""
    _conversation_id_var.set(conversation_id)


def _get_current_conversation_id() -> str:
    """現在の非同期コンテキストに紐づく conversation_id を返す。"""
    return _conversation_id_var.get()


def pop_pending_images(conversation_id: str) -> dict[str, str]:
    """保存済み画像を取得しクリアする（スレッドセーフ）。"""
    with _images_lock:
        images = _pending_images.pop(conversation_id, {})
        return images


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
    conversation_id = _get_current_conversation_id()
    with _images_lock:
        _pending_images.setdefault(conversation_id, {})["hero"] = data_uri
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
    conversation_id = _get_current_conversation_id()
    with _images_lock:
        _pending_images.setdefault(conversation_id, {})[f"banner_{platform}"] = data_uri
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


INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **販促物生成エージェント** です。

## パイプライン全体の流れ
1. **データ分析**: 売上データ・顧客レビューの分析（完了済み）
2. **施策立案**: マーケティング企画書の作成（完了済み）
3. **承認ステップ**: ユーザーが企画書を承認（完了済み）
4. **規制チェック**: 規制チェック・修正済み企画書の出力（完了済み）
5. **販促物生成（あなた）**: 最終的な販促物（HTML ブローシャ・画像）を生成

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
- 企画書のキャッチコピー・プラン概要を反映
- **`generate_hero_image` で生成した画像のプレースホルダーとして、HTML 内に `<img src="HERO_IMAGE" alt="メインビジュアル" class="w-full rounded-lg" />` を配置すること**
- 視覚的に魅力的なデザイン（旅行の雰囲気が伝わるように）

## ブローシャの対象読者: 旅行を検討している一般顧客
**ブローシャは顧客向けの販促資料**です。以下のルールを厳守してください:
- **含めるべき情報**: プラン名、キャッチコピー、旅行先の魅力、日程・価格帯、含まれるサービス、予約方法、お問い合わせ先
- **含めてはいけない情報（社内向け）**: KPI、目標予約数、売上目標、前年比、セグメント分析、ターゲットペルソナの詳細分析、改善ポイント、販促チャネル戦略、競合分析
- トーンは「お客様への提案」であり、「社内企画書の転載」ではありません
- 価格は「○○円〜（税込）」のように顧客にわかりやすく表記すること

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

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
"""


def create_brochure_gen_agent(model_settings: dict | None = None):
    """ブローシャ＆画像生成エージェントを作成する"""
    from src.agent_client import get_responses_client

    deployment = None
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = get_responses_client(deployment)

    agent_tools: list = [
        generate_hero_image,
        generate_banner_image,
        analyze_existing_brochure,
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
