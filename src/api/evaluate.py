"""品質評価 API。Built-in + カスタム評価器でパイプライン成果物を評価する。"""

import json
import logging
import os
import re
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings

router = APIRouter(prefix="/api", tags=["evaluation"])
logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)


class EvaluateRequest(BaseModel):
    """評価リクエスト"""

    query: str = Field(..., description="ユーザーの指示テキスト")
    response: str = Field(..., description="企画書の Markdown テキスト")
    html: str = Field("", description="ブローシャの HTML テキスト（オプション）")


# --- カスタム評価器（Code-based） ---


def _evaluate_travel_law_compliance(response: str, html: str) -> dict:
    """旅行業法準拠チェック（code-based カスタム評価器）。

    旅行業法で記載が求められる項目の有無をスコア化する。
    """
    required_items = {
        "旅行業登録番号": ["登録番号", "旅行業", "観光庁長官"],
        "取引条件": ["取引条件", "旅行条件", "契約"],
        "取消料": ["取消料", "キャンセル料", "キャンセルポリシー"],
        "旅程": ["日程", "ルート", "行程", "日目"],
        "価格表示": ["円", "税込", "価格", "料金"],
    }
    text = f"{response}\n{html}"
    found = 0
    details: dict[str, bool] = {}
    for item_name, keywords in required_items.items():
        matched = any(kw in text for kw in keywords)
        details[item_name] = matched
        if matched:
            found += 1
    score = found / len(required_items)
    return {
        "score": round(score, 2),
        "details": details,
        "reason": f"{len(required_items)} 項目中 {found} 項目が記載されています",
    }


def _evaluate_brochure_accessibility(html: str) -> dict:
    """コンバージョン期待度（code-based カスタム評価器）。

    予約につながる要素（CTA・価格明確さ・限定感・特典）の有無をスコア化する。
    """
    text = html or ""
    checks = {
        "CTA（予約導線）": any(kw in text for kw in ["予約", "申込", "お問い合わせ", "電話", "URL", "QR"]),
        "価格表示の明確さ": any(kw in text for kw in ["円", "税込", "～", "から"]),
        "限定感の訴求": any(kw in text for kw in ["期間限定", "先着", "早割", "残りわずか", "特別"]),
        "特典・付加価値": any(kw in text for kw in ["特典", "無料", "プレゼント", "ポイント", "割引"]),
        "安心感の提供": any(kw in text for kw in ["キャンセル", "全額返金", "保証", "サポート", "添乗員"]),
    }
    score = sum(1 for v in checks.values() if v) / len(checks)
    return {
        "score": round(score, 2),
        "details": checks,
        "reason": f"{len(checks)} 項目中 {sum(1 for v in checks.values() if v)} 項目が含まれています",
    }


def _evaluate_plan_structure(response: str) -> dict:
    """企画書構成チェック（code-based カスタム評価器）。"""
    required_sections = {
        "タイトル": ["#"],
        "キャッチコピー": ["キャッチ", "コピー"],
        "ターゲット": ["ターゲット", "ペルソナ"],
        "プラン概要": ["概要", "日数", "ルート", "日程"],
        "差別化": ["差別化", "独自", "強み"],
        "KPI": ["KPI", "目標", "予約数"],
        "販促チャネル": ["チャネル", "販促", "SNS", "広告"],
        "価格帯": ["円", "価格", "料金"],
    }
    found = 0
    details: dict[str, bool] = {}
    for section, keywords in required_sections.items():
        matched = any(kw in response for kw in keywords)
        details[section] = matched
        if matched:
            found += 1
    score = found / len(required_sections)
    return {
        "score": round(score, 2),
        "details": details,
        "reason": f"{len(required_sections)} 必須セクション中 {found} セクションが含まれています",
    }


# --- Built-in 評価器（AI-assisted） ---


