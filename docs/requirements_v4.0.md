# 要件定義書: 旅行マーケティング AI マルチエージェント パイプライン

> **プロジェクト名**: Team D ハッカソン  
> **作成日**: 2026-04-01  
> **作成者**: Team D (Tokunaga / Matsumoto / mmatsuzaki)  
> **ステータス**: v4.0

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
- 7 つの AI エージェント（データ検索 / 施策生成 / 規制チェック / 企画書修正 / 販促物生成 / 動画生成 / 品質レビュー）
- Microsoft Agent Framework によるエージェント実装
- FastAPI バックエンドによるオーケストレーション（Agent Framework SequentialBuilder は直接使用せず、FastAPI 内で独自に承認フロー・修正ルーティング・画像 side-channel を制御）
- 業務 DB（Fabric Lakehouse + デモデータ）
- レギュレーション文書リポジトリ（Foundry IQ Knowledge Base）
- 画像生成（GPT Image 1.5）
- Azure API Management AI Gateway（トークン管理・監視・負荷分散）
- APIM AI Gateway 経由のモデル呼び出し（トークン制限・メトリクス・監視）
- Content Safety + Prompt Shield によるプロンプトインジェクション防止
- Foundry Observability（トレーシング + 評価）
- フロントエンド多言語 UI（日本語・英語・中国語）+ ダーク/ライトモード
- Voice Live による音声入力（Preview）
- Foundry Evaluations による品質評価 API（`/api/evaluate`）+ 評価起点の改善フロー
- Logic Apps による承認後自動アクション（Teams 通知 + SharePoint 保存）
- Content Understanding による既存パンフレット PDF 解析
- Photo Avatar + Voice Live による販促紹介動画の自動生成
- Azure Cosmos DB による会話履歴の永続化
- GitHub Copilot SDK による品質レビューエージェント統合
- Fabric Data Agent 連携（Published URL 経由の自然言語データ分析）
- 評価起点の成果物改善フロー（評価 → フィードバック → 企画書再生成 → 再承認 → 下流再生成）
- 成果物バージョン管理（VersionSelector による v1/v2 全成果物切替）

**Out of Scope:**

- 実際の旅行予約システムとの連携
- 本番環境への展開
- 決済・課金機能
- Teams 公開（Foundry → Teams チャネル配信）: 現行アーキテクチャでは FastAPI がオーケストレーターのため、Foundry Agent Service に登録されたエージェントが存在せず Teams 公開できない。Hosted Agent 化後の将来課題

---

## 2. システム構成

### 2.1 設計原則

本システムはサーバーレス / フル PaaS アーキテクチャで構築する。VM やセルフホストのミドルウェアは使わず、Azure のマネージドサービスだけで完結させる。

- **コンピュート**: Azure Container Apps（サーバーレススケーリング、ゼロインスタンスまでスケールダウン可能）
- **オーケストレーション**: FastAPI バックエンド（エージェントオーケストレーション・SSE ストリーミング・承認フロー制御）
- **データ**: Fabric Lakehouse（サーバーレス SQL エンドポイント）
- **ナレッジ**: Foundry IQ（マネージド検索）
- **API Gateway**: Azure API Management AI Gateway（マネージド）
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
│ FastAPI バックエンド           │  ← SSE ストリーミング
│ (オーケストレーション /        │     承認フロー制御
│  Content Safety / 画像        │     修正ルーティング
│  side-channel)               │
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│ Azure API Management         │  ← AI Gateway
│ (トークン管理/負荷分散/監視)  │
│ ※ APIM_GATEWAY_URL 設定時    │
└──────────┬──────────────────┘
           │
           ▼  ※ Content Safety + Prompt Shield（入力時点で適用）
           │
┌─────────────────────────────┐
│ AI Services (gpt-5.4-mini)   │
│                              │
│ エージェント実装:              │
│ Microsoft Agent Framework     │  ← Python (1.0.0rc5)
└──┬──┬──┬──┬──┬──┬──┬───────┘
   │  │  │  │  │  │  │
   ▼  │  │  │  │  │  │     ┌───────────────────────┐
 Agent1│  │  │  │  │  │     │ Fabric Data Agent      │
 データ│  │  │  │  │  │◄───►│ (NL2SQL 最優先)        │
 検索  │  │  │  │  │  │     ├───────────────────────┤
   │  │  │  │  │  │  │     │ Fabric Lakehouse       │
   │  │  │  │  │  │  │◄───►│ (SQL エンドポイント)    │ ← Tokunaga
   │  │  │  │  │  │  │     └───────────────────────┘
   │  ▼  │  │  │  │  │
   │ Agent2│  │  │  │  │     ┌───────────────────────┐
   └►施策 │  │  │  │  │◄───►│ Web Search ツール       │
     生成 │  │  │  │  │     │ (市場トレンド取得)      │
       │  │  │  │  │  │     └───────────────────────┘
       │  │  │  │  │  │
       ▼  │  │  │  │  │
     ┌─────────┐     │  │     ← Human-in-the-Loop: ユーザー承認
     │ 承認     │     │  │
     └────┬────┘     │  │
          ▼          │  │
       Agent3a       │  │     ┌───────────────────────┐
       規制    ◄─────┘  │────►│ Foundry IQ             │
       チェック          │     │ Knowledge Base          │ ← mmatsuzaki
          │             │     └───────────────────────┘
          ▼             │     ┌───────────────────────┐
       Agent3b          │────►│ Web Search ツール       │
       企画書修正        │     │ (外務省・気象情報等)    │
          │             │     └───────────────────────┘
          ▼             │
       Agent4           │
       販促物           │     ┌───────────────────────┐
       生成    ─────────┤────►│ GPT Image 1.5          │
          │             │     └───────────────────────┘
          ▼             │
       Agent5           │     ┌───────────────────────┐
       動画生成  ───────┤────►│ Photo Avatar           │
          │             │     └───────────────────────┘
          ▼             │
       📦 成果物         │
       (企画書 / ブローシャ│
        / 画像 / 動画)   │
          │             │
          ▼             ▼
       Agent6 (オプショナル)
       品質レビュー
          │
          ▼
       Foundry Observability
       (トレーシング + 評価 API)
