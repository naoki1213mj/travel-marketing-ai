# Travel Marketing AI — エージェント詳細仕様

## プロジェクト概要

旅行会社のマーケ担当者が自然言語で指示すると、企画書・販促ブローシャ・バナー画像・動画を全自動生成するマルチエージェントパイプライン。Microsoft Foundry + Azure のフル PaaS 構成。

- **チーム**: Team D (Tokunaga / Matsumoto / mmatsuzaki)
- **デプロイ先**: East US 2
- 要件定義書: [docs/requirements_v4.0.md](docs/requirements_v4.0.md)

## アーキテクチャ

```text
ユーザー → React (Vite/Tailwind/i18n) + 🎤 Voice Live → FastAPI (SSE)
  → FastAPI 直接オーケストレーション
    → Agent1 (データ検索: Fabric Lakehouse + Code Interpreter)
    → Agent2 (施策生成: Web Search)
    → [承認ステップ]
    → Agent3a (規制チェック: Foundry IQ + Web Search)
    → Agent3b (企画書修正: チェック結果を反映)
    → Agent4 (販促物生成: GPT Image 2 既定 / GPT Image 1.5 / MAI-Image-2 + Content Understanding)
    → Agent5 (動画生成: Photo Avatar)
  → モデル配備側のガードレール + 軽量ローカル入力/ツール応答ガード → 成果物表示
  → Agent6 (品質レビュー: GitHubCopilotAgent) ← オプショナル
  → Logic Apps (Teams 通知 + SharePoint 保存)
  → Foundry Evaluations (品質ダッシュボード)

補足: APIM AI Gateway は Azure 側で接続・ポリシーを構成しているが、アプリコードは project endpoint を直接使用する。
```

## 技術スタック

| 層 | 技術 | バージョン |
|---|------|----------|
| フロントエンド | React + TypeScript + Vite + Tailwind CSS | React 19.2 / Vite 8 / Tailwind CSS 4.2 / TypeScript 5.9 |
| バックエンド | FastAPI + uvicorn | Python 3.14 |
| パッケージ管理 | uv | 最新 |
| 推論モデル | gpt-5.4-mini（既定）/ gpt-5.5（deployment 作成後に選択可） | gpt-5.4-mini: GA (2026-03-17~) / gpt-5.5: GA (2026-04-24~) |
| 画像生成 | GPT Image 2（既定）/ GPT Image 1.5 / MAI-Image-2 | GA（UI から選択可能） |
| エージェント実装 | Microsoft Agent Framework | 1.0.0 (GA) |
| オーケストレーション | FastAPI 直接オーケストレーション | `src/api/chat.py` で実装 |
| データ | Fabric Lakehouse | Delta Parquet + SQL EP |
| ナレッジ | Foundry IQ Knowledge Base | Preview |
| AI Gateway | Azure API Management | GA |
| デプロイ | Azure Container Apps + azd | GA |
| CI/CD | GitHub Actions (DevSecOps) | — |
| 音声入力 | Voice Live API | Preview |
| 文書解析 | Content Understanding | GA |
| 販促動画 | Photo Avatar + Voice Live | Preview |
| ワークフロー自動化 | Azure Logic Apps | GA |
| 配信チャネル | Microsoft Teams | GA |

## 間違えやすい API / 設定

