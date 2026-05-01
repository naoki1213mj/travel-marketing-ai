# 承認 token のセキュリティモデル

> 対象範囲: `/api/chat/{thread_id}/approve` (= ユーザー承認), `/api/chat/{thread_id}/manager-approval-callback` (= 上司承認 callback)
> 関連 commit: `5b949ab` → `9735577` → `7a554d9`

## 1. なぜ token が要るか

`chat()` が `marketing-plan-agent` (Agent2) を完了すると `approval_request` SSE event を発行し、フロントエンドはユーザーの「承認」/ 修正指示を受け取って `/api/chat/{id}/approve` を POST する。承認 POST は `regulation-check-agent` → `plan-revision-agent` → `brochure-gen-agent` → `video-gen-agent` を起動するため、誰でもなりすませると **他人の plan を勝手に承認 → 勝手に画像/動画を生成 → 課金させる** ことができる。

匿名ユーザーは `request_identity.py` で `anon-{sha256(forwarded_for | client_host | user_agent | accept_language)}` という fingerprint owner_id しか持たない。Container Apps Envoy 経由で `X-Forwarded-For` の並び順や Connection 再利用の影響を受けるため、同じブラウザでも fingerprint がリクエスト間で揺らぐ。`conversation_id` (UUID4) は server-issued で 122 bit エントロピがあるが、ログ・スクショ・Application Insights・referer・社内 chat への漏洩経路が多く、**bearer secret として使うには弱い**。

そこで、**per-conversation の `approval_token` を承認 PoP (proof-of-possession) として併用する** 設計にした。

## 2. ライフサイクル

```
chat()
 ├─ Agent1 → Agent2 完了
 ├─ secrets.token_urlsafe(32) で approval_token を発行
 ├─ _store_pending_approval_context() で in-memory 保存
 │   key = "{owner_id}:{conversation_id}", value.approval_token = <new>
 ├─ approval_request SSE event に approval_token を含めて配布
 │   { conversation_id, plan_markdown, approval_token, approval_scope, ... }
 └─ save_conversation() で Cosmos へ persist
     metadata.pending_approval_token = <token>   (status=awaiting_approval のみ)

frontend
 ├─ approval_request handler で state.approvalRequest.approval_token を保存
 └─ user の「承認」/ 修正テキスト送信時に sendApproval() の 6 番目引数で渡す
     POST /api/chat/{id}/approve  body { conversation_id, response, approval_token }

approve()
 ├─ extract_request_identity() で caller_identity["user_id"] 取得
 ├─ _post_approval_events(... owner_id=caller, approval_token=body.approval_token)
 ├─ _load_pending_approval_context() で照合
 │   ├─ 匿名 (anon-*) かつ token 不在 → 即拒否 (return None)
 │   ├─ in-memory _matches_approval_credentials() で確認
 │   └─ Cosmos fallback で metadata.pending_approval_token と hmac.compare_digest()
 ├─ 一致 → Agent3a → Agent3b → Agent4 → Agent5 起動
 └─ 不一致 / 不在 → APPROVAL_CONTEXT_NOT_FOUND error event 返却

_refine_events() (修正指示パス)
 ├─ 同じ approval_token で context 復元
 ├─ marketing-plan-agent で revision 生成
 ├─ secrets.token_urlsafe(32) で **新しい approval_token** を発行 (rotation)
 └─ 新 token を含む approval_request event を再配布

agent5 完了
 └─ _pop_pending_approval_context() で in-memory から破棄
     conversation status が completed/error に変わると Cosmos からも token を消す
```

## 3. 認可マトリクス (`_matches_approval_credentials`)

| 条件 | 結果 |
|---|---|
| 両 token 提示 + `hmac.compare_digest` 一致 | ✅ allow |
| 両 token 提示 + 不一致 | ❌ deny (owner_id 一致でも拒否) |
| stored token あり / lookup token なし、owner_id 一致 | ✅ allow (内部 save_conversation 同期パス) |
| stored token あり / lookup token なし、owner_id 不一致 | ❌ deny |
| どちらか empty owner | ✅ allow (legacy / 旧 in-memory entry) |
| owner_id 厳密一致 (token 比較不要) | ✅ allow |
| 両 anon-* + 両 token 不在 | ✅ allow (legacy 互換) |
| その他 | ❌ deny |

