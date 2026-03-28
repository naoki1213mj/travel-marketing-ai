# 要件定義書: 旅行マーケティング AI マルチエージェント パイプライン

> **プロジェクト名**: Team D ハッカソン  
> **作成日**: 2026-03-27  
> **作成者**: Team D (Tokunaga / Matsumoto / mmatsuzaki)  
> **ステータス**: Draft v3.4

---

## 1. プロジェクト概要

### 1.1 背景

旅行会社のマーケティング担当者は、販売データの分析・施策立案・法令チェック・販促物制作を手作業で行っており、以下の課題を抱えている。

| ステップ | 現状の課題 | 影響 |
|---------|-----------|------|
| データ分析 | 週次データの抽出・加工に半日以上 | 対策が必要な商品を見落とすリスク |
| 施策立案 | 制作会社への依頼で1週間以上 | リアルタイムな需要対応ができない |
| 法令チェック | 膨大な規則を目視確認 | 属人的判断による見落とし・手戻り |
| 販促物制作 | デザイナーへの外注で数日〜1週間 | タイミングを逃した機会損失 |

### 1.2 目的

マルチ AI エージェントによるパイプラインを構築し、自然言語の指示から企画書・販促ブローシャ・バナー画像を全自動で生成する仕組みを実現する。さらに、生成結果に対してマルチターンの対話で修正・微調整を可能にし、マーケティング担当者が「対話しながら仕上げていく」体験を提供する。

### 1.3 スコープ

**In Scope:**

- Web フロントエンド（チャット UI + 成果物プレビュー + 修正対話）
- 4 つの AI エージェント（データ検索 / 施策生成 / 規制チェック / 販促物生成）
- Microsoft Agent Framework によるエージェント実装
- Foundry Agent Service Workflows によるオーケストレーション（Human-in-the-Loop 含む）
- 業務 DB（Fabric Lakehouse + デモデータ）
- レギュレーション文書リポジトリ（Foundry IQ Knowledge Base）
- 画像生成（GPT Image 1.5）
- Azure API Management AI Gateway（トークン管理・監視・負荷分散）
- Azure Functions MCP サーバー（カスタムツール連携）
- Content Safety + Prompt Shield によるプロンプトインジェクション防止
- Foundry Observability（トレーシング + 評価）
- フロントエンド多言語 UI（日本語・英語・中国語）+ ダーク/ライトモード

**Out of Scope:**

- 実際の旅行予約システムとの連携
- 本番環境への展開
- 決済・課金機能
- 動画生成

---

## 2. システム構成

### 2.1 設計原則

本システムはサーバーレス / フル PaaS アーキテクチャで構築する。VM やセルフホストのミドルウェアは使わず、Azure のマネージドサービスだけで完結させる。

- **コンピュート**: Azure Container Apps（サーバーレススケーリング、ゼロインスタンスまでスケールダウン可能）
- **オーケストレーション**: Foundry Agent Service Workflows（フルマネージド）
- **データ**: Fabric Lakehouse（サーバーレス SQL エンドポイント）
- **ナレッジ**: Foundry IQ（マネージド検索）
- **API Gateway**: Azure API Management AI Gateway（マネージド）
- **カスタムツール**: Azure Functions（Flex Consumption プラン、従量課金。VNet 統合対応）
- **監視**: Foundry Observability + Application Insights（マネージド）
- **シークレット**: Azure Key Vault（マネージド。セキュリティ詳細は §2.5 参照）
- **認証**: DefaultAzureCredential（詳細は §2.5 参照）

### 2.2 全体アーキテクチャ

```
 👤 マーケ担当者
  │
  ▼
┌─────────────────────────────┐
│ Web フロントエンド             │  ← Matsumoto
│ (チャット UI / 成果物プレビュー │
│  / マルチターン修正対話)       │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Azure API Management         │  ← AI Gateway
│ (トークン管理/負荷分散/監視)  │
└──────────┬──────────────────┘
           │
           ▼  ※ Content Safety + Prompt Shield（入力時点で適用）
           │
┌─────────────────────────────┐
│ Foundry Agent Service        │
│ Workflows                    │  ← Sequential + Human-in-the-Loop
│ (オーケストレーション)         │
│                              │
│ エージェント実装:              │
│ Microsoft Agent Framework     │  ← Python (1.0.0rc5)
└──┬────┬────┬────┬───────────┘
   │    │    │    │
   ▼    │    │    │     ┌───────────────────────┐
 Agent1  │    │    │     │ Fabric Lakehouse       │
 データ  │    │    │◄───►│ (SQL エンドポイント)    │ ← Tokunaga
 検索    │    │    │     └───────────────────────┘
   │    ▼    │    │
   │  Agent2  │    │     ┌───────────────────────┐
   └►施策    │    │◄───►│ Web Search ツール       │
     生成    │    │     │ (市場トレンド取得)      │
       │    │    │     └───────────────────────┘
       │    ▼    │
       │  ┌─────────┐   ← Human-in-the-Loop: ユーザー承認
       │  │ 承認     │
       │  └────┬────┘
       │       ▼
       │  Agent3       ┌───────────────────────┐
       └►規制    ◄────►│ Foundry IQ             │
         チェック       │ Knowledge Base          │ ← mmatsuzaki
           │          └───────────────────────┘
           │          ┌───────────────────────┐
           │    ◄────►│ Web Search ツール       │
           │          │ (外務省・気象情報等)    │
           ▼          └───────────────────────┘
         Agent4
         販促物        ┌───────────────────────┐
         生成    ────►│ GPT Image 1.5          │
           │          └───────────────────────┘
           │          ┌───────────────────────┐
           ├────────►│ Azure Functions         │
           │          │ MCP サーバー            │
           ▼          └───────────────────────┘
       📦 成果物
       (企画書 / ブローシャ / 画像)
           │
           ▼
       Foundry Observability
       (トレーシング + 評価ダッシュボード)
```

### 2.3 技術スタック

