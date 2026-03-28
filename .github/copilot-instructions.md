# Travel Marketing AI Multi-Agent Pipeline

## What This Is

旅行会社マーケ担当者向けの AI マルチエージェントパイプライン。自然言語指示から企画書・販促物・バナー画像を全自動生成する。Team D ハッカソン作品。

## Tech Stack

- **バックエンド**: Python 3.14 / FastAPI / uvicorn / SSE ストリーミング
- **フロントエンド**: React 18 / TypeScript / Vite / Tailwind CSS / i18n (日英中)
- **推論モデル**: gpt-5.4-mini (GA)
- **画像生成**: GPT Image 1.5 (GA, 要アクセス承認)
- **エージェント**: Microsoft Agent Framework 1.0.0rc5 (RC, Breaking Changes あり)
- **オーケストレーション**: Foundry Agent Service Workflows (Preview)
- **データ**: Fabric Lakehouse (Delta Parquet + SQL エンドポイント)
- **ナレッジ**: Foundry IQ Knowledge Base (Preview)
- **MCP サーバー**: Azure Functions (Flex Consumption)
- **AI Gateway**: Azure API Management
- **デプロイ**: Azure Container Apps / Docker マルチステージ / azd
- **CI/CD**: GitHub Actions (DevSecOps: Ruff + pytest + tsc → ACR → Container Apps)
- **パッケージ管理**: uv（pip ではなく uv を使う）
- **音声入力**: Voice Live API (Preview, Foundry Agent Service 統合)
- **文書解析**: Content Understanding (GA, 既存パンフレット PDF 解析)
- **販促動画**: Photo Avatar + Voice Live (Preview, 紹介動画自動生成)
- **ワークフロー自動化**: Azure Logic Apps (承認後の Teams 通知 + SharePoint 保存)
- **配信チャネル**: Microsoft Teams (Foundry から直接公開)

## Coding Guidelines

- パッケージ管理は uv。`uv add <pkg>` / `uv sync` / `uv run pytest`
- 型ヒント必須。`str | None` 形式（`Optional[str]` ではなく）
- 変数名・関数名は英語。コメントと docstring は日本語
- エラーは具体的な例外型で catch（bare except 禁止）
- コミットメッセージは Conventional Commits 形式
- Azure 認証は DefaultAzureCredential。API キーをコードにハードコードしない
- シークレットは .env（.gitignore 済み）。.env.example はプレースホルダーのみ
- Azure AI Foundry ではなく Microsoft Foundry と書く（2025-11 リネーム済み）
- 不明な API は公式ドキュメントか PyPI で確認してから使う

## Key Decisions

1. **Hosted Agent 優先**: Foundry Agent Service の VNet 分離は Hosted Agent 未対応なので、ネットワーク分離は Container Apps 層と Key Vault 層に限定する
2. **Flex Consumption**: Azure Functions の MCP サーバーは Flex Consumption プラン（旧 Consumption はレガシー）
3. **FastAPI 中継**: フロントエンドは直接 Foundry API を叩かず、FastAPI バックエンド経由で SSE ストリーミングする
4. **Content Safety 4 層**: 入力(Prompt Shield) → モデル(Content Filter) → ツール応答(Prompt Shield for tool response) → 出力(Text Analysis)
5. **East US 2 推奨**: Code Interpreter のリージョン可用性の制約により Japan East ではなく East US 2 または Sweden Central を使う
6. **Web Search のデータ境界**: DPA 対象外。クエリデータが Azure の geo boundary 外に流れる可能性がある
7. **Photo Avatar の位置づけ**: 「AI アシスタントの顔」ではなく「販促素材の一部」として使う。マーケ担当者が作る成果物に組み込む
8. **付加価値機能は独立設計**: §14 の 6 機能はコアパイプラインと独立しており、個別に有効化・無効化できる

## Resources

- 要件定義書: `docs/requirements_v3.md`
- Agent Framework パターン: `.github/skills/agent-framework-patterns/SKILL.md`
- Hosted Agent デプロイ: `.github/skills/foundry-hosted-agent/SKILL.md`
- SSE + Content Safety: `.github/skills/sse-content-safety/SKILL.md`
- Foundry Workflows: `.github/skills/foundry-workflows/SKILL.md`
- フロントエンド UI 設計: `.github/skills/agent-demo-frontend/SKILL.md`

## Quick Commands

```bash
uv sync                                  # Python 依存インストール
cd frontend && npm ci                     # Node 依存インストール
uv run uvicorn src.main:app --reload      # バックエンド起動
cd frontend && npm run dev                # フロントエンド起動
uv run pytest                             # テスト
uv run ruff check .                       # リント
azd up                                    # Azure デプロイ
```
