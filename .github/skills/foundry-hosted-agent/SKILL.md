---
name: foundry-hosted-agent
description: >-
  Foundry Agent Service に Hosted Agent としてデプロイする手順。
  Docker イメージのビルド、ACR への push、Hosted Agent の作成・公開、
  Managed Identity の設定、制約（private networking 未対応）を含む。
  Triggers: "Hosted Agent", "デプロイ", "Foundry にデプロイ", "publish agent",
  "ACR", "agent identity", "エージェント公開"
---

# Foundry Hosted Agent デプロイ手順

## 前提条件

- Microsoft Foundry プロジェクト作成済み
- Azure Container Registry (ACR) 作成済み
- ACR に対して User Access Administrator 以上のロールを持っていること
- `azure-ai-projects >= 2.0.0` がインストール済み

## 制約（2026-03 時点）

- Hosted Agent は **Preview**
- **private networking 未対応**: ネットワーク分離された Foundry リソース内では作成不可
- 課金: 2026年4月1日以降、マネージドホスティングランタイムの課金が開始予定
- 公開前のエージェントはプロジェクトの Managed Identity で実行される
- 公開後は専用の Agent Identity が割り当てられる（リソースの権限再設定が必要）

## デプロイ手順

### 1. Docker イメージのビルド

```bash
# マルチステージビルド
docker build --platform linux/amd64 -t travel-agents:latest .

# ACR にタグ付け
docker tag travel-agents:latest <ACR_NAME>.azurecr.io/travel-agents:latest

# ACR にログイン＆push
az acr login --name <ACR_NAME>
docker push <ACR_NAME>.azurecr.io/travel-agents:latest
```

### 2. Hosted Agent の作成（SDK）

```python
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

client = AIProjectClient(
    project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

# Hosted Agent 作成
agent = client.agents.create_hosted_agent(
    name="travel-marketing-pipeline",
    image=f"{acr_name}.azurecr.io/travel-agents:latest",
    environment_variables={
        "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
    },
)
```

### 3. 公開

```python
# エージェントアプリケーションリソースに公開
published = client.agents.publish(agent_name="travel-marketing-pipeline")
# 公開後、Agent Identity に必要なロールを再割当てする
```

### 4. 公開後の権限再設定

公開すると専用の Agent Identity が生成される。
プロジェクトの Managed Identity の権限は引き継がれないため、以下を再設定する:

- Key Vault: Key Vault Secrets User
- Fabric: 該当するデータアクセスロール
- Azure AI Search: Search Index Data Reader
- Content Safety: Cognitive Services User

## 参照

- 公式ドキュメント: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents
- サンプル: https://github.com/microsoft-foundry/foundry-samples
