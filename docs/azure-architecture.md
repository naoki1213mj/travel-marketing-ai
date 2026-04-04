# Azure アーキテクチャ

このドキュメントは、要件書の理想構成ではなく、現在の実装と Azure 側の実配備前提をベースに整理したものです。詳細な構成図は [architecture.drawio](architecture.drawio) も参照してください。

## 1. ランタイム実行フロー

```mermaid
flowchart LR
    user[マーケ担当者] --> ui[React 19 frontend]
    ui --> api[FastAPI SSE API]
    ui -. evaluate / refine .-> eval[/POST /api/evaluate/]
    eval --> foundryEval[Foundry Evaluations]
    api --> flow[FastAPI orchestration pipeline]

    flow --> a1[data-search-agent]
    a1 --> dataAgent[Fabric Data Agent Published URL]
    dataAgent --> fabricDelta[Lakehouse tables]
    a1 -. fallback .-> fabric[Fabric Lakehouse SQL]
    fabric -. fallback .-> csv[CSV fallback]

    flow --> a2[marketing-plan-agent]
    a2 --> web[Foundry Web Search]

    flow --> approval{担当者 approval_request}
    approval --> a3a[regulation-check-agent]
    a3a --> search[Azure AI Search / Foundry IQ]
    a3a --> safetyweb[Web Search for safety info]

    a3a --> a3b[plan-revision-agent]
    a3b --> managerApproval{上司承認オプション}
    managerApproval -->|off| a4[brochure-gen-agent]
    managerApproval -->|on| portal[Built-in manager approval page]
    managerApproval -. optional notify .-> managerWorkflow[Optional Teams or email notification workflow]
    managerWorkflow --> manager[Manager receives approval URL]
    manager --> portal
    portal --> apiCallback[/POST manager-approval-callback/]
    apiCallback --> a4[brochure-gen-agent]

    a4 --> image[gpt-image-1.5 / MAI-Image-2]
    a4 --> cu[Content Understanding]

    a4 --> a5[video-gen-agent]
    a5 --> speech[Speech / Photo Avatar video]

    subgraph fabricLayer[Fabric Data]
        fabricSQL[Fabric SQL Endpoint]
        fabricAgent[Published Data Agent]
        fabricDelta[Delta Parquet tables]
    end
    fabric --> fabricSQL
    dataAgent --> fabricAgent
    fabricSQL --> fabricDelta
    flow --> ui

    api -. optional .-> review[quality-review-agent]
    api -. post approval actions .-> logicPost[Logic Apps post actions]
    logicPost --> sharepoint[SharePoint]
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

    foundryProject -. travel-ai-gateway connection .-> apim
    apim -. backend auth .-> aiServices

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
| --- | --- |
| AI Services | `kind=AIServices`、`allowProjectManagement=true`、`disableLocalAuth=true`、`gpt-5-4-mini` を既定で配備 |
| Foundry project | `accounts/projects@2025-06-01` |
| Container Apps | System-assigned MI、`/api/health` と `/api/ready` の probe、0-3 レプリカ |
| APIM | BasicV2、Managed Identity。`scripts/postprovision.py` で Foundry AI Gateway 接続とトークン制限・メトリクスのポリシーを適用 |
| Logic Apps | Consumption、HTTP trigger ベース。`post_approval_actions` の payload を受け付ける |
| Manager approval notification workflow | 現行 IaC では未作成。組み込み上司承認ページはアプリに含まれ、Teams やメールで自動通知したい場合だけ別 workflow を用意して `MANAGER_APPROVAL_TRIGGER_URL` を FastAPI に渡す |
| Cosmos DB | Serverless、`disableLocalAuth=true`、Private Endpoint、RBAC |
| Key Vault | Private Endpoint、RBAC |
| Observability | Log Analytics + Application Insights |

## 4. IaC の後に手動で補う項目

| 項目 | 理由 |
| --- | --- |
| Azure AI Search の作成と `regulations-index` の投入 | Foundry IQ の実データ検索に必要 |
| Foundry project と Azure AI Search の接続 | `search_knowledge_base()` の既定接続に必要 |
| `FABRIC_DATA_AGENT_URL` | Agent1 が Fabric Data Agent Published URL を優先利用するため |
| `FABRIC_SQL_ENDPOINT` | Agent1 の Fabric Lakehouse SQL フォールバック接続に必要（未設定時は CSV フォールバック） |
| `EVAL_MODEL_DEPLOYMENT` | `/api/evaluate` に評価専用 deployment を使う場合 |
| `CONTENT_UNDERSTANDING_ENDPOINT` | PDF 解析ツールが参照 |
| `SPEECH_SERVICE_ENDPOINT` / `SPEECH_SERVICE_REGION` | Photo Avatar 動画生成ツールが参照（HD voice + SSML ナレーション、`casual-sitting` スタイル） |
| `VOICE_SPA_CLIENT_ID` / `AZURE_TENANT_ID` | Voice Live の MSAL.js 認証（Entra アプリ登録が必要） |
| `LOGIC_APP_CALLBACK_URL` | 承認継続後アクションに必要 |
| `MANAGER_APPROVAL_TRIGGER_URL` | 任意。上司承認 URL を送る通知 workflow の HTTP trigger URL |
| Microsoft Teams connector の認可 | Teams で通知する場合に外部 notification workflow 側で必要 |

## 5. 認証モデル

| 実行主体 | 認証方式 | 主な用途 |
| --- | --- | --- |
| FastAPI / Container App | `DefaultAzureCredential` | Foundry、Fabric Data Agent / Fabric SQL、Cosmos DB、Azure AI Search |
| APIM | Managed Identity | Foundry バックエンドへの認証 |
| AI Search bootstrap script | Foundry 接続または API key | 初期インデックス投入 |

Container App の Managed Identity には、Bicep で Foundry 関連ロール、Cosmos DB Data Contributor、Key Vault Secrets User、AcrPull が割り当てられます。

## 6. 現在の実装メモ

- `POST /api/chat` の Azure モードは、FastAPI 内で Agent1 → Agent2 を実行後に担当者向け `approval_request` を返します。
- 担当者承認後は Agent3a → Agent3b を実行し、`manager_approval_enabled=true` の場合は組み込み上司承認ページ URL を生成して待機します。上司承認オフならそのまま Agent4 → Agent5 に進みます。
- `MANAGER_APPROVAL_TRIGGER_URL` が設定されている場合だけ、その approval URL を上司へ届ける外部 notification workflow も併せて呼び出します。未設定または送信失敗時は共有リンク運用へフォールバックします。
- 上司承認待ちは会話 status と `approval_request.approval_scope=manager` で復元され、差し戻し時は担当者承認 UI へ戻ります。2 回目以降の上司承認でも、直前の確定版は `pendingVersion` として保持され、UI から見失わないようにしています。
- 組み込み上司承認ページは URL fragment に token を載せ、`GET /api/chat/{thread_id}/manager-approval-request` と `POST /api/chat/{thread_id}/manager-approval-callback` を使って承認状態を更新します。承認ページは `current_version` と `previous_versions` を受け取り、今回版と過去の確定版を横並びで比較します。
- Microsoft Learn の Teams connector 情報では Teams 操作は Logic Apps Standard 側の可用性が前提なので、現行の Consumption workflow は post approval actions 専用に維持し、通知 workflow は別リソース扱いにしています。
- Agent1 は `FABRIC_DATA_AGENT_URL` がある場合、Fabric Data Agent Published URL を最優先で使用します。利用不可時のみ Fabric Lakehouse SQL endpoint の pyodbc 接続、その後 CSV → ハードコードデータへフォールバックします。
- Agent4 は顧客向けブローシャを生成し、KPI・売上目標・社内分析を含めません。
- Agent5（動画生成）は Photo Avatar で SSML ナレーションを生成し、`ja-JP-Nanami:DragonHDLatestNeural` 音声、冒頭ジェスチャー、`casual-sitting` スタイルの販促動画を MP4/H.264 で生成します。
- Agent6 は `GitHubCopilotAgent` + `PermissionHandler.approve_all` で動作し、利用不可時は `FoundryChatClient` にフォールバックします。
- Code Interpreter はランタイムで自動検出され、利用不可時はグレースフルにフォールバックします。
- APIM は Azure 側に作られ、`scripts/postprovision.py` で Foundry AI Gateway 接続（`travel-ai-gateway`）の作成とトークン制限ポリシー（80,000 tokens/min）の適用が自動実行されます。加えて Voice Live 用 Prompt Agent と Entra SPA アプリ登録も作成します。
- `/api/evaluate` は Built-in 評価器（Relevance / Coherence / Fluency）に加え、旅行業法準拠、コンバージョン期待度、訴求力、差別化、KPI 妥当性、ブランドトーンを返し、成功時は Foundry にログします。
- フロントエンドは各 `done` イベントごとに成果物スナップショットを保持し、`VersionSelector` で企画書・ブローシャ・画像・動画をまとめて切り替えます。
- 新しい版の生成中は、右ペインはそのラウンドのライブワークスペースとして更新されます。同時に `VersionSelector` から確定済みバージョンを読み取り専用で確認でき、生成中チップでライブ表示に戻せます。
- 評価比較 UI はフロントエンド内で完結し、現在の版と比較対象版を上部カードで併記します。比較対象を変えてもメインの成果物プレビューは切り替わりません。
- 評価レスポンスに `task_adherence` が含まれる場合でも、現行 UI ではノイズ低減のため比較差分、総合サマリ、改善フィードバックから除外しています。
- 品質レビューは主フロー後の追加 `text` イベントとして返ります。最終承認後はブローシャ生成完了時点でユーザー向け `done` を返し、動画 URL、品質レビュー、承認後アクションは background update として同じ会話へ追記されることがあります。
- パイプラインは 5 ユーザー向けステップで、内部は 7 エージェントで構成されています（Agent3a+3b がステップ 4、Agent4+5 がステップ 5 を共有）。
- モデル配備側のガードレールを主軸にしつつ、FastAPI 側では明らかな入力 / ツール応答の指示上書きだけを軽量ガードでブロックします。Prompt Shields や tool-response 介入などの追加 guardrail は Azure / Foundry 側で明示設定した場合のみ有効です。
- Azure AI Search の実行時検索は Managed Identity ベースです。API キーはセットアップ用スクリプトの任意経路にだけ残っています。
- Voice Live API は MSAL.js + Entra アプリ登録認証で動作し、`/api/voice-token` と `/api/voice-config` エンドポイントを提供します。
- 会話履歴は Cosmos DB に保存され、フロントエンドの `restoreConversation()` で再推論なしに復元されます。

## 6.1 実機メモ

- `2026-04-04` 時点で、Azure 実機は `/api/health=ok`、`/api/ready=ready` を確認済みです。
- テキストの実行 deployment は `gpt-5-4-mini`、評価用 deployment は `gpt-4-1-mini`、画像は `gpt-image-1.5` を使用しています。
- Fabric は workspace `TeamD`、capacity `teamdfabric`（F64, Japan East）、Lakehouse `Travel_Lakehouse` が稼働し、現行アプリでは SQL endpoint を接続済みです。
- APIM AI Gateway の `travel-ai-gateway` 接続と token policy は post-provision で構成済みです。

## 7. Phase2 拡張候補

- Azure Functions MCP は現行 IaC とランタイムには含めません。現在のツール呼び出しはエージェント内 `@tool` 実装が前提です。
- MCP 化を検討するのは、AI Gateway で統制したい新規リモートツールや、別コネクタ境界に分離したい外部連携を追加するときだけです。
- 将来実装する場合は Azure Functions MCP extension を優先し、Flex Consumption、stateless、streamable HTTP の制約を前提に設計します。
- Teams / メール通知 workflow は MCP 対象ではなく、必要になった場合だけ `MANAGER_APPROVAL_TRIGGER_URL` で呼ぶ外部 workflow として追加します。
