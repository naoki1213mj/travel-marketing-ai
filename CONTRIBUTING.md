# Contributing to Travel Marketing AI

Team D ハッカソンプロジェクトへの貢献ガイド。社内引継ぎ / 後続オーナー向け。

## はじめに

このプロジェクトは Microsoft Foundry + Azure フル PaaS 構成のマルチエージェントパイプラインです。技術スタックの全体像は [README.md](README.md) と [AGENTS.md](AGENTS.md) を参照してください。

## 開発フロー

### 1. ローカルセットアップ

```bash
# Python 依存
uv sync

# Node 依存
cd frontend && npm ci && cd ..

# 環境変数の準備 (テンプレートをコピーして実値を埋める)
cp .env.example .env
```

### 2. ローカル実行

```bash
# バックエンド (FastAPI + uvicorn)
uv run uvicorn src.main:app --reload --port 8000

# フロントエンド (Vite, バックエンドへ proxy)
cd frontend && npm run dev
```

### 3. テスト

```bash
# Python (pytest, 570+ tests)
uv run pytest

# TypeScript (vitest, 240+ tests)
cd frontend && npm run test

# 型チェック
cd frontend && npx tsc --noEmit

# Lint
uv run ruff check .
cd frontend && npm run lint
```

### 4. コミット規約

- Conventional Commits 形式: `feat(scope): summary`, `fix(scope): summary`, `docs(scope): ...`
- コミットメッセージ末尾に必ず以下を含める:
  ```
  Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
  ```
- 1 commit = 1 論理変更。large refactor は予め plan.md / GitHub issue で合意してから

### 5. Pull Request

- ブランチ命名: `feat/...` / `fix/...` / `docs/...`
- PR 説明には: 何を変えたか / なぜ / どうテストしたか / rubber-duck critique したか
- CI (CI / Security Scan / Deploy) が全て ✅ になるまで merge しない
- main ブランチへの直接 push は緊急時のみ (deploy.yml が auto-deploy するため事故時の影響大)

## 必須プラクティス

### コードスタイル

- **Python**: 型ヒント必須 (`str | None` 形式、`Optional[str]` 不可)。bare `except` 禁止、具体的な例外型で catch
- **TypeScript**: strict mode、`any` 禁止、型推論できない箇所は明示的型注釈
- **コメント**: 日本語で記述。docstring も日本語
- **変数 / 関数名**: 英語

### セキュリティ

- 詳細は [SECURITY.md](SECURITY.md) 参照
- シークレットを `.env` 以外に書かない (`.env` は `.gitignore` 済)
- API キーをコードにハードコードしない、Azure 認証は `DefaultAzureCredential`
- 承認 token / Cosmos credentials を log に出さない
- secret commit を防ぐため `gitleaks` が CI で動いている

### Rubber-duck Review (必須)

このリポジトリでは、些細な変更でも **rubber-duck エージェントで critique を取ることが必須** です (User 明示要望 2026-05-01、`AGENTS.md` `## 変更の規律` 参照)。

- **planning フェーズ**: 実装前に `Task(rubber-duck, ...)` で plan critique を取り、blocking 指摘を反映してから実装開始
- **実装後**: 大きな変更や疑問のある箇所では、もう一度 rubber-duck で final implementation review
- 「trivial だから skip」しないこと

### Azure 本番への変更

- `azd up` / `az` CLI で Azure 本番リソースを直接変更する場合は、理由と影響範囲を先に説明して合意を取る
- `git push origin main` は GitHub Actions deploy.yml を自動 trigger する。事故防止のため commit 前に必ず CI で local / staging テスト

## ディレクトリ構成

主要なディレクトリは [README.md](README.md) を参照。新ファイルを追加する際は、関連ディレクトリ規約を踏襲してください:

- バックエンド agent: `src/agents/<agent_name>.py` + `tests/test_agents.py` に対応テスト
- API ルーター: `src/api/<router>.py` + `tests/test_<router>.py`
- フロントエンド component: `frontend/src/components/<Component>.tsx` + `<Component>.test.tsx`
- スクリプト: `scripts/<purpose>/<script>.py` (使い捨ては避ける、再利用可能な形に)

## Microsoft Foundry / Fabric 変更

`scripts/fabric_data_overhaul/v2_artifacts/` 配下のスクリプトで Fabric Data Agent / Ontology / Semantic Model を更新する場合:

1. **Backup を必ず取る** (`backups/` ディレクトリ)
2. **rubber-duck で plan critique**
3. **prompt-injection probe で behavior 確認** (例: `probe_gql_hint.py`)
4. **後で smoke を流して回帰なし確認** (`smoke_test_v6.py`)

詳細は `scripts/fabric_data_overhaul/v2_artifacts/phase10_summary.md` の手順を参照。

## Issue / PR テンプレート (将来追加予定)

- Bug report / Feature request / Documentation issue のテンプレートは未整備。必要に応じて `.github/ISSUE_TEMPLATE/` を追加してください

## 質問

- 技術的な質問: GitHub issue を切る、または `docs/` 配下のドキュメントを参照
- セキュリティ脆弱性: [SECURITY.md](SECURITY.md) の手順に従って private 報告
