# Travel Marketing AI — Demo Script

旅行マーケ担当者向け AI パイプラインのデモを Microsoft IQ 三位一体（**Work IQ + Foundry IQ + Fabric IQ**）の価値で示すための台本。

> ターゲット: ハッカソン審査員 / 社内 PoC レビューア
> 所要時間: 8〜12 分
> 前提環境: Azure Container Apps `ca-wmbvhdhcsuyb2` (East US 2) / Fabric workspace `ws-3iq-demo` / Fabric capacity `fcdemoeastus2001`

---

## 1. デモ前チェックリスト

### 1.1 Fabric capacity / Data Agent の準備

| 項目 | 確認方法 | 期待値 |
|---|---|---|
| Fabric capacity Active | Fabric portal → Admin portal → Capacity settings | `fcdemoeastus2001` が **Active** |
| Travel_Ontology_DA_v2 公開済み | Fabric portal → ws-3iq-demo → Travel_Ontology_DA_v2 | Published / 最新 aiInstructions v6 |
| lh_travel_marketing_v2 上書きなし | Fabric portal → ws-3iq-demo → SQL endpoint で `SELECT COUNT(*) FROM booking` | 約 50,000 行 |
| Container App env | `az containerapp show … query 'properties.template.containers[0].env'` | `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2` |

### 1.2 Capacity warmup（重要）

cold start で NL2Ontology が `submit_tool_outputs BadRequest` を返すことがあるため、デモ開始 5 分前に warmup 実行を推奨。

```bash
# 5〜10 分前に実行
uv run python scripts/fabric_data_overhaul/warmup_v2.py
```

このスクリプトは Phase 9.6 で grade A を獲得した代表 4 prompt を 1 巡だけ流す。
warmup スクリプトが無い場合は portal 上で `Travel_Ontology_DA_v2` に「ハワイ夏の売上を教えて」を 1 回投げて回答完了するまで待つ。

### 1.3 Web UI 確認

```bash
# /api/health と /api/ready が両方 OK
curl https://<container-app-fqdn>/api/health
curl https://<container-app-fqdn>/api/ready
```

---

## 2. デモ台本（推奨フロー）

### 2.1 オープニング（30 秒）

> 「旅行会社マーケ担当者の自然言語指示から、企画書・販促ブローシャ・SNS バナー・紹介動画までを一気通貫で自動生成するマルチエージェント AI です。今日はこのパイプラインが Microsoft の **3 つの IQ**（Work IQ / Foundry IQ / Fabric IQ）でどう価値を出すかをお見せします。」

UI 上で 7 段階の進捗バー（データ検索 → 施策生成 → 承認 → 規制チェック → 修正 → 販促物 → 動画）を見せる。

### 2.2 シーン A: Work IQ（Microsoft 365 から workplace 文脈を取り込む）

**Settings パネル**で `Work IQ: foundry_tool` が ON、user は MSAL でサインイン済みであることを示す。

**入力**:
> 「先週の Teams ミーティングで議論した『学生向け夏休みハワイ旅行』の企画を作って。マーケ部の優先度メモも踏まえて。」

**ポイント**:
- バックエンドが Foundry Prompt Agent + Work IQ MCP connection を `tool_choice` で呼び、Teams meeting / SharePoint notes を per-user delegated でフェッチ。
- ツール呼び出しカードに **Work IQ** バッジが表示され、参照したソース種別（meeting_notes / documents_notes）が明示される。

### 2.3 シーン B: Fabric IQ（Travel_Ontology_DA_v2 でリアル分析）

データ検索ステップで以下の Fabric Data Agent v2 デモプロンプトを使う（**12/14 grade A** 達成済）。

#### 推奨プロンプト（grade A 確定）

| # | 質問 | 出力期待値 |
|---|---|---|
| **D1** | 「2024年に最も売上が伸びた destination_region をランキングで教えてください」 | 上位 5 region と revenue (¥) のテーブル |
| **D2** | 「学生向けの春の沖縄予約件数は？」 | booking_count, 平均単価, 内訳 |
| **D3** | 「ハワイの夏のリピート顧客比率を教えてください」 | リピート率（%）+ 母数 |
| **D4** | 「キャンセル率が最も高い product_type と理由仮説を教えてください」 | cancellation rate ランキング + 仮説 |
| **D5** | 「直近 3 年の月別売上推移を教えてください（時系列）」 | 月別売上の時系列リスト + 季節傾向 |

> 💡 **避けるべき質問**: 「円安後の海外売上回復」「インバウンド比率の四半期推移」など、multi-table × time-series × ratio の組合せは Fabric Data Agent サービスの構造的制限で `submit_tool_outputs BadRequest` が出やすい。デモでは扱わない。