| カテゴリ | 技術 | 用途 | 備考 |
|---------|------|------|------|
| フロントエンド | React + TypeScript | チャット UI・成果物プレビュー・修正対話 | Vite でビルド。Tailwind CSS |
| バックエンド | FastAPI + uvicorn | SSE ストリーミング API・静的ファイル配信 | Python 3.14。Workflows との中継 |
| 推論モデル | gpt-5.4-mini | 4 エージェントの推論・テキスト生成 | GA（2026年3月〜）。低レイテンシ・高スループット |
| 画像生成モデル | GPT Image 1.5 | バナー画像・ブローシャ用画像 | Microsoft Foundry 上で GA |
| AI Gateway | Azure API Management (AI Gateway) | トークン管理・負荷分散・セマンティックキャッシュ・監視 | Foundry ポータルから有効化可能 |
| オーケストレーション | Foundry Agent Service Workflows | Sequential + Human-in-the-Loop Workflow 制御 | Portal ビジュアルビルダーまたは YAML。Preview |
| エージェント実装 | Microsoft Agent Framework (Python) | 4 つのエージェントのコア実装 | 1.0.0rc5。Hosted Agent として Foundry にデプロイ |
| データ基盤 | Fabric Data Factory + Lakehouse | デモデータの取り込み・格納・クエリ | Delta Parquet 格納。SQL エンドポイント経由 |
| ナレッジ検索 | Foundry IQ Knowledge Base | レギュレーション文書の agentic retrieval | Azure AI Search が基盤。Preview |
| 外部情報検索 | Web Search ツール (Preview) | 外務省安全情報・気象情報・市場トレンド | 追加 Azure リソース不要 |
| カスタムツール | Azure Functions (MCP サーバー) | テンプレート適用・Teams 通知・PDF 変換等 | Flex Consumption プラン。MCP で Agent4 に接続 |
| 入力安全性 | Content Safety + Prompt Shield | 悪意ある入力のブロック・Jailbreak 検知 | モデルデプロイメント設定で有効化 |
| 監視・評価 | Foundry Observability | トレーシング・品質評価・ダッシュボード | Application Insights 連携。評価・監視 GA / トレーシング Preview |
| デプロイ | Azure Container Apps | フロントエンド + バックエンドの一体デプロイ | Docker マルチステージビルド。azd up 対応 |
| インフラ | Microsoft Azure | 全体のホスティング・運用 | — |

### 2.4 Foundry IQ と Web Search ツールの使い分け

**Foundry IQ Knowledge Base（静的な社内規則）**
旅行業法ガイドライン・景品表示法チェックリスト・社内ブランドガイドライン・NG 表現リストなど、更新頻度が低い社内管理文書を格納する。Azure AI Search のインデックスに取り込み、Foundry IQ の agentic retrieval でエージェントに接続する。

**Web Search ツール（リアルタイム外部情報）**
外務省海外安全情報・気象警報・感染症情報など、常に最新の状態を参照する必要がある情報は、Foundry Agent Service の Web Search ツール（Preview）を使う。追加の Azure リソースが不要で、Bing リソースの管理を自動で行うため、セットアップが簡単。Agent2（施策生成）でも市場トレンドの取得に活用する。

### 2.5 エンタープライズセキュリティ

Azure のベストプラクティスに沿ったセキュリティ構成を適用する。

**重要な制約: Hosted Agent と private networking の優先順位**
Foundry Agent Service の private networking（VNet 分離）は Prompt Agent と Workflow Agent に対応している。Hosted Agent は Preview 期間中は private networking に未対応で、ネットワーク分離された Foundry リソース内では作成できない。本プロジェクトでは **Hosted Agent（Agent Framework でのコード実装）を優先** し、ネットワーク分離は Container Apps 層と Key Vault 層に限定する。Foundry Agent Service 自体の VNet 分離は、Hosted Agent が private networking に対応した時点で適用する。

**ネットワーク分離（Container Apps / Key Vault 層）:**
- Container Apps は VNet 統合で配置する（snet-container-apps サブネット）
- Key Vault は Private Endpoint 経由でのみアクセス可能にする（`publicNetworkAccess: Disabled`）
- Application Insights は Container Apps からの OTel トレースを受け取る
- Foundry Agent Service 層は現時点では Basic Setup（パブリックエンドポイント）で構成する

**認証・認可:**
- Container Apps は System Managed Identity を使い、Foundry / Key Vault / Fabric に認証する
- API キーのハードコードは禁止。すべて DefaultAzureCredential 経由にする
- Key Vault の RBAC で Key Vault Secrets User ロールを Container Apps の Managed Identity に割り当てる

**シークレット管理:**
- `PROJECT_ENDPOINT`、`APPLICATIONINSIGHTS_CONNECTION_STRING`、`CONTENT_SAFETY_ENDPOINT` 等の機密情報は Key Vault に格納する
- アプリケーション起動時に Key Vault から自動ロードし、環境変数として利用する
- `.env` は `.gitignore` に含め、`.env.example` にはプレースホルダーのみ記載する

**コード・リポジトリ:**
- リポジトリは Public（ハッカソン要件）。機密情報の漏洩を CI/CD で自動検出する（§13 参照）
- Azure サブスクリプション ID・テナント ID・リソース名をコードに含めない
- ACR、Container Apps へのデプロイは OIDC Workload Identity Federation で認証する（サービスプリンシパルのシークレット不要）

**データ境界の例外:**
- Web Search ツール（Bing grounding 系）は Azure の DPA（Data Processing Agreement）対象外。クエリデータが Azure のコンプライアンス / geo boundary の外に流れる可能性がある。機密性の高い入力をそのまま Web Search に渡さないよう、エージェント側のプロンプトで制御する
- Foundry IQ の MCP 接続は現 Preview では per-request headers を付けられないため、ユーザーごとの権限制御はできない。本プロジェクトではアプリ共通権限（Managed Identity）でナレッジベースを読む前提とする

### 2.6 AI Gateway（Azure API Management）

本プロジェクトでは Azure API Management を **アプリ前段のリバースプロキシ**として配置する。Container Apps（FastAPI）と Foundry Agent Service の間に挟み、トークン消費量のリアルタイム可視化、複数モデルデプロイメントへの負荷分散（ラウンドロビン / 優先度ベース）、TPM 制限による暴走防止、Application Insights と連携したリクエストログの一元監視を提供する。

Foundry ポータルから直接有効化できる「Foundry 統合 AI Gateway」もあるが、こちらは Preview で、same tenant / same subscription / v2 tier 等の条件がある。本プロジェクトでは既存の APIM リソースをリバースプロキシとして使う方式を基本とする。

**MCP ツールガバナンスの制約:** MCP ツールを AI Gateway 配下に置くガバナンス機能は Preview。現時点では Foundry ポータルで新規作成した MCP ツールのうち、managed OAuth を使わないものだけが対象。MCP 全体を一律に Gateway 配下に置ける前提では設計しない。

### 2.7 Azure Functions MCP サーバー

Azure Functions で MCP サーバーを構築し、エージェントのカスタムツールとして接続する。

**実装方法:**
Azure Functions の Flex Consumption プランで MCP サーバーを実装し、Foundry Agent Service の Remote MCP ツールとして登録する。Flex Consumption は VNet 統合・スケールトゥゼロ・従量課金に対応しており、公式 MCP ガイドが推奨するプランである（旧 Consumption プランはレガシー扱い）。認証は Managed Identity で行い、API キーの管理を不要にする。なお、標準 MCP SDK を Azure Functions 上でホストする方式は Public Preview。

**想定ツール:**
- `generate_brochure_pdf`: HTML ブローシャを PDF に変換する
- `apply_brand_template`: 社内ブランドテンプレートを適用する
- `notify_teams`: 成果物完成時に Teams チャネルに通知を送信する（Microsoft Graph API 経由）
- `save_to_sharepoint`: 生成した成果物を SharePoint に保存する（Microsoft Graph API 経由）

### 2.8 バックエンド構成（FastAPI + SSE）

React フロントエンドと Foundry Agent Service Workflows の間に FastAPI バックエンドを配置する。フロントエンドが直接 Conversations API を叩くのではなく、バックエンドを中継する設計にする理由は以下の通り。