```

### 2.3 技術スタック

| カテゴリ | 技術 | 用途 | 備考 |
|---------|------|------|------|
| フロントエンド | React + TypeScript | チャット UI・成果物プレビュー・修正対話 | Vite でビルド。Tailwind CSS |
| バックエンド | FastAPI + uvicorn | SSE ストリーミング API・静的ファイル配信 | Python 3.14。エージェントオーケストレーション |
| 推論モデル | gpt-5.4-mini | 7 エージェントの推論・テキスト生成 | GA（2026年3月〜）。低レイテンシ・高スループット |
| 画像生成モデル | GPT Image 1.5 | バナー画像・ブローシャ用画像 | Microsoft Foundry 上で GA |
| AI Gateway | Azure API Management (AI Gateway) | トークン管理・負荷分散・セマンティックキャッシュ・監視 | Foundry ポータルから有効化可能 |
| オーケストレーション | FastAPI バックエンド（独自オーケストレーション） | エージェント実行順序・承認フロー・修正ルーティング・画像 side-channel・動画ポーリング | `src/api/chat.py` で制御 |
| エージェント実装 | Microsoft Agent Framework (Python) | 7 つのエージェントのコア実装 | 1.0.0rc5。Hosted Agent として Foundry にデプロイ |
| データ基盤 | Fabric Data Factory + Lakehouse | デモデータの取り込み・格納・クエリ | Delta Parquet 格納。SQL エンドポイント経由 |
| Fabric Data Agent | Fabric Data Agent Published URL | NL2SQL によるデータ分析 | OpenAI Assistants API 互換 |
| ナレッジ検索 | Foundry IQ Knowledge Base | レギュレーション文書の agentic retrieval | Azure AI Search が基盤。Preview |
| 外部情報検索 | Web Search ツール (Preview) | 外務省安全情報・気象情報・市場トレンド | 追加 Azure リソース不要 |
| 入力安全性 | Content Safety + Prompt Shield | 悪意ある入力のブロック・Jailbreak 検知 | モデルデプロイメント設定で有効化 |
| 監視・評価 | Foundry Observability | トレーシング・品質評価・ダッシュボード | Application Insights 連携。評価・監視・トレーシングすべて GA |
| デプロイ | Azure Container Apps | フロントエンド + バックエンドの一体デプロイ | Docker マルチステージビルド。azd up 対応 |
| 音声入力 | Voice Live API | リアルタイム音声対話。マイクから指示 → パイプライン実行 | Foundry Agent Service に統合。Preview |
| 文書解析 | Content Understanding | 既存パンフレット PDF のレイアウト・テキスト解析 | Foundry Tools。RAG アナライザー。GA |
| 販促動画 | Photo Avatar + Voice Live | 旅行プラン紹介動画の自動生成（15〜30 秒） | Photo Avatar は Preview |
| ワークフロー自動化 | Azure Logic Apps | 承認後の Teams 通知・SharePoint 保存・メール送信 | 1,400+ コネクタ。ノーコード |
| 会話履歴 | Azure Cosmos DB (NoSQL) | 会話スレッド・成果物バージョンの永続化 | サーバーレス容量モード。DefaultAzureCredential 認証 |
| Copilot 統合 | GitHub Copilot SDK (Python) | Agent Framework と Copilot SDK の連携。品質レビューエージェント | Technical Preview。`GitHubCopilotAgent` で統合 |
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

- Key Vault は Private Endpoint 経由でのみアクセス可能にする（`publicNetworkAccess: Disabled`）
- Application Insights は Container Apps からの OTel トレースを受け取る
- Foundry Agent Service 層は現時点では Basic Setup（パブリックエンドポイント）で構成する

**認証・認可:**
- Container Apps は System Managed Identity を使い、Foundry / Key Vault / Fabric に認証する
- API キーのハードコードは禁止。すべて DefaultAzureCredential 経由にする
- Key Vault の RBAC で Key Vault Secrets User ロールを Container Apps の Managed Identity に割り当てる

**シークレット管理:**

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

本プロジェクトでは Azure API Management を **アプリ前段のリバースプロキシ**として配置する。Container Apps（FastAPI）と AI Services の間に挟み、トークン消費量のリアルタイム可視化、複数モデルデプロイメントへの負荷分散（ラウンドロビン / 優先度ベース）、TPM 制限による暴走防止、Application Insights と連携したリクエストログの一元監視を提供する。

`APIM_GATEWAY_URL` が設定されている場合、エージェントのモデル呼び出しは APIM AI Gateway を経由する。未設定時は project endpoint に直接接続（フォールバック）。

Foundry ポータルから直接有効化できる「Foundry 統合 AI Gateway」もあるが、こちらは Preview で、same tenant / same subscription / v2 tier 等の条件がある。本プロジェクトでは既存の APIM リソースをリバースプロキシとして使う方式を基本とする。

### 2.7 カスタムツール実装方針

**v3.7 からの変更**: Azure Functions MCP サーバーは廃止し、カスタムツールはすべてエージェント内の `@tool` デコレータで直接定義する方式に変更した。Logic Apps callback は FastAPI の `_trigger_logic_app()` で HTTP トリガーを呼び出す。

### 2.8 バックエンド構成（FastAPI + SSE）

React フロントエンドと AI Services の間に FastAPI バックエンドを配置する。フロントエンドが直接 AI Services を叩くのではなく、バックエンドを中継する設計にする理由は以下の通り。

1. **SSE ストリーミングの制御**: エージェントからのレスポンスをパースし、エージェント進捗・ツールイベント・テキストコンテンツを SSE イベントとして分離してフロントエンドに送信できる
2. **Content Safety の入力チェック**: Prompt Shield をバックエンド側で実行し、悪意ある入力をブロックしてからエージェントに渡せる
3. **認証の集約**: DefaultAzureCredential をバックエンドで管理し、フロントエンドにトークンを露出させない
4. **会話履歴の管理**: マルチターン修正対話のための会話コンテキストをバックエンド側で保持できる
5. **オーケストレーション制御**: エージェント実行順序・承認フロー・画像 side-channel・動画ポーリングを直接制御できる

```
React (Vite)  ←→  FastAPI (uvicorn)  ←→  APIM AI Gateway  ←→  AI Services
   :5173            :8000                    (APIM_GATEWAY_URL     (project endpoint
   SSE stream       POST /api/chat            設定時)              フォールバック)
                    GET  /api/health
                    POST /api/evaluate
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
| FE-05 | レギュレーションチェック結果表示 | Agent3a のチェック結果をハイライト表示する |
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
FastAPI バックエンドの `/api/chat` エンドポイントでユーザー入力を受け取った直後に、Azure AI Content Safety の Prompt Shield を実行する。プロンプトインジェクション攻撃が検出された場合は 400 エラーを返し、エージェントには渡さない。

**モデル呼び出し時（Content Filter）:**
Microsoft Foundry のモデルデプロイメント設定で Content filter を有効にする。各エージェントが gpt-5.4-mini を呼び出す際に、入出力の両方が自動的にフィルタリングされる。

**ツール応答時（Prompt Shield for tool response）:**
Web Search ツールの応答にもプロンプトインジェクション（間接攻撃）のリスクがある。Foundry Agent Service のガードレール設定で、tool response に対する Prompt Shield を有効にする。これにより、外部データソースから注入された悪意ある指示を検出・ブロックできる。AI Gateway 側でも `llm-content-safety` ポリシーで tool response の検査を適用する。

**出力時（Text Analysis）:**
パイプライン完了後、生成されたコンテンツ全体に対して Azure AI Content Safety の Text Analysis を実行する。4 つのカテゴリ（Hate / SelfHarm / Sexual / Violence）のスコアを取得し、結果を SSE イベントとしてフロントエンドに送信する。フロントエンドは Content Safety バッジ（FE-11）で結果を表示する。

**AI Gateway 層（ポリシー適用）:**
API Management の Content Safety ポリシーでも追加のフィルタリングを適用し、多層防御を実現する。

### 3.3 Human-in-the-Loop（承認フロー）

Agent2（施策生成）の完了後に、ユーザーが企画書の内容を確認する承認ステップを設ける。

FastAPI バックエンド内で `_pending_approvals` 辞書と SSE `approval_request` イベントにより Human-in-the-Loop を実装する。Agent2（施策生成）完了後に `approval_request` を返し、ユーザーの応答を `POST /api/chat/{thread_id}/approve` で受け付ける。

1. Agent2 が企画書 Markdown を生成する
2. FastAPI が `approval_request` SSE イベントをフロントエンドに送信し、承認を求める
3. 「承認」→ Agent3a に進む / 「修正」→ 修正指示を入力し Agent2 が再生成 → 再度承認を求める

法令チェック前に人間が内容を確認できるため、修正コストを減らせる。

### 3.4 フロントエンド ↔ バックエンド接続

