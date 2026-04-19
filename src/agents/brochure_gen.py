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
from src.tool_telemetry import trace_tool_invocation

logger = logging.getLogger(__name__)

# 1x1 透明 PNG（フォールバック用）
_FALLBACK_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)

# 画像生成モデル名（デフォルト値）
_DEFAULT_IMAGE_MODEL = "gpt-image-1.5"

# 利用可能な画像生成モデル
AVAILABLE_IMAGE_MODELS = {
    "gpt-image-1.5": {
        "label": "GPT Image 1.5",
        "format": "openai",
        "sizes": ["1024x1024", "1024x1536", "1536x1024"],
        "qualities": ["low", "medium", "high"],
    },
    "MAI-Image-2": {
        "label": "MAI-Image-2",
        "format": "mai",
        "min_dimension": 768,
        "max_pixels": 1_048_576,
    },
}

_BANNER_PLATFORM_ALIASES = {
    "twitter": "x",
}

_BANNER_PLATFORM_SPECS = {
    "instagram": {
        "label": "Instagram",
        "gpt_size": "1024x1024",
        "mai_size": "1024x1024",
        "display_aspect_ratio": "1:1",
        "prompt_suffix": (
            "Square Instagram campaign creative, centered focal subject,"
            " leave clean negative space for copy, no embedded text, no logos."
        ),
    },
    "x": {
        "label": "X",
        "gpt_size": "1536x1024",
        "mai_size": "1365x768",
        "display_aspect_ratio": "1.91:1",
        "prompt_suffix": (
            "Wide X social banner, cinematic horizontal composition,"
            " leave generous left and right safe margins for copy,"
            " no embedded text, no logos."
        ),
    },
    "facebook": {
        "label": "Facebook",
        "gpt_size": "1536x1024",
        "mai_size": "1344x768",
        "display_aspect_ratio": "1.75:1",
        "prompt_suffix": (
            "Wide Facebook travel promotion creative, balanced horizontal composition,"
            " leave clear safe space for copy, no embedded text, no logos."
        ),
    },
}

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

# 画像設定コンテキスト変数（ツール関数から参照）
_image_settings_var: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "brochure_image_settings",
    default={},
)

# モジュールレベルフォールバック（Agent Framework がコンテキスト変数をコピーしない場合の保険）
_image_settings_fallback: dict = {}


def set_current_image_settings(settings: dict) -> None:
    """現在の画像生成設定をコンテキスト変数にセットする。"""
    global _image_settings_fallback
    _image_settings_var.set(settings)
    _image_settings_fallback = settings


def _get_current_image_settings() -> dict:
    """現在の非同期コンテキストに紐づく画像設定を返す。"""
    settings = _image_settings_var.get()
    if not settings and _image_settings_fallback:
        logger.info("コンテキスト変数が空。モジュールレベルフォールバックを使用: %s", _image_settings_fallback)
        return _image_settings_fallback
    return settings


def _get_image_openai_client(deployment: str = _DEFAULT_IMAGE_MODEL):
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
                    "x-ms-oai-image-generation-deployment": deployment,
                },
            )
            logger.info("Responses API 画像クライアント作成: base_url=%s, deployment=%s", base_url, deployment)
        except (ImportError, ValueError, OSError) as exc:
            logger.warning("画像クライアント初期化失敗: %s", exc)
            _image_openai_client = None
        except Exception as exc:
            logger.exception("画像クライアント初期化で予期しないエラー: %s", exc)
            _image_openai_client = None
    return _image_openai_client


async def _generate_image(prompt: str, size: str = "1024x1024") -> str:
    """画像設定に応じて適切なモデルで画像を生成し、data URI を返す。"""
    img_settings = _get_current_image_settings()
    image_model = img_settings.get("image_model", _DEFAULT_IMAGE_MODEL)

    logger.info("画像生成ディスパッチ: model=%s, settings=%s", image_model, img_settings)

    model_info = AVAILABLE_IMAGE_MODELS.get(image_model)
    if model_info and model_info["format"] == "mai":
        width, height = _parse_size_for_mai(size, img_settings)
        logger.info("MAI-Image-2 パス: width=%d, height=%d", width, height)
        return await _generate_image_mai(prompt, width, height)

    quality = img_settings.get("image_quality", "medium")
    logger.info("GPT パス: size=%s, quality=%s", size, quality)
    return await _generate_image_gpt(prompt, size, quality)


def _parse_size_for_mai(size: str, img_settings: dict) -> tuple[int, int]:
    """GPT 形式の size 文字列または MAI 設定から width/height を導出する。"""
    # UI から明示的に指定されていればそれを使う
    if img_settings.get("image_width") and img_settings.get("image_height"):
        w = int(img_settings["image_width"])
        h = int(img_settings["image_height"])
    else:
        # GPT の size 文字列をパースし、MAI の制約内に収める
        parts = size.split("x")
        w, h = int(parts[0]), int(parts[1])

    # MAI 制約: 最小 768px、w×h ≤ 1,048,576
    w = max(768, w)
    h = max(768, h)
    max_pixels = AVAILABLE_IMAGE_MODELS["MAI-Image-2"]["max_pixels"]
    if w * h > max_pixels:
        # アスペクト比を維持しつつ制約内に収める
        ratio = (max_pixels / (w * h)) ** 0.5
        w = max(768, int(w * ratio))
        h = max(768, int(h * ratio))
    return w, h