1. **SSE ストリーミングの制御**: Workflows からのレスポンスをパースし、エージェント進捗・ツールイベント・テキストコンテンツを SSE イベントとして分離してフロントエンドに送信できる
2. **Content Safety の入力チェック**: Prompt Shield をバックエンド側で実行し、悪意ある入力をブロックしてから Workflows に渡せる
3. **認証の集約**: DefaultAzureCredential をバックエンドで管理し、フロントエンドにトークンを露出させない
4. **会話履歴の管理**: マルチターン修正対話のための会話コンテキストをバックエンド側で保持できる

```
React (Vite)  ←→  FastAPI (uvicorn)  ←→  API Management  ←→  Foundry Workflows
   :5173            :8000                    AI Gateway          Conversations API
   SSE stream       POST /api/chat
                    GET  /api/health
```

開発時は Vite の proxy 設定で `/api` をバックエンドに転送する。本番は Docker マルチステージビルドでフロントエンドの静的ファイルを FastAPI から配信する（§11 参照）。

---

## 3. 機能要件

### 3.1 Web フロントエンド

**担当: Matsumoto**

| ID | 機能 | 詳細 |
|----|------|------|
| FE-01 | チャット入力 | マーケ担当が自然言語で施策の指示を入力できる |
| FE-02 | エージェント進捗表示 | 各エージェントの処理状況をリアルタイムに表示する |
| FE-03 | 企画書表示 | Agent2 が生成した Markdown 企画書をレンダリング表示する |
| FE-04 | 承認 UI | Agent2 完了後、ユーザーが「承認」または「修正指示」を選択できる |
| FE-05 | レギュレーションチェック結果表示 | Agent3 のチェック結果をハイライト表示する |
| FE-06 | ブローシャプレビュー | Agent4 が生成した HTML ブローシャをプレビュー表示する |
| FE-07 | 画像表示 | GPT Image 1.5 で生成されたバナー画像を表示する |
| FE-08 | エクスポート | 企画書を Markdown (.md)、ブローシャを HTML、画像を PNG としてダウンロードできる。JSON 形式での一括エクスポートも可能 |
| FE-09 | マルチターン修正対話 | 成果物生成後、チャットで修正指示を出せる |
| FE-10 | 成果物バージョン管理 | 修正のたびにバージョンを記録し、以前の版に戻せる |
| FE-11 | Content Safety バッジ | 生成結果の安全性チェック状況を動的バッジで表示（🟢 安全 / 🔴 要確認 / ⚪ 確認中） |
| FE-12 | 処理メトリクス | 生成完了後にレイテンシ・ツール呼び出し数・トークン消費量を表示する |
| FE-13 | 多言語 UI（i18n） | UI ラベル・ボタン・メッセージを日本語・英語・中国語で切り替え可能にする。翻訳データは `lib/i18n.ts` で管理 |
| FE-14 | ダーク/ライトモード | システム設定に連動するテーマ切替。Tailwind CSS の `dark:` クラスで全コンポーネント対応 |

### 3.2 Content Safety（多層防御）

**入力時（Prompt Shield）:**
FastAPI バックエンドの `/api/chat` エンドポイントでユーザー入力を受け取った直後に、Azure AI Content Safety の Prompt Shield を実行する。プロンプトインジェクション攻撃が検出された場合は 400 エラーを返し、Workflows には渡さない。

**モデル呼び出し時（Content Filter）:**
Microsoft Foundry のモデルデプロイメント設定で Content filter を有効にする。各エージェントが gpt-5.4-mini を呼び出す際に、入出力の両方が自動的にフィルタリングされる。

**ツール応答時（Prompt Shield for tool response）:**
Web Search や MCP ツールの応答にもプロンプトインジェクション（間接攻撃）のリスクがある。Foundry Agent Service のガードレール設定で、tool response に対する Prompt Shield を有効にする。これにより、外部データソースから注入された悪意ある指示を検出・ブロックできる。AI Gateway 側でも `llm-content-safety` ポリシーで tool response の検査を適用する。

**出力時（Text Analysis）:**
パイプライン完了後、生成されたコンテンツ全体に対して Azure AI Content Safety の Text Analysis を実行する。4 つのカテゴリ（Hate / SelfHarm / Sexual / Violence）のスコアを取得し、結果を SSE イベントとしてフロントエンドに送信する。フロントエンドは Content Safety バッジ（FE-11）で結果を表示する。

**AI Gateway 層（ポリシー適用）:**
API Management の Content Safety ポリシーでも追加のフィルタリングを適用し、多層防御を実現する。

### 3.3 Human-in-the-Loop（承認フロー）

Agent2（施策生成）の完了後に、ユーザーが企画書の内容を確認する承認ステップを設ける。

1. Agent2 が企画書 Markdown を生成する
2. Workflows の Question ノードでユーザーに表示し、承認を求める
3. 「承認」→ Agent3 に進む / 「修正」→ 修正指示を入力し Agent2 が再生成 → 再度承認を求める

法令チェック前に人間が内容を確認できるため、修正コストを減らせる。

### 3.4 フロントエンド ↔ バックエンド ↔ Workflows 接続

```
React           FastAPI                   Foundry Agent Service
(SSE client)    (SSE streaming API)       (Conversations API)
    │                │                         │
    ├─ POST /api/chat ──►│                     │
    │                ├─ Prompt Shield ────────►│
    │                ├─ POST /conversations ──►│
    │                │◄── SSE stream ──────────┤
    │◄── SSE events ─┤   (agent progress)      │
    │  (parsed)      ├─ Content Safety ───────►│
    │◄── safety ─────┤   (output analysis)     │
    │◄── done ───────┤                         │
```

**SSE イベントの種類:**

| イベント種別 | 用途 | フロントエンドの処理 |
|------------|------|-------------------|
| `agent_progress` | どのエージェントが処理中か | ステップインジケーターを更新 |
| `tool_event` | ツール呼び出しの開始・完了 | ツール使用バッジをアニメーション表示 |
| `text` | エージェントのテキスト出力 | チャットに追記 / 成果物プレビューに反映 |
| `image` | GPT Image 1.5 の生成結果 | 画像をインライン表示（base64） |
| `approval_request` | Human-in-the-Loop の承認要求 | 承認 UI を表示 |
| `safety` | Content Safety 分析結果 | Content Safety バッジを更新 |
| `error` | エラー発生 | エラーメッセージ + 再試行ボタンを表示 |
| `done` | パイプライン完了 | 処理メトリクスを表示。修正対話モードに切替 |

**Human-in-the-Loop の実装:**
Workflows の Question ノードが承認を求めると、バックエンドは `approval_request` イベントをフロントエンドに送信する。フロントエンドは承認 UI を表示し、ユーザーの回答を `POST /api/chat/{thread_id}/approve` で送信する。バックエンドは Conversations API 経由でユーザーの回答を Workflows に返す。

### 3.5 Agent 1: データ検索エージェント

