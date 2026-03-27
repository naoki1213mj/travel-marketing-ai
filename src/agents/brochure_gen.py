"""Agent4: ブローシャ＆画像生成エージェント。HTML ブローシャとバナー画像を生成する。"""

from agent_framework import AzureOpenAIResponsesClient, tool
from azure.identity import DefaultAzureCredential

from src.config import get_settings

# --- ツール定義 ---

@tool
async def generate_hero_image(
    prompt: str,
    destination: str,
    style: str = "photorealistic",
) -> str:
    """GPT Image 1.5 でヒーロー画像を生成する。

    Args:
        prompt: 画像生成プロンプト（英語推奨）
        destination: 旅行先の地名
        style: 画像スタイル（photorealistic/illustration/watercolor）
    """
    # TODO: GPT Image 1.5 API 呼び出しに置き換え
    # 現在はプレースホルダーの 1x1 PNG を返す
    return (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )


@tool
async def generate_banner_image(
    prompt: str,
    platform: str = "instagram",
) -> str:
    """GPT Image 1.5 で SNS バナー画像を生成する。

    Args:
        prompt: 画像生成プロンプト（英語推奨）
        platform: SNS プラットフォーム（instagram/twitter/facebook）
    """
    # TODO: GPT Image 1.5 API 呼び出しに置き換え
    return (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )


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
    )
    return client.as_agent(
        name="brochure-gen-agent",
        instructions=INSTRUCTIONS,
        tools=[generate_hero_image, generate_banner_image],
        model=settings["model_name"],
    )