```
React           FastAPI
(SSE client)    (SSE streaming API + エージェントオーケストレーション)
    │                │
    ├─ POST /api/chat ──►│
    │                ├─ Prompt Shield ──────────►│
    │                ├─ Agent1〜6 を順次実行 ───►│ AI Services
    │                │◄── エージェント応答 ───────┤ (APIM 経由)
    │◄── SSE events ─┤   (agent progress)
    │  (parsed)      ├─ Content Safety ────────►│
    │◄── safety ─────┤   (output analysis)
    │◄── done ───────┤
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
Agent2 完了後、バックエンドは `approval_request` イベントをフロントエンドに送信する。フロントエンドは承認 UI を表示し、ユーザーの回答を `POST /api/chat/{thread_id}/approve` で送信する。バックエンドは承認結果に応じて Agent3a 以降のパイプラインを続行、または Agent2 を再実行する。

### 3.5 Agent 1: データ検索エージェント

**担当: Tokunaga / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG1-01 | 入力解析 | ユーザー指示からターゲット・季節・地域・予算等を抽出する |
| AG1-02 | 販売履歴検索 | Fabric Lakehouse の `sales_results` を SQL エンドポイント経由で検索する |
| AG1-03 | トレンド分析 | 前年比・セグメント比率等の売上トレンドを算出する |
| AG1-04 | 顧客評価分析 | `customer_reviews` から人気プラン・不満点を抽出する |
| AG1-05 | 分析サマリ生成 | Markdown 形式で分析サマリを出力する |
| AG1-06 | データ可視化 | Foundry Agent Service の組み込み Code Interpreter ツールで売上推移グラフ・セグメント円グラフを生成する。**注意: Code Interpreter はリージョンによって利用不可**。デプロイ先リージョンのツール可用性を事前に確認すること（§8 参照） |
| AG1-07 | Fabric Data Agent 検索 | `FABRIC_DATA_AGENT_URL` が設定されている場合、Fabric Data Agent Published URL を最優先で使い、NL2SQL でデータ分析を実行する。利用不可時は SQL → CSV にフォールバック |

### 3.6 Agent 2: マーケ施策作成エージェント

**担当: Matsumoto / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG2-01 | データ活用 | Agent1 の分析結果をもとに施策を立案する |
| AG2-02 | 市場トレンド取得 | Web Search ツールで最新の旅行トレンド・競合情報を取得する |
| AG2-03 | プラン生成 | 複数の旅行プラン案を生成する |
| AG2-04 | 課題反映 | 顧客の不満点を改善ポイントとして反映する |
| AG2-05 | コピー生成 | キャッチコピー案を複数パターン生成する |
| AG2-06 | 企画書出力 | Markdown 形式の企画書として出力する |

### 3.7a Agent 3a: レギュレーションチェックエージェント

**担当: mmatsuzaki / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG3-01 | 旅行業法チェック | 書面交付義務・広告表示規制・取引条件明示の適合性を確認する |
| AG3-02 | 景品表示法チェック | 有利誤認・優良誤認・二重価格表示の違反がないか確認する |
| AG3-03 | ブランドガイドラインチェック | 社内のトーン＆マナー・ロゴ使用規定への準拠を確認する |
| AG3-04 | NG 表現検出 | 「最安値」「業界 No.1」「絶対」等の禁止表現を検出する |
| AG3-05 | 外部安全情報確認 | 外務省危険情報・気象警報を Web Search ツールで確認する |
| AG3-06 | 修正提案 | 違反箇所に対する修正案を提示する |

### 3.7b Agent 3b: 企画書修正エージェント

**担当: mmatsuzaki / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG3-07 | 修正済みドキュメント出力 | 修正済み企画書を出力する（独立したエージェントとして実行）。Agent3a のチェック結果（違反指摘・修正提案）と元の企画書を受け取り、すべての指摘事項を反映した完全な修正版企画書を生成する |

### 3.8 Agent 4: ブローシャ＆画像生成エージェント

**担当: Matsumoto / 推論モデル: gpt-5.4-mini + GPT Image 1.5 / 実装: Microsoft Agent Framework (Python)**

> **注意**: Agent4 は**顧客向け**ブローシャを生成し、KPI・売上目標・社内分析・競合分析などの社内情報を含めない。

| ID | 機能 | 詳細 |
|----|------|------|
| AG4-01 | HTML ブローシャ生成 | チェック済み Markdown から顧客向け HTML ブローシャを生成する |
| AG4-03 | ヒーロー画像生成 | GPT Image 1.5 でメイン画像を生成する |
| AG4-04 | バナー画像生成 | GPT Image 1.5 で SNS バナーを生成する |
| AG4-06 | 既存パンフレット参照 | Content Understanding で既存の旅行パンフレット PDF を解析し、レイアウト構成・キャッチコピーのトーン・写真配置を参考にしてブローシャを生成する |
| AG4-07 | 旅行条件埋め込み | 取引条件・登録番号等をブローシャに自動挿入する |

### 3.8b Agent 5: 動画生成エージェント (video-gen-agent)

**担当: Matsumoto / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python)**

| ID | 機能 | 詳細 |
|----|------|------|
| AG5-01 | 販促紹介動画生成 | Photo Avatar で販促紹介動画（casual-sitting スタイル、ja-JP-NanamiNeural 音声）を MP4/H.264 で自動生成する。企画書のサマリーを元に、ナレーション付きの紹介動画を自動作成する |

**構成:**
- アバター: `lisa`
- スタイル: `casual-sitting`
- 音声: `ja-JP-NanamiNeural`
- 出力: MP4 動画（15〜30 秒、720p）

### 3.8c Agent 6: 品質レビューエージェント (quality-review-agent)

**担当: Matsumoto / 推論モデル: gpt-5.4-mini / 実装: Microsoft Agent Framework (Python) + GitHub Copilot SDK**

`GitHubCopilotAgent` を優先使用し、`PermissionHandler.approve_all` で自動権限承認を設定。利用不可時は `AzureOpenAIResponsesClient` にフォールバック。バックグラウンドで実行され、`AZURE_AI_PROJECT_ENDPOINT` 未設定時はスキップされる。

**チェック項目:**

| ID | 機能 | 詳細 |
|----|------|------|
| AG6-01 | 企画書構造品質 | 企画書の 5 必須セクション（タイトル / キャッチコピー / ターゲット / 概要 / KPI）を検証する |
| AG6-02 | ブローシャアクセシビリティ | HTML アクセシビリティ 4 項目チェック（alt属性 / lang属性 / フッター / フォントサイズ）を行う |
| AG6-03 | テキストトーン一貫性 | ブランドガイドライン準拠のトーン一貫性を確認する |
| AG6-04 | 旅行業法適合 | 旅行業法の表記ルール準拠を最終チェックする |

**成果物一覧:**

| # | 成果物 | 形式 | 生成モデル |
|---|--------|------|-----------|
| 1 | マーケ施策 企画書 | Markdown | gpt-5.4-mini (Agent2) |
| 2 | 販促ブローシャ | HTML | gpt-5.4-mini (Agent4) |
| 3 | ヒーロー画像 | PNG | GPT Image 1.5 |
| 4 | SNS バナー画像 | PNG | GPT Image 1.5 |
| 5 | 販促紹介動画 | MP4 | Voice Live + Photo Avatar (Agent5) |

### 3.9 マルチターン修正対話

パイプライン完了後、ユーザーがチャットで成果物の修正指示を出せる。修正対象のエージェントだけを再実行する。

| 修正対象 | 例 | 再実行範囲 |
|---------|-----|-----------|
| 企画書の内容 | 「キャッチコピーを変えて」 | キーワードルーティングで marketing-plan-agent を再実行 |
| 規制チェック結果 | 「この表現は許可されているので戻して」 | Agent3a → Agent3b |
| ブローシャ・画像 | 「画像の色味をもっと明るくして」 | Agent4 のみ |
| 評価フィードバック | 「評価結果を踏まえて改善して」 | 評価フィードバック → marketing-plan 再生成 → 再承認 → 下流全再生成 |

### 3.10 Observability（評価・監視・トレーシング）

Foundry Observability は 3 つの機能で構成される。評価（Evaluations）・監視（Monitoring）・トレーシング（Tracing）はすべて GA（2026年3月16日〜）。

**評価（`/api/evaluate` エンドポイント）:** FastAPI バックエンドの `/api/evaluate` エンドポイントとして実装済み。以下の評価器を使用する。

**組み込み評価器（GA）:**
- **Relevance**: Agent2 の施策が Agent1 の分析結果を適切に反映しているか
- **Coherence**: 企画書の論理的一貫性
- **Fluency**: テキストの流暢さ・自然さ

**カスタムコード評価器:**
- **travel_law_compliance**: 旅行業法の必須表記チェック
- **conversion_potential**: コンバージョンポテンシャル（CTA・価格訴求等）

**カスタムプロンプト評価器（LLM judge）:**
- **appeal**: 訴求力
- **differentiation**: 差別化ポイント
- **kpi_validity**: KPI の妥当性
- **brand_tone**: ブランドトーンの一貫性
- **overall**: 総合評価

**Foundry ポータル連携:** 評価結果は Foundry ポータルにもログされ、`foundry_portal_url` で確認できる。

**将来追加予定:**
- Groundedness（ナレッジベース接地性）
- ToolCallAccuracy（ツール呼び出し精度）
- TaskAdherence（タスク遵守度）
- 継続的評価（本番トラフィックの自動サンプリング）
- Azure Monitor アラート（品質閾値低下の通知）

**トレーシング（GA）:** Microsoft Agent Framework は OpenTelemetry を組み込みで提供しており、Application Insights にトレースデータを自動送信する。エージェント間の実行フロー・ツール呼び出し・レイテンシを可視化できる。

---

## 4. データ要件

### 4.1 業務 DB スキーマ

**担当: Tokunaga**

データは Fabric Data Factory で Lakehouse（OneLake）に Delta Parquet 形式で格納する。Agent1 からは SQL エンドポイント経由で Function calling で参照する。

#### sales_results（販売実績）

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
| sales_results | 500〜1,000件 | 過去1年分。地域・セグメント分散 |
| customer_reviews | 200〜500件 | 評価 1〜5 分布 |
| plan_master | 30〜50件 | 地域・カテゴリごとに網羅 |

**デモデータ生成方法:**
Python スクリプト（Faker + カスタム季節係数）で生成し、Fabric Data Factory で Lakehouse にロードする。

### 4.3 レギュレーション文書

**担当: mmatsuzaki**

Azure Blob Storage にアップロード → Foundry IQ で Knowledge Source として接続 → Knowledge Base を作成（自動インデキシング・ベクトル化）→ Agent3a のツールとして接続する。

---

## 5. エージェント間インターフェース

### 5.1 オーケストレーション実装方針

SequentialBuilder ワークフローは v4.0 で廃止。FastAPI バックエンド (`src/api/chat.py`) がエージェントの実行順序・承認フロー・修正ルーティング・画像 side-channel・動画ポーリングを直接制御する。

エージェントは Microsoft Agent Framework (Python) で実装し、将来的に Hosted Agent として Foundry Agent Service にデプロイする。

> **重要:** 新しい Foundry Agent Service は OpenAI Responses API をベースにしている。旧 Assistants API ベースの Agent Service (classic) は deprecated（2027年3月31日 retired）。本プロジェクトでは新しい Agent Service API のみを使用する。Learn ドキュメントで `/foundry-classic/` パスの記事を参照しないこと。

**実行フロー:**

```
Agent1 (data-search-agent)
  ↓ データ分析結果
