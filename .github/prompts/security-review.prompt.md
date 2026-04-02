---
description: 'セキュリティレビューを実行する。シークレット漏洩、認証設定、AI ガードレール、ネットワーク分離を点検する。'
mode: agent
tools: ['filesystem', 'terminal', 'search']
---

# セキュリティレビュー

プロジェクト全体をセキュリティ観点でレビューしてください。

## チェック項目

### 1. シークレット漏洩

- `.env` が `.gitignore` に含まれているか
- コードに API キー・接続文字列・サブスクリプション ID がハードコードされていないか
- `.env.example` にプレースホルダーのみが記載されているか
- Gitleaks で検出されるパターンがないか

### 2. 認証

- DefaultAzureCredential が使われているか
- Managed Identity が正しく設定されているか
- OIDC Workload Identity Federation が CI/CD で使われているか
- API キーベースの認証が残っていないか

### 3. AI ガードレール

- 入力時: `/api/chat` で明らかなプロンプト注入 / 指示上書きパターンをブロックしているか
- モデル: Content Filter がデプロイメント設定で有効か
- ツール応答: 外部データをモデルに渡す前の軽量ガードがあるか
- AI Gateway: `llm-content-safety` など追加ポリシーが必要なら適切に設定されているか

### 4. ネットワーク

- Container Apps が VNet 統合で配置されているか
- Key Vault が Private Endpoint 経由のみか
- Web Search のデータ境界例外が認識されているか

### 5. 依存関係

- `uv run pip-audit` で脆弱性がないか
- `cd frontend && npm audit` で脆弱性がないか

## 出力形式

問題を見つけたら、ファイルパスと行番号、問題の内容、修正案を示してください。