**担当: Tokunaga / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG1-01 | 入力解析 | ユーザー指示からターゲット・季節・地域・予算等を抽出する |
| AG1-02 | 販売履歴検索 | Fabric Lakehouse の `sales_history` を SQL エンドポイント経由で検索する |
| AG1-03 | トレンド分析 | 前年比・セグメント比率等の売上トレンドを算出する |
| AG1-04 | 顧客評価分析 | `customer_reviews` から人気プラン・不満点を抽出する |
| AG1-05 | 分析サマリ生成 | Structured Output（JSON Schema 指定）でスキーマ準拠の JSON を出力する |
| AG1-06 | データ可視化 | Foundry Agent Service の組み込み Code Interpreter ツールで売上推移グラフ・セグメント円グラフを生成する。**注意: Code Interpreter はリージョンによって利用不可**。デプロイ先リージョンのツール可用性を事前に確認すること（§8 参照） |

### 3.6 Agent 2: マーケ施策作成エージェント

**担当: Matsumoto / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG2-01 | データ活用 | Agent1 の分析結果 JSON をもとに施策を立案する |
| AG2-02 | 市場トレンド取得 | Web Search ツールで最新の旅行トレンド・競合情報を取得する |
| AG2-03 | プラン生成 | 複数の旅行プラン案を生成する |
| AG2-04 | 課題反映 | 顧客の不満点を改善ポイントとして反映する |
| AG2-05 | コピー生成 | キャッチコピー案を複数パターン生成する |
| AG2-06 | 企画書出力 | Markdown 形式の企画書として出力する |

### 3.7 Agent 3: レギュレーションチェックエージェント

**担当: mmatsuzaki / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG3-01 | 旅行業法チェック | 書面交付義務・広告表示規制・取引条件明示の適合性を確認する |
| AG3-02 | 景品表示法チェック | 有利誤認・優良誤認・二重価格表示の違反がないか確認する |
| AG3-03 | ブランドガイドラインチェック | 社内のトーン＆マナー・ロゴ使用規定への準拠を確認する |
| AG3-04 | NG 表現検出 | 「最安値」「業界 No.1」「絶対」等の禁止表現を検出する |
| AG3-05 | 外部安全情報確認 | 外務省危険情報・気象警報を Web Search ツールで確認する |
| AG3-06 | 修正提案 | 違反箇所に対する修正案を提示する |
| AG3-07 | 修正済みドキュメント出力 | 修正を反映した Markdown を出力する |

### 3.8 Agent 4: ブローシャ＆画像生成エージェント

**担当: Matsumoto / 推論モデル: gpt-5.4-mini + GPT Image 1.5 / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG4-01 | HTML ブローシャ生成 | チェック済み Markdown から HTML ブローシャを生成する |
| AG4-02 | ブランドテンプレート適用 | MCP サーバー経由で社内テンプレートを適用する |
| AG4-03 | ヒーロー画像生成 | GPT Image 1.5 でメイン画像を生成する |
| AG4-04 | バナー画像生成 | GPT Image 1.5 で SNS バナーを生成する |
| AG4-05 | 旅行条件埋め込み | 取引条件・登録番号等をブローシャに自動挿入する |
| AG4-06 | 完了通知 | MCP サーバー経由で Teams に通知する |

**成果物一覧:**

| # | 成果物 | 形式 | 生成モデル |
|---|--------|------|-----------|
| 1 | マーケ施策 企画書 | Markdown | gpt-5.4-mini (Agent2) |
| 2 | 販促ブローシャ | HTML | gpt-5.4-mini (Agent4) |
| 3 | ヒーロー画像 | PNG | GPT Image 1.5 |
| 4 | SNS バナー画像 | PNG | GPT Image 1.5 |

### 3.9 マルチターン修正対話

パイプライン完了後、ユーザーがチャットで成果物の修正指示を出せる。修正対象のエージェントだけを再実行する。

| 修正対象 | 例 | 再実行範囲 |
|---------|-----|-----------|
| 企画書の内容 | 「キャッチコピーを変えて」 | Agent2 → Agent3 → Agent4 |
| 規制チェック結果 | 「この表現は許可されているので戻して」 | Agent3 → Agent4 |
| ブローシャ・画像 | 「画像の色味をもっと明るくして」 | Agent4 のみ |

### 3.10 Observability（評価・監視・トレーシング）

Foundry Observability は 3 つの機能で構成される。評価（Evaluations）と監視（Monitoring）は GA、トレーシング（Tracing）は Preview（2026年3月末に GA 予定）。

**評価（GA）:** Foundry の組み込み評価器で以下の指標をモニタリングする。

- **Groundedness**: Agent3 の結果がナレッジベースに基づいているか
- **Relevance**: Agent2 の施策が Agent1 の分析結果を適切に反映しているか
- **ToolCallAccuracy**: 各エージェントが適切なツールを呼び出しているか
- **TaskAdherence**: 最終成果物がユーザーの指示を満たしているか

**トレーシング（Preview）:** Microsoft Agent Framework は OpenTelemetry を組み込みで提供しており、Application Insights にトレースデータを自動送信する。エージェント間の実行フロー・ツール呼び出し・レイテンシを可視化できる。

---

## 4. データ要件

### 4.1 業務 DB スキーマ

**担当: Tokunaga**

データは Fabric Data Factory で Lakehouse（OneLake）に Delta Parquet 形式で格納する。Agent1 からは SQL エンドポイント経由で Function calling で参照する。

#### sales_history（販売履歴）

| カラム | 型 | 説明 |
|--------|-----|------|
| booking_id | VARCHAR(20) | 予約ID (PK) |
| plan_name | VARCHAR(100) | プラン名 |
| destination | VARCHAR(50) | 目的地 |
| departure_date | DATE | 出発日 |
| pax | INT | 人数 |
| revenue | DECIMAL(10,0) | 売上額（円） |
| customer_segment | VARCHAR(30) | 顧客セグメント |
| booking_date | DATE | 予約日 |

#### customer_reviews（カスタマー評価）

| カラム | 型 | 説明 |
|--------|-----|------|
| review_id | VARCHAR(20) | レビューID (PK) |
| plan_name | VARCHAR(100) | プラン名 |
| rating | INT | 5段階評価（1〜5） |
| comment | TEXT | レビューコメント |
| review_date | DATE | 投稿日 |

#### plan_master（プランマスタ）

| カラム | 型 | 説明 |
|--------|-----|------|
| plan_id | VARCHAR(20) | プランID (PK) |
| plan_name | VARCHAR(100) | プラン名 |
| region | VARCHAR(50) | 地域 |
| season | VARCHAR(20) | 推奨季節 |
| price_range | VARCHAR(30) | 価格帯 |
| category | VARCHAR(30) | カテゴリ |
| itinerary | TEXT | 行程概要 |
| duration_days | INT | 日数 |

### 4.2 デモデータ要件

| テーブル | レコード数目安 | 備考 |
|---------|--------------|------|
| sales_history | 500〜1,000件 | 過去1年分。地域・セグメント分散 |
| customer_reviews | 200〜500件 | 評価 1〜5 分布 |
| plan_master | 30〜50件 | 地域・カテゴリごとに網羅 |

**デモデータ生成方法:**
Python スクリプト（Faker + カスタム季節係数）で生成し、Fabric Data Factory で Lakehouse にロードする。

