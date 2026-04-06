# Azure アーキテクチャ

現在の実装と Azure 実環境に基づくアーキテクチャ資料です。

## 1. ランタイム実行フロー

```mermaid
flowchart TD
    user([マーケ担当者]) --> ui[React 19 Frontend]
    ui --> api[FastAPI SSE API]
    ui -.-> eval[POST /api/evaluate]
    eval --> foundryEval[Foundry Evaluations]

    api --> flow[FastAPI Orchestration]
    api -.-> apimMcp[APIM improvement-mcp]
    apimMcp --> mcpFunc[Azure Functions MCP]
    mcpFunc --> mcpTool[generate_improvement_brief]

    subgraph agents[Agent Pipeline]
        direction TB
        a1[data-search-agent] --> a2[marketing-plan-agent]
        a2 --> approve{担当者承認}
        approve --> a3a[regulation-check-agent]
        a3a --> a3b[plan-revision-agent]
        a3b --> mgr{上司承認?}
        mgr -->|off| a4[brochure-gen-agent]
        mgr -->|on| portal[上司承認ページ]
        portal --> a4
        a4 --> a5[video-gen-agent]
    end

    flow --> agents

    subgraph data[Data Sources]
        direction LR
        dataAgent[Fabric Data Agent]
        fabricSQL[Fabric SQL Endpoint]
        csv[CSV Fallback]
    end

    subgraph knowledge[Knowledge & Search]
        direction LR
        aiSearch[Azure AI Search]
        webSearch[Foundry Web Search]
    end

    subgraph media[Media Services]
        direction LR
        gptImg[GPT Image 1.5]
        maiImg[MAI-Image-2]
        avatar[Speech / Photo Avatar]
        cu[Content Understanding]
    end

    a1 -.-> data
    a2 -.-> webSearch
    a3a -.-> knowledge
    a4 -.-> gptImg
    a4 -.-> maiImg
    a4 -.-> cu
    a5 -.-> avatar

    api -.-> review[quality-review-agent]
    api -.-> logic[Logic Apps]
    api -.-> cosmos[Cosmos DB]
```

## 2. Azure リソース構成

```mermaid
flowchart TD
    gha[GitHub Actions] --> acr[Container Registry]
    acr --> ca[Container Apps]

    subgraph compute[Compute & Gateway]
        ca
        apim[APIM AI Gateway]
        func[Functions MCP]
    end

    subgraph ai[AI Services]
        foundry[Microsoft Foundry Project]
        aiSvc[AI Services Account]
        aiSearch[Azure AI Search]
    end

    subgraph storage[Data & Storage]
        cosmos[Cosmos DB]
        kv[Key Vault]
    end

    subgraph observe[Observability]
        logs[Log Analytics]
        appi[Application Insights]
    end

    subgraph network[Network]
        vnet[VNet]
        pep[Private Endpoints]
    end

    ca --> foundry
    foundry --> aiSvc
    ca --> cosmos
    ca --> kv
    ca -.-> apim
    apim -.-> func
    apim -.-> aiSvc
    aiSearch --> foundry
    ca --> appi
    appi --> logs
    vnet --> ca
    pep --> cosmos
    pep --> kv
```

## 3. IaC で作られるリソース

| リソース | 構成 |
| --- | --- |
| AI Services | `kind=AIServices`, `allowProjectManagement=true`, `disableLocalAuth=true`, `gpt-5-4-mini` 自動配備 |
| Foundry Project | `accounts/projects@2025-06-01` |
| Container Apps | System MI, health/readiness probe, 0–3 replicas |
| APIM | BasicV2, Managed Identity, AI Gateway policy |
| Azure Functions MCP | Flex Consumption, `mcp_server/` zip 配備 (postprovision) |
| Logic Apps | Consumption, HTTP trigger (post-approval actions) |
| Cosmos DB | Serverless, `disableLocalAuth=true`, Private Endpoint, RBAC |
| Key Vault | Private Endpoint, RBAC |
| Observability | Log Analytics + Application Insights |

## 4. postprovision 後の手動設定

| 項目 | 理由 |
| --- | --- |
| Azure AI Search + `regulations-index` 投入 | ナレッジベース検索に必要 |
| Foundry → AI Search 接続追加 | `get_default(ConnectionType.AZURE_AI_SEARCH)` が前提 |
| `FABRIC_DATA_AGENT_URL` | Agent1 が Fabric Data Agent を優先するため |
| `SPEECH_SERVICE_ENDPOINT` / `SPEECH_SERVICE_REGION` | Photo Avatar 動画生成 |
| `VOICE_SPA_CLIENT_ID` / `AZURE_TENANT_ID` | Voice Live MSAL.js 認証 |

上記以外の環境変数（`IMPROVEMENT_MCP_ENDPOINT`, `COSMOS_DB_ENDPOINT` 等）は `azd up` で自動注入されます。

## 5. 認証モデル

| 実行主体 | 認証方式 | 用途 |
| --- | --- | --- |
| Container App | `DefaultAzureCredential` | Foundry, Fabric, Cosmos DB, AI Search |
| APIM | Managed Identity | Foundry バックエンド接続 |
| AI Search bootstrap | Foundry connection or API key | 初期インデックス投入 |

Container App の MI には Bicep で Foundry 関連ロール, Cosmos DB Data Contributor, Key Vault Secrets User, AcrPull が付与されます。

## 6. Remote MCP

- 現在 Azure Functions MCP で提供するのは `generate_improvement_brief`（評価改善用）のみ
- 他のツールはエージェント内 `@tool` 実装
- 新規リモートツール追加時も、Functions MCP + APIM 公開 + FastAPI graceful fallback の同パターンを推奨