| ✅ 正しい | ❌ 間違い | 理由 |
|----------|---------|------|
| `FoundryChatClient(project_endpoint=..., model=..., credential=DefaultAzureCredential())` | `AzureOpenAIResponsesClient(...)` | GA で Foundry クライアントへ移行 |
| `client.as_agent(name=..., tools=..., middleware=...)` | `Agent(chat_client=...)` | `chat_client` コンストラクタは旧パターン |
| `@tool` デコレータ | `@ai_function` | `@ai_function` は削除済み |
| `await agent.run("文字列")` | `agent.run(Message(role=..., contents=[...]))` | `run()` が入力を正規化する |
| `SequentialBuilder(participants=[...]).build()` | `.participants()` fluent builder | fluent builder は削除済み |
| `AZURE_AI_PROJECT_ENDPOINT` | `AZURE_OPENAI_ENDPOINT` | Foundry Project EP を使う |
| `await call_next()` | `call_next(context)` | middleware continuation に引数は渡さない |
| `TypedDict + load_settings()` | Pydantic Settings | 軽量 settings パターンを使う |
| `uv add agent-framework-core==1.0.0 agent-framework-foundry==1.0.0` | `uv add agent-framework --prerelease=allow` | GA 版は通常インストール。beta connector だけ `--prerelease=allow` |
| `Flex Consumption` プラン | `Consumption` プラン | 旧 Consumption はレガシー |
| `Microsoft Foundry` | `Azure AI Foundry` | 2025-11 にリネーム済み |

## エージェント詳細

### Agent1: data-search-agent（データ検索）

**ファイル**: `src/agents/data_search.py` + `src/foundry_prompt_agents.py:run_data_search_prompt_agent` (Foundry path)
**役割**: Fabric Lakehouse SQL endpoint から売上データ・顧客レビューをリアルタイム検索し、ターゲット・季節・地域・予算情報を抽出する。Code Interpreter による高度なデータ分析にも対応。既定では `data_search_runtime=foundry_preprovisioned` で動作し、事前作成済み Foundry Prompt Agent + `MicrosoftFabricPreviewTool` を `agent_reference` で実行する。delegated user token を OBO で Foundry → Fabric Data Agent に forward することで Fabric workspace audit log に実 user の UPN を記録する。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `MicrosoftFabricPreviewTool` (Foundry built-in) | Fabric Data Agent (`Travel_Ontology_DA_v2`) を OBO で呼ぶ。Pass 1 で `tool_choice=ToolChoiceAllowed(mode="required", tools=[fabric_dataagent_preview])` で強制呼び出し | ✅ Foundry Agent Service + Fabric DA OBO (delegated `https://ai.azure.com/user_impersonation`) | Pass 1 で zero-fabric / 401 / 403 / connection misconfig 時は Pass 2 (function tool) に降格 |
| `search_sales_history(query, season, region)` | Pass 2 fallback: 売上履歴テーブル直接検索 | ✅ Fabric Lakehouse (pyodbc + Azure AD トークン認証 `SQL_COPT_SS_ACCESS_TOKEN`) | CSV (`data/sales_history.csv`) → ハードコードデータ |
| `search_customer_reviews(plan_name, min_rating)` | Pass 2 fallback: 顧客レビュー直接検索 | ✅ Fabric Lakehouse (pyodbc + Azure AD トークン認証) | CSV (`data/customer_reviews.csv`) → ハードコードデータ |
| Code Interpreter | データ分析・可視化（`ENABLE_CODE_INTERPRETER=true` で有効化） | ✅ Foundry Agent Service | グレースフルフォールバック（ツールなしで続行） |

**出力形式**: Markdown（ターゲット分析 / 売上トレンド / 顧客評価 / 推奨事項の 4 セクション）

**データソース優先順位 (Foundry path)**: Fabric Data Agent (Pass 1) → SQL endpoint via function tool (Pass 2) → CSV → ハードコードデータ

**Foundry path 前提条件**:

- `caller_identity["auth_mode"] == "delegated"` AND `delegated_user_access_token` 非空 AND `FOUNDRY_FABRIC_CONNECTION_ID` 非空 の AND 条件で発動
- 上記いずれかが満たされない場合は warn log + telemetry reason を残して legacy 直行
- 401/403/OBO/connection misconfig 限定で Pass 2 → still fail なら legacy retry。500 / その他は fail loud
- end user に必要な RBAC: `Azure AI User` (Foundry) + Fabric workspace `Read` + Fabric Data Agent `Read` + 下位 data source `Build`/`Read`