### 4.3 レギュレーション文書

**担当: mmatsuzaki**

Azure Blob Storage にアップロード → Foundry IQ で Knowledge Source として接続 → Knowledge Base を作成（自動インデキシング・ベクトル化）→ Agent3 のツールとして MCP 経由で接続する。

---

## 5. エージェント間インターフェース

### 5.1 Workflows 実装方針

Foundry Agent Service Workflows で Sequential + Human-in-the-Loop パターンを組み合わせる。エージェントは Microsoft Agent Framework (Python) で実装し、Hosted Agent として Foundry Agent Service にデプロイする。

```yaml
# Workflows 構成イメージ（v3.0: Human-in-the-Loop + 修正ループ）
kind: workflow
trigger:
  kind: OnConversationStart
  id: trigger_wf
actions:
  - kind: InvokeAzureAgent
    id: agent1_data_search
    agent:
      name: DataSearchAgent
    input:
      messages: =UserMessage(System.LastMessageText)
    output:
      messages: Local.LatestMessage

  # --- 施策生成 + 承認ループ ---
  - kind: InvokeAzureAgent
    id: agent2_marketing
    agent:
      name: MarketingPlanAgent
    input:
      messages: =Local.LatestMessage
    output:
      messages: Local.LatestMessage

  - kind: Question
    id: user_approval
    variable: Local.ApprovalResponse
    entity: StringPrebuiltEntity
    prompt: >
      上記の企画書を確認してください。
      承認する場合は「承認」、修正したい場合は修正内容を入力してください。

  # 承認されるまでループ（ForEach + Break パターンの代替として
  # Workflows の Question ノードは承認されるまで繰り返し質問可能）
  - kind: ConditionGroup
    id: approval_check
    conditions:
      - id: if_approved
        condition: =!IsBlank(Find("承認", Local.ApprovalResponse))
        actions:
          # --- 承認後: 規制チェック → 販促物生成 ---
          - kind: InvokeAzureAgent
            id: agent3_regulation
            agent:
              name: RegulationCheckAgent
            input:
              messages: =Local.LatestMessage
            output:
              messages: Local.LatestMessage
          - kind: InvokeAzureAgent
            id: agent4_brochure
            agent:
              name: BrochureGenAgent
            input:
              messages: =Local.LatestMessage
            output:
              messages: Local.LatestMessage
            autoSend: true
    elseActions:
      # --- 修正: Agent2 再生成 → 再度承認を求める ---
      - kind: InvokeAzureAgent
        id: agent2_revision
        agent:
          name: MarketingPlanAgent
        input:
          messages: =Local.ApprovalResponse
        output:
          messages: Local.LatestMessage
      - kind: Question
        id: user_approval_retry
        variable: Local.ApprovalResponse
        entity: StringPrebuiltEntity
        prompt: >
          修正した企画書を確認してください。
          承認する場合は「承認」、さらに修正したい場合は修正内容を入力してください。
```

> **補足:** Workflows の Preview 版では、無限ループ構造に制限がある場合がある。修正ループを 1 回で十分でない場合は、フロントエンド側で再度 Workflow を呼び出す設計も検討する。

**エラーハンドリング方針:**
- 各エージェントの呼び出しが失敗した場合、Workflows は該当ステップで停止する
- フロントエンドは Workflows の状態をポーリングし、エラー時はエラーメッセージと「再試行」ボタンを表示する
- Foundry Observability のトレースで失敗箇所の入出力を確認し、原因を特定できる

---

## 6. UX デザイン方針

### 6.1 対話フロー

ユーザーの体験を「一発生成 → 確認 → 微調整」のサイクルにする。

1. **指示**: 「春の沖縄ファミリー向けプランを企画して」と入力
2. **進捗の可視化**: 各エージェントの処理状況をステップバーで表示。Agent1 の分析グラフはインラインで表示
3. **承認ポイント**: 企画書が表示されたら「承認」「修正」ボタンを提示。修正する場合はチャットで修正指示を入力
4. **成果物プレビュー**: ブローシャ・画像をタブ切り替えでプレビュー
5. **修正対話**: 成果物確認後も「キャッチコピーを変えて」等の修正が可能

### 6.2 UI コンポーネント設計

Social AI Studio のパターンを参考に、以下のコンポーネント構成で実装する。

| コンポーネント | 役割 | 受け取るデータ |
|--------------|------|-------------|
| `App.tsx` | レイアウト管理・状態管理・SSE 接続 | — |
| `Header.tsx` | アプリタイトル・テーマ切替 | — |
| `InputForm.tsx` | 自然言語入力・地域/季節/予算の選択 | — |
| `PipelineStepper.tsx` | 5 ステップの進捗表示（Agent1〜4 + 承認） | `agent_progress` イベント |
| `ToolEventBadges.tsx` | ツール使用状況のアニメーションバッジ | `tool_event` イベント |
| `AnalysisView.tsx` | Agent1 の分析グラフ・サマリ表示 | `text` イベント（JSON） |
| `PlanApproval.tsx` | 企画書プレビュー + 承認/修正ボタン | `approval_request` イベント |
| `RegulationResults.tsx` | 規制チェック結果のハイライト表示 | `text` イベント |
| `BrochurePreview.tsx` | HTML ブローシャのプレビュー | `text` イベント |
| `ImageGallery.tsx` | GPT Image 1.5 の生成画像表示 | `image` イベント |
| `ArtifactTabs.tsx` | 企画書/ブローシャ/画像のタブ切替 | 各成果物の集約 |
| `VersionSelector.tsx` | 成果物バージョンの切替 | 修正履歴 |
| `RefineChat.tsx` | マルチターン修正対話の入力 | — |
| `SafetyBadge.tsx` | Content Safety 結果の動的バッジ | `safety` イベント |
| `MetricsBar.tsx` | 処理メトリクス（レイテンシ・トークン等） | `done` イベント |

### 6.3 画面レイアウト

```
┌────────────────────────────────────────────────────────┐
│ Header (タイトル + テーマ切替)                            │
├──────────────────────┬─────────────────────────────────┤
│                      │                                 │
│  InputForm           │  ArtifactTabs                   │
│  (自然言語入力)       │  ┌─ 企画書 ─ ブローシャ ─ 画像 ┐│
│                      │  │                             ││
│  PipelineStepper     │  │  成果物プレビュー             ││
│  [1]─[2]─[✓]─[3]─[4]│  │  (Markdown / HTML / 画像)    ││
│                      │  │                             ││
│  ToolEventBadges     │  │  SafetyBadge                ││
│  🌐 Web Search ✓    │  │  VersionSelector            ││
│  📁 Foundry IQ ✓    │  └─────────────────────────────┘│
│  🖼 Image Gen 🔄    │                                 │
│                      │  PlanApproval                   │
│  RefineChat          │  [✅ 承認] [✏️ 修正]            │
│  (修正対話)          │                                 │
│                      │  MetricsBar                     │
│                      │  ⏱ 2.3min · 🛠 6 tools · 📝 3K│
├──────────────────────┴─────────────────────────────────┤
│ Footer                                                  │
└────────────────────────────────────────────────────────┘
```

