# Security Review: Travel Marketing AI Multi-Agent Pipeline

**Date**: 2026-03-31 (Follow-up: 2026-03-29 review)
**Reviewer**: Security Review Agent
**Ready for Production**: No (conditionally — see summary)
**Critical Issues**: 1
**High Issues**: 3
**Medium Issues**: 8
**Low Issues**: 5
**Fixed since last review**: 9

---

## Previous Review Status (2026-03-29)

以下の指摘は対策済みと確認:

| Former ID | 件名 | 状態 |
|-----------|------|------|
| S-01 | Content Safety fail-open（endpoint 未設定） | ✅ Fixed — `_content_safety_required()` + `is_production_environment()` で本番は fail-close |
| S-02 | Text Analysis fail-open（エラー時） | ✅ Fixed — `SafetyScores.check_failed` フィールド追加、`status: "error"` 判定を実装 |
| S-03 | `disableLocalAuth: false` | ✅ Fixed — `disableLocalAuth: true` に変更済み |
| S-05 | 承認エンドポイント Prompt Shield なし | ✅ Fixed — `approve()` に `check_prompt_shield(body.response)` 追加済み |
| S-06 | エラーメッセージ情報漏洩 | ✅ Fixed — `_execute_agent()` はジェネリックメッセージを返す |
| S-08 | トークン同期取得 | ✅ Fixed — `get_bearer_token_provider()` でキャッシュ付き非同期トークン取得に変更 |
| S-10 | レート制限なし | ✅ Fixed — `slowapi` で `/api/chat` (10/min), `/api/upload-pdf` (5/min) にレート制限追加 |
| S-11 | Dockerfile `npm install` | ✅ Fixed — `npm ci --force` に変更 |
| S-13 | CI にセキュリティスキャンなし | ✅ Fixed — `security.yml` に Trivy / Gitleaks / pip-audit / bandit / npm audit 追加 |

以下の指摘は部分対応または未対応:

| Former ID | 件名 | 状態 |
|-----------|------|------|
| S-04 | API エンドポイントに認証なし | ⚠️ Open — 引き続き High |
| S-09 | RBAC 過剰権限 | ⚠️ Open — 引き続き Medium |
| S-12 | Key Vault purge protection | 🔶 Partial — `enablePurgeProtection: true` 追加済み、`softDeleteRetentionInDays: 7` は最短値のまま |
| S-14 | VNet 統合なし | ⚠️ Open — CAE の VNet 統合コメントアウトのまま |
| S-15 | CORS 設定 | 🔶 Partial — `ALLOWED_ORIGINS` 環境変数化済み、`allow_methods/headers=["*"]` は残存 |

---

## Priority 1 — Must Fix ⛔

### SEC-01: `except json.JSONDecodeError, AttributeError:` — Python 3 構文エラー — Critical

**File**: `src/api/chat.py` L756
**Category**: Software Defect (Security Impact) / A05 - Security Misconfiguration

Python 3 では `except A, B:` 構文は無効（Python 2 のレガシー構文）。正しくは `except (A, B):` とタプルにする必要がある。この行は `SyntaxError` を引き起こし、モジュール全体のインポートが失敗する。

```python
# 現在（L756）: SyntaxError
except json.JSONDecodeError, AttributeError:

# 修正
except (json.JSONDecodeError, AttributeError):
```

**影響**: `chat.py` のインポートが失敗 → FastAPI アプリケーション全体が起動不能になる可能性がある。テストがモックで通過している場合、本番デプロイ時に初めて顕在化する。

---

## Priority 2 — Should Fix ⚠️

### SEC-02: API エンドポイントに認証なし — High（前回 S-04 より継続）

**File**: `src/api/chat.py` L1659, `src/api/voice.py` L16
**Category**: A01 - Broken Access Control

すべての API エンドポイントに認証・認可メカニズムがない:

- `/api/chat` (POST) — LLM パイプライン実行
- `/api/chat/{thread_id}/approve` (POST) — 承認フロー
- `/api/conversations` (GET) — 会話履歴一覧
- `/api/conversations/{id}` (GET) — 会話詳細（社内データ含む）
- `/api/voice-token` (GET) — **AAD トークンを認証なしで返す**
- `/api/upload-pdf` (POST) — ファイルアップロード
- `/api/replay/{id}` (GET) — リプレイ