**Rollback 手順**:

1. **即時** (revision rollback): `az containerapp revision list -g <rg> -n <ca>` → 旧 image SHA の revision に `az containerapp ingress traffic set --revision-weight <old>=100`
2. **中期** (env override): `DATA_SEARCH_RUNTIME=legacy` を Container App env に設定 (新 revision 作成・即時切替ではない)
3. **最終** (commit revert): PR 3 commit を revert + push

---

### Agent2: marketing-plan-agent（施策生成）

**ファイル**: `src/agents/marketing_plan.py`
**役割**: Agent1 の分析結果をもとにマーケティング企画書を作成する。景品表示法違反表現を回避。既定では `marketing_plan_runtime=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool` で動作し、事前作成済み Foundry Prompt Agent を `agent_reference` で実行する。Work IQ 有効時はブラウザが取得した `https://ai.azure.com/user_impersonation` を backend が Foundry Responses へ渡し、Agent definition に含まれる Work IQ MCP connection を per-user で使う。`graph_prefetch` は明示 rollback 経路として残しており、必要時だけ Microsoft Graph Copilot Chat API から短い workplace brief を先読みする。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `search_market_trends(query)` | 最新の旅行市場トレンド・競合情報を検索 | ✅ Foundry Agent Service (Bing grounding / Web Search) | ハードコードトレンドデータ |

**出力形式**: Markdown（タイトル / キャッチコピー 3 案 / ターゲットペルソナ / プラン概要 / 差別化ポイント / 改善ポイント / 販促チャネル / KPI の 8 セクション）

**Work IQ ランタイム補足**:

- 既定: `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`
- connector 対応: `meeting_notes` → Teams meeting artifacts、`emails` → Outlook Email、`teams_chats` → Teams、`documents_notes` → SharePoint / OneDrive
- `foundry_tool` ではブラウザの `https://ai.azure.com/user_impersonation` token を backend が Foundry Responses client へ渡し、事前作成済み Prompt Agent に添付された Work IQ MCP connection を `tool_choice={"type":"mcp","server_label":"mcp_M365Copilot"}` で最低 1 回使わせる
- rollback: `WORKIQ_RUNTIME=graph_prefetch` を指定すると Microsoft Graph Copilot Chat API で短い brief を先読みする

---

### Agent3a: regulation-check-agent（規制チェック）

**ファイル**: `src/agents/regulation_check.py`
**役割**: 企画書のコンプライアンスを 6 項目チェック（旅行業法 / 景品表示法 / ブランドガイドライン / NG 表現 / ナレッジベース / 安全情報）。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `search_knowledge_base(query)` | レギュレーション文書をナレッジベースから検索 | ✅ Foundry IQ → Azure AI Search | 静的レスポンス |
| `check_ng_expressions(text)` | 禁止表現（最安値・業界No.1 等）をスキャン | ローカル処理（ハードコードリスト） | — |
| `check_travel_law_compliance(document)` | 旅行業法チェックリスト 5 項目を検証 | ローカル処理（キーワード検索） | — |
| `search_safety_info(destination)` | 渡航先の安全情報（外務省警告・気象警報） | ✅ Foundry Agent Service (Bing grounding) | 静的安全データ |

**出力形式**: Markdown（チェック結果テーブル ✅/⚠️/❌ / 違反詳細 / 修正提案）

---

### Agent3b: plan-revision-agent（企画書修正）

**ファイル**: `src/agents/plan_revision.py`
**役割**: 規制チェック結果を反映した修正版企画書のみ出力する。Agent3a のチェック結果（違反指摘・修正提案）と元の企画書を受け取り、すべての指摘事項を反映した完全な修正版企画書を生成する。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| （なし） | ツールは使用しない。LLM のみで企画書を修正する | — | — |

**出力形式**: 完全な修正済み企画書（Markdown）。チェック結果テーブルは含まない。