---

## 7. 非機能要件

| 項目 | 要件 |
|------|------|
| レスポンス | パイプライン全体で 3〜5 分以内（Human-in-the-Loop の待ち時間を除く） |
| 入力安全性 | Content Safety + Prompt Shield。AI Gateway 層 + モデルデプロイメント設定の多層防御 |
| 可用性 | ハッカソンデモ時に安定稼働すること（SLA 不要） |
| スケーラビリティ | 単一ユーザーのデモ利用を想定 |
| データ | デモデータを使用（個人情報・機密情報を含まない） |
| 監視 | AI Gateway のトークン消費量・リクエストログを Application Insights で一元監視 |
| 品質評価 | Foundry Observability の組み込み評価器でエージェント品質をモニタリング |
| エラーハンドリング | エージェント失敗時は Workflows が該当ステップで停止。フロントエンドに「再試行」ボタンを表示。Observability トレースで失敗箇所を特定 |
| デプロイ | Docker マルチステージビルド → Azure Container Apps。azd up で一発デプロイ。ローカル開発は Vite proxy + uvicorn |
| テスト | バックエンドに最低限のユニットテスト（pytest）。SSE イベントパースのテストを優先 |
| セキュリティ | Container Apps 層: VNet 統合 + Private Endpoint（Key Vault）。Foundry 層: Hosted Agent が private networking 対応後に VNet 分離を適用（§2.5 参照）。DefaultAzureCredential + Managed Identity。Key Vault でシークレット一元管理。OIDC Workload Identity Federation でデプロイ認証 |
| CI/CD | GitHub Actions DevSecOps: Ruff lint → pytest → tsc → ACR build → Container Apps deploy → Health check → Trivy + Gitleaks + 依存関係監査（§13 参照） |
| アーキテクチャ原則 | サーバーレス / フル PaaS。VM やセルフホストのミドルウェアは使わない。すべて Azure マネージドサービスで構成する |

---

## 8. 前提条件・制約

| 項目 | 内容 |
|------|------|
| Azure サブスクリプション | チームメンバー全員が同一サブスクリプションにアクセスできること |
| デプロイリージョン | Code Interpreter のツール可用性がリージョン依存のため、**East US 2 または Sweden Central** を推奨。Japan East は Code Interpreter が利用できない可能性があるため、事前にツール可用性テーブルを確認すること |
| Microsoft Foundry プロジェクト | New Foundry ポータルでプロジェクト作成済み |
| Microsoft Agent Framework | Python 1.0.0rc5。`uv add agent-framework --prerelease=allow` でインストール（pip の場合は `pip install agent-framework --pre`）。Python 3.14 以上 |
| FastAPI + uvicorn | バックエンド Web フレームワーク。`uv add fastapi uvicorn` でインストール |
| Foundry Agent Service | Prompt Agent は GA。Hosted Agent は Preview（private networking 未対応）。Agent Framework 実装を Hosted Agent としてデプロイ。Foundry 層の VNet 分離は Hosted Agent 対応後に適用 |
| gpt-5.4-mini | Microsoft Foundry 上で GA（2026年3月17日〜）。デプロイ済みであること。低レイテンシ・高スループットのエージェント向けモデル |
| GPT Image 1.5 | Microsoft Foundry 上で GA。ただし利用にはアクセス承認（申請フォーム）が必要。事前に承認を取得し、デプロイ済みであること |
| Foundry IQ | Preview。Azure AI Search リソースが必要。MCP 接続は per-request headers 未対応のため、アプリ共通権限（Managed Identity）で読み取る前提 |
| Workflows | Preview。一部予期しない動作の可能性あり |
| Web Search ツール | Preview。追加 Azure リソース不要だが利用にコスト発生。**DPA 対象外**: クエリデータが Azure の geo boundary 外に流れる可能性あり |
| Azure API Management | AI Gateway 用。Foundry ポータルから有効化可能。Free Tier あり |
| Azure Functions | MCP サーバー用。Flex Consumption プラン（Consumption プランはレガシー。公式 MCP ガイドは Flex Consumption 前提） |
| Azure Container Apps | フロントエンド + バックエンドのデプロイ先。azd up で一発デプロイ |
| Node.js | フロントエンドビルド用。20 以上 |
| Microsoft Fabric | Fabric 容量（Trial / Premium）が有効であること |
| Foundry Observability | 評価・監視は GA。トレーシングは Preview（2026年3月末 GA 予定）。Application Insights リソースが必要 |

---

## 9. 担当分担

| 担当 | ロール | 担当範囲 |
|------|--------|---------|
| **Tokunaga** | Data SE | Fabric Lakehouse / Fabric Data Factory / デモデータ生成 / Agent1 実装 |
| **Matsumoto** | App SE | Web フロントエンド（コンポーネント群 + SSE クライアント）/ FastAPI バックエンド / Agent2 実装 / Agent4 実装 |
| **mmatsuzaki** | Infra SE | Infrastructure（Bicep IaC + Container Apps + azd）/ API Management AI Gateway / Azure Functions MCP サーバー / Foundry IQ Knowledge Base / Agent3 実装 / Content Safety / Observability 設定 |

---

## 10. デモシナリオ

**想定時間: 5〜7 分**（承認ステップ含む）

> **短縮版（3〜5 分）:** ハッカソンの持ち時間が短い場合は、Step 6（承認）を自動承認に切り替え、Step 12（修正対話）を省略する。

| Step | 画面表示 | 説明 |
|------|---------|------|
| 1 | チャット入力画面 | 「春の沖縄ファミリー向けプランを企画して」と入力 |
| 2 | Agent1: 🔄 分析中 | 販売データ・顧客評価を検索・分析。グラフを自動生成 |
| 3 | 分析サマリ + グラフ | 売上推移グラフ・セグメント円グラフをインライン表示 |
| 4 | Agent2: 🔄 生成中 | 分析結果 + 市場トレンド（Web Search）で施策立案 |
| 5 | 企画書 + 承認ボタン | 「✅ 承認」「✏️ 修正」ボタンを表示 |
| 6 | **承認**: ユーザーが承認 | デモのハイライト |
| 7 | Agent3: 🔄 チェック中 | 旅行業法・景表法・安全情報をチェック |
| 8 | チェック結果表示 | 修正箇所をハイライト |
| 9 | Agent4: 🔄 生成中 | ブローシャ・画像を生成 |
| 10 | 成果物プレビュー | タブ切り替えで表示 |
| 11 | Teams 通知 | 完成通知が Teams に投稿される |
| 12 | **修正対話** | 「キャッチコピーをもっとポップに」→ Agent4 のみ再実行 |
| 13 | Observability | トレーシング・品質評価結果を表示（デモの締め） |

---

## 11. デプロイ戦略

### 11.1 Docker マルチステージビルド

Social AI Studio と同じパターンで、フロントエンドとバックエンドを 1 つのコンテナにまとめる。