Agent2 (marketing-plan-agent)
  ↓ 企画書 Markdown
  ↓ [承認ステップ — _pending_approvals + SSE approval_request]
Agent3a (regulation-check-agent)
  ↓ チェック結果（✅/⚠️/❌）
Agent3b (plan-revision-agent)
  ↓ 修正済み企画書
Agent4 (brochure-gen-agent)
  ↓ HTML ブローシャ + 画像（side-channel 経由）
Agent5 (video-gen-agent)
  ↓ MP4 動画（非同期ポーリング）
Agent6 (quality-review-agent) ← バックグラウンド実行（オプショナル）
```

**エラーハンドリング方針:**

- 各エージェントの呼び出しが失敗した場合、FastAPI はエラー SSE イベントを送信する
- フロントエンドはエラーメッセージと「再試行」ボタンを表示する
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
| `App.tsx` | レイアウト管理・状態管理・SSE 接続（ヘッダー含む） | — |
| `InputForm.tsx` | 自然言語入力 | — |
| `PipelineStepper.tsx` | 7 ステップの進捗表示（Agent1〜6 + 承認） | `agent_progress` イベント |
| `WorkflowAccordion.tsx` | エージェント処理詳細のアコーディオン表示 | `agent_progress` + `tool_event` イベント |
| `ToolEventBadges.tsx` | ツール使用状況のアニメーションバッジ | `tool_event` イベント |
| `AnalysisView.tsx` | Agent1 の分析グラフ・サマリ表示 | `text` イベント |
| `ApprovalBanner.tsx` | 企画書プレビュー + 承認/修正ボタン | `approval_request` イベント |
| `RegulationResults.tsx` | 規制チェック結果のハイライト表示 | `text` イベント |
| `BrochurePreview.tsx` | HTML ブローシャのプレビュー | `text` イベント |
| `ImageGallery.tsx` | GPT Image 1.5 の生成画像表示 | `image` イベント |
| `ArtifactTabs.tsx` | 企画書/ブローシャ/画像/動画のタブ切替 | 各成果物の集約 |
| `VersionSelector.tsx` | 成果物バージョンの切替（v1/v2 全成果物） | 修正履歴 |
| `PlanVersionTabs.tsx` | 企画書バージョンのタブ切替 | バージョン履歴 |
| `RefineChat.tsx` | マルチターン修正対話の入力 | — |
| `SafetyBadge.tsx` | Content Safety 結果の動的バッジ | `safety` イベント |
| `MetricsBar.tsx` | 処理メトリクス（レイテンシ・トークン等） | `done` イベント |
| `ConversationHistory.tsx` | 過去の会話一覧・再開 | Cosmos DB データ |
| `SettingsPanel.tsx` | 設定パネル（言語・テーマ等） | — |
| `EvaluationPanel.tsx` | 品質評価結果の表示・改善フロー | `/api/evaluate` レスポンス |
| `VideoPreview.tsx` | Agent5 の動画プレビュー | MP4 URL |
| `PdfUpload.tsx` | 既存パンフレット PDF アップロード | — |
| `VoiceInput.tsx` | Voice Live 音声入力 UI | — |
| `LanguageSwitcher.tsx` | 言語切替（日英中） | — |
| `ThemeToggle.tsx` | ダーク/ライトモード切替 | — |
| `ErrorBoundary.tsx` | React エラーバウンダリ | — |
| `ErrorRetry.tsx` | エラー表示 + 再試行ボタン | `error` イベント |

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
│                      │  ApprovalBanner                 │
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
| レスポンス | コアパイプライン（Agent1〜4）: 3〜5 分以内（Human-in-the-Loop の待ち時間を除く）。販促動画生成（Agent5）は追加で 1〜2 分。動画生成はパイプライン完了後に非同期で実行し、完了次第フロントエンドに通知する |
| 入力安全性 | Content Safety + Prompt Shield。AI Gateway 層 + モデルデプロイメント設定の多層防御 |
| 可用性 | ハッカソンデモ時に安定稼働すること（SLA 不要） |
| スケーラビリティ | 単一ユーザーのデモ利用を想定 |
| データ | デモデータを使用（個人情報・機密情報を含まない） |
| 監視 | AI Gateway のトークン消費量・リクエストログを Application Insights で一元監視 |
| 品質評価 | `/api/evaluate` エンドポイントで品質評価を実行。Foundry ポータルにもログ |
| エラーハンドリング | エージェント失敗時は FastAPI がエラー SSE イベントを送信。フロントエンドに「再試行」ボタンを表示。Observability トレースで失敗箇所を特定 |
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
| Foundry Agent Service | Prompt Agent は GA。Hosted Agent は Preview（private networking 未対応）。Agent Framework 実装を Hosted Agent としてデプロイ。Foundry 層の VNet 分離は Hosted Agent 対応後に適用。**Hosted Agent の課金は 2026年4月1日以降に開始予定**（Preview 期間中はホスティングランタイム無料） |
| gpt-5.4-mini | Microsoft Foundry 上で GA（2026年3月17日〜）。デプロイ済みであること。低レイテンシ・高スループットのエージェント向けモデル |
| GPT Image 1.5 | Microsoft Foundry 上で GA。ただし利用にはアクセス承認（申請フォーム）が必要。事前に承認を取得し、デプロイ済みであること |
| Foundry IQ | Preview。Azure AI Search リソースが必要。MCP 接続は per-request headers 未対応のため、アプリ共通権限（Managed Identity）で読み取る前提 |
| Web Search ツール | Preview。追加 Azure リソース不要だが利用にコスト発生。**DPA 対象外**: クエリデータが Azure の geo boundary 外に流れる可能性あり |
| Azure API Management | AI Gateway 用。Foundry ポータルから有効化可能。Free Tier あり。Container App に `APIM_GATEWAY_URL` が設定されている場合、モデル呼び出しは APIM AI Gateway 経由になる |
| Azure Container Apps | フロントエンド + バックエンドのデプロイ先。azd up で一発デプロイ |
| Node.js | フロントエンドビルド用。22 以上 |
| Microsoft Fabric | Fabric 容量（Trial / Premium）が有効であること |
| Fabric Data Agent | `FABRIC_DATA_AGENT_URL` が設定されている場合、Agent1 は Fabric Data Agent Published URL を最優先で使い NL2SQL でデータ分析を実行する |
| Foundry Observability | 評価・監視・トレーシングすべて GA（2026年3月16日〜）。Application Insights リソースが必要。カスタム評価器は Preview |
| Voice Live API | Preview。Foundry Agent Service に統合。音声モデルのデプロイ不要（フルマネージド）。リージョン制約あり（対応リージョンを事前確認） |
| Content Understanding | GA。Foundry リソースが必要。`prebuilt-document-rag` アナライザーを使用 |
| Photo Avatar | Preview。Voice Live と組み合わせて使用。標準アバター 30 種を利用可能。カスタムアバターは写真 1 枚から生成 |
| Azure Logic Apps | Consumption または Standard プラン。承認後の自動アクション用。Teams / SharePoint / Outlook コネクタを使用 |
| Azure Cosmos DB | 会話履歴の永続化用。NoSQL API を使用。サーバーレス容量モードで十分（ハッカソン用途） |
| GitHub Copilot SDK | Technical Preview。Python SDK を使用。Copilot CLI のインストールと認証が前提。Agent Framework と統合可能（`GitHubCopilotAgent`） |

> **⚠️ classic API を使わないこと:** Foundry Agent Service には旧版（classic、Assistants API ベース）と新版（Responses API ベース）がある。classic は deprecated（2027年3月31日 retired）。Microsoft Learn のドキュメントで `/foundry-classic/` パスの記事は参照しない。新版のパスは `/azure/foundry/agents/`。

---

## 9. 担当分担

| 担当 | ロール | 担当範囲 |
|------|--------|---------|
| **Tokunaga** | Data SE | Fabric Lakehouse / Fabric Data Factory / デモデータ生成 / Agent1 実装 / Content Understanding（既存パンフレット PDF の取り込み・解析） |
| **Matsumoto** | App SE | Web フロントエンド（コンポーネント群 + SSE クライアント + Voice Live マイク UI）/ FastAPI バックエンド / Agent2 実装 / Agent4 実装 / Agent5 実装（販促動画生成）/ Agent6 実装（品質レビュー） |
| **mmatsuzaki** | Infra SE | Infrastructure（Bicep IaC + Container Apps + azd）/ API Management AI Gateway / Foundry IQ Knowledge Base / Agent3a 実装 / Agent3b 実装 / Content Safety / Observability 設定 / Voice Live API 接続 / Logic Apps 構成 / Foundry Evaluations 設定 |

---

## 10. デモシナリオ

**想定時間: 7〜10 分**（承認ステップ + 動画生成含む）

> **短縮版（5〜7 分）:** 動画生成（Step 9 の一部）と修正対話（Step 12）を省略する。
> **最短版（3〜5 分）:** 短縮版に加え、承認を自動承認に切り替え、評価デモ（Step 13）を省略する。

| Step | 画面表示 | 説明 |
|------|---------|------|
| 1 | チャット入力画面 | 🎤 マイクボタンを押して「春の沖縄ファミリー向けプランを企画して」と音声で指示（Voice Live）。テキスト入力でも可 |
| 2 | Agent1: 🔄 分析中 | 販売データ・顧客評価を検索・分析。グラフを自動生成 |
| 3 | 分析サマリ + グラフ | 売上推移グラフ・セグメント円グラフをインライン表示 |
| 4 | Agent2: 🔄 生成中 | 分析結果 + 市場トレンド（Web Search）で施策立案 |
| 5 | 企画書 + 承認ボタン | 「✅ 承認」「✏️ 修正」ボタンを表示 |
| 6 | **承認**: ユーザーが承認 | デモのハイライト |
| 7 | Agent3a/3b: 🔄 チェック・修正中 | 旅行業法・景表法・安全情報をチェックし修正版を出力 |
| 8 | チェック結果表示 | 修正箇所をハイライト |
| 9 | Agent4: 🔄 生成中 | ブローシャ・画像を生成。Agent5 が**紹介動画**を非同期生成 |
| 10 | 成果物プレビュー | タブ切り替えで企画書 / ブローシャ / 画像 / **動画**を表示 |
| 11 | Teams 通知 + SharePoint 保存 | Logic Apps 経由で完成通知 + 成果物を自動保存 |
| 12 | **修正対話** | 「キャッチコピーをもっとポップに」→ Agent2 のみ再実行 |
| 13 | 品質評価 | 企画書タブ内で評価を実行し、改善ボタンでフィードバックループを実演 |
| 14 | Observability | トレーシング・処理メトリクスを表示（デモの締め） |

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
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
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
uv run uvicorn src.main:app --reload --port 8000

# フロントエンド（別ターミナル）
cd frontend && npm install && npx vite
# → http://localhost:5173（/api は Vite proxy で :8000 に転送）
```