**ポイント**:
- ツール呼び出しカードに **Fabric Data Agent v2** バッジ + workspace ID + dataagent ID が表示される。
- 回答に実際の `total_revenue_jpy` / `pax_count` / `rating` 値が出てくることを強調。
- v1 (旧 schema 800 行 / 2025 年のみ / 2 テーブル) との比較で「**5 年分 / 50,000 件 / 10 テーブル / 業界統計準拠**」だと説明。

### 2.4 シーン C: Foundry IQ（規制チェック）

承認ステップで企画書を承認した後、規制チェックエージェントが走る。

**ポイント**:
- Foundry IQ Knowledge Base から旅行業法・景品表示法・ブランドガイドライン・NG 表現を検索し、根拠カードと一緒に違反箇所を指摘。
- Web Search で外務省渡航情報まで自動でクロスチェック。
- 修正版企画書はチェック結果を反映済み。

### 2.5 シーン D: 販促物 + 動画生成

- HTML ブローシャは Tailwind CSS / レスポンシブ / 旅行業登録番号フッター付き。
- ヒーロー画像 + SNS バナーは GPT Image 2 / GPT Image 1.5 / MAI-Image-2 から UI で選択可能。
- 動画は Photo Avatar (Lisa / casual-sitting / ja-JP-Nanami:DragonHDLatestNeural) で SSML ナレーション付き MP4。

### 2.6 クロージング（30 秒）

> 「自然言語 1 行から、Work IQ で社内文脈を取り込み、Fabric IQ で実データ分析、Foundry IQ で規制チェック、画像 / 動画まで全自動。これが Microsoft IQ 三位一体の価値です。」

---

## 3. トラブルシューティング

### 3.1 Fabric Data Agent から "Failed to generate NL2Ontology query" が出た場合

1. ブラウザを再読み込みし、もう一度送信（cold start のことが多い）
2. それでも失敗する場合: そのプロンプトをスキップし、別の grade A プロンプト（D1〜D5）に変更
3. 全部失敗する場合: capacity が pause になっている可能性 → Fabric portal で Active を確認

### 3.2 Work IQ が認証エラーで止まった場合

- `Settings → Work IQ → Sign in` をクリックして MSAL ポップアップで再ログイン
- それでも失敗する場合: `WORKIQ_RUNTIME=graph_prefetch` に切替（短い workplace brief だけ取れる）

### 3.3 画像生成が黒画像になった場合

- `IMAGE_PROJECT_ENDPOINT_MAI` 設定済みなら MAI-Image-2 に切替
- 黒画像は 1×1 PNG fallback の signal なので、再生成かモデル切替で対処

---

## 4. v1 / v2 切替（運用メモ）

```bash
# 現在の version を確認
az containerapp show \
  --name ca-wmbvhdhcsuyb2 --resource-group <rg> \
  --query "properties.template.containers[0].env[?name=='FABRIC_DATA_AGENT_RUNTIME_VERSION'].value" -o tsv

# v1 → v2 に切替（GitHub Actions vars を更新後 deploy.yml を再実行するのが正規ルート）
# 緊急時の手動切替（dev のみ推奨）:
az containerapp update \
  --name ca-wmbvhdhcsuyb2 --resource-group <rg> \
  --set-env-vars FABRIC_DATA_AGENT_RUNTIME_VERSION=v2 \
                 FABRIC_DATA_AGENT_URL_V2=https://api.fabric.microsoft.com/v1/workspaces/096ff72a-6174-4aba-8f0c-140454fa6c3f/dataagents/b85b67a4-bac4-4852-95e1-443c02032844/aiassistant/openai

# 即時 rollback
az containerapp update \
  --name ca-wmbvhdhcsuyb2 --resource-group <rg> \
  --set-env-vars FABRIC_DATA_AGENT_RUNTIME_VERSION=v1
```

> v1 (Travel_Ontology_DA + travel_sales/travel_review) は rollback 用に残してある。v1 の URL は touch しない設計。

---

## 5. 関連リンク

- 要件定義書: [requirements_v4.0.md](requirements_v4.0.md)
- Phase 9.5 baseline smoke: `scripts/fabric_data_overhaul/v2_artifacts/smoke_results.json`
- Phase 9.6 final smoke (12/14 grade A): `scripts/fabric_data_overhaul/v2_artifacts/smoke_results_v6_retry2.json`
- Phase 9.6 完了レポート: [docs/fabric-data-overhaul/phase96_smoke_results.md](fabric-data-overhaul/phase96_smoke_results.md)
- v2 resource IDs: `scripts/fabric_data_overhaul/v2_artifacts/v2_ids.txt`
