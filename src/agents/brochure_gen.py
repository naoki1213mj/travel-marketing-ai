"""Agent4: ブローシャ＆画像生成エージェント。HTML ブローシャとバナー画像を生成する。"""

import logging

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)

# 1x1 透明 PNG（フォールバック用）
_FALLBACK_IMAGE = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
)


def _get_openai_client():
    """画像生成用の OpenAI クライアントを返す"""
    from openai import AzureOpenAI

    settings = get_settings()
    endpoint = settings["project_endpoint"].split("/api/projects/")[0]
    credential = DefaultAzureCredential()
    # get_token は同期呼び出しだが、OpenAI クライアント初期化時にのみ使われるため許容する
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    # Foundry endpoint を OpenAI 互換の endpoint に変換
    azure_endpoint = endpoint
    if ".services.ai.azure.com" in azure_endpoint:
        azure_endpoint = azure_endpoint.replace(".services.ai.azure.com", ".openai.azure.com")
    return AzureOpenAI(
        azure_endpoint=azure_endpoint,
        api_version="2025-04-01-preview",
        azure_ad_token=token.token,
    )


async def _generate_image(prompt: str, size: str = "1024x1024") -> str:
    """OpenAI Images API で画像を生成し、data URI を返す"""
    try:
        client = _get_openai_client()
        settings = get_settings()
        response = client.images.generate(
            model=settings["model_name"],
            prompt=prompt,
            n=1,
            size=size,
            response_format="b64_json",
        )
        b64_data = response.data[0].b64_json
        return f"data:image/png;base64,{b64_data}"
    except Exception:
        logger.exception("画像生成に失敗。フォールバック画像を返します")
        return _FALLBACK_IMAGE


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
    return await _generate_image(full_prompt, "1792x1024")


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
    size = "1024x1024" if platform == "instagram" else "1792x1024"
    return await _generate_image(prompt, size)


INSTRUCTIONS = """\
あなたは旅行販促物の制作エージェントです。
Agent3（規制チェック）でチェック済みの企画書を受け取り、以下の成果物を生成してください。

## 生成する成果物

### 1. HTML ブローシャ
- レスポンシブデザインの HTML で販促ブローシャを作成
- 企画書の内容をビジュアル豊かに表現
- 旅行条件（取引条件・旅行会社登録番号等）をフッターに自動挿入
- Tailwind CSS のクラスを使ったスタイリング

### 2. ヒーロー画像
- `generate_hero_image` ツールで旅行先のメインビジュアルを生成
- プロンプトは英語で記述し、旅行先の魅力を表現

### 3. SNS バナー画像
- `generate_banner_image` ツールで SNS 用バナーを生成
- Instagram / Twitter 用のサイズを考慮

## 出力ルール
- HTML ブローシャのコードは ```html で囲んで出力
- 画像は生成ツールを使い、base64 データを返す
- 旅行業法の必須記載事項（取引条件・登録番号）をフッターに含める
"""


def create_brochure_gen_agent():
    """ブローシャ＆画像生成エージェントを作成する"""
    settings = get_settings()
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
        deployment_name=settings["model_name"],
    )
    return client.as_agent(
        name="brochure-gen-agent",
        instructions=INSTRUCTIONS,
        tools=[generate_hero_image, generate_banner_image],
    )
