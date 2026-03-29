"""Agent3: レギュレーションチェックエージェント。企画書の法令・規制適合性を確認する。"""

import asyncio
import json
import logging
import urllib.request

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)

# --- AIProjectClient（遅延初期化シングルトン — Foundry IQ KB 検索用） ---

_project_client: object | None = None
_project_client_initialized: bool = False

# Foundry IQ ナレッジベースのインデックス名
_KB_INDEX_NAME = "regulations-index"


def _get_project_client():
    """AIProjectClient を遅延初期化で取得する。未設定・失敗時は None。"""
    global _project_client, _project_client_initialized
    if _project_client_initialized:
        return _project_client
    _project_client_initialized = True
    try:
        settings = get_settings()
        endpoint = settings["project_endpoint"]
        if not endpoint:
            logger.info("project_endpoint 未設定、Foundry IQ は無効")
            return None
        from azure.ai.projects import AIProjectClient

        _project_client = AIProjectClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        logger.info("AIProjectClient を初期化しました")
        return _project_client
    except Exception as e:
        logger.warning("AIProjectClient 初期化失敗: %s", e)
        return None


# --- NG 表現リスト（Foundry IQ 未接続時のフォールバック） ---

NG_EXPRESSIONS = [
    {"expression": "最安値", "reason": "景品表示法 - 有利誤認のおそれ", "suggestion": "お得な価格帯"},
    {"expression": "業界No.1", "reason": "景品表示法 - 優良誤認のおそれ", "suggestion": "多くのお客様に選ばれている"},
    {"expression": "絶対", "reason": "景品表示法 - 断定的表現", "suggestion": "きっと（推量表現に変更）"},
    {"expression": "完全保証", "reason": "景品表示法 - 有利誤認のおそれ", "suggestion": "充実のサポート体制"},
    {
        "expression": "今だけ",
        "reason": "景品表示法 - 有利誤認（期間限定の根拠が必要）",
        "suggestion": "期間限定（具体的な期日を明記）",
    },
]

TRAVEL_LAW_CHECKLIST = [
    "書面交付義務: 取引条件を書面で明示しているか",
    "広告表示規制: 旅行業者の登録番号を表示しているか",
    "取引条件明示: 旅行代金・日程・宿泊先・交通手段を明記しているか",
    "取消料規定: キャンセル料の規定を明記しているか",
    "企画旅行: 主催旅行会社の責任範囲を明記しているか",
]


# --- フォールバック・ヘルパー ---


def _get_fallback_regulations(query: str) -> str:
    """Foundry IQ 未接続時のフォールバック規制データを返す。"""
    return json.dumps(
        {
            "source": "フォールバックデータ（Foundry IQ 未接続時）",
            "query": query,
            "ng_expressions": NG_EXPRESSIONS,
            "travel_law_checklist": TRAVEL_LAW_CHECKLIST,
            "note": "Foundry IQ Knowledge Base 接続後は実データを検索します",
        },
        ensure_ascii=False,
    )


