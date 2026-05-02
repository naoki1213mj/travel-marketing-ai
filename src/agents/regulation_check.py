"""Agent3: レギュレーションチェックエージェント。企画書の法令・規制適合性を確認する。"""

import asyncio
import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Any

from agent_framework import tool
from azure.identity import DefaultAzureCredential

from src.config import get_settings
from src.tool_telemetry import build_tool_event_data, emit_tool_event, redact_sensitive_text, trace_tool_invocation

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_evidence_quote(value: object, *, max_length: int = 220) -> str:
    normalized = redact_sensitive_text(str(value or "").replace("\n", " ").strip())
    return f"{normalized[: max_length - 1]}…" if len(normalized) > max_length else normalized


def _emit_regulation_evidence_event(
    tool_name: str,
    *,
    source: str,
    provider: str,
    evidence: list[dict[str, object]],
    charts: list[dict[str, object]] | None = None,
) -> None:
    """規制チェックで利用した根拠を追加の tool_event として送る。"""
    emit_tool_event(
        build_tool_event_data(
            tool_name,
            "completed",
            agent_name="regulation-check-agent",
            source=source,
            provider=provider,
            evidence=evidence,
            charts=charts,
        )
    )


def _fallback_regulation_evidence(query: str) -> list[dict[str, object]]:
    return [
        {
            "id": "local-regulation-rules",
            "title": "ローカル規制チェックリスト",
            "source": "local-check",
            "quote": _safe_evidence_quote(
                f"Foundry IQ 未接続時のフォールバックとして、NG 表現 {len(NG_EXPRESSIONS)} 件と旅行業法チェック {len(TRAVEL_LAW_CHECKLIST)} 件を参照。"
            ),
            "retrieved_at": _utc_now_iso(),
            "metadata": {
                "query_length": len(query),
                "ng_expression_count": len(NG_EXPRESSIONS),
                "travel_law_check_count": len(TRAVEL_LAW_CHECKLIST),
            },
        }
    ]


def _reference_evidence(references: list[dict[str, Any]], *, source: str) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for index, ref in enumerate(references[:_iq_top_k]):
        title = str(ref.get("title", "") or "規制ナレッジ").strip()
        score = float(ref.get("score") or ref.get("rerankerScore") or 0)
        if score < _iq_score_threshold:
            continue
        item: dict[str, object] = {
            "id": f"{source}-reference-{index + 1}",
            "title": title[:100],
            "source": source,
            "relevance": min(max(score, 0), 1),
            "retrieved_at": _utc_now_iso(),
            "metadata": {"knowledge_base": _KB_NAME},
        }
        url = str(ref.get("url", "") or ref.get("sourceUrl", "")).strip()
        if url:
            item["url"] = url
        evidence.append(item)
    return evidence


def _result_evidence(results: list[dict[str, Any]], *, source: str, query: str) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for index, result in enumerate(results[:_iq_top_k]):
        content = result.get("content") or result.get("chunk") or ""
        title = result.get("title") or result.get("source") or "規制ナレッジ"
        evidence.append(
            {
                "id": f"{source}-result-{index + 1}",
                "title": str(title)[:100],
                "source": source,
                "quote": _safe_evidence_quote(content),
                "relevance": 0.8,
                "retrieved_at": _utc_now_iso(),
                "metadata": {"query_length": len(query)},
            }
        )
    return evidence


def _local_check_chart(rows: list[dict[str, object]], *, title: str, source: str) -> list[dict[str, object]]:
    return [
        {
            "chart_type": "table",
            "title": title,
            "data": rows,
            "metadata": {"source": source},
        }
    ]

# --- Foundry IQ Agentic Retrieval 設定 ---

# Knowledge Base 名（setup_knowledge_base.py で作成）
_KB_NAME = "regulations-kb"
_KB_API_VERSION = "2025-11-01-preview"

# Search エンドポイントのキャッシュ
_search_endpoint: str | None = None
_search_api_key: str | None = None
_search_initialized: bool = False

# Foundry IQ 検索パラメータ（UI から設定可能）
_iq_top_k: int = 5
_iq_score_threshold: float = 0.0
_iq_reasoning_effort: str = "low"


