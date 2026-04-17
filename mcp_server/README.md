# Improvement Brief MCP Server

Azure Functions MCP extension を使って `generate_improvement_brief` ツールを公開する最小構成です。

## 前提

- Python 3.14 以上
- [uv](https://docs.astral.sh/uv/)
- Azure Functions Core Tools v4
- Azure Functions Flex Consumption でのデプロイ権限

## ローカル起動

```powershell
cd mcp_server
uv venv
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
func start
```

MCP endpoint は `http://localhost:7071/runtime/webhooks/mcp` です。

## Azure への出し方

既定では `azd provision` 後の `scripts/postprovision.py` が、このディレクトリを Flex Consumption Function App へ zip 配備し、そのまま APIM の `improvement-mcp` route まで同期します。

個別に再配備したい場合は、リポジトリ直下で次を実行します。

```powershell
uv run python scripts/deploy_improvement_mcp.py
```

このスクリプトは次を行います。

1. improvement MCP 用 storage account と Function App を作成または再利用する
2. Function App に system assigned managed identity を付与し、runtime / deployment storage を keyless 構成へ揃える
3. `mcp_server/` を zip 化して Flex Consumption Function App へ remote build 付きで配備する
4. Function App の system key `mcp_extension` を取得する
5. APIM の backend / `improvement-mcp` API / policy を更新する
6. FastAPI 側の `IMPROVEMENT_MCP_ENDPOINT` が `https://<apim>.azure-api.net/improvement-mcp/runtime/webhooks/mcp` を向く状態に揃える

## APIM 登録

1. Function App を Azure に配置する
2. APIM の `Expose an existing MCP server` で `https://<funcapp>.azurewebsites.net/runtime/webhooks/mcp` を登録する
3. FastAPI 側には APIM 公開 endpoint `https://<apim>.azure-api.net/<base-path>/runtime/webhooks/mcp` を `IMPROVEMENT_MCP_ENDPOINT` として設定する
4. APIM が `subscriptionRequired=false` なら `IMPROVEMENT_MCP_API_KEY` は不要。必須にする場合だけ `IMPROVEMENT_MCP_API_KEY` と `IMPROVEMENT_MCP_API_KEY_HEADER` を設定する

## 互換性メモ

- APIM の公開 path は `/mcp` ではなく `/runtime/webhooks/mcp` になる
- クライアントは `Accept: application/json, text/event-stream` を送る
- Azure Functions MCP extension では JSON-RPC request id を数値文字列にすると安定する
- `tools/call` の `content[].text` は JSON だけでなく Python リテラル文字列として返る場合がある
- 新しい tenant / policy では storage account の key-based auth が無効化されることがあるため、postprovision は managed identity ベースの deployment storage に自動で切り替える