@tool
async def search_knowledge_base(query: str) -> str:
    """Foundry IQ ナレッジベースから規制・法令情報を検索する。

    Args:
        query: 検索クエリ（例: 「景品表示法 有利誤認」「旅行業法 広告規制」）
    """
    client = _get_project_client()
    if client is None:
        logger.info("Foundry IQ KB 未接続、フォールバック使用")
        return _get_fallback_regulations(query)
    try:
        from azure.ai.projects.models import ConnectionType

        conn = client.connections.get_default(
            connection_type=ConnectionType.AZURE_AI_SEARCH,
            include_credentials=True,
        )
        if conn is None:
            raise ValueError("Azure AI Search 接続が未構成です")

        # Azure AI Search REST API — DefaultAzureCredential (MI) で認証
        search_endpoint = conn.endpoint_url.rstrip("/")
        search_url = f"{search_endpoint}/indexes/{_KB_INDEX_NAME}/docs/search?api-version=2024-07-01"
        body = json.dumps({"search": query, "top": 5, "queryType": "simple"}).encode()

        credential = DefaultAzureCredential()
        token = credential.get_token("https://search.azure.com/.default")

        req = urllib.request.Request(
            search_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token.token}",
            },
            method="POST",
        )
        response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=15)
        data = json.loads(response.read().decode())

        results = []
        for doc in data.get("value", []):
            content = doc.get("content", doc.get("chunk", ""))
            title = doc.get("title", "")
            results.append({"title": title, "content": content[:500]})

        if not results:
            logger.info("Foundry IQ KB 検索結果なし、フォールバック使用")
            return _get_fallback_regulations(query)

        return json.dumps(
            {
                "source": "Foundry IQ Knowledge Base",
                "query": query,
                "results": results,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.warning("Foundry IQ KB 検索失敗: %s", e)
        return _get_fallback_regulations(query)


@tool
async def check_ng_expressions(text: str) -> str:
    """テキスト内の NG 表現（禁止表現）を検出する。

    Args:
        text: チェック対象のテキスト
    """
    found = []
    for ng in NG_EXPRESSIONS:
        if ng["expression"] in text:
            found.append(ng)
    return json.dumps(found, ensure_ascii=False) if found else "NG 表現は検出されませんでした。"


@tool
async def check_travel_law_compliance(document: str) -> str:
    """旅行業法の必須記載事項の適合性をチェックする。

    Args:
        document: チェック対象の企画書テキスト
    """
    results = []
    for item in TRAVEL_LAW_CHECKLIST:
        keyword = item.split(":")[0].strip()
        found = keyword in document or any(w in document for w in keyword.split("・"))
        status = "✅ 適合" if found else "⚠️ 要確認"
        results.append({"check_item": item, "status": status})
    return json.dumps(results, ensure_ascii=False)


INSTRUCTIONS = """\
あなたは旅行業界の法規制チェックエージェントです。
Agent2（施策生成エージェント）が作成した企画書を受け取り、以下の観点でレギュレーションチェックを行ってください。

## チェック項目
1. **旅行業法チェック**: 書面交付義務・広告表示規制・取引条件明示の適合性
2. **景品表示法チェック**: 有利誤認・優良誤認・二重価格表示の違反がないか
3. **ブランドガイドラインチェック**: トーン＆マナー・ロゴ使用規定への準拠
4. **NG 表現検出**: 禁止表現（「最安値」「業界No.1」「絶対」等）の検出
5. **ナレッジベース検索**: Foundry IQ で旅行業界の規制・ガイドラインを検索
6. **外部安全情報**: 目的地の外務省危険情報・気象警報（Web Search で最新情報を確認すること）

## 出力フォーマット（Markdown）
1. チェック結果一覧（✅ 適合 / ⚠️ 要修正 / ❌ 違反）
2. 違反・要修正箇所の具体的な指摘
3. 修正提案（元の表現 → 修正案）
4. 修正を反映した企画書（Markdown）

必ず `check_ng_expressions` と `check_travel_law_compliance` ツールを使ってチェックしてください。
`search_knowledge_base` ツールで関連する規制・法令のナレッジを検索し、チェックの精度を高めてください。
"""


def create_regulation_check_agent(model_settings: dict | None = None):
    """レギュレーションチェックエージェントを作成する"""
    settings = get_settings()
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
        deployment_name=settings["model_name"],
    )

    # Foundry 組み込み Web Search（安全情報検索用 — Bing リソース不要）
    agent_tools: list = [
        check_ng_expressions,
        check_travel_law_compliance,
        search_knowledge_base,
        client.get_web_search_tool(
            user_location={"country": "JP", "region": "Tokyo"},
            search_context_size="medium",
        ),
    ]

    agent_kwargs: dict = {
        "name": "regulation-check-agent",
        "instructions": INSTRUCTIONS,
        "tools": agent_tools,
    }
    if model_settings:
        if "temperature" in model_settings:
            agent_kwargs["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            agent_kwargs["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            agent_kwargs["top_p"] = model_settings["top_p"]
    return client.as_agent(**agent_kwargs)