特に `/api/voice-token` は Azure AD トークン（`https://ai.azure.com/.default` スコープ）をそのまま返すため、認証なしでは第三者が Azure リソースへのアクセストークンを取得できる。

**修正案**:

1. **最小対策**: Container Apps の Easy Auth（Entra ID 認証）を有効化
2. **推奨**: FastAPI ミドルウェアで JWT トークン検証を実装

---

### SEC-03: `_pending_approvals` メモリ枯渇リスク — High

**File**: `src/api/chat.py` L100
**Category**: A05 - Security Misconfiguration / DoS

`_pending_approvals: dict[str, PendingApprovalContext] = {}` はモジュールレベルの辞書で、サイズ制限・TTL・エビクションがない。承認されなかった会話コンテキスト（企画書 Markdown + 分析結果）が無限に蓄積し、コンテナの OOM Kill を引き起こす可能性がある。

```python
# 現在: サイズ制限なし
_pending_approvals: dict[str, PendingApprovalContext] = {}
```

**修正案**: TTL 付きの `cachetools.TTLCache` を使うか、最大サイズを制限する:

```python
from cachetools import TTLCache
_pending_approvals: TTLCache = TTLCache(maxsize=100, ttl=3600)  # 最大100件、1時間TTL
```

---

### SEC-04: Security スキャン `continue-on-error: true` で CI がブロックされない — High

**File**: `.github/workflows/security.yml` L81-L93
**Category**: A06 - Vulnerable and Outdated Components

`security.yml` の `pip-audit`、`npm audit`、`bandit` ステップに全て `continue-on-error: true` が設定されている。脆弱性やセキュリティ問題が検出されてもワークフローは成功扱いになり、アラートが見落とされる。

```yaml
# 現在: セキュリティスキャン失敗を無視
- name: pip-audit
  run: uv run pip-audit
  continue-on-error: true

- name: bandit (Python code security)
  run: uv run bandit -r src/ -ll --skip B101
  continue-on-error: true
```

**修正案**: `continue-on-error: true` を削除するか、`allow-failure` とは別にアラート通知で可視化する。

---

## Priority 3 — Recommended Changes 📋

### SEC-05: RBAC 過剰権限 `Cognitive Services Contributor` — Medium（前回 S-09 より継続）

**File**: `infra/modules/ai-project-app-access.bicep` L10-L22
**Category**: A01 - Broken Access Control (Least Privilege)

Container App MI に `Cognitive Services Contributor`（`25fbc0a9-...`）が割り当てられており、モデルデプロイメントの作成・削除・変更が可能。ランタイムに不要。

**修正案**: 削除して `Cognitive Services OpenAI User` + `Azure AI Developer` + `Azure AI User` の 3 ロールに限定。

---

### SEC-06: VNet 統合コメントアウト — Medium（前回 S-14 より継続）

**File**: `infra/main.bicep` L110
**Category**: A05 - Security Misconfiguration / Zero Trust

Container Apps Environment の VNet 統合が `// subnetId: vnet.outputs.containerAppsSubnetId` とコメントアウトされている。

**修正案**: 新規デプロイ時にコメント解除して VNet 統合を有効化。

---

### SEC-07: CORS `allow_methods=["*"]`, `allow_headers=["*"]` — Medium

**File**: `src/main.py` L107-L112
**Category**: A05 - Security Misconfiguration

CORS オリジンは `ALLOWED_ORIGINS` で制御されるようになったが、メソッドとヘッダーは `["*"]` のまま。

```python
# 修正
allow_methods=["GET", "POST", "OPTIONS"],
allow_headers=["Content-Type", "X-Request-Id"],
```

---

### SEC-08: `/api/conversations` の `limit` パラメータ上限なし — Medium

**File**: `src/api/conversations.py` L17
**Category**: A05 - Security Misconfiguration / DoS

`limit` パラメータにバリデーションがなく、`?limit=999999` で大量データを取得可能。

```python
# 修正
from fastapi import Query
async def conversations_list(limit: int = Query(default=20, ge=1, le=100)) -> JSONResponse:
```

---

