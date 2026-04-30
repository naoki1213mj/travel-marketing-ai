# Phase 9.4 / 9.5 / 9.6 v2 アーティファクト

このディレクトリは **Phase 9.3 (10テーブル v2 Lakehouse) 構築後に Phase 9.4-9.5 で再構築した
SemanticModel / Ontology / DataAgent**、および **Phase 9.6 で行った aiInstructions エンリッチ +
Ontology timeseriesProperties パッチ** の生成・デプロイ・検証スクリプト一式と、各フェーズのスモークテスト結果を
保存している。

## ファイル

### Phase 9.4–9.5 (基盤構築)

| ファイル | 役割 |
|---|---|
| `build_sm_v2.py` | `travel_SM_v2` (Direct Lake、12 measures、4 hierarchies、9 relationships) を TMDL から生成・POST |
| `verify_sm_v2.py` | Power BI executeQueries API で 14/15 DAX measure を検証 |
| `build_ontology_v2.py` | `travelIQ_v2` (10 EntityType / 9 RelationshipType) JSON ペイロードを生成・POST |
| `build_data_agent_v2.py` | `Travel_Ontology_DA_v2` (aiInstructions 約 10KB を含む config) を生成・POST |
| `smoke_test_v2.py` | 14 本の日本語プロンプトをエージェントに投げ、A/B/C 採点 |
| `retry_failed.py` | timeout 系プロンプトを 360 秒に延長して再試行 |
| `smoke_results.json` | 14 本スモーク結果（生 JSON） |
| `retry_results.json` | 再試行結果 |
| `v2_ids.txt` | 確定した v2 item id |

### Phase 9.6 (aiInstructions エンリッチ + Ontology timeseries パッチ)

| ファイル | 役割 |
|---|---|
| `update_data_agent_v2.py` | `Travel_Ontology_DA_v2` の aiInstructions を v6 に更新（DA ID 不変、updateDefinition LRO） |
| `data_agent_v6/` | v6 aiInstructions の生成済みコンフィグ（draft + published mirror） |
| `verify_da_v6.py` | DA の published definition から aiInstructions が v6 になっているかを確認 |
| `dump_ontology.py` | フル ontology の getDefinition LRO ダンプ → `ontology_full.json` |
| `survey_ts.py` | 全 10 エンティティの DateTime/数値プロパティと現在の `timeseriesProperties` 件数を表示 |
| `map_entities.py` | エンティティ ID ↔ 名前 + プロパティ ID マップ |
| `patch_ontology.py` | booking/payment/cancellation の数値メトリックを `properties` → `timeseriesProperties` に移し、`dataBinding` を `TimeSeries` に変更したパッチを生成 → `ontology_patched.json` |
| `deploy_ontology_patch.py` | パッチを `POST /ontologies/{id}/updateDefinition` で投入し LRO を待つ |
| `poll_lro.py` | SSL/接続エラー時の LRO ポーリング再実行（max_retries=5 の `requests.Session`） |
| `smoke_test_v6.py` | 14 プロンプト・TIMEOUT_S=300・トークン更新つきの v6 スモーク実行 |
| `smoke_extended.py` | P13/P14 専用、TIMEOUT_S=600 のスローパス再試行 |
| `smoke_results_v6*.json` | 6 回分のリトライ実行結果（best-of の元データ） |
| `bestof_strict.py` | LARGE_YEN 閾値 + LLM-fallback フレーズリスト + 補正版 NODATA 正規表現を持つ厳格採点・best-of 集計器 |
| `bestof_v6.py` | 旧採点器（参考、naive grader） |
| `final_summary.py` | strict best-of の最終結果を 1 prompt 1 行で表示 |
| `diag_p08.py` | 単一プロンプトを `run.steps` 付きで投げる診断ヘルパー |
| `inspect_steps.py` | 結果 JSON から `run.steps` を取り出してフォーマット表示 |
| `check_p08.py` | データ存在の SQL 直接検証（パリ春 confirmed/completed = 192件 / ¥192.6M） |
| `query_distinct_values.py` | `destination_region` `season` `customer_segment` 等の DISTINCT 値を取得し `distinct_values.json` に保存 |
| `probe_ontology.py` / `probe_da_definition.py` | 公開 API でオントロジ／DA の定義を確認 |
| `booking_entity.json` / `booking_databinding.json` | パッチ前のリファレンス用エクスポート |
| `ontology_full.json` / `ontology_patched.json` | パッチ前後のフル ontology ペイロード |

## v2 Item IDs

```
Workspace:   096ff72a-6174-4aba-8f0c-140454fa6c3f  (ws-3iq-demo)
Lakehouse:   5e02348e-d2a4-47fb-b63d-257ed3be7731  (lh_travel_marketing_v2)
Semantic:    ce2bb828-d850-46aa-bc5e-224ea9a60667  (travel_SM_v2)
Ontology:    10cd6675-405a-4366-b91b-d57242a28914  (travelIQ_v2)
DataAgent:   b85b67a4-bac4-4852-95e1-443c02032844  (Travel_Ontology_DA_v2)
```

## v1 ロールバック先

v1 (`travel_SM` / `travelIQ` / `Travel_Ontology_DA`) は完全に手付かず。問題があれば
`.env` の `FABRIC_DATA_AGENT_URL` を v1 id に戻すだけで切替可能。

## Direct Lake / Ontology のハマりどころ（学び）

1. **Direct Lake framing**: TMDL POST 直後の DAX は 400 エラー。`POST /datasets/{id}/refreshes`
   (`{"type":"automatic","commitMode":"transactional"}`) を 1 回叩く必要あり。
2. **Fabric Ontology contextualization**:
   - `entityIdParts` には PK propertyId のみを入れる（FK は入れない）
   - `dataBindingTable` = source (many) 側の物理テーブル
   - `sourceKeyRefBindings`: source PK 列 → source PK propId（length = source `entityIdParts.length`）
   - `targetKeyRefBindings`: source 側にある FK 列 → target PK propId（length = target `entityIdParts.length`）
3. **Data Agent endpoint**: `https://api.fabric.microsoft.com/v1/workspaces/{ws}/dataagents/{da}/aiassistant/openai`
   （`dataagents` は小文字）。Token audience: `https://analysis.windows.net/powerbi/api/.default`。

詳細は `docs/fabric-data-overhaul/phase95_smoke_results.md` 参照。

## 再生成手順

各スクリプトの先頭に `WORKSPACE_ID` `LAKEHOUSE_ID` 等が定数定義されているので、別環境への移植時は値を書き換えて実行する。

```powershell
# 1. SemanticModel
python build_sm_v2.py

# 2. Ontology
python build_ontology_v2.py

# 3. Data Agent
python build_data_agent_v2.py

# 4. Smoke test
python smoke_test_v2.py
```

各スクリプトは Azure CLI 経由で `DefaultAzureCredential` 相当のトークンを取得する
(`az account get-access-token --resource ...`) ので、実行前に `az login` 済みであること。