---

### Agent4: brochure-gen-agent（販促物生成）

**ファイル**: `src/agents/brochure_gen.py`
**役割**: 規制チェック済み企画書から顧客向け成果物を生成（HTML ブローシャ / ヒーロー画像 / SNS バナー）。ブローシャは**顧客向け販促資料**であり、KPI・売上目標・社内分析・競合分析などの社内情報は含めない。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `generate_hero_image(prompt, destination, style)` | 目的地メインビジュアル画像生成（1792x1024px） | ✅ GPT Image 2（既定）/ GPT Image 1.5 / MAI-Image-2 — UI から選択 | 可視 SVG プレースホルダー |
| `generate_banner_image(prompt, platform)` | SNS バナー画像生成（Instagram/Twitter/Facebook サイズ対応） | ✅ GPT Image 2（既定）/ GPT Image 1.5 / MAI-Image-2 — UI から選択 | 可視 SVG プレースホルダー |

**出力形式**: 顧客向け HTML ブローシャ（Tailwind CSS / レスポンシブ / 旅行業登録番号フッター付き）+ Base64 画像 data URI

**画像生成ランタイム補足**: GPT 系画像モデルは `AZURE_AI_PROJECT_ENDPOINT` から AI Services account endpoint を導出し、Azure OpenAI Images API を Managed Identity で呼び出す。429 / timeout / 5xx / connection error は上限付き retry/backoff し、最終失敗時は透明 PNG ではなく可視 SVG placeholder を返す。MAI 経路は `IMAGE_PROJECT_ENDPOINT_MAI` を使い、429 は `Retry-After` を尊重して直列化する。

**顧客向けルール**:

- 含めるべき情報: プラン名、キャッチコピー、旅行先の魅力、日程・価格帯、含まれるサービス、予約方法
- 含めてはいけない情報: KPI、目標予約数、売上目標、前年比、セグメント分析、競合分析

---

### Agent5: video-gen-agent（動画生成）

**ファイル**: `src/agents/video_gen.py`
**役割**: Photo Avatar で販促動画を生成する。企画書のサマリーを元に、ナレーション付きの紹介動画を自動作成する。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `generate_promo_video(summary, avatar_style)` | Lisa / `casual-sitting` 固定の Photo Avatar プロモ動画生成（SSML ナレーション、イントロジェスチャー、`ja-JP-Nanami:DragonHDLatestNeural` 音声、MP4/H.264）。`avatar_style` は後方互換用で選択肢は表示しない | ✅ Speech / Photo Avatar API | スキップ |

**出力形式**: MP4 動画 URL

**構成**:

- アバター: `lisa`
- スタイル: `casual-sitting`
- 音声: `ja-JP-Nanami:DragonHDLatestNeural`
- ナレーション: SSML で間と締め文を最適化し、冒頭でジェスチャーを挿入

---

### Agent6: quality-review-agent（品質レビュー）

**ファイル**: `src/agents/quality_review.py`
**役割**: 生成された成果物の品質を 4 観点でレビュー（企画書構造 / ブローシャアクセシビリティ / テキストトーン一貫性 / 旅行業法適合）。バックグラウンドで実行され、`AZURE_AI_PROJECT_ENDPOINT` 未設定時はスキップされる。

**実装**: `ENABLE_GITHUB_COPILOT_REVIEW_AGENT=true` のときだけ preview の `GitHubCopilotAgent` を使用する opt-in 構成。既定では `FoundryChatClient` にフォールバックし、自動権限承認は行わない。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `review_plan_quality(plan_markdown)` | 企画書の 5 必須セクション（タイトル / キャッチコピー / ターゲット / 概要 / KPI）を検証 | ローカル処理（キーワード検索） | — |
| `review_brochure_accessibility(html_content)` | HTML アクセシビリティ 4 項目チェック（alt属性 / lang属性 / フッター / フォントサイズ） | ローカル処理 | — |