### SEC-09: `SEARCH_API_KEY` 環境変数で API キー認証 — Medium

**File**: `src/agents/regulation_check.py` L55
**Category**: A07 - Identification and Authentication Failures

Azure AI Search への認証に API キー（`SEARCH_API_KEY` 環境変数）を使用。DefaultAzureCredential へのフォールバックも実装されている（L156-L158）が、API キーが環境変数に設定されていればそちらが優先される。

**修正案**: API キーの使用を廃止し、DefaultAzureCredential のみを使用。Search Index Data Reader RBAC ロールを MI に割り当てる。

---

### SEC-10: Dockerfile `npm ci --force` のピア依存スキップ — Medium

**File**: `Dockerfile` L4
**Category**: A06 - Vulnerable and Outdated Components

`--force` フラグはピア依存関係のチェックをスキップする。互換性のない依存関係が検出されずにインストールされる可能性がある。

```dockerfile
# 修正（peer dependency の問題が解決済みなら）
RUN npm ci
```

---

### SEC-11: `/api/replay/{conversation_id}` にレート制限なし — Medium

**File**: `src/api/conversations.py` L32
**Category**: A05 - Security Misconfiguration / DoS

リプレイエンドポイントに `@limiter.limit()` デコレータがない。SSE ストリーミングを含むため、接続数を圧迫するリスクがある。

---

### SEC-12: Key Vault `softDeleteRetentionInDays: 7` — Medium（前回 S-12 部分対応）

**File**: `infra/modules/key-vault.bicep` L17
**Category**: A05 - Security Misconfiguration

`enablePurgeProtection: true` は追加済みだが、`softDeleteRetentionInDays: 7` は最短値。本番環境では 90 日を推奨。

---

## Priority 4 — Low / Informational ℹ️

### SEC-13: エージェント Instructions にガードレールなし — Low（前回 S-16 継続）

**Files**: 全エージェントの `INSTRUCTIONS`
**Category**: OWASP LLM01 / LLM06

各エージェントにシステム指示漏洩防止やデータ流出防止のガードレールがない。間接プロンプトインジェクション対策として `INSTRUCTIONS` にルールを追加すべき。

---

### SEC-14: `thread_id` パスパラメータ未バリデーション — Low（前回 S-17 継続）

**File**: `src/api/chat.py` L1749
**Category**: A03 - Injection

`/api/chat/{thread_id}/approve` の `thread_id` に UUID バリデーションがない。

---

### SEC-15: Cosmos DB Private Endpoint 条件付き — Low

**File**: `infra/modules/cosmos-db.bicep`
**Category**: A05 - Security Misconfiguration

Cosmos DB の Private Endpoint は `privateEndpointsSubnetId` が空でない場合のみ作成。VNet 統合がコメントアウトされている現状では、Cosmos DB はパブリック接続。ただし `disableLocalAuth: true` で AAD 認証のみのため、キーベースのアクセスは不可。

---

### SEC-16: `_trigger_logic_app` SSRF リスク（低） — Low

**File**: `src/api/chat.py` L1565
**Category**: A10 - SSRF

`LOGIC_APP_CALLBACK_URL` 環境変数の URL に HTTP POST する。環境変数が改ざんされた場合 SSRF のリスクがあるが、環境変数の改ざんにはインフラレベルのアクセスが必要。Container App の secrets 機能（`secretRef`）で保護されている。

---

### SEC-17: `/api/voice-token` が AAD トークンをレスポンスに含む — Low

**File**: `src/api/voice.py` L16-L39
**Category**: A07 - Identification and Authentication Failures

AAD トークン（`https://ai.azure.com/.default` スコープ）をフロントエンドに返すエンドポイント。トークン自体は一時的（約1時間）だが、SEC-02 の認証なし問題と組み合わさると第三者がトークンを取得できる。SEC-02 が解決されれば低リスク。

---

## Good Practices Observed ✅