### 11.4 プロジェクト構成

```
travel-marketing-agents/
├── .github/
│   ├── copilot-instructions.md
│   ├── instructions/         # Copilot 用コーディング規約
│   └── skills/               # Copilot 用スキル定義
├── src/
│   ├── __init__.py
│   ├── main.py               # FastAPI エントリポイント
│   ├── config.py             # 環境変数ロード（TypedDict + load_settings）
│   ├── agent_client.py       # エージェントクライアント（APIM Gateway 対応）
│   ├── conversations.py      # Cosmos DB / インメモリ会話管理
│   ├── hosted_agent.py       # Foundry Hosted Agent エントリポイント（stub）
│   ├── http_client.py        # HTTP クライアントユーティリティ
│   ├── agents/               # 7 エージェント定義
│   │   ├── data_search.py    # Agent1: データ検索
│   │   ├── marketing_plan.py # Agent2: 施策生成
│   │   ├── regulation_check.py # Agent3a: 規制チェック
│   │   ├── plan_revision.py  # Agent3b: 企画書修正
│   │   ├── brochure_gen.py   # Agent4: ブローシャ + 画像生成
│   │   ├── video_gen.py      # Agent5: 動画生成
│   │   └── quality_review.py # Agent6: 品質レビュー
│   ├── api/                  # FastAPI ルーター
│   │   ├── chat.py           # /api/chat (SSE) + 承認 + 修正ルーティング
│   │   ├── conversations.py  # /api/conversations + /api/replay
│   │   ├── evaluate.py       # /api/evaluate（品質評価）
│   │   ├── health.py         # /api/health + /api/ready
│   │   └── voice.py          # /api/voice-token + /api/voice-config
│   ├── middleware/            # Content Safety（Prompt Shield + Text Analysis）
│   │   └── __init__.py
│   └── tools/                # ツールユーティリティ
│       └── __init__.py
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/       # 27 コンポーネント（§6.2 参照）
│   │   ├── hooks/            # useSSE, useTheme, useI18n
│   │   └── lib/              # i18n.ts, sse-client.ts, export.ts, msal-auth.ts, voice-live.ts
│   ├── vite.config.ts
│   └── package.json
├── data/
│   ├── demo_data_generator.py  # Faker ベースのデモデータ生成
│   └── demo-replay.json        # リプレイ用 SSE イベント
├── regulations/                 # Foundry IQ 用レギュレーション文書
├── infra/
│   ├── main.bicep              # Bicep IaC
│   └── modules/                # VNet, Cosmos DB, APIM 等
├── tests/                       # pytest テスト
├── Dockerfile                   # Container Apps 用マルチステージ
├── Dockerfile.agent             # Hosted Agent 用
├── azure.yaml                   # azd 設定
├── pyproject.toml
└── README.md
```