def set_iq_search_params(
    top_k: int = 5,
    score_threshold: float = 0.0,
    reasoning_effort: str = "low",
) -> None:
    """Foundry IQ 検索パラメータを設定する（エージェント作成時に呼ばれる）。"""
    global _iq_top_k, _iq_score_threshold, _iq_reasoning_effort
    _iq_top_k = top_k
    _iq_score_threshold = score_threshold
    _iq_reasoning_effort = reasoning_effort


def _get_search_credentials() -> tuple[str, str]:
    """Azure AI Search のエンドポイントと API key を取得する。

    優先順位 (rubber-duck 監査 2026-05-02):
      1. `get_settings()` から `search_endpoint` / `search_api_key` (env var
         `SEARCH_ENDPOINT` または `AZURE_SEARCH_ENDPOINT`、`SEARCH_API_KEY`
         または `AZURE_SEARCH_API_KEY` を解決済)
      2. Foundry project connection (project endpoint 経由で credentials を取得)

    `os.environ` を直接読むと alias env var (`AZURE_SEARCH_*`) が無視され、
    `/api/ready/deep` が「configured」と表示しても runtime fallback に
    流れる「looks healthy / actually fallbacking」 不整合を起こすため、
    settings 経由で統一する。
    """
    global _search_endpoint, _search_api_key, _search_initialized
    if _search_initialized:
        return _search_endpoint or "", _search_api_key or ""
    _search_initialized = True

    # settings 経由で取得 (alias env var も拾える)
    settings = get_settings()
    ep = str(settings.get("search_endpoint", "") or "").strip()
    key = str(settings.get("search_api_key", "") or "").strip()
    if ep and key:
        _search_endpoint = ep.rstrip("/")
        _search_api_key = key
        return _search_endpoint, _search_api_key

    # Foundry project connection から取得
    try:
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
    async with trace_tool_invocation(
        "foundry_iq_search",
        agent_name="regulation-check-agent",
        source="foundry",
        provider="foundry",
    ):
        search_endpoint, api_key = _get_search_credentials()
        if not search_endpoint:
            logger.info("Search endpoint 未設定、フォールバック使用")
            _emit_regulation_evidence_event(
                "foundry_iq_search",
                source="foundry",
                provider="foundry",
                evidence=_fallback_regulation_evidence(query),
                charts=_local_check_chart(
                    [{"category": "NG expressions", "count": len(NG_EXPRESSIONS)}, {"category": "Travel law checks", "count": len(TRAVEL_LAW_CHECKLIST)}],
                    title="ローカル規制チェック項目",
                    source="local-check",
                ),
            )
            return _get_fallback_regulations(query)

        try:
            # Agentic Retrieval API で Knowledge Base にクエリを送信
            url = f"{search_endpoint}/knowledgebases/{_KB_NAME}/retrieve?api-version={_KB_API_VERSION}"
            request_body = {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": query}],
                    }
                ],
                "retrievalReasoningEffort": {"kind": _iq_reasoning_effort},
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

            # 参照情報を追加（IQ パラメータでフィルタリング）
            references = data.get("references", [])
            ref_summaries = []
            for ref in references[:_iq_top_k]:
                title = ref.get("title", "")
                score = ref.get("rerankerScore", 0)
                if title and score >= _iq_score_threshold:
                    ref_summaries.append({"title": title, "score": score, "url": ref.get("url", "") or ref.get("sourceUrl", "")})

            if not results:
                logger.info("Foundry IQ KB 検索結果なし、フォールバック使用")
                _emit_regulation_evidence_event(
                    "foundry_iq_search",
                    source="foundry",
                    provider="foundry",
                    evidence=_fallback_regulation_evidence(query),
                )
                return _get_fallback_regulations(query)

            evidence = [*_result_evidence(results, source="foundry_iq", query=query), *_reference_evidence(ref_summaries, source="foundry_iq")]
            _emit_regulation_evidence_event(
                "foundry_iq_search",
                source="foundry",
                provider="foundry",
                evidence=evidence,
                charts=_local_check_chart(
                    [{"title": item.get("title", ""), "score": item.get("score", 0)} for item in ref_summaries],
                    title="Foundry IQ 参照スコア",
                    source="foundry_iq",
                )
                if ref_summaries
                else None,
            )
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
            _emit_regulation_evidence_event(
                "foundry_iq_search",
                source="foundry",
                provider="foundry",
                evidence=_fallback_regulation_evidence(query),
            )
            return _get_fallback_regulations(query)
        except Exception as e:
            logger.warning("Foundry IQ KB 検索失敗: %s", e)
            _emit_regulation_evidence_event(
                "foundry_iq_search",
                source="foundry",
                provider="foundry",
                evidence=_fallback_regulation_evidence(query),
            )
            return _get_fallback_regulations(query)