| # | 項目 | 詳細 |
|---|------|------|
| ✅ | DefaultAzureCredential 一貫使用 | AI Services / Cosmos DB / Key Vault / Fabric 全て AAD 認証 |
| ✅ | Pydantic リクエストバリデーション | `ChatRequest.message`: min=1, max=5000 + `@field_validator` でサニタイズ |
| ✅ | Content Safety 4 層防御 | Prompt Shield → Content Filter → Tool Response Shield → Text Analysis |
| ✅ | 本番 fail-close パターン | `is_production_environment()` で本番は Content Safety 障害時にブロック |
| ✅ | SSE ペイロード `json.dumps()` | XSS / インジェクション耐性あり |
| ✅ | Dockerfile 非 root ユーザー | `appuser` / `agentuser` で実行 |
| ✅ | HEALTHCHECK 設定 | Dockerfile + Container App Liveness/Readiness プローブ |
| ✅ | AI Services `disableLocalAuth: true` | API キー認証無効化 |
| ✅ | Cosmos DB `disableLocalAuth: true` | AAD 認証のみ |
| ✅ | Key Vault RBAC + Private Endpoint + Purge Protection | 三重防御 |
| ✅ | GitHub Actions OIDC | `id-token: write` + `azure/login@v2` でシークレットレス |
| ✅ | System Managed Identity | Container App / APIM / AI Services 全て MI |
| ✅ | PDF アップロードバリデーション | 拡張子 + サイズ + マジックバイト検証 |
| ✅ | パストラバーサル防止 | `analyze_existing_brochure()` で `data/` のみ許可 |
| ✅ | iframe sandbox | `BrochurePreview.tsx` で `sandbox=""` (最も制限的) |
| ✅ | HTML エクスポートサニタイズ | script/iframe/form/on*属性を除去 |
| ✅ | レート制限 | `/api/chat` (10/min), `/api/upload-pdf` (5/min) |
| ✅ | Deploy ロールバック | ヘルスチェック失敗時に前リビジョンへ自動ロールバック |
| ✅ | Concurrency guard | `concurrency: production-deploy` で同時デプロイ防止 |

---

## Summary: Priority Matrix

| Priority | ID | Finding | Effort | Status |
|----------|------|---------|--------|--------|
| ⛔ Critical | SEC-01 | `except` Python 3 構文エラー | 小 | NEW |
| ⚠️ High | SEC-02 | API エンドポイントに認証なし | 中 | Open (S-04) |
| ⚠️ High | SEC-03 | `_pending_approvals` メモリ枯渇 | 小 | NEW |
| ⚠️ High | SEC-04 | Security スキャン `continue-on-error` | 小 | NEW |
| 📋 Medium | SEC-05 | RBAC 過剰権限 | 小 | Open (S-09) |
| 📋 Medium | SEC-06 | VNet 統合コメントアウト | 大 | Open (S-14) |
| 📋 Medium | SEC-07 | CORS `allow_methods/headers=["*"]` | 小 | Partial (S-15) |
| 📋 Medium | SEC-08 | `limit` パラメータ上限なし | 小 | NEW |
| 📋 Medium | SEC-09 | `SEARCH_API_KEY` API キー認証 | 中 | NEW |
| 📋 Medium | SEC-10 | `npm ci --force` | 小 | NEW |
| 📋 Medium | SEC-11 | `/api/replay` レート制限なし | 小 | NEW |
| 📋 Medium | SEC-12 | Key Vault retention 7 days | 小 | Partial (S-12) |
| ℹ️ Low | SEC-13 | Agent Instructions ガードレール | 小 | Open (S-16) |
| ℹ️ Low | SEC-14 | `thread_id` 未バリデーション | 小 | Open (S-17) |
| ℹ️ Low | SEC-15 | Cosmos DB Private Endpoint 条件付き | 中 | NEW |
| ℹ️ Low | SEC-16 | Logic App SSRF（低リスク） | 小 | NEW |
| ℹ️ Low | SEC-17 | Voice token エンドポイント | 小 | NEW |

---

**推奨アクション**:

1. **即時修正 (SEC-01)**: `except` 構文エラーを修正 — アプリケーション起動不能のリスク
2. **短期 (SEC-02〜04)**: API 認証の実装、メモリ制限追加、CI セキュリティゲート強化
3. **中期 (SEC-05〜12)**: RBAC 最小権限化、VNet 統合有効化、CORS 制限
4. **ハッカソンでの許容範囲**: SEC-13〜17 は Low/Info でハッカソン環境では許容可能
