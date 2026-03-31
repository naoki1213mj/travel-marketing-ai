# Azure アーキテクチャ

このドキュメントは、要件書の理想構成ではなく、現在の実装と Azure 側の実配備前提をベースに整理したものです。詳細な構成図は [architecture.drawio](architecture.drawio) も参照してください。

## 1. ランタイム実行フロー

```mermaid
flowchart LR
    user[マーケ担当者] --> ui[React 19 frontend]
    ui --> api[FastAPI SSE API]
    api --> inputshield[Prompt Shield]
    inputshield --> flow[SequentialBuilder workflow in FastAPI]

    flow --> a1[data-search-agent]
    a1 --> fabric[Fabric Lakehouse SQL]
    a1 -.-> csv[CSV fallback]

    flow --> a2[marketing-plan-agent]
    a2 --> web[Foundry Web Search]

    flow --> approval{approval_request}
    approval --> a3a[regulation-check-agent]
    a3a --> search[Azure AI Search / Foundry IQ]
    a3a --> safetyweb[Web Search for safety info]

    a3a --> a3b[plan-revision-agent]

    flow --> a4[brochure-gen-agent]
    a4 --> image[gpt-image-1.5]
    a4 --> cu[Content Understanding]

    flow --> a5[video-gen-agent]
    a5 --> speech[Speech / Photo Avatar video]

    flow --> outputsafety[Text Analysis]

    subgraph fabricLayer[Fabric Data]
        fabricSQL[Fabric SQL Endpoint]
        fabricDelta[Delta Parquet tables]
    end
    fabric --> fabricSQL
    fabricSQL --> fabricDelta
    outputsafety --> ui

    api -. optional .-> review[quality-review-agent]
    api -. approval continuation .-> logic[Logic Apps callback]
    logic --> teams[Teams]
    logic --> sharepoint[SharePoint]
```

## 2. Azure リソース構成

```mermaid
flowchart TB
    gha[GitHub Actions] --> acr[Azure Container Registry]
    acr --> ca[Azure Container Apps]

    ca --> foundryProject[Microsoft Foundry project]
    foundryProject --> aiServices[AI Services account]
    ca --> cosmos[Cosmos DB Serverless]
    ca --> kv[Key Vault]
    ca --> appi[Application Insights]
    ca --> logic[Logic Apps]

    apim[API Management AI Gateway] -. travel-ai-gateway connection .-> aiServices
    ca -. not in active runtime path yet .-> apim

    aiSearch[Azure AI Search] --> foundryProject

    subgraph network[Network boundary]
        cae[Container Apps Environment]
        vnet[VNet]
        pep[Private Endpoints]
    end

    vnet --> cae
    pep --> cosmos
    pep --> kv
    ca --> cae
```

## 3. IaC で作られるもの

| リソース | 構成 |
|---|---|
| AI Services | `kind=AIServices`、`allowProjectManagement=true`、`disableLocalAuth=true`、`gpt-5-4-mini` を既定で配備 |
| Foundry project | `accounts/projects@2025-06-01` |
| Container Apps | System-assigned MI、`/api/health` と `/api/ready` の probe、0-3 レプリカ |
| APIM | BasicV2、Managed Identity、AI Gateway ポリシー、トークン制限とメトリクス発行 |
| Logic Apps | Consumption、HTTP trigger ベース |
| Cosmos DB | Serverless、`disableLocalAuth=true`、Private Endpoint、RBAC |
| Key Vault | Private Endpoint、RBAC |
| Observability | Log Analytics + Application Insights |

## 4. IaC の後に手動で補う項目

| 項目 | 理由 |
|---|---|
| Azure AI Search の作成と `regulations-index` の投入 | Foundry IQ の実データ検索に必要 |
| Foundry project と Azure AI Search の接続 | `search_knowledge_base()` の既定接続に必要 |
| `FABRIC_SQL_ENDPOINT` | Agent1 の Fabric Lakehouse リアルタイムデータ検索に必要（未設定時は CSV フォールバック） |
| `CONTENT_UNDERSTANDING_ENDPOINT` | PDF 解析ツールが参照 |
| `SPEECH_SERVICE_ENDPOINT` / `SPEECH_SERVICE_REGION` | Photo Avatar 動画生成ツールが参照（`casual-sitting` スタイル） |
| `VOICE_SPA_CLIENT_ID` / `AZURE_TENANT_ID` | Voice Live の MSAL.js 認証（Entra アプリ登録が必要） |
| `LOGIC_APP_CALLBACK_URL` | 承認継続後の HTTP callback に必要 |

## 5. 認証モデル

| 実行主体 | 認証方式 | 主な用途 |
|---|---|---|
| FastAPI / Container App | `DefaultAzureCredential` | Foundry、Cosmos DB、Azure AI Search、Content Safety |
| APIM | Managed Identity | Foundry バックエンドへの認証 |
| AI Search bootstrap script | Foundry 接続または API key | 初期インデックス投入 |

Container App の Managed Identity には、Bicep で Foundry 関連ロール、Cosmos DB Data Contributor、Key Vault Secrets User、AcrPull が割り当てられます。

## 6. 現在の実装メモ

- `POST /api/chat` の Azure モードは、FastAPI 内で Agent1 → Agent2 を実行後に `approval_request` を返し、承認後に Agent3a → Agent3b → Agent4 → Agent5 を続行します。
- Agent1 は Fabric Lakehouse SQL endpoint に pyodbc + Azure AD トークン認証で接続します。`FABRIC_SQL_ENDPOINT` 未設定時は CSV → ハードコードデータにフォールバック。
- Agent4 は顧客向けブローシャを生成し、KPI・売上目標・社内分析を含めません。
- Agent5（動画生成）は Photo Avatar で `casual-sitting` スタイル、`ja-JP-NanamiNeural` 音声の販促動画を MP4/H.264 で生成します。
- Agent6 は `GitHubCopilotAgent` + `PermissionHandler.approve_all` で動作し、利用不可時は `AzureOpenAIResponsesClient` にフォールバックします。
- Code Interpreter はランタイムで自動検出され、利用不可時はグレースフルにフォールバックします。
- APIM は Azure 側に作られ、`scripts/postprovision.py` で Foundry AI Gateway 接続（`travel-ai-gateway`）の作成とトークン制限ポリシー（80,000 tokens/min）の適用が自動実行されます。また Voice Agent の作成も行います。
- 品質レビューは主フロー後の追加 `text` イベントとして返ります。主 workflow participant ではありません。
- パイプラインは 5 ユーザー向けステップで、内部は 7 エージェントで構成されています（Agent3a+3b がステップ 4、Agent4+5 がステップ 5 を共有）。
- Azure AI Search の実行時検索は Managed Identity ベースです。API キーはセットアップ用スクリプトの任意経路にだけ残っています。
- Voice Live API は MSAL.js + Entra アプリ登録認証で動作し、`/api/voice-token` と `/api/voice-config` エンドポイントを提供します。
- 会話履歴は Cosmos DB に保存され、フロントエンドの `restoreConversation()` で再推論なしに復元されます。
