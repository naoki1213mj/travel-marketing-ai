# Security Policy

Travel Marketing AI (Team D) のセキュリティポリシー。

## サポート対象

ハッカソン期間中のためバージョン管理は流動的です。`main` ブランチの最新 commit のみがサポート対象です。production 想定の長期運用は別途検討が必要です。

| バージョン | サポート |
|-----------|---------|
| `main` (latest) | ✅ active |
| 過去 commit | ❌ |

## 脆弱性の報告

セキュリティ脆弱性を発見した場合、**public な GitHub issue は作らないでください**。代わりに以下のいずれかで報告してください:

- GitHub Security Advisory (推奨): [リポジトリ Security タブ](https://github.com/naoki1213mj/travel-marketing-ai/security/advisories/new)
- Team D メンテナへの直接連絡 (社内 Teams / 内部メール)

報告内容に含めてほしい情報:

- 影響範囲 (どのコンポーネント / どの API / どの Azure リソース)
- 再現手順
- 想定される影響度 (CVSS スコアあれば歓迎)
- 提案される修正案 (あれば)

## セキュリティ実装の主要事項

### 1. 認証 / 認可

- **Entra ID Bearer 認証** — `/api/chat`, `/api/approve`, `/api/conversations` 等の主要 API で `extract_request_identity()` により owner ID を解決
- **匿名フィンガープリント** — Bearer 不在時は IP + UA の SHA-256 ハッシュ (`anon-{32hex}`) を fallback owner として使用。**完全匿名アクセスを許可するか tenant 内のみに制限するかは `REQUIRE_AUTHENTICATED_OWNER` env var で制御**
  - フィンガープリントは Connection 再利用や X-Forwarded-For 並び順で揺らぐため、`approval_token` (per-conversation 32-byte urlsafe bearer) を併用して cross-fingerprint approval を防止 (詳細は [docs/approval-security.md](docs/approval-security.md) 参照)
- **Cross-owner protection** — 実ユーザー (Entra) → 別の実ユーザーの cross-owner approval は token 一致でも拒否、anon fingerprint shift だけ token rescue を許可 (`src/api/chat.py:_load_pending_approval_context`)

### 2. シークレット管理

- すべての secret は **Key Vault** から `secretRef` で Container App env var に注入。`.env` ファイルは `.gitignore` 済 / レポジトリに混入しない
- **secret rotation cadence**:

  | Secret | Cadence | 手順 |
  |--------|---------|------|
  | `SEARCH_API_KEY` (Azure AI Search admin key) | 90 日 | Azure Portal で regenerate → Key Vault に新値設定 → Container App revision 更新 |
  | `MANAGER_APPROVAL_TRIGGER_URL` (Logic Apps SAS) | 180 日 | Logic Apps で regenerate → Key Vault 更新 |
  | Cosmos DB connection / Storage account keys | Managed Identity 利用のため rotation 対象外 |
  | `IMPROVEMENT_MCP_API_KEY` (APIM subscription) | 180 日 | APIM Portal で regenerate |

- 手動 rotation 後は必ず post-rotation smoke (`/api/ready/deep`) を流して live で動作確認

### 3. 承認フロー (approval_token bearer security)

- `chat()` が Agent2 marketing-plan-agent 完了時に `secrets.token_urlsafe(32)` で per-conversation token を mint
- SSE `approval_request` event の `approval_token` フィールドで client に配布
- Cosmos の `metadata.pending_approval_token` に `awaiting_approval` 中のみ保存
- `_load_pending_approval_context` が `hmac.compare_digest()` で定数時間比較
- 詳細は [docs/approval-security.md](docs/approval-security.md) 参照

### 4. 入力ガード

- `check_prompt_shield()` — Azure Content Safety Prompt Shield で injection 検知 (chat / approve 両方適用)
- `_safe_evidence_quote()` — evidence quote のサニタイズ
- 各 agent tool で path traversal / SSRF 防止 (例: `analyze_existing_brochure` は `data/` 配下のみアクセス許可)

### 5. データ保護

- **Cosmos DB**: private endpoint 経由 (`publicNetworkAccess: Disabled`)、partition by `/user_id` で owner isolation
- **Container Apps**: VNet 統合 CAE (`cae-wmbvhdhcsuyb2-pn`) 配下で実行
- **OneLake / Lakehouse**: Fabric workspace `ws-3iq-demo` の MI 経由のみアクセス
- すべての outbound HTTP は Azure 内ネットワークまたは TLS 1.2+ で実施

### 6. Supply Chain

- GitHub Actions: `gitleaks` (secret detection) + `Trivy` (container image scan) を CI 必須
- Docker base image は最新 LTS (Python 3.14-slim) を使用、`.dockerignore` で `.env` / `.git` 等を除外
- Python 依存は `uv` で lock file 管理 (`uv.lock`)
- 既知の脆弱な依存は dependabot で自動 PR (近々有効化予定)

### 7. Audit Log

- approval flow の全ての /approve POST は INFO log に diagnostic 出力 (`caller_owner_kind`, `save_owner_kind`, `context_owner_kind`, `has_token`, `token_len`, `context_resolved`, `is_approved`)。token VALUE は記録しない
- Cosmos approval-critical の保存失敗は ERROR + OpenTelemetry span で fail-loud
- すべてのリクエストは `request_id` (uuid4) を持ち App Insights traces に紐付く

### 8. 既知の制約 (Hardening 中)

| 項目 | 現状 | 対応予定 |
|------|------|---------|
| 匿名 fingerprint の安定性 | 完全に安定ではない | HttpOnly + SameSite=Strict cookie 化 (D2) |
| `_pending_approvals` in-memory | single-replica 前提 | Cosmos / Redis 移行 (D1) |
| `SEARCH_API_KEY` | 共有 admin key | Managed Identity 化 (A3) |
| Cosmos pending_approval_token | TTL なし | 24h auto-cleanup (D3) |
| Azure AI Search 落ち時 NG list | hardcoded 5 件 fallback | UI 警告 + content expansion (E2 expanded) |

これらは [CHANGELOG.md](CHANGELOG.md) `[Unreleased]` セクションでも追跡しています。

### 9. Penetration Test / Red Team

- 現在は社内 review のみ。external pentest は未実施
- 過去の rubber-duck audit:
  - 2026-05-01: approval token cross-owner gap (実ユーザー間 token rescue) → 修正済 (`commit ea2c8ba`)
  - 2026-05-01: `_image_settings_fallback` cross-user data leak (single-replica でも発生) → 修正済 (`commit d3d2867`)
  - 2026-05-01: anon fingerprint shift partition mismatch → 修正済 (`commit ea2c8ba`)

## 報告者への謝意

セキュリティ脆弱性を responsible disclosure で報告いただいた方には、CHANGELOG.md の "Acknowledgments" セクションに記載 (希望者のみ) します。

## 参考資料

- [docs/approval-security.md](docs/approval-security.md) — 承認 token のセキュリティモデル詳細
- [docs/azure-setup.md](docs/azure-setup.md) — Azure リソース設定 / RBAC ガイド
- [docs/deployment-guide.md](docs/deployment-guide.md) — 本番デプロイ手順 + cutover runbook
