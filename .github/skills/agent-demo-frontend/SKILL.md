---
name: agent-demo-frontend
description: >-
  旅行マーケティング AI パイプラインのフロントエンド UI 設計・実装ガイド。
  チャット UI、SSE ストリーミング表示、ツール呼び出し可視化、承認フロー、
  Generative UI（グラフ・画像・ブローシャプレビュー）、マルチエージェント表現、
  多言語対応（i18n: 日英中）、ダーク/ライトモードをカバーする。
  Triggers: "フロントエンド", "UI", "チャット", "コンポーネント", "デモ画面",
  "デザイン", "ストリーミング", "承認フロー", "i18n", "ダークモード"
---

# 旅行マーケティング AI — フロントエンド設計ガイド

## ビジュアルテーゼ

明るく落ち着いた、余白の多いプロフェッショナルな操作画面。
旅行会社のマーケ担当者が安心して使えるエンタープライズ品質。

## コンポーネント構成（§6.2 準拠、16 コンポーネント）

```
frontend/src/
├── components/
│   ├── InputForm.tsx           # 自然言語入力・地域/季節/予算の選択
│   ├── PipelineStepper.tsx     # 5 ステップの進捗表示（Agent1〜4 + 承認）
│   ├── ToolEventBadges.tsx     # ツール使用状況のアニメーションバッジ
│   ├── AnalysisView.tsx        # Agent1 の分析グラフ・サマリ表示
│   ├── PlanApproval.tsx        # 企画書プレビュー + 承認/修正ボタン
│   ├── RegulationResults.tsx   # 規制チェック結果のハイライト表示
│   ├── BrochurePreview.tsx     # HTML ブローシャのプレビュー
│   ├── ImageGallery.tsx        # GPT Image 1.5 の生成画像表示
│   ├── ArtifactTabs.tsx        # 企画書/ブローシャ/画像のタブ切替
│   ├── VersionSelector.tsx     # 成果物バージョンの切替
│   ├── RefineChat.tsx          # マルチターン修正対話の入力
│   ├── SafetyBadge.tsx         # Content Safety 結果の動的バッジ
│   ├── MetricsBar.tsx          # 処理メトリクス（レイテンシ・トークン等）
│   ├── LanguageSwitcher.tsx    # 言語切替（日/英/中）
│   ├── ThemeToggle.tsx         # ダーク/ライト切替
│   └── ErrorRetry.tsx          # エラー表示 + リトライボタン
├── hooks/
│   ├── useSSE.ts              # SSE 接続管理
│   ├── useTheme.ts            # テーマ管理（system 連動）
│   └── useI18n.ts             # 多言語管理
└── lib/
    ├── sse-client.ts          # SSE クライアント
    └── i18n.ts                # 翻訳データ（ja/en/zh）
```

## 画面レイアウト

```
┌──────────────────────────────────────────────┐
│ ヘッダー: ロゴ + プロダクト名 + 言語 + テーマ  │
├────────────────────┬─────────────────────────┤
│                    │                          │
│  チャット領域        │   成果物プレビュー        │
│  (MessageList)     │   (Markdown/HTML/画像)    │
│                    │                          │
│  ├ AgentProgress   │   ├ MarkdownPreview      │
│  ├ ToolCallDisplay │   ├ BrochurePreview      │
│  ├ ApprovalCard    │   ├ ImageGallery         │
│  └ AgentMessage    │   └ ExportPanel          │
│                    │                          │
├────────────────────┤   SafetyBadge            │
│ ChatInput          │   MetricsPanel           │
└────────────────────┴─────────────────────────┘
```

## SSE イベントと UI の対応（§3.4 準拠）

| SSE イベント | 表示コンポーネント | 動作 |
|---|---|---|
| `agent_progress` | PipelineStepper | ステップインジケーターを更新 |
| `tool_event` | ToolEventBadges | ツール使用バッジをアニメーション表示 |
| `text` | AnalysisView / RegulationResults / BrochurePreview | チャットに追記・成果物プレビュー反映 |
| `approval_request` | PlanApproval | 承認/修正ボタンを表示 |
| `image` | ImageGallery | 画像をインライン表示（base64） |
| `safety` | SafetyBadge | 4 カテゴリのスコアをバッジ表示 |
| `error` | ErrorRetry | エラー内容 + リトライボタン |
| `done` | MetricsBar | 処理メトリクス表示。修正対話モードに切替 |

## デザインルール

- **カードはデフォルトで使わない。** 背景色の差とスペースで区切る
- **フォント**: Noto Sans JP（日本語対応、Inter/Roboto 禁止）
- **アクセントカラー**: 1 色（旅行テーマならブルー系）
- **ダークモード**: Tailwind の `dark:` クラスで全対応。CSS 変数でカラー管理
- **ストリーミング**: テキストはチャンク単位で段階表示。一括表示禁止
- **ツール呼び出し**: インラインで折りたたみ。別パネルに飛ばさない
- **承認フロー**: フォーカスはデフォルトで「修正」側（誤承認防止）
- **エージェント表示**: アバター（アイコン + 名前 + 役割）で区別。擬人化しない
- **エラー**: 隠さない。何が起きたか説明 + リトライボタン

## 参照

- 詳細なデザインルール・アンチパターン・Trust UX は `agent-demo-frontend` 汎用スキルを参照
- 要件定義 §6: `docs/requirements_v3.md`