---

## 12. テスト戦略

### 12.1 バックエンドテスト（pytest）

最低限のテストを用意し、デモ前のリグレッションを防止する。

| テスト対象 | テスト内容 | 優先度 |
|-----------|----------|-------|
| `api/health.py` | ヘルスチェックの 200 レスポンス | 高 |
| `api/chat.py` | 不正リクエストの 400 レスポンス | 高 |
| `middleware/__init__.py` | Prompt Shield の安全/ブロック判定 | 中 |
| `api/chat.py` | SSE イベントのパース（マーカー文字列の分離） | 中 |
| `agents/*.py` | 各エージェントのツール定義・出力形式 | 中 |
| `config.py` | 設定ロード・フォールバック | 高 |
| `conversations.py` | 会話 CRUD・フォールバック | 中 |

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

## 14. 付加価値機能

Phase C（デプロイ後）で追加する機能。コアパイプラインとは独立しており、個別に有効化・無効化できる設計にする。

### 14.1 Voice Live（音声入力チャネル）


**実装方針:**

- フロントエンドにマイクボタンを追加し、WebSocket で Voice Live API に接続する
- Voice Live がノイズ抑制・エコーキャンセル・割り込み対応をフルマネージドで提供するため、音声処理のコードは不要
- 既存の Foundry Agent にそのまま音声チャネルを追加する形。新しいエージェントは作らない

- テキスト入力と音声入力を切り替え可能にする（音声のみに限定しない）

**状態:** Preview（2026年3月〜）

### 14.2 Foundry Evaluations（品質評価 API）

**ステータス: 実装済み（`/api/evaluate` エンドポイント）**

FastAPI バックエンドの `/api/evaluate` エンドポイントとして実装。組み込み評価器は GA、カスタム評価器は Preview。

**設定内容:**

- 組み込み評価器（GA）: Relevance, Coherence, Fluency
- カスタムコード評価器: travel_law_compliance, conversion_potential
- カスタムプロンプト評価器（LLM judge / Preview）: appeal, differentiation, kpi_validity, brand_tone, overall
- 評価結果は Foundry ポータルにもログされ、`foundry_portal_url` で確認可能
- フロントエンドの `EvaluationPanel` から評価実行 → フィードバック → 改善フローを実行可能

**将来追加予定:**
- Groundedness（ナレッジベース接地性）
- ToolCallAccuracy（ツール呼び出し精度）
- TaskAdherence（タスク遵守度）
- 継続的評価（本番トラフィックの自動サンプリング）
- Azure Monitor アラート（品質閾値低下の通知）

### 14.3 Teams 公開

**ステータス: Out of Scope（v4.0）**

現行アーキテクチャでは FastAPI Container App がオーケストレーターであり、Foundry Agent Service にパイプライン全体のエージェントが登録されていない。Foundry → Teams のチャネル公開は Foundry Agent Service に Prompt Agent / Hosted Agent が登録されていることが前提。将来 Hosted Agent としてデプロイした場合に再検討する。

### 14.4 Logic Apps（承認後自動アクション）
承認フロー完了後に Logic Apps の 1,400 以上のコネクタを使って後続処理を自動化する。

**想定フロー:**

1. Agent4 が成果物を生成完了
2. Logic Apps が起動（FastAPI の `_trigger_logic_app()` で HTTP トリガーを呼び出す）

3. Teams チャネルに完成通知を投稿（Adaptive Card 付き）
4. SharePoint ドキュメントライブラリに成果物を保存
5. Outlook でクライアントに企画書のサマリをメール送信（オプション）

**実装方針:** ノーコードで構成可能

### 14.5 Content Understanding（既存パンフレット PDF 解析）

Content Understanding（Foundry Tools）の RAG アナライザーで既存の旅行パンフレット PDF を解析し、Agent4 のブローシャ生成の参考入力にする。

**解析対象:**

- キャッチコピーのトーンと文体
- 写真の種類と配置パターン
- 図表やチャートの使い方

**実装方針:**

- Content Understanding の `prebuilt-document-rag` アナライザーで PDF を Markdown に変換
- 変換結果を Agent4 の instructions に「参考レイアウト」として渡す
- Agent4 は解析結果を参照しつつ、新しいプラン内容に合わせたブローシャを生成する

**状態:** GA

### 14.6 販促紹介動画の自動生成（Agent5: Voice Live + Photo Avatar）
Voice Live と Photo Avatar（Preview）を組み合わせて、旅行プランの紹介動画を自動生成する。Agent5 として独立したエージェントで実装（§3.8b 参照）。

**ユースケース:** 企画書のサマリテキストを、バーチャルツアーガイドのアバターが音声で読み上げる 15〜30 秒の動画を自動生成する。SNS 投稿・Web サイト・社内プレゼンの販促素材として使う。

**実装方針:**

- Photo Avatar は 1 枚の写真から表情豊かなアバターを自動生成する（標準アバター 30 種 + カスタム対応）
- 旅行会社のコンシェルジュキャラクターとして、ブランドに合ったアバターを選択・作成する
- 企画書サマリ → Voice Live がテキストを音声化 → Photo Avatar が口の動きと表情を同期 → MP4 動画として出力
- アバターは「AI アシスタント」ではなく「販促素材の一部」として位置づける

**成果物:** MP4 動画（15〜30 秒、720p）

