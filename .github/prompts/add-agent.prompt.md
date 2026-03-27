---
description: '新しいエージェントを追加する。Agent Framework rc5 パターンに従い、ツール定義・テスト・SSE イベント対応を含む。'
mode: agent
tools: ['filesystem', 'terminal']
---

# 新しいエージェントの追加

以下の手順で {{agent_name}} エージェントを実装してください。

## 手順

1. `src/agents/{{agent_name}}.py` を作成する
   - `AzureOpenAIResponsesClient` でクライアントを作成
   - `@tool` デコレータでツールを定義
   - `client.as_agent()` でエージェントを作成
   - `docs/requirements_v3.md` の該当セクションを参照して要件を満たすこと

2. `src/tools/{{agent_name}}_tools.py` を作成する（ツールが多い場合）

3. `tests/test_{{agent_name}}.py` を作成する
   - ツール関数のユニットテスト
   - モックを使ったエージェント実行テスト

4. `src/api/chat.py` の SSE ストリーミングに新エージェントの進捗イベントを追加する

5. `src/workflows/pipeline.py` の Sequential Workflow に参加者を追加する

## 確認事項

- Agent Framework rc5 の API パターンに従っているか（`.github/skills/agent-framework-patterns/SKILL.md` 参照）
- Structured Output（JSON Schema）を使っているか
- Content Safety middleware が適用されているか