```dockerfile
# Stage 1: React フロントエンドビルド
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python バックエンド
FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src/ ./src/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
ENV SERVE_STATIC=true PORT=8000
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 11.2 Azure Container Apps デプロイ

Azure Developer CLI（azd）で一発デプロイできる構成にする。

```bash
azd auth login
azd up
```

Container Apps は System Managed Identity で Foundry・Key Vault 等に認証する。環境変数は Key Vault から自動ロードする。

### 11.3 ローカル開発

```bash
# バックエンド
uv sync
uv run uvicorn src.api:app --reload --port 8000

# フロントエンド（別ターミナル）
cd frontend && npm install && npx vite
# → http://localhost:5173（/api は Vite proxy で :8000 に転送）
```

### 11.4 プロジェクト構成

```
travel-marketing-pipeline/
├── .github/
│   └── copilot-instructions.md
├── src/
│   ├── api.py              # FastAPI エンドポイント（SSE + CRUD）
│   ├── config.py            # 環境変数ロード（Key Vault 対応）
│   ├── workflows_client.py  # Conversations API クライアント
│   ├── content_safety.py    # Prompt Shield + Text Analysis
│   └── models.py            # Pydantic データモデル
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/      # §6.2 のコンポーネント群
│   │   └── lib/
│   │       └── api.ts       # SSE クライアント
│   ├── vite.config.ts
│   └── package.json
├── data/
│   └── demo_data_generator.py  # Faker ベースのデモデータ生成
├── regulations/                 # Foundry IQ 用レギュレーション文書
├── infra/
│   └── main.bicep              # Bicep IaC（Container Apps + Key Vault）
├── Dockerfile
├── azure.yaml                  # azd 設定
├── pyproject.toml
└── README.md
```

---

## 12. テスト戦略

### 12.1 バックエンドテスト（pytest）

最低限のテストを用意し、デモ前のリグレッションを防止する。

| テスト対象 | テスト内容 | 優先度 |
|-----------|----------|-------|
| `models.py` | Pydantic モデルのバリデーション（必須フィールド・型・範囲） | 高 |
| `api.py /api/health` | ヘルスチェックの 200 レスポンス | 高 |
| `api.py /api/chat` | 不正リクエストの 400 レスポンス | 高 |
| `content_safety.py` | Prompt Shield の安全/ブロック判定 | 中 |
| `workflows_client.py` | SSE イベントのパース（マーカー文字列の分離） | 中 |

```bash
uv run python -m pytest tests/ -q
```

### 12.2 フロントエンドテスト（vitest）

SSE イベントのパース関数にユニットテストを書く。コンポーネントのテストはハッカソン期間では省略可能。

---

## 13. CI/CD パイプライン（DevSecOps）

GitHub Actions で CI / Deploy / Security Scan の 3 ワークフローを構成する。

### 13.1 パイプラインフロー

```
git push (main)
    │
    ├──→ ci.yml（CI: テスト + リント）
    │     ├── 🔍 Ruff lint（Python）
    │     ├── 🧪 pytest（バックエンドテスト）
    │     └── 🎨 tsc --noEmit + npm run build（フロントエンド）
    │
    ├──→ deploy.yml（Deploy: CI 通過後）
    │     ├── 🔐 Azure Login（OIDC Workload Identity Federation）
    │     ├── 🐳 az acr build（Docker マルチステージ → ACR）
    │     ├── 🚀 az containerapp update（Container App 更新）
    │     └── ✅ Health check（/api/health × 5 回リトライ）
    │
    └──→ security.yml（Security: push / PR / 週次）
          ├── 🛡️ Trivy（ファイルシステム脆弱性スキャン → SARIF）
          ├── 🔐 Gitleaks（シークレット検出）
          └── 📦 npm audit + pip-audit（依存関係監査）
```

### 13.2 認証方式（OIDC）

GitHub Actions から Azure へのデプロイは OIDC Workload Identity Federation で認証する。サービスプリンシパルのシークレットを GitHub に保存する必要がなく、トークンは一時的に発行される。

```yaml
# deploy.yml の認証部分
- name: Azure Login (OIDC)
  uses: azure/login@v2
  with:
    client-id: ${{ vars.AZURE_CLIENT_ID }}
    tenant-id: ${{ vars.AZURE_TENANT_ID }}
    subscription-id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