**状態:** Photo Avatar は Preview

### 14.7 会話履歴の永続化（Azure Cosmos DB）

Azure Cosmos DB（NoSQL API）に会話スレッドと成果物を保存し、過去のセッションを再開・参照できるようにする。

**データモデル:**

```
conversations (コンテナ)
├── id: スレッド ID（UUID）
├── partitionKey: ユーザー ID
├── created_at: 作成日時
├── updated_at: 最終更新日時
├── status: "in_progress" | "completed" | "cancelled"
├── input: ユーザーの入力テキスト（地域・季節・予算など）
├── messages: SSE イベントの配列（agent_progress, text, approval_request 等）
├── artifacts: 成果物のフラット辞書（plan_md, brochure_html, images, video_url 等）
└── metadata: 処理メトリクス（レイテンシ・トークン数）
```

> **注意:** 成果物は当初バージョン配列で設計していたが、実装ではフラット辞書として保存している。UI 側のバージョン管理（`ArtifactSnapshot[]`）は Cosmos DB とは独立して動作する。

**実装方針:**

- Cosmos DB サーバーレス容量モードを使う（ハッカソン用途では十分。RU 課金ではなく使った分だけ課金）
- DefaultAzureCredential + Managed Identity で認証。接続文字列のハードコードは禁止
- FastAPI の `/api/conversations` エンドポイントで一覧取得・詳細取得を提供
- フロントエンドに履歴一覧画面を追加（`ConversationHistory` コンポーネント）

- COSMOS_DB_ENDPOINT が未設定の場合はインメモリ辞書にフォールバック（ローカル開発用）

**ユースケース:**

- 過去に作った企画書を参照して「前回の沖縄プランをベースに、今度は北海道版を作って」と指示する
- 修正対話の全バージョン履歴を保存し、任意のバージョンに戻れる

### 14.8 GitHub Copilot SDK 統合（Agent6: 品質レビューエージェント）

GitHub Copilot SDK（Technical Preview）を Agent Framework と組み合わせ、生成物の品質レビューを行う Agent6 として実装済み（§3.8c 参照）。

**背景:** Agent Framework は `GitHubCopilotAgent` クラスを提供しており、Copilot SDK で動くエージェントを Agent Framework のパイプラインに組み込める。Copilot SDK は Copilot CLI と同じエージェントエンジンを使い、計画・ツール呼び出し・ファイル操作を自動で行う。

**状態:** Technical Preview

**前提:** Copilot CLI がインストール済みで `copilot auth` 認証済みであること

### 14.9 デモリプレイ機能

本番相当の Azure リソースで事前に実行した結果を録画し、デモ時に高速リプレイできる機能。

**背景:** 本番環境では Agent1〜6 のパイプライン全体で 3〜5 分かかる。ハッカソンのデモ時間（7〜10 分）の大半がエージェントの処理待ちになると、審査員に見せたい機能を十分にデモできない。

**実装方針:**
- **録画:** 本番相当の Azure 環境でパイプラインを事前実行し、SSE イベントストリームをタイムスタンプ付きで Cosmos DB に保存する（§14.7 の `messages` フィールドを流用）
- **リプレイ:** デモ時にフロントエンドの「リプレイモード」をオンにすると、保存済みの SSE イベントを再生速度を上げて再送する。バックエンドの `GET /api/replay/{thread_id}` エンドポイントが、録画済みイベントを 5〜10 倍速で SSE として配信する
- **ライブとの切替:** リプレイモードとライブモードは URL パラメータ（`?mode=replay&thread_id=xxx`）で切り替え可能。審査員には「事前に本番環境で実行した結果のリプレイです」と明示する
- **リプレイ中も UI は通常通り動作する:** PipelineStepper のアニメーション、ToolEventBadges、SafetyBadge、ImageGallery、BrochurePreview がリアルタイムに更新される。見た目はライブ実行と同じ
- **Cosmos DB 未設定時:** ローカルの JSON ファイル（`data/demo-replay.json`）から読み込む。Azure 接続不要でもリプレイ可能

**デモシナリオでの使い方:**

1. まずリプレイモードで全体の流れを 1〜2 分で見せる（「事前に実行した結果をお見せします」）
2. その後、ライブモードで音声入力 → 承認 → 修正対話をインタラクティブにデモする（「ここからはライブです」）
3. 組み合わせることで、7〜10 分の持ち時間を最大限に活用できる

**状態:** 自前実装（Azure 依存は Cosmos DB のみ。JSON フォールバックあり）

---

## 付録 A: 用語集

| 用語 | 説明 |
|------|------|
| Microsoft Foundry | 旧称 Azure AI Foundry（2025年11月リネーム）。エンタープライズ AI の統合プラットフォーム |
| Foundry Agent Service | AI エージェントのフルマネージドサービス。OpenAI Responses API ベース。Prompt Agent は GA、Hosted Agent / Workflow Agent は Preview。旧 Assistants API ベースの classic 版は deprecated（2027年3月31日 retired） |
| Microsoft Agent Framework | Semantic Kernel / AutoGen の後継 OSS フレームワーク。Python / .NET 対応。RC 状態 |
| Hosted Agent | Agent Framework 等で実装したエージェントを Foundry にコンテナデプロイする形態 |
| AI Gateway | Azure API Management の AI 向け機能群 |
| Fabric Data Factory | Microsoft Fabric のデータ統合サービス |
| Fabric Lakehouse | Delta Parquet でデータを格納し SQL エンドポイントで参照可能なデータストア |
| Fabric Data Agent | Fabric のデータに対して自然言語で問い合わせ可能な AI エージェント。Published URL 経由で利用 |
| Foundry IQ Knowledge Base | エンタープライズナレッジレイヤー。Azure AI Search 基盤。Preview |
| Web Search ツール | Foundry Agent Service の Web 検索ツール。追加リソース不要。Preview |
| MCP | Model Context Protocol。エージェントと外部ツールの通信プロトコル |
| gpt-5.4-mini | OpenAI の高効率推論モデル（2026年3月リリース）。GPT-5.4 の能力を小型化し、ツール呼び出し・マルチモーダル・推論を低レイテンシで実行できる。Microsoft Foundry 上で GA |
| GPT Image 1.5 | OpenAI の画像生成モデル。Microsoft Foundry 上で GA。利用にはアクセス承認が必要 |
| Content Safety | Azure AI Content Safety + Prompt Shield |
| Foundry Observability | 評価（Evaluations）・監視（Monitoring）・トレーシング（Tracing）の統合機能。すべて GA（2026年3月16日〜）。カスタム評価器は Preview |
| Human-in-the-Loop | ワークフロー中に人間の判断・承認を挟む設計パターン |
| SSE | Server-Sent Events。サーバーからクライアントへの一方向リアルタイム通信。HTTP 上で動作し WebSocket より軽量 |
| FastAPI | Python の非同期 Web フレームワーク。SSE ストリーミングに対応 |
| azd | Azure Developer CLI。`azd up` で Azure リソースのプロビジョニングとアプリデプロイを一括実行できるツール |
| OIDC Workload Identity Federation | GitHub Actions から Azure に認証する仕組み。サービスプリンシパルのシークレットを保存せず、一時トークンで認証する |
| DevSecOps | 開発（Dev）・セキュリティ（Sec）・運用（Ops）を CI/CD パイプラインに統合する手法 |
| i18n | 多言語対応（Internationalization）。UI ラベル・メッセージを複数言語で表示する仕組み |
| ブローシャ | 旅行プランの販促パンフレット・チラシ |
| Voice Live | Foundry Agent Service 統合の音声対話 API。Preview |
| Photo Avatar | 1 枚の写真から表情豊かなアバター動画を生成する Azure AI サービス。Preview |
| Content Understanding | Foundry Tools の文書解析サービス。PDF をマークダウンに変換。GA |
| Logic Apps | Azure のノーコードワークフロー自動化サービス。1,400 以上のコネクタ |
| Azure Cosmos DB | Microsoft のグローバル分散 NoSQL データベース。サーバーレス容量モード対応 |
| GitHub Copilot SDK | Copilot のエージェントエンジンを外部アプリに組み込む SDK。Python / TypeScript / Go / .NET 対応 |
| GitHubCopilotAgent | Agent Framework が提供する Copilot SDK 統合クラス。パイプラインに Copilot エージェントを組み込める |