async def _run_builtin_evaluators(query: str, response: str) -> dict:
    """azure-ai-evaluation SDK の Built-in 評価器を実行する。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not endpoint:
        return {"error": "AZURE_AI_PROJECT_ENDPOINT が未設定です"}

    try:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        parsed = urlparse(endpoint)
        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"

        eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", settings["model_name"])
        model_config = {
            "azure_endpoint": azure_endpoint,
            "azure_deployment": eval_model,
            "api_version": "2024-10-21",
            "api_key": token.token,
        }

        from azure.ai.evaluation import (
            CoherenceEvaluator,
            FluencyEvaluator,
            GroundednessEvaluator,
            RelevanceEvaluator,
            TaskAdherenceEvaluator,
        )

        evaluators = {
            "relevance": RelevanceEvaluator(model_config=model_config, is_reasoning_model=True),
            "coherence": CoherenceEvaluator(model_config=model_config, is_reasoning_model=True),
            "fluency": FluencyEvaluator(model_config=model_config, is_reasoning_model=True),
            "groundedness": GroundednessEvaluator(model_config=model_config, is_reasoning_model=True),
            "task_adherence": TaskAdherenceEvaluator(model_config=model_config, is_reasoning_model=True),
        }

        results: dict[str, dict] = {}
        for name, evaluator in evaluators.items():
            try:
                # Groundedness は context パラメータが必要
                if name == "groundedness":
                    result = evaluator(query=query, response=response, context=response)
                else:
                    result = evaluator(query=query, response=response)
                score = result.get(name, result.get(f"gpt_{name}"))
                reason = result.get(f"{name}_reason", result.get(f"{name}_label", ""))
                results[name] = {
                    "score": float(score) if score is not None else -1,
                    "reason": str(reason),
                }
            except (ValueError, OSError, RuntimeError) as exc:
                logger.warning("Built-in 評価器 %s でエラー: %s", name, exc)
                results[name] = {"score": -1, "reason": str(exc)}

        return results

    except ImportError as exc:
        return {"error": f"azure-ai-evaluation が未インストール: {exc}"}
    except (ValueError, OSError) as exc:
        return {"error": f"認証エラー: {exc}"}


# --- Prompt-based カスタム評価器 ---


async def _run_marketing_quality_evaluator(query: str, response: str) -> dict:
    """企画書の総合品質を LLM ジャッジで評価する（prompt-based カスタム評価器）。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not endpoint:
        return {"score": -1, "reason": "AZURE_AI_PROJECT_ENDPOINT が未設定"}

    try:
        from src.agent_client import get_shared_credential

        credential = get_shared_credential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        parsed = urlparse(endpoint)
        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"

        from openai import AzureOpenAI

        eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", settings["model_name"])
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=token.token,
            api_version="2024-10-21",
        )

        judge_prompt = """\
あなたは旅行マーケティング企画書の品質審査官です。
以下のユーザー依頼と企画書を評価し、JSON 形式で結果を返してください。

## 評価基準（各 1〜5 点）
1. **appeal**: 顧客訴求力（キャッチコピーの魅力・ターゲットへの共感度・「行きたい」と思わせる力）
2. **differentiation**: 差別化ポイントの具体性（競合との違いが明確か）
3. **kpi_validity**: KPI の妥当性（測定可能で現実的な目標か）
4. **brand_tone**: ブランド一貫性（トーンの統一・ターゲット層に適した表現・景品表示法 NG 表現の回避）

## ユーザー依頼
{query}

## 企画書
{response}

## 出力形式（JSON のみ、他のテキストは出力しない）
{{
  "appeal": <1-5>,
  "differentiation": <1-5>,
  "kpi_validity": <1-5>,
  "brand_tone": <1-5>,
  "overall": <1-5（4項目の平均）>,
  "reason": "<50文字以内の総合コメント>"
}}"""

        completion = client.chat.completions.create(
            model=eval_model,
            messages=[
                {"role": "system", "content": "JSON のみ出力してください。"},
                {"role": "user", "content": judge_prompt.format(query=query, response=response)},
            ],
            temperature=0.1,
            max_completion_tokens=500,
        )

        answer = completion.choices[0].message.content or ""
        # JSON 部分を抽出
        json_match = re.search(r"\{[^}]+\}", answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"score": -1, "reason": f"JSON パース失敗: {answer[:200]}"}

    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Marketing Quality Evaluator でエラー: %s", exc)
        return {"score": -1, "reason": str(exc)}


# --- Foundry ポータル連携（クラウド評価） ---


async def _log_to_foundry(query: str, response: str, scores: dict) -> str | None:
    """評価結果を Foundry ポータルに記録し、ダッシュボード URL を返す。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not endpoint:
        return None

    try:
        from azure.ai.evaluation import evaluate
        from azure.identity import DefaultAzureCredential

        # Foundry project URL を構築
        azure_ai_project = endpoint

        # 一時データファイルを作成
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            json.dump({"query": query, "response": response}, f, ensure_ascii=False)
            f.write("\n")
            temp_path = f.name

        from azure.ai.evaluation import CoherenceEvaluator, RelevanceEvaluator

        parsed = urlparse(endpoint)
        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"
        eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", settings["model_name"])

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        model_config = {
            "azure_endpoint": azure_endpoint,
            "azure_deployment": eval_model,
            "api_version": "2024-10-21",
            "api_key": token.token,
        }

        result = evaluate(
            data=temp_path,
            evaluators={
                "relevance": RelevanceEvaluator(model_config=model_config, is_reasoning_model=True),
                "coherence": CoherenceEvaluator(model_config=model_config, is_reasoning_model=True),
            },
            azure_ai_project=azure_ai_project,
        )

        # クリーンアップ
        os.unlink(temp_path)

        # ポータル URL を返す
        studio_url = result.get("studio_url", "")
        if studio_url:
            logger.info("Foundry ポータルに評価結果を記録: %s", studio_url)
            return studio_url
        return None

    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Foundry ポータル連携に失敗: %s", exc)
        return None


# --- エンドポイント ---


@router.post("/evaluate")
@limiter.limit("5/minute")
async def evaluate_artifacts(
    request: Request, body: EvaluateRequest, background_tasks: BackgroundTasks
) -> JSONResponse:
    """パイプライン成果物の品質を評価する。

    Built-in 評価器（Relevance / Coherence / Fluency）+
    カスタム評価器（旅行業法準拠 / 企画書構成 / ブローシャアクセシビリティ / 企画書品質）
    でスコアリングし、結果を返す。
    """
    results: dict = {}

    # Code-based カスタム評価器（即座に実行）
    results["custom"] = {
        "travel_law_compliance": _evaluate_travel_law_compliance(body.response, body.html),
        "conversion_potential": _evaluate_brochure_accessibility(body.html or body.response),
    }

    # Built-in AI-assisted 評価器
    results["builtin"] = await _run_builtin_evaluators(body.query, body.response)

    # Prompt-based カスタム評価器（LLM ジャッジ）
    results["marketing_quality"] = await _run_marketing_quality_evaluator(body.query, body.response)

    # Foundry ポータル連携はレスポンスを待たずに非同期実行する
    background_tasks.add_task(_log_to_foundry, body.query, body.response, results.copy())

    return JSONResponse(results)