外部 `/approve` 経由 (`_load_pending_approval_context`) では、**匿名 lookup は token を必ず要求** する追加ガードがある:

```python
if is_anonymous_lookup and not normalized_token:
    return None  # 早期拒否
```

これにより匿名同士の cross-fingerprint 攻撃 + ID 推測攻撃の両方を封じている。

## 4. 防御している攻撃

| 攻撃 | 防御 |
|---|---|
| 匿名ユーザーが他人の `conversation_id` を SNS / ログから拾って承認 | token 必須 → `APPROVAL_CONTEXT_NOT_FOUND` |
| 同一 fingerprint (NAT 配下の同僚など) からの偶然 collision | token 必須 + token rotation |
| Cosmos cross-partition lookup を悪用した ID 衝突攻撃 (異なる owner で同じ id を作る) | cross-partition 検索は token 一致時のみ実行、Cosmos doc 側 metadata.pending_approval_token と再検証 |
| Timing side-channel で token を推測 | `hmac.compare_digest()` で定数時間比較 |
| token 漏洩した古い revision を後の修正版に流用 | `_refine_events()` で revision 毎に rotation |
| 永続化された token を奪取 | status が `completed` / `error` に遷移した時点で Cosmos metadata から削除、in-memory は `_pop_pending_approval_context()` で消える |

## 5. 防御しないもの (= 上位レイヤーで保証する前提)

| 想定 | 補足 |
|---|---|
| TLS 劣化 / MITM | Container Apps の HTTPS 必須 (`allowInsecure: false`) で吸収 |
| Authenticated user (`user-*`) 同士の承認権限分離 | Entra Bearer 認証の owner_id 一致で代替 (token なし運用も許容) |
| Manager 承認 callback の token | 別系統の `manager_callback_token` (HMAC + Logic Apps trigger key) で保護。詳細は [`manager-approval-workflow.md`](manager-approval-workflow.md) |
| Voice Live / Foundry MCP の delegated auth | MSAL + Entra app `travel-voice-spa` でユーザー承認済 token を取得 (別文脈) |

## 6. 関連 token の対応表

| token | 用途 | 配布経路 | 保存場所 | 検証 |
|---|---|---|---|---|
| `approval_token` | user 承認の PoP | `approval_request` SSE event | in-memory `_pending_approvals` + Cosmos `metadata.pending_approval_token` | `hmac.compare_digest`、`_load_pending_approval_context` |
| `manager_callback_token` | Logic Apps → backend callback の HMAC | `MANAGER_APPROVAL_TRIGGER_URL` 経由 Logic Apps へ署名付き payload | Cosmos `metadata.manager_approval_callback_token` (`awaiting_manager_approval` 中のみ) | `_is_manager_approval_token_valid` (constant-time) |
| `manager_approval_token` (URL fragment) | 上司承認ページの一時 token | `manager_approval_url` (URL fragment, never query string) | Logic Apps Workflow Run 状態 + frontend hash params | `_extract_manager_approval_token` (body / `X-Manager-Approval-Token` header のみ受理。クエリ拒否) |

## 7. 監視 / 監査

- App Insights `traces` で `path=/api/chat/.*/approve` + `code=APPROVAL_CONTEXT_NOT_FOUND` を観測すれば bearer 不一致 / 漏洩攻撃の検知に使える
- `_pending_approvals` dict サイズが増え続ける場合は `_pop_pending_approval_context` 経路の問題 (status 遷移漏れ) の signal
- Cosmos `metadata.pending_approval_token` が `completed` 状態の doc に残っていたら save_conversation 経路の bug

## 8. テスト

- `tests/test_pending_approval_lookup.py` (16 cases) — `_matches_approval_credentials` / `_get_pending_approval_context_from_memory` / `_load_pending_approval_context` の挙動マトリクス
- E2E: `chat → approve WITHOUT token → APPROVAL_CONTEXT_NOT_FOUND ✅` + `chat → approve WITH token → 5 agents 完走 ✅` を本番 CA (`ca-wmbvhdhcsuyb2-pn`, image `7a554d9`) で確認済
