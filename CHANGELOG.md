# Changelog

このプロジェクトの主要な変更を記録します。形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) を採用します。

ハッカソン段階のため `0.x.y` を維持し、デモブロッカー級の変更を Minor、セキュリティ / バグ修正を Patch として扱います。

## [Unreleased]

### Added

- **Work IQ ソース別観測性 + コネクタ実行バッジ** — backend が `tool_event.source_metadata[]` を発行、frontend は `connector_used` を sky-tone バッジで表示。Foundry MCP は per-source attribution を expose しないため、selected sources を一律 'used' と claim する代わりに「コネクタが正常実行された」という honest セマンティクスを採用。`graph_prefetch` rollback path のみ件数とプレビューが付く ([0721f85](https://github.com/naoki1213mj/travel-marketing-ai/commit/0721f85))
- **Microsoft 3IQ パネルの Fabric IQ 説明に `Ontology` を追加** — Fabric Data Agent v2 は semantic ontology (`travelIQ_v2`) を使うため UI 表示を更新 ([79016b0](https://github.com/naoki1213mj/travel-marketing-ai/commit/79016b0))
- **SSE event schema (`docs/sse-event-schema.md`)** — Work IQ 拡張 `WorkIqSourceMetadata` 型 + `connector_used` セマンティクス + UI hide rule + tool subtype table を追加
- **Fabric Data Agent v2 Phase 11d 指示** — aiInstructions 10,458 chars master block + lakehouse userDescription / dataSourceInstructions のリファクタ。Demo 4/4 grade A、smoke 11/14 grade A (P10 timeout / P13/P14 platform バグを除く全グリーン) ([c954e66](https://github.com/naoki1213mj/travel-marketing-ai/commit/c954e66))
- **Phase 12 エージェント instructions tuning** — Gap 1/2/3/5/6/7 全対応。`src/agents/_shared_instructions.py` で SHARED_PREFIX + NO_FOLLOWUP_RULE + SCOPE_RESPECT_RULE を共有化 ([122601d](https://github.com/naoki1213mj/travel-marketing-ai/commit/122601d), [a3f3b38](https://github.com/naoki1213mj/travel-marketing-ai/commit/a3f3b38))

### Changed

- **Work IQ ソース別ステータスパネルを auto-hide** — `foundry_tool` runtime で全ソースが `connector_used` のみ・件数 / プレビュー / サマリ無しの場合、UI ノイズになるため非表示。runtime 表示と Settings の "この会話で有効" バッジで activation は別途可視化される ([79016b0](https://github.com/naoki1213mj/travel-marketing-ai/commit/79016b0))
- **ブローシャ画像出力を JPEG @ 85 に変更** — gpt-image-2 medium PNG が 25–43 MB で Cosmos 永続化 threshold を超過し cold reload で SVG placeholder 化していた問題を解消 (~50–100x size reduction) ([0c0eb40](https://github.com/naoki1213mj/travel-marketing-ai/commit/0c0eb40))

### Fixed

- **Refine without explicit `refineContext` の観測性** — frontend が完了状態 RefineChat から refine を投げるとき `source: 'post_completion'` を明示。backend は明示なしの refine を WARN ログで App Insights に記録 (重複 refine round の regression detector) ([25bcb34](https://github.com/naoki1213mj/travel-marketing-ai/commit/25bcb34))

### Predicted next changes

- Azure AI Search を Managed Identity 化 (A3) — `SEARCH_API_KEY` 廃止
- Cosmos `pending_approval_token` の自動 TTL 削除 (D3)
- `_pending_approvals` を Cosmos / Redis に移行 (D1)

## [0.5.0] — 2026-05-02

### Added

- **Fabric Data Agent §F GQL examples + §G anti-patterns** ([4282458](https://github.com/naoki1213mj/travel-marketing-ai/commit/4282458)) — Live で「夏のハワイ学生旅行」が `¥38,926,615 / 39件 / 131名` の実データを返すようになった (NL2Ontology の `booking_id` leak バグを根治)
- **Microsoft 3IQ ブランドの UI 可視化** ([758fd69](https://github.com/naoki1213mj/travel-marketing-ai/commit/758fd69)) — Workflow 上部に Work IQ / Fabric IQ / Foundry IQ 各タイル、各 evidence カードに色付き IQ chip を追加
- **`/api/ready/deep` deep dependency probe** ([8dc748a](https://github.com/naoki1213mj/travel-marketing-ai/commit/8dc748a)) — Cosmos / Foundry / Search / Fabric Data Agent の実認可済 round-trip を確認 (ACA probe には繋がない設計)
- **承認 token bearer security** ([7a554d9](https://github.com/naoki1213mj/travel-marketing-ai/commit/7a554d9)) — `/api/chat/{id}/approve` に per-conversation 32-byte urlsafe token、`hmac.compare_digest` で定数時間比較、`docs/approval-security.md` 参照

### Fixed

- **Workflow stepper の動画 ✓ 誤表示** ([3b2eec2](https://github.com/naoki1213mj/travel-marketing-ai/commit/3b2eec2)) — 完了後 refine round で過去版動画 URL を参照していた問題を修正
- **ライトモード contrast** ([3b2eec2](https://github.com/naoki1213mj/travel-marketing-ai/commit/3b2eec2)) — IQStatusStrip タイル + Work IQ context tools chip の可読性向上
- **Fabric Data Agent silent failure pattern** ([ea2c8ba](https://github.com/naoki1213mj/travel-marketing-ai/commit/ea2c8ba)) — `取得ができません` (が-particle 型) 失敗を検出する pattern を追加
- **APPROVAL_CONTEXT_NOT_FOUND** ([ea2c8ba](https://github.com/naoki1213mj/travel-marketing-ai/commit/ea2c8ba)) — anon fingerprint shift + 単一パーティション lookup の partition mismatch を canonical owner resolve で修正
- **`_image_settings_fallback` cross-user data leak** ([d3d2867](https://github.com/naoki1213mj/travel-marketing-ai/commit/d3d2867)) — 単一 global mutable dict → conversation-keyed dict + lock
- **Fabric workspace MI grant** (cutover runtime fix) — 新 CA MI を Fabric workspace `ws-3iq-demo` に Member 登録、旧 MI 削除

### Changed

- **Phase 10 Fabric tune** ([a1133aa](https://github.com/naoki1213mj/travel-marketing-ai/commit/a1133aa)) — best-of 12/14 grade A 達成、aiInstructions 圧縮 (19k → 2.5k)、dataSourceInstructions 拡充 (6.7k → 16k → 18k with §F+§G)
- **Blue-green CAE 移行** ([2026-05-01]) — VNet 統合 CAE `cae-wmbvhdhcsuyb2-pn` への切替完了、旧 `cae-wmbvhdhcsuyb2` 削除

## [0.4.0] — 2026-04-30

### Added

- **Fabric Lakehouse v2** (`lh_travel_marketing_v2`) — 10 Delta tables in `dbo` schema、Travel_Ontology_DA_v2 (`b85b67a4-...`) で利用
- **Voice Live API** (Preview) — 音声入力対応
- **Photo Avatar 動画生成** — Lisa / casual-sitting 固定、`ja-JP-Nanami:DragonHDLatestNeural`

### Fixed

- **Fabric Data Agent v1 → v2 ルーティング** — `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2` で env-driven 切替

## 過去の主要マイルストーン

- 2026-04 — Phase 9 Fabric overhaul (workspace `ws-3iq-demo` 移行)
- 2026-03 — gpt-5.4-mini GA、画像生成 GPT Image 2 既定
- 2026-02 — Foundry リソースモデル (Hub+Project ではなく `accounts/projects@2025-06-01`)

## メモ

- 詳細な commit log は `git log --oneline` で参照
- ハッカソン期間中は破壊的変更も含む実験的更新を頻繁に行うため、production 利用は非推奨
