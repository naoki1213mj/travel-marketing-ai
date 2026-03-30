"""Agent3: レギュレーションチェックエージェント。企画書の法令・規制適合性を確認する。"""

import asyncio
import json
import logging
import os
import urllib.request

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)

# --- Foundry IQ Agentic Retrieval 設定 ---

# Knowledge Base 名（setup_knowledge_base.py で作成）
_KB_NAME = "regulations-kb"
_KB_API_VERSION = "2025-11-01-preview"

# Search エンドポイントのキャッシュ
_search_endpoint: str | None = None
_search_api_key: str | None = None
_search_initialized: bool = False


def _get_search_credentials() -> tuple[str, str]:
    """Azure AI Search のエンドポイントと API key を取得する。"""
    global _search_endpoint, _search_api_key, _search_initialized
    if _search_initialized:
        return _search_endpoint or "", _search_api_key or ""
    _search_initialized = True

    # 環境変数から直接取得
    ep = os.environ.get("SEARCH_ENDPOINT", "")
    key = os.environ.get("SEARCH_API_KEY", "")
    if ep and key:
        _search_endpoint = ep.rstrip("/")
        _search_api_key = key
        return _search_endpoint, _search_api_key

    # Foundry project connection から取得
    try:
        settings = get_settings()
        endpoint = settings["project_endpoint"]
        if not endpoint:
            return "", ""
        from azure.ai.projects import AIProjectClient
        from azure.ai.projects.models import ConnectionType

        client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
        conn = client.connections.get_default(
            connection_type=ConnectionType.AZURE_AI_SEARCH,
            include_credentials=True,
        )
        if conn is None:
            return "", ""

        _search_endpoint = (getattr(conn, "target", "") or "").rstrip("/")
        credentials = getattr(conn, "credentials", None)
        _search_api_key = credentials.get("key", "") if credentials is not None and hasattr(credentials, "get") else ""
        logger.info("Search credentials 取得: endpoint=%s", _search_endpoint)
        return _search_endpoint, _search_api_key
    except Exception as e:
        logger.warning("Search credentials 取得失敗: %s", e)
        return "", ""


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
    """Foundry IQ ナレッジベースから規制・法令情報を検索する（Agentic Retrieval API）。

    Args:
        query: 検索クエリ（例: 「景品表示法 有利誤認」「旅行業法 広告規制」）
    """
    search_endpoint, api_key = _get_search_credentials()
    if not search_endpoint:
        logger.info("Search endpoint 未設定、フォールバック使用")
        return _get_fallback_regulations(query)

    try:
        # Agentic Retrieval API で Knowledge Base にクエリを送信
        url = f"{search_endpoint}/knowledgebases/{_KB_NAME}/retrieve?api-version={_KB_API_VERSION}"
        request_body = {
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": query}],
            }],
            "retrievalReasoningEffort": {"kind": "low"},
            "includeActivity": True,
        }

        body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        else:
            credential = DefaultAzureCredential()
            token = credential.get_token("https://search.azure.com/.default")
            headers["Authorization"] = f"Bearer {token.token}"

        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=30)
        data = json.loads(response.read().decode())

        # Agentic Retrieval レスポンスからテキストを抽出
        responses = data.get("response", [])
        results = []
        for resp_item in responses:
            for content_item in resp_item.get("content", []):
                if content_item.get("type") == "text":
                    text = content_item.get("text", "")
                    if text.strip():
                        results.append({"content": text[:2000], "source": "Foundry IQ Agentic Retrieval"})

        # 参照情報を追加
        references = data.get("references", [])
        ref_summaries = []
        for ref in references[:5]:
            title = ref.get("title", "")
            score = ref.get("rerankerScore", 0)
            if title:
                ref_summaries.append({"title": title, "score": score})

        if not results:
            logger.info("Foundry IQ KB 検索結果なし、フォールバック使用")
            return _get_fallback_regulations(query)

        return json.dumps(
            {
                "source": "Foundry IQ Agentic Retrieval",
                "knowledge_base": _KB_NAME,
                "query": query,
                "results": results,
                "references": ref_summaries,
            },
            ensure_ascii=False,
        )
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning("Foundry IQ KB 検索失敗 (HTTP %d): %s", e.code, error_body)
        # KB が未作成 (404) の場合は直接 Index 検索にフォールバック
        if e.code == 404:
            return await _fallback_index_search(query, search_endpoint, api_key)
        return _get_fallback_regulations(query)
    except Exception as e:
        logger.warning("Foundry IQ KB 検索失敗: %s", e)
        return _get_fallback_regulations(query)


async def _fallback_index_search(query: str, search_endpoint: str, api_key: str) -> str:
    """KB が未作成の場合に直接 Index を検索するフォールバック。"""
    try:
        url = f"{search_endpoint}/indexes/regulations-index/docs/search?api-version=2024-07-01"
        body = json.dumps({"search": query, "top": 5, "queryType": "simple"}).encode()
        headers: dict[str, str] = {"Content-Type": "application/json", "api-key": api_key}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=15)
        data = json.loads(response.read().decode())
        results = []
        for doc in data.get("value", []):
            content = doc.get("content", doc.get("chunk", ""))
            title = doc.get("title", "")
            if content:
                results.append({"title": title, "content": content[:500]})
        if results:
            return json.dumps({"source": "Azure AI Search (直接検索)", "query": query, "results": results}, ensure_ascii=False)
    except Exception as e:
        logger.warning("Index 直接検索もも失敗: %s", e)
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