def _normalize_banner_platform(platform: str) -> str:
    """バナーの platform 名を正規化する。"""
    normalized = platform.strip().lower() if platform else "instagram"
    normalized = _BANNER_PLATFORM_ALIASES.get(normalized, normalized)
    return normalized if normalized in _BANNER_PLATFORM_SPECS else "instagram"


def _get_banner_platform_spec(platform: str) -> dict[str, str]:
    """platform ごとのバナー仕様を返す。"""
    normalized = _normalize_banner_platform(platform)
    img_settings = _get_current_image_settings()
    image_model = img_settings.get("image_model", _DEFAULT_IMAGE_MODEL)
    base_spec = _BANNER_PLATFORM_SPECS[normalized]
    size = base_spec["mai_size"] if image_model == "MAI-Image-2" else base_spec["gpt_size"]
    return {
        "platform": normalized,
        "label": base_spec["label"],
        "size": size,
        "display_aspect_ratio": base_spec["display_aspect_ratio"],
        "prompt_suffix": base_spec["prompt_suffix"],
    }


async def _generate_image_gpt(prompt: str, size: str = "1024x1024", quality: str = "medium") -> str:
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
                tools=[{"type": "image_generation", "size": size, "quality": quality}],
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


async def _generate_image_mai(prompt: str, width: int = 1024, height: int = 1024) -> str:
    """MAI Image API で画像を生成し、data URI を返す。

    API: POST /mai/v1/images/generations
    認証: Azure AD トークン（https://cognitiveservices.azure.com/.default）
    """
    try:
        settings = get_settings()
        endpoint = settings.get("image_project_endpoint_mai", "")
        if not endpoint:
            logger.info("IMAGE_PROJECT_ENDPOINT_MAI 未設定。フォールバック画像を返します")
            return _FALLBACK_IMAGE

        api_url = f"{endpoint.rstrip('/')}/mai/v1/images/generations"

        # MAI-Image-2 はリソース直接アクセスのため cognitiveservices スコープを使用
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        body = json.dumps(
            {
                "model": "MAI-Image-2",
                "prompt": prompt,
                "width": width,
                "height": height,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        def _sync_request():
            with urllib.request.urlopen(request, timeout=60) as resp:
                resp_body = resp.read().decode("utf-8")
                logger.info("MAI-Image-2 API レスポンス: status=%d, body_len=%d", resp.status, len(resp_body))
                return json.loads(resp_body)

        result = await asyncio.wait_for(asyncio.to_thread(_sync_request), timeout=90)

        data_list = result.get("data", [])
        if not data_list or not data_list[0].get("b64_json"):
            logger.warning("MAI-Image-2: 画像データなし。レスポンスキー: %s", list(result.keys()))
            return _FALLBACK_IMAGE

        b64_data = data_list[0]["b64_json"]
        logger.info("MAI-Image-2 画像生成成功: b64_len=%d", len(b64_data))
        return f"data:image/png;base64,{b64_data}"
    except TimeoutError:
        logger.warning("MAI-Image-2 画像生成タイムアウト（90秒）。フォールバック画像を返します")
        return _FALLBACK_IMAGE
    except (ValueError, OSError, urllib.error.URLError) as exc:
        logger.warning("MAI-Image-2 画像生成に失敗。フォールバック画像を返します: %s", exc)
        return _FALLBACK_IMAGE
    except Exception as exc:
        logger.exception("MAI-Image-2 画像生成で予期しないエラー。フォールバック画像を返します: %s", exc)
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
    async with trace_tool_invocation("generate_hero_image", agent_name="brochure-gen-agent"):
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
    async with trace_tool_invocation("generate_banner_image", agent_name="brochure-gen-agent"):
        spec = _get_banner_platform_spec(platform)
        full_prompt = f"{prompt}. {spec['prompt_suffix']}"
        data_uri = await _generate_image(full_prompt, spec["size"])
        conversation_id = _get_current_conversation_id()
        with _images_lock:
            _pending_images.setdefault(conversation_id, {})[f"banner_{spec['platform']}"] = data_uri
        return json.dumps(
            {
                "status": "generated",
                "type": "banner",
                "platform": spec["platform"],
                "size": spec["size"],
                "display_aspect_ratio": spec["display_aspect_ratio"],
                "message": f"{spec['label']} 用バナーを生成しました。",
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
    async with trace_tool_invocation("analyze_existing_brochure", agent_name="brochure-gen-agent"):
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
3. **SNS バナー**: `generate_banner_image` で Instagram と X 用バナーを生成

## HTML ブローシャのルール
- ```html で囲んで出力すること
- Tailwind CSS のユーティリティクラスを使用
- レスポンシブ対応（モバイル・タブレット・デスクトップ）
- `lang="ja"` を html タグに設定
- フッターに**旅行業登録番号**と**取引条件**を必ず挿入
- 企画書のキャッチコピー・プラン概要を反映
- **`generate_hero_image` で生成した画像のプレースホルダーとして、HTML 内に `<img src="HERO_IMAGE" alt="メインビジュアル" class="w-full rounded-lg" />` を配置すること**
- **SNS バナー用のセクションを作り、HTML 内に `<img src="INSTAGRAM_BANNER_IMAGE" alt="Instagramバナー" />` と `<img src="X_BANNER_IMAGE" alt="Xバナー" />` を配置すること**
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
    - **必ず 2 回**呼ぶ: `platform="instagram"` と `platform="x"`
    - `prompt` に旅行先名と季節を英語で含める
    - Instagram: 正方形、中央に主題、コピーを載せる余白、テキストなし
    - X: 横長、シネマティックな横構図、左右にコピー用の余白、テキストなし
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