**出力形式**: Markdown（セクションごとの ✅/⚠️/❌ チェックリスト）

---

## 実行フロー（FastAPI Orchestration）

```text
Agent1 (data-search-agent)
  ↓ データ分析結果
Agent2 (marketing-plan-agent)
  ↓ 企画書 Markdown
  ↓ [承認ステップ — ユーザーが承認/修正を選択]
Agent3a (regulation-check-agent)
  ↓ チェック結果（✅/⚠️/❌）
Agent3b (plan-revision-agent)
  ↓ 修正済み企画書
Agent4 (brochure-gen-agent)
  ↓ HTML ブローシャ + 画像
Agent5 (video-gen-agent)
  ↓ MP4 動画
Agent6 (quality-review-agent) ← バックグラウンド実行（オプショナル）
```

**フレームワーク**: FastAPI バックエンドによる直接オーケストレーション
**エントリポイント**: `src/api/chat.py` → `workflow_event_generator()` / `approve()`

## Azure 接続状態サマリ

| ツール / サービス | Azure 接続 | フォールバック動作 |
|-----------------|-----------|------------------|
| Fabric Data Agent v2 (NL2Ontology) | `FABRIC_DATA_AGENT_URL_V2` + `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2` 設定時 | v1 (`FABRIC_DATA_AGENT_URL`) → SQL endpoint → CSV |
| Fabric Lakehouse v2 (売上・レビュー検索) | `FABRIC_SQL_ENDPOINT` 設定時（pyodbc + Azure AD トークン認証、live 既定 lakehouse `lh_travel_marketing_v2`） | CSV ファイル → ハードコードデータ |
| Web Search (市場トレンド) | `AZURE_AI_PROJECT_ENDPOINT` 設定時 | ハードコードトレンドデータ |
| Foundry IQ (ナレッジベース検索) | `AZURE_AI_PROJECT_ENDPOINT` + AI Search (`SEARCH_ENDPOINT` / `SEARCH_API_KEY`) 設定時 | 静的レスポンス |
| Web Search (安全情報) | `AZURE_AI_PROJECT_ENDPOINT` 設定時 | 静的安全データ |
| gpt-image-2 / gpt-image-1.5 (画像生成) | `AZURE_AI_PROJECT_ENDPOINT` + モデルデプロイ時 | **可視 SVG プレースホルダー** (透明 PNG ではない) |
| MAI-Image-2 (画像生成) | `IMAGE_PROJECT_ENDPOINT_MAI` + モデルデプロイ時 | **可視 SVG プレースホルダー** (透明 PNG ではない) |
| Speech / Photo Avatar (動画生成) | `SPEECH_SERVICE_ENDPOINT` 設定時 | スキップ |
| Cosmos DB (会話履歴 + approval token) | `COSMOS_DB_ENDPOINT` 設定時 | インメモリストア |

> **注**: 全環境変数が未設定の場合でもモックデモモードで動作する。

## 承認 token のセキュリティ (2026-05-01 commit 7a554d9 〜)

`/api/chat/{id}/approve` は per-conversation の `approval_token` (32-byte urlsafe) で保護されている。

- **発行**: `chat()` が `marketing-plan-agent` (Agent2) 完了後に `secrets.token_urlsafe(32)` で mint
- **配布**: `approval_request` SSE event の `approval_token` フィールドで client に配布
- **保存**: `_pending_approvals[owner_id:conversation_id]` (in-memory) + Cosmos `metadata.pending_approval_token` (`awaiting_approval` 中のみ)
- **検証**: `_load_pending_approval_context` が `hmac.compare_digest()` で定数時間比較。匿名 lookup は token 必須、不在 / 不一致は `APPROVAL_CONTEXT_NOT_FOUND` で即拒否
- **rotation**: `_refine_events()` で修正版を出すたびに新 token に置換 (古い token 漏洩で新版を承認させない)
- **Frontend**: `useSSE.ts` の `approval_request` handler で state に保存、`sendApproval()` の 6 番目引数で次の POST に echo
- 詳細は `docs/approval-security.md` を参照