```

GitHub Variables に設定する値:
- `AZURE_CLIENT_ID`: Federated Credential のクライアント ID
- `AZURE_TENANT_ID`: Azure AD テナント ID
- `AZURE_SUBSCRIPTION_ID`: Azure サブスクリプション ID

### 13.3 Python リント設定

```toml
# pyproject.toml
[tool.ruff]
target-version = "py314"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
ignore = ["E501", "E402"]
```

---

## 付録 A: 用語集

| 用語 | 説明 |
|------|------|
| Microsoft Foundry | 旧称 Azure AI Foundry（2025年11月リネーム）。エンタープライズ AI の統合プラットフォーム |
| Foundry Agent Service | AI エージェントのフルマネージドサービス。Prompt Agent は GA、Hosted Agent / Workflow Agent は Preview |
| Microsoft Agent Framework | Semantic Kernel / AutoGen の後継 OSS フレームワーク。Python / .NET 対応。RC 状態 |
| Workflows | Foundry Agent Service のワークフロー機能。Preview |
| Hosted Agent | Agent Framework 等で実装したエージェントを Foundry にコンテナデプロイする形態 |
| AI Gateway | Azure API Management の AI 向け機能群 |
| Fabric Data Factory | Microsoft Fabric のデータ統合サービス |
| Fabric Lakehouse | Delta Parquet でデータを格納し SQL エンドポイントで参照可能なデータストア |
| Foundry IQ Knowledge Base | エンタープライズナレッジレイヤー。Azure AI Search 基盤。Preview |
| Web Search ツール | Foundry Agent Service の Web 検索ツール。追加リソース不要。Preview |
| MCP | Model Context Protocol。エージェントと外部ツールの通信プロトコル |
| gpt-5.4-mini | OpenAI の高効率推論モデル（2026年3月リリース）。GPT-5.4 の能力を小型化し、ツール呼び出し・マルチモーダル・推論を低レイテンシで実行できる。Microsoft Foundry 上で GA |
| GPT Image 1.5 | OpenAI の画像生成モデル。Microsoft Foundry 上で GA。利用にはアクセス承認が必要 |
| Content Safety | Azure AI Content Safety + Prompt Shield |
| Foundry Observability | 評価（Evaluations）・監視（Monitoring）・トレーシング（Tracing）の統合機能。評価と監視は GA、トレーシングは Preview |
| Structured Output | JSON Schema 指定によるスキーマ準拠出力の保証 |
| agentic retrieval | Foundry IQ の LLM ベースクエリエンジン |
| Human-in-the-Loop | ワークフロー中に人間の判断・承認を挟む設計パターン |
| SSE | Server-Sent Events。サーバーからクライアントへの一方向リアルタイム通信。HTTP 上で動作し WebSocket より軽量 |
| FastAPI | Python の非同期 Web フレームワーク。SSE ストリーミングに対応 |
| azd | Azure Developer CLI。`azd up` で Azure リソースのプロビジョニングとアプリデプロイを一括実行できるツール |
| OIDC Workload Identity Federation | GitHub Actions から Azure に認証する仕組み。サービスプリンシパルのシークレットを保存せず、一時トークンで認証する |
| DevSecOps | 開発（Dev）・セキュリティ（Sec）・運用（Ops）を CI/CD パイプラインに統合する手法 |
| i18n | 多言語対応（Internationalization）。UI ラベルやメッセージを複数言語で表示する仕組み |
| ブローシャ | 旅行プランの販促パンフレット・チラシ |

---

## 付録 B: 参考リンク

| リソース | URL |
|---------|-----|
| Microsoft Agent Framework (GitHub) | https://github.com/microsoft/agent-framework |
| Microsoft Agent Framework (PyPI) | https://pypi.org/project/agent-framework/ |
| Agent Framework RC ブログ | https://devblogs.microsoft.com/foundry/microsoft-agent-framework-reaches-release-candidate/ |
| Foundry Agent Service 概要 | https://learn.microsoft.com/en-us/azure/foundry/agents/overview |
| Foundry Agent Service GA ブログ | https://devblogs.microsoft.com/foundry/foundry-agent-service-ga/ |
| Workflows を構築する | https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/workflow |
| Workflows 紹介ブログ | https://devblogs.microsoft.com/foundry/introducing-multi-agent-workflows-in-foundry-agent-service/ |
| Foundry IQ とは | https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq |
| Foundry IQ を Agent Service に接続 | https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect |
| Web Search ツール | https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-search |
| Web grounding 概要（ツール比較） | https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-overview |
| AI Gateway (API Management) | https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities |
| AI Gateway を Foundry で有効化 | https://learn.microsoft.com/en-us/azure/foundry/configuration/enable-ai-api-management-gateway-portal |
| AI Gateway Labs (GitHub) | https://github.com/Azure-Samples/AI-Gateway |
| GPT Image 1.5 (Foundry) | https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-openai%E2%80%99s-gpt-image-1-5-in-microsoft-foundry/4478139 |
| Fabric Data Factory 概要 | https://learn.microsoft.com/en-us/fabric/data-factory/data-factory-overview |
| Foundry Observability | https://learn.microsoft.com/en-us/azure/foundry/concepts/observability |
| Foundry Observability GA ブログ | https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/generally-available-evaluations-monitoring-and-tracing-in-microsoft-foundry/4502760 |
| gpt-5.4-mini / nano 発表ブログ | https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-openai%E2%80%99s-gpt-5-4-mini-and-gpt-5-4-nano-for-low-latency-ai/4500569 |
| gpt-5.4-mini モデルカタログ | https://ai.azure.com/catalog/models/gpt-5.4-mini |
| Azure Container Apps 概要 | https://learn.microsoft.com/en-us/azure/container-apps/overview |
| OIDC Workload Identity Federation (GitHub Actions) | https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust-user-assigned-managed-identity |
| Hosted Agent 概要（private networking 制約含む） | https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents |
| Azure Functions MCP サーバー（Flex Consumption） | https://learn.microsoft.com/en-us/azure/azure-functions/scenario-custom-remote-mcp-server |
| Azure Functions MCP SDK ホスティング | https://learn.microsoft.com/en-us/azure/azure-functions/scenario-host-mcp-server-sdks |
| Flex Consumption プラン概要 | https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan |
| Web grounding 概要（データ境界制約含む） | https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-overview |
| Prompt Shield（tool response 対応） | https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/content-filter-prompt-shields |
| ツール可用性（リージョン・モデル別） | https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions |
| MCP ツールガバナンス | https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/governance |
| Private networking 設定 | https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks |

---

## 付録 C: 変更履歴

| 日付 | 版 | 変更内容 | 変更者 |
|------|----|---------|--------|
| 2026-03-27 | 1.0 | 初版作成 | Team D |
| 2026-03-27 | 2.0 | 技術スタック全面見直し。Sora 2 をスコープ外に変更 | Team D |
| 2026-03-27 | 3.0 | エージェント実装を Microsoft Agent Framework (Python) に変更。Bing grounding → Web Search ツールに変更。Human-in-the-Loop 承認フロー追加（修正ループ含む）。マルチターン修正対話機能追加。Azure API Management AI Gateway 追加。Azure Functions MCP サーバー追加（実装方法明記）。Foundry Observability 追加。Agent1 に Code Interpreter・Structured Output 追加。Agent2 に Web Search（市場トレンド）追加。Agent4 に MCP 連携追加。UX デザイン方針セクション追加。フロントエンド ↔ Workflows 接続方法追加。エラーハンドリング方針追加。Hosted Agent の Preview 状態を明記 | Team D |
| 2026-03-27 | 3.1 | Social AI Studio（既存ハッカソンプロジェクト）のパターンを参考にブラッシュアップ。FastAPI バックエンド構成を追加（§2.6）。SSE ストリーミング設計とイベント種別を定義（§3.4 拡張）。Content Safety を入力/モデル/出力の 3 層に分離（§3.2 拡張）。フロントエンドコンポーネント設計を具体化（§6.2 拡張、16 コンポーネント + レイアウト図）。デプロイ戦略を追加（§11: Docker マルチステージ + Container Apps + azd + プロジェクト構成）。テスト戦略を追加（§12: pytest + vitest）。技術スタックに TypeScript / Tailwind CSS / Vite を追加。FE-11（Safety バッジ）/ FE-12（処理メトリクス）を追加。エクスポート形式を Markdown / HTML / PNG / JSON に具体化 | Team D |
| 2026-03-27 | 3.2 | 推論モデルを gpt-5.4-mini に変更。サーバーレス / フル PaaS 設計原則を追加（§2.1）。エンタープライズセキュリティ設計を追加（§2.5: VNet + Private Endpoint + Managed Identity + Key Vault + OIDC）。CI/CD DevSecOps パイプラインを追加（§13: CI + Deploy + Security Scan via GitHub Actions）。フロントエンド多言語 UI（i18n: 日英中）+ ダーク/ライトモード対応を追加（FE-13, FE-14） | Team D |
| 2026-03-28 | 3.3 | 有識者レビュー反映。Hosted Agent と private networking の優先順位を明記（§2.5）。Azure Functions を Flex Consumption に修正（§2.1/§2.7/§8）。Observability のトレーシング Preview を明記（§3.10/§8/用語集）。GPT Image 1.5 のアクセス承認前提を追加（§8/用語集）。Web Search のデータ境界例外を追加（§2.5/§8）。AI Gateway の位置づけを明確化（§2.6）。Prompt Shield を tool response に拡張（§3.2）。Code Interpreter のリージョン依存を注記（§3.5/§8）。Foundry IQ のアプリ共通権限前提を明記（§2.5/§8）。Out of Scope から多言語対応を削除し In Scope に移動 | Team D |
| 2026-03-28 | 3.4 | Python 3.12 → 3.14 に変更（§2.3 技術スタック表、§8 前提条件、§11.1 Dockerfile、§13.3 Ruff target-version）。ghcp-setup（Copilot カスタム命令一式）との整合確認済み | Team D |
