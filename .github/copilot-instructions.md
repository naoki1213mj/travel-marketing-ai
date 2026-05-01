# Travel Marketing AI Multi-Agent Pipeline

## What This Is

旅行会社マーケ担当者向けの AI マルチエージェントパイプライン。自然言語指示から企画書・販促物・バナー画像・動画を全自動生成する。Team D ハッカソン作品。

## Tech Stack

- **バックエンド**: Python 3.14 / FastAPI / uvicorn / SSE ストリーミング
- **フロントエンド**: React 19 / TypeScript / Vite / Tailwind CSS / i18n (日英中)
- **推論モデル**: gpt-5.4-mini (GA、既定) / gpt-5.5 (GA、要 quota / opt-in)
- **画像生成**: gpt-image-2 (GA、既定) / gpt-image-1.5 (GA) / MAI-Image-2 (別 endpoint, GA)
- **エージェント**: Microsoft Agent Framework 1.0.0 (GA)
- **オーケストレーション**: FastAPI 直接オーケストレーション (Sequential Workflow on top of Agent Framework)
- **データ**: Microsoft Fabric Lakehouse (Phase 9 v2 schema, Delta Parquet + SQL endpoint) + Fabric Data Agent v2 (Phase 10 tuned)
- **ナレッジ**: Foundry IQ Knowledge Base (Azure AI Search backend)
- **MCP サーバー**: Azure Functions (Flex Consumption)
- **AI Gateway**: Azure API Management
- **デプロイ**: Azure Container Apps (VNet 統合 CAE) / Docker マルチステージ / azd
- **CI/CD**: GitHub Actions (DevSecOps: Ruff + pytest + tsc → ACR `az acr build` → Container Apps)
- **パッケージ管理**: uv（pip ではなく uv を使う）
- **音声入力**: Voice Live API (Preview, Foundry Agent Service 統合)
- **文書解析**: Content Understanding (GA, 既存パンフレット PDF 解析)
- **販促動画**: Photo Avatar + Voice Live (Lisa / casual-sitting 固定, ja-JP-Nanami:DragonHDLatestNeural)
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
4. **ガードレール方針**: モデル配備側の Content Filter を主軸にし、FastAPI 側では明らかな入力 / ツール応答の指示上書きだけを軽量ガードで弾く
5. **East US 2 推奨**: Code Interpreter のリージョン可用性の制約により Japan East ではなく East US 2 または Sweden Central を使う
6. **Web Search のデータ境界**: DPA 対象外。クエリデータが Azure の geo boundary 外に流れる可能性がある
7. **Photo Avatar の位置づけ**: 「AI アシスタントの顔」ではなく「販促素材の一部」として使う。マーケ担当者が作る成果物に組み込む
8. **付加価値機能は独立設計**: §14 の 6 機能はコアパイプラインと独立しており、個別に有効化・無効化できる
9. **ACR ビルド**: Docker Desktop は使わず、`az acr build` でリモートビルドする。ローカルに Docker Engine は不要。CI/CD の deploy.yml でも `az acr build` を使う
10. **Foundry リソースモデル**: Hub+Project ではなく、新しい Foundry リソースモデル（`CognitiveServices/accounts` + `accounts/projects@2025-06-01`、`allowProjectManagement: true`）を使う
11. **VNet 統合 CAE は blue-green**: Azure は既存 CAE への vnetConfiguration 追加と既存 Container App の managedEnvironmentId 変更を許可しないため、`-pn` サフィックス付きで side-by-side cutover する設計（2026-05-01 完了済）
12. **承認 token は per-conversation bearer**: `/api/chat/{id}/approve` は `approval_token` (32-byte urlsafe) で保護。`chat()` が Agent2 完了時に発行、`approval_request` SSE で配布、frontend が echo。匿名 lookup は token 必須 (`APPROVAL_CONTEXT_NOT_FOUND` で拒否)。`hmac.compare_digest` で定数時間比較。詳細は `docs/approval-security.md`

## Live snapshot (2026-05-01 cutover complete)

| 項目 | 値 |
|---|---|
| Public URL | `https://ca-wmbvhdhcsuyb2-pn.wonderfultree-f9803f6f.eastus2.azurecontainerapps.io/` |
| CAE | `cae-wmbvhdhcsuyb2-pn` (VNet integrated, `snet-container-apps`) |
| Fabric workspace | `ws-3iq-demo` (capacity `fcdemoeastus2001`, East US 2, F64, Active) |
| Fabric Data Agent | `Travel_Ontology_DA_v2` (Phase 10 tuned, best-of 12/14 grade A) |
| Fabric Lakehouse | `lh_travel_marketing_v2` (10 Delta tables in `dbo`) |
| 既知 platform 問題 | Phase 10 P13 / P14 prompts で Fabric `submit_tool_outputs` BadRequest (Microsoft サポート起票待ち) |
| Old `ca-wmbvhdhcsuyb2` / `cae-wmbvhdhcsuyb2` | 2026-05-01 に削除済 |

## Resources

- 要件定義書: `docs/requirements_v4.0.md`
- 承認 token セキュリティ: `docs/approval-security.md`
- API リファレンス: `docs/api-reference.md`
- デプロイガイド: `docs/deployment-guide.md`
- Azure セットアップ: `docs/azure-setup.md`
- デモ台本: `docs/demo-script.md`
- Phase 10 Fabric tune サマリ: `scripts/fabric_data_overhaul/v2_artifacts/phase10_summary.md`
- Agent Framework パターン: `.github/skills/agent-framework-patterns/SKILL.md`
- Hosted Agent デプロイ: `.github/skills/foundry-hosted-agent/SKILL.md`
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
gh run list --workflow=deploy.yml --limit 3  # 直近の deploy 状況
```