## Live 本番運用情報 (2026-05-01 cutover complete)

| 項目 | 値 |
|---|---|
| Public URL | `https://ca-wmbvhdhcsuyb2-pn.wonderfultree-f9803f6f.eastus2.azurecontainerapps.io/` |
| Container App | `ca-wmbvhdhcsuyb2-pn` |
| CAE | `cae-wmbvhdhcsuyb2-pn` (VNet integrated, `snet-container-apps`) |
| Cosmos DB | private endpoint 経由 (`publicNetworkAccess: Disabled`) |
| Fabric workspace | `ws-3iq-demo` |
| Fabric capacity | `fcdemoeastus2001` (East US 2, F64, Active) |
| Fabric Data Agent | `Travel_Ontology_DA_v2` (Phase 11d 適用済み 2026-05-04) |
| Fabric Lakehouse | `lh_travel_marketing_v2` (10 Delta tables in `dbo`) |
| 既知 platform 問題 | Phase 10 P13 / P14 prompts に起因する Fabric `submit_tool_outputs` BadRequest (Phase 11d でも未解決, Microsoft サポート起票待ち) |
| 削除済 | `ca-wmbvhdhcsuyb2`, `cae-wmbvhdhcsuyb2` (2026-05-01 削除) |

## ディレクトリ構成（代表ファイルのみ）

> 全ファイルを列挙すると陳腐化が早いため、主要モジュールのみ記載します。完全な一覧は `git ls-files` を参照してください。

```text
travel-marketing-agents/
├── src/                          # バックエンド (Python 3.14)
│   ├── agents/                   # 7 エージェント定義
│   │   ├── _shared_instructions.py  # 共有 prompt 部品
│   │   ├── data_search.py        # Agent1: データ検索
│   │   ├── marketing_plan.py     # Agent2: 施策生成（+ Web Search / Work IQ）
│   │   ├── regulation_check.py   # Agent3a: 規制チェック
│   │   ├── plan_revision.py      # Agent3b: 企画書修正
│   │   ├── brochure_gen.py       # Agent4: ブローシャ + 画像生成
│   │   ├── video_gen.py          # Agent5: 動画生成
│   │   └── quality_review.py     # Agent6: 品質レビュー
│   ├── api/                      # FastAPI ルーター
│   │   ├── chat.py               # /api/chat (SSE) + 承認継続 + 会話保存
│   │   ├── conversations.py      # /api/conversations + /api/replay
│   │   ├── capabilities.py       # /api/capabilities
│   │   ├── evaluate.py           # /api/evaluate
│   │   ├── health.py             # /api/health + /api/ready
│   │   ├── sources.py            # /api/sources/* (PDF / text / audio ingestion)
│   │   └── voice.py              # /api/voice-config
│   ├── middleware/               # 軽量入力 / ツール応答ガード
│   ├── pipeline_schemas.py       # SSE / pipeline schema (Pydantic)
│   ├── conversations.py          # Cosmos DB / インメモリ会話管理
│   ├── work_iq_session.py        # Work IQ session state
│   ├── work_iq_context.py        # Work IQ MCP runtime
│   ├── foundry_prompt_agents.py  # Foundry Prompt Agent runtime (delegated OBO)
│   ├── source_ingestion.py       # PDF / text / audio source pipeline
│   ├── improvement_mcp.py        # Functions MCP `generate_improvement_brief` クライアント
│   ├── mai_transcribe.py         # MAI Transcribe (audio source)
│   ├── request_identity.py       # cookie-based anonymous owner ID
│   ├── session_cookie.py         # tm_session_id cookie helpers
│   ├── tool_telemetry.py         # tool_event observability
│   ├── foundry_tracing.py        # OpenTelemetry / App Insights wiring
│   ├── hosted_agent.py           # Foundry Hosted Agent エントリポイント
│   ├── config.py                 # 設定（TypedDict + load_settings）
│   └── main.py                   # FastAPI エントリポイント
├── frontend/                     # フロントエンド (React 19 + Tailwind v4)
│   └── src/
│       ├── components/           # UI コンポーネント (Settings / WorkIqSourceStatus / Approval / ToolEventCard 等)
│       ├── hooks/                # useSSE, useTheme, useI18n
│       └── lib/                  # i18n.ts, sse-client.ts, event-schemas.ts, msal-auth.ts, voice-live.ts, iq-brand.ts
├── infra/                        # Bicep IaC (17 モジュール)
├── data/                         # デモデータ + demo-replay.json
├── regulations/                  # レギュレーション文書
├── tests/                        # pytest テスト群
├── scripts/                      # postprovision / Fabric DA tuning / 運用スクリプト
├── docs/                         # ドキュメント (api-reference / sse-event-schema / azure-* / approval-security 等)
├── Dockerfile                    # マルチステージ（Container Apps 用）
├── Dockerfile.agent              # Hosted Agent 用
├── azure.yaml                    # azd 設定
└── .github/workflows/            # CI + Deploy + Security
```