---

## 付録 B: 参考リンク

| リソース | URL |
|---------|-----|
| Microsoft Agent Framework (GitHub) | <https://github.com/microsoft/agent-framework> |
| Microsoft Agent Framework (PyPI) | <https://pypi.org/project/agent-framework/> |
| Agent Framework RC ブログ | <https://devblogs.microsoft.com/foundry/microsoft-agent-framework-reaches-release-candidate/> |
| Foundry Agent Service 概要 | <https://learn.microsoft.com/en-us/azure/foundry/agents/overview> |
| Foundry Agent Service GA ブログ | <https://devblogs.microsoft.com/foundry/foundry-agent-service-ga/> |
| Workflows を構築する | <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/workflow> |
| Workflows 紹介ブログ | <https://devblogs.microsoft.com/foundry/introducing-multi-agent-workflows-in-foundry-agent-service/> |
| Foundry IQ とは | <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq> |
| Foundry IQ を Agent Service に接続 | <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect> |
| Web Search ツール | <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-search> |
| Web grounding 概要 | <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/web-overview> |
| AI Gateway (API Management) | <https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities> |
| AI Gateway を Foundry で有効化 | <https://learn.microsoft.com/en-us/azure/foundry/configuration/enable-ai-api-management-gateway-portal> |
| AI Gateway Labs (GitHub) | <https://github.com/Azure-Samples/AI-Gateway> |
| GPT Image 1.5 (Foundry) | <https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-openai%E2%80%99s-gpt-image-1-5-in-microsoft-foundry/4478139> |
| Fabric Data Factory | <https://learn.microsoft.com/en-us/fabric/data-factory/data-factory-overview> |
| Foundry Observability | <https://learn.microsoft.com/en-us/azure/foundry/concepts/observability> |
| Foundry Observability GA ブログ | <https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/generally-available-evaluations-monitoring-and-tracing-in-microsoft-foundry/4502760> |
| gpt-5.4-mini / nano 発表ブログ | <https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/introducing-openai%E2%80%99s-gpt-5-4-mini-and-gpt-5-4-nano-for-low-latency-ai/4500569> |
| gpt-5.4-mini モデルカタログ | <https://ai.azure.com/catalog/models/gpt-5.4-mini> |
| Azure Container Apps 概要 | <https://learn.microsoft.com/en-us/azure/container-apps/overview> |
| OIDC Workload Identity Federation | <https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust-user-assigned-managed-identity> |
| Hosted Agent 概要 | <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents> |
| Prompt Shield（Content Filter） | <https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/content-filter-prompt-shields> |
| ツール可用性（リージョン・モデル別） | <https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions> |
| MCP ツールガバナンス | <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/governance> |
| Private networking 設定 | <https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks> |
| Voice Live 概要 | <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live> |
| Voice Live + Foundry Agent クイックスタート | <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-agents-quickstart> |
| Voice Live 発表ブログ | <https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/public-preview-voice-native-agents-in-microsoft-foundry/4502756> |
| Content Understanding 概要 | <https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/overview> |
| Content Understanding ドキュメント解析 | <https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/overview> |
| Content Understanding プリビルトアナライザー | <https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/concepts/prebuilt-analyzers> |
| Logic Apps 概要 | <https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-overview> |
| Azure Cosmos DB for NoSQL | <https://learn.microsoft.com/en-us/azure/cosmos-db/nosql/> |
| Azure Cosmos DB Python SDK | <https://learn.microsoft.com/en-us/azure/cosmos-db/nosql/quickstart-python> |
| GitHub Copilot SDK リポジトリ | <https://github.com/github/copilot-sdk> |
| Agent Framework + Copilot SDK 統合 | <https://devblogs.microsoft.com/semantic-kernel/build-ai-agents-with-github-copilot-sdk-and-microsoft-agent-framework/> |
| Copilot SDK 発表ブログ | <https://github.blog/news-insights/company-news/build-an-agent-into-any-app-with-the-github-copilot-sdk/> |
| Azure Functions Python サポート状況 | <https://learn.microsoft.com/en-us/azure/azure-functions/supported-languages> |
| Flex Consumption プラン概要 | <https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan> |

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
| 2026-03-29 | 3.5 | 付加価値機能 9 件を追加（§14）: Voice Live 音声入力、Foundry Evaluations 品質ダッシュボード、Teams 公開、Logic Apps 承認後自動アクション、Content Understanding 既存パンフレット解析、Photo Avatar + Voice Live 販促動画生成、Cosmos DB 会話履歴永続化、GitHub Copilot SDK 統合（品質レビューエージェント）、デモリプレイ機能。Agent4 に AG4-05（動画生成）/ AG4-06（パンフレット参照）を追加。成果物に販促紹介動画（MP4）を追加。デモシナリオ 14 ステップに拡張（7〜10分）。§7 レスポンス時間に動画生成の非同期実行を追記。§8 に Voice Live / Content Understanding / Photo Avatar / Logic Apps / Azure Functions Python 3.14 制約を追加。§9 担当分担更新。技術スタック表に 5 サービスを追加。Out of Scope から動画生成を削除 | Team D |
| 2026-03-29 | 3.6 | Azure Functions Python 3.14 のリモートビルド非対応を §8 に明記（Functions は 3.13 で構成）。会話履歴の永続化を追加（§14.7: Cosmos DB、データモデル定義、フォールバック設計）。GitHub Copilot SDK 統合を追加（§14.8: GitHubCopilotAgent による品質レビューエージェント、コード例付き）。デモリプレイ機能を追加（§14.9: SSE イベントの録画・高速再生、ライブ/リプレイ切替、JSON フォールバック）。技術スタック表に Cosmos DB / Copilot SDK を追加。参考リンク 5 件追加 | Team D |
| 2026-03-29 | 3.7 | ファクトチェック反映。トレーシングを Preview → GA に修正（§2.3/§3.10/§8/用語集、2026年3月16日 GA 確認）。Foundry Agent Service が OpenAI Responses API ベースであることを §5.1/用語集に追記。classic API (Assistants API ベース) の deprecated 注意を §5.1/§8 に追記。Hosted Agent の課金開始日（2026年4月1日以降）を §8 に追記。§14.2 の Custom evaluators を Preview と明記 | Team D |
| 2026-04-01 | 4.0 | 実装追従: エージェント数を 4→7 に更新（Agent3b/Agent5/Agent6 追加）。Foundry Workflows を廃止し FastAPI 直接オーケストレーションに変更。Azure Functions MCP を廃止し @tool 直接定義に変更。APIM AI Gateway 経由のモデル呼び出しを追加。Fabric Data Agent 連携を追加。Evaluations を `/api/evaluate` コード実装で反映。Teams 公開を Out of Scope に変更。テーブル名 sales_history→sales_results。UI コンポーネント 27 個に更新。プロジェクト構成を現行ディレクトリに合わせて更新。付録 B 参考リンク修復 | Team D |