async def _fallback_index_search(query: str, search_endpoint: str, api_key: str) -> str:
    """KB が未作成の場合に直接 Index を検索するフォールバック。"""
    try:
        url = f"{search_endpoint}/indexes/regulations-index/docs/search?api-version=2024-07-01"
        body = json.dumps({"search": query, "top": _iq_top_k, "queryType": "simple"}).encode()
        headers: dict[str, str] = {"Content-Type": "application/json", "api-key": api_key}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=15)
        data = json.loads(response.read().decode())
        results = []
        for doc in data.get("value", []):
            content = doc.get("content", doc.get("chunk", ""))
            title = doc.get("title", "")
            if content:
                results.append({"title": title, "content": content[:500], "url": doc.get("url", "") or doc.get("source_url", "")})
        if results:
            _emit_regulation_evidence_event(
                "foundry_iq_search",
                source="foundry",
                provider="foundry",
                evidence=_result_evidence(results, source="azure_ai_search", query=query),
                charts=_local_check_chart(
                    [{"title": row.get("title", ""), "source": "Azure AI Search"} for row in results],
                    title="Azure AI Search 直接検索結果",
                    source="azure_ai_search",
                ),
            )
            return json.dumps(
                {"source": "Azure AI Search (直接検索)", "query": query, "results": results}, ensure_ascii=False
            )
    except Exception as e:
        logger.warning("Index 直接検索もも失敗: %s", e)
    _emit_regulation_evidence_event(
        "foundry_iq_search",
        source="foundry",
        provider="foundry",
        evidence=_fallback_regulation_evidence(query),
    )
    return _get_fallback_regulations(query)


@tool
async def check_ng_expressions(text: str) -> str:
    """テキスト内の NG 表現（禁止表現）を検出する。

    Args:
        text: チェック対象のテキスト
    """
    async with trace_tool_invocation("check_ng_expressions", agent_name="regulation-check-agent"):
        found = []
        for ng in NG_EXPRESSIONS:
            if ng["expression"] in text:
                found.append(ng)
        _emit_regulation_evidence_event(
            "check_ng_expressions",
            source="local",
            provider="local",
            evidence=[
                {
                    "id": "local-ng-expression-dictionary",
                    "title": "NG 表現辞書",
                    "source": "local-check",
                    "quote": _safe_evidence_quote(
                        "検出: " + "、".join(item["expression"] for item in found)
                        if found
                        else "禁止表現リストを照合し、該当表現は検出されませんでした。"
                    ),
                    "retrieved_at": _utc_now_iso(),
                    "metadata": {"matched_count": len(found), "rule_count": len(NG_EXPRESSIONS)},
                }
            ],
            charts=_local_check_chart(
                [{"expression": item["expression"], "reason": item["reason"], "suggestion": item["suggestion"]} for item in found],
                title="検出された NG 表現",
                source="local-check",
            )
            if found
            else None,
        )
        return json.dumps(found, ensure_ascii=False) if found else "NG 表現は検出されませんでした。"