## 実装メトリクス（規模感）

> **重要**: 厳密な行数 / ファイル数 / テスト件数は更新が追いつかないため、ここでは**規模感のみ**を示します。
> ライブの正確な値は CI ログ（直近の `pytest -q` / `vitest --run` の合計、`uv run ruff check .` のスキャン対象）と `git ls-files | wc -l` を直接参照してください。

| 項目 | 規模感 |
|------|------|
| バックエンド Python | 40+ モジュール（agents / api / pipeline schemas / Work IQ runtime / Foundry tracing / source ingestion 等） |
| フロントエンド | 50+ TSX コンポーネント · 40+ TS ライブラリ（i18n 日英中、Tailwind v4、React 19） |
| インフラ Bicep | 17 モジュール（main + VNet / Cosmos / Foundry / APIM / Functions / Logic Apps 等） |
| テストコード | pytest 700+ · vitest 280+ |
| CI/CD ワークフロー | CI + Deploy + Security (3 本) |

## Quick Commands

```bash
# 依存インストール
uv sync
cd frontend && npm ci && cd ..

# ローカル開発
uv run uvicorn src.main:app --reload --port 8000
cd frontend && npm run dev              # Vite dev server (proxy → :8000)

# テスト
uv run pytest
cd frontend && npm run test

# リント
uv run ruff check .
cd frontend && npx tsc --noEmit

# デプロイ
azd up                                   # 初回: プロビジョニング + デプロイ
azd deploy                               # 2 回目以降: コードのみ

# Docker ローカル確認
docker build -t travel-agents .
docker run -p 8000:8000 --env-file .env travel-agents
```

## 変更の規律

- 依頼された変更だけ行う。隣接コードを勝手に「改善」しない
- 既存のスタイルに合わせる
- コミット・push は、現在の会話で明示依頼がある場合だけ行う
- Azure 本番への直接反映は、理由と影響範囲を先に説明して了承を得る
- **rubber-duck エージェントを毎回使う**: 些細な変更でも実装前または実装後に rubber-duck で critique を取り、blocking 指摘を反映してから push する。「trivial だから skip」はしない (User 明示要望 2026-05-01)

## Breaking Changes<https://learn.microsoft.com/en-us/agent-framework/support/upgrade/python-2026-significant-changes>

<https://learn.microsoft.com/en-us/azure/foundry/agents/overview>

- Agent Framework: <https://learn.microsoft.com/en-us/agent-framework/support/upgrade/python-2026-significant-changes>
- Foundry Agent Service: <https://learn.microsoft.com/en-us/azure/foundry/agents/overview>
