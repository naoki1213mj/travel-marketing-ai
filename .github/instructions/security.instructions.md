---
name: 'セキュリティルール'
description: 'エンタープライズセキュリティの規約（全ファイル適用）'
applyTo: 'src/**,frontend/src/**,infra/**,scripts/**,Dockerfile,azure.yaml,.github/workflows/**'
---

## セキュリティルール

### 認証・認可

- DefaultAzureCredential を使う。API キーのハードコードは絶対禁止
- Container Apps は System Managed Identity で Foundry / Key Vault / Fabric に認証
- Key Vault RBAC: Key Vault Secrets User ロールを Managed Identity に割り当て
- GitHub Actions → Azure: OIDC Workload Identity Federation（シークレット不要）

### シークレット管理

- `PROJECT_ENDPOINT`, `APPLICATIONINSIGHTS_CONNECTION_STRING` 等は Key Vault に格納
- `.env` は `.gitignore` 済み。`.env.example` にはプレースホルダーのみ
- Azure サブスクリプション ID・テナント ID・リソース名をコードに含めない
- ACR のログイン情報はコードに書かない（OIDC で認証）

### Content Safety

- 入力: Prompt Shield でプロンプトインジェクション検出（FastAPI 側）
- モデル: Content Filter をデプロイメント設定で有効化
- ツール応答: Prompt Shield for tool response で間接攻撃を検出
- 出力: Text Analysis で 4 カテゴリ（Hate/SelfHarm/Sexual/Violence）スキャン
- AI Gateway: `llm-content-safety` ポリシーで追加フィルタリング

### ネットワーク

- Container Apps は VNet 統合で配置
- Key Vault は Private Endpoint 経由のみアクセス可能
- Foundry Agent Service は Basic Setup（Hosted Agent が private networking 未対応）
- Web Search ツールは DPA 対象外（geo boundary 外にデータが流れる可能性あり）

### リポジトリ

- Public リポジトリ（ハッカソン要件）
- Gitleaks でシークレット検出を CI/CD で自動実行
- Trivy でコンテナ脆弱性スキャン
- npm audit + pip-audit で依存関係監査