@tool
async def check_travel_law_compliance(document: str) -> str:
    """旅行業法の必須記載事項の適合性をチェックする。

    Args:
        document: チェック対象の企画書テキスト
    """
    async with trace_tool_invocation("check_travel_law_compliance", agent_name="regulation-check-agent"):
        results = []
        for item in TRAVEL_LAW_CHECKLIST:
            keyword = item.split(":")[0].strip()
            found = keyword in document or any(w in document for w in keyword.split("・"))
            status = "✅ 適合" if found else "⚠️ 要確認"
            results.append({"check_item": item, "status": status})
        _emit_regulation_evidence_event(
            "check_travel_law_compliance",
            source="local",
            provider="local",
            evidence=[
                {
                    "id": "local-travel-law-checklist",
                    "title": "旅行業法チェックリスト",
                    "source": "local-check",
                    "quote": _safe_evidence_quote(f"{len(results)} 項目を確認し、{sum(1 for row in results if row['status'].startswith('✅'))} 項目が適合判定です。"),
                    "retrieved_at": _utc_now_iso(),
                    "metadata": {"check_count": len(results)},
                }
            ],
            charts=_local_check_chart(results, title="旅行業法チェック結果", source="local-check"),
        )
        return json.dumps(results, ensure_ascii=False)


INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **規制チェックエージェント** です。

## パイプライン全体の流れ
1. **データ分析**: 売上データ・顧客レビューの分析（完了済み）
2. **施策立案**: マーケティング企画書の作成（完了済み）
3. **承認ステップ**: ユーザーが企画書を承認（完了済み）
4. **規制チェック（あなた）**: 承認された企画書の法令・規制適合性を検証
5. **販促物生成**: あなたの修正提案を反映した販促物を生成

## あなたの役割
提出された企画書を受け取り、日本の旅行業関連法令・規制に適合しているかを
徹底的にチェックします。違反があれば具体的な修正提案を行います。
あなたの出力は後続の修正エージェントに渡されるため、正確性が極めて重要です。

## 入力
ユーザーが承認した企画書（Markdown）

## チェック項目（全項目を必ず実施）
1. **旅行業法チェック**: 書面交付義務・広告表示規制・取引条件明示の適合性
2. **景品表示法チェック**: 有利誤認・優良誤認・二重価格表示の違反がないか
3. **ブランドガイドラインチェック**: トーン＆マナー・ロゴ使用規定への準拠
4. **NG 表現検出**: 禁止表現（「最安値」「業界No.1」「絶対」等）の検出
5. **ナレッジベース検索**: Foundry IQ で旅行業界の規制・ガイドラインを検索
6. **外部安全情報**: 目的地の外務省危険情報・気象警報

## 重要: チェック結果のみ出力すること
修正済みの企画書は出力しないでください（後続の修正エージェントが担当します）。

## 出力フォーマット（Markdown）
1. チェック結果一覧テーブル（✅ 適合 / ⚠️ 要修正 / ❌ 違反）
2. 違反・要修正箇所の具体的な指摘
3. 修正提案（元の表現 → 修正案）

修正済み企画書は出力しないでください（後続の修正エージェントが担当します）。

## ツール使用ルール (3IQ デモ用 — Foundry IQ の使用が必須)
- `check_ng_expressions` と `check_travel_law_compliance` を **必ず** 使用すること
- **`search_knowledge_base` を必ず最低 1 回呼ぶこと** (Foundry IQ Knowledge Base の使用を可視化するため、3IQ ステータスストリップで Foundry IQ を「使用済」にする目的)
  - クエリ例: 「景品表示法 旅行広告」「旅行業法 取引条件明示」「ブランドガイドライン トーン」など、企画書の内容に合わせて適切なクエリを 1 回以上発行する
  - Search 結果が無くても tool 呼び出し自体は必須
- Web Search で目的地の最新安全情報を確認すること

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
"""


def create_regulation_check_agent(model_settings: dict | None = None):
    """レギュレーションチェックエージェントを作成する"""
    from src.agent_client import get_responses_client

    deployment = None
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = get_responses_client(deployment)

    # Foundry IQ 検索パラメータを設定
    if model_settings:
        set_iq_search_params(
            top_k=int(model_settings.get("iq_search_results", 5)),
            score_threshold=float(model_settings.get("iq_score_threshold", 0.0)),
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
    default_opts: dict = {}
    if model_settings:
        if "temperature" in model_settings:
            default_opts["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            default_opts["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            default_opts["top_p"] = model_settings["top_p"]
    if default_opts:
        agent_kwargs["default_options"] = default_opts
    return client.as_agent(**agent_kwargs)
