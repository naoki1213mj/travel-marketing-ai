---
name: 'React TypeScript ルール'
description: 'フロントエンド React/TypeScript コードの規約'
applyTo: 'frontend/**/*.tsx, frontend/**/*.ts'
---

## React / TypeScript 規約

### コンポーネント構成

- 関数コンポーネント + hooks のみ。クラスコンポーネントは使わない
- コンポーネントは `frontend/src/components/` 配下。1 ファイル 1 コンポーネント
- カスタム hooks は `frontend/src/hooks/` 配下（useSSE, useTheme, useI18n 等）
- ユーティリティは `frontend/src/lib/` 配下（i18n.ts, sse-client.ts 等）

### スタイリング

- Tailwind CSS のユーティリティクラスを使う。CSS ファイルは最小限
- ダークモード: `dark:` プレフィックスで対応。システム設定に連動
- カラーは CSS 変数で管理。ハードコードしない

### 多言語対応 (i18n)

- 翻訳データは `frontend/src/lib/i18n.ts` で管理
- 対応言語: 日本語 (ja), 英語 (en), 中国語 (zh)
- UI ラベル・ボタン・エラーメッセージは全て翻訳キー経由
- `useI18n()` hook で言語切替

### SSE クライアント

- `frontend/src/lib/sse-client.ts` で SSE 接続を管理
- イベント種別ごとにハンドラを分離する
- 接続断時はリトライロジックを入れる

### 環境変数

- `VITE_API_BASE_URL` でバックエンド URL を指定
- Vite の proxy 設定で `/api` をバックエンドに転送（開発時）
- `.env` は .gitignore 済み。`.env.example` にプレースホルダー

### ビルド

- `npm run build` で本番ビルド
- `npx tsc --noEmit` で型チェック
- テスト: `npm run test`（vitest）
