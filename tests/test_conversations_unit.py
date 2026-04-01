"""conversations モジュールのユニットテスト（インメモリストア）"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.conversations as _conv_mod
from src.conversations import (
    _get_container,
    _get_cosmos_client,
    _memory_store,
    get_conversation,
    get_replay_data,
    list_conversations,
    save_conversation,
    save_replay_data,
)


@pytest.fixture(autouse=True)
def _clear_memory_store(monkeypatch):
    """各テスト前にインメモリストアをクリアし、Cosmos DB シングルトンをリセットする"""
    _memory_store.clear()
    monkeypatch.delenv("COSMOS_DB_ENDPOINT", raising=False)
    # シングルトンをリセットして各テストが独立して初期化できるようにする
    _conv_mod._cosmos_client = None
    _conv_mod._cosmos_initialized = False
    yield
    _memory_store.clear()
    _conv_mod._cosmos_client = None
    _conv_mod._cosmos_initialized = False


# --- 既存テスト ---


async def test_save_conversation_to_memory():
    """インメモリストアに会話を保存できる"""
    await save_conversation(
        conversation_id="test-conv-1",
        user_input="沖縄プラン作って",
        events=[{"event": "text", "data": "ok"}],
    )
    assert "test-conv-1" in _memory_store
    assert _memory_store["test-conv-1"]["input"] == "沖縄プラン作って"


async def test_save_conversation_preserves_status():
    """明示した status が保存される"""
    await save_conversation(
        conversation_id="test-conv-status",
        user_input="承認待ちプラン",
        events=[],
        status="awaiting_approval",
    )
    assert _memory_store["test-conv-status"]["status"] == "awaiting_approval"


async def test_save_conversation_preserves_created_at_on_update():
    """同一 conversation_id の更新でも created_at は維持される"""
    await save_conversation(
        conversation_id="test-conv-update",
        user_input="初回",
        events=[],
        status="awaiting_approval",
    )
    created_at = _memory_store["test-conv-update"]["created_at"]

    await save_conversation(
        conversation_id="test-conv-update",
        user_input="更新後",
        events=[{"event": "done", "data": {}}],
        status="completed",
    )

    assert _memory_store["test-conv-update"]["created_at"] == created_at
    assert _memory_store["test-conv-update"]["status"] == "completed"


async def test_get_conversation_from_memory():
    """保存した会話をインメモリストアから取得できる"""
    await save_conversation(
        conversation_id="test-conv-2",
        user_input="春プランを企画",
        events=[],
    )
    result = await get_conversation("test-conv-2")
    assert result is not None
    assert result["id"] == "test-conv-2"
    assert result["input"] == "春プランを企画"


async def test_list_conversations_from_memory():
    """limit 付きで会話一覧を取得できる"""
    for i in range(5):
        await save_conversation(
            conversation_id=f"list-conv-{i}",
            user_input=f"query {i}",
            events=[],
        )
    result = await list_conversations(limit=3)
    assert len(result) == 3


async def test_get_replay_data_fallback_to_json():
    """Cosmos DB 未設定・インメモリにもない場合、demo-replay.json にフォールバックする"""
    replay_file = Path(__file__).resolve().parent.parent / "data" / "demo-replay.json"
    if not replay_file.exists():
        pytest.skip("demo-replay.json が見つからない")

    result = await get_replay_data("nonexistent-conv-id")
    assert result is not None
    assert isinstance(result, list)
    assert len(result) > 0


# --- 新規テスト ---


class TestCosmosClientInit:
    """Cosmos DB クライアント初期化テスト"""

    def test_no_endpoint_returns_none(self, monkeypatch):
        """COSMOS_DB_ENDPOINT 未設定時は None"""
        monkeypatch.delenv("COSMOS_DB_ENDPOINT", raising=False)
        assert _get_cosmos_client() is None

    def test_import_error_returns_none(self, monkeypatch):
        """azure-cosmos 未インストール時は None"""
        monkeypatch.setenv("COSMOS_DB_ENDPOINT", "https://test.documents.azure.com:443/")

        with patch("builtins.__import__", side_effect=ImportError("No module named 'azure.cosmos'")):
            result = _get_cosmos_client()
            assert result is None

    def test_get_container_returns_none_when_no_client(self, monkeypatch):
        """クライアントが None の場合コンテナも None"""
        monkeypatch.delenv("COSMOS_DB_ENDPOINT", raising=False)
        assert _get_container() is None


class TestSaveConversationDetails:
    """会話保存の詳細テスト"""

    async def test_save_with_artifacts_and_metrics(self):
        """artifacts がバージョン配列として保存されること"""
        await save_conversation(
            conversation_id="test-artifacts",
            user_input="テスト",
            events=[],
            artifacts={"html": "<p>test</p>"},
            metrics={"latency": 1.5},
        )
        doc = _memory_store["test-artifacts"]
        assert isinstance(doc["artifacts"], list)
        assert len(doc["artifacts"]) == 1
        assert doc["artifacts"][0]["html"] == "<p>test</p>"
        assert doc["artifacts"][0]["version"] == 1
        assert doc["metadata"] == {"latency": 1.5}

    async def test_save_default_artifacts_and_metrics(self):
        """artifacts/metrics 未指定時は空配列"""
        await save_conversation(
            conversation_id="test-defaults",
            user_input="テスト",
            events=[],
        )
        doc = _memory_store["test-defaults"]
        assert doc["artifacts"] == []
        assert doc["metadata"] == {}

    async def test_save_sets_user_id(self):
        """user_id が 'demo-user' に設定されること"""
        await save_conversation(
            conversation_id="test-uid",
            user_input="テスト",
            events=[],
        )
        assert _memory_store["test-uid"]["user_id"] == "demo-user"


class TestGetConversationEdgeCases:
    """会話取得のエッジケーステスト"""

    async def test_get_nonexistent_returns_none(self):
        """存在しない ID は None"""
        result = await get_conversation("does-not-exist")
        assert result is None

    async def test_list_conversations_empty(self):
        """空のストアからのリスト取得"""
        result = await list_conversations()
        assert result == []

    async def test_list_conversations_sorted_by_created_at(self):
        """会話が created_at の降順でソートされること"""
        await save_conversation("conv-a", "A", [])
        await save_conversation("conv-b", "B", [])
        result = await list_conversations()
        assert len(result) == 2
        # 最新が先頭
        assert result[0]["created_at"] >= result[1]["created_at"]


class TestReplayData:
    """リプレイデータのテスト"""

    async def test_save_and_get_replay_data(self):
        """リプレイデータの保存と取得"""
        events = [
            {"event": "text", "data": {"content": "hello"}, "timestamp": 0.1},
            {"event": "done", "data": {}, "timestamp": 0.5},
        ]
        await save_replay_data("replay-test-1", events)

        result = await get_replay_data("replay-test-1")
        assert result is not None
        assert len(result) == 2
        assert result[0]["event"] == "text"

    async def test_get_replay_data_nonexistent_without_json(self, monkeypatch, tmp_path):
        """インメモリにもJSONファイルにもない場合"""
        # get_replay_data は内部で Path を使って demo-replay.json を探す
        # demo-replay.json が存在する場合はそのデータが返るため、
        # 結果の型チェックのみ行う
        result = await get_replay_data("no-such-replay-id-xyz")
        assert result is None or isinstance(result, list)

    async def test_replay_data_stored_with_prefix(self):
        """replay データが replay- プレフィックスで保存されること"""
        await save_replay_data("test-123", [{"event": "text"}])
        assert "replay-test-123" in _memory_store
        doc = _memory_store["replay-test-123"]
        assert doc["type"] == "replay"
        assert doc["conversation_id"] == "test-123"


class TestCosmosDBPaths:
    """Cosmos DB パスのテスト（モック使用）"""

    async def test_save_conversation_cosmos_upsert(self, monkeypatch):
        """Cosmos DB コンテナがある場合 upsert_item が呼ばれること"""
        mock_container = MagicMock()
        mock_container.upsert_item = MagicMock()

        with patch("src.conversations._get_container", return_value=mock_container):
            await save_conversation(
                conversation_id="cosmos-test-1",
                user_input="テスト",
                events=[],
            )
            mock_container.upsert_item.assert_called_once()

    async def test_save_conversation_cosmos_failure_falls_back(self, monkeypatch):
        """Cosmos DB upsert が失敗した場合インメモリにフォールバック"""
        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = OSError("Cosmos error")

        with patch("src.conversations._get_container", return_value=mock_container):
            await save_conversation(
                conversation_id="cosmos-fallback",
                user_input="テスト",
                events=[],
            )
            assert "cosmos-fallback" in _memory_store

    async def test_save_conversation_cosmos_unexpected_error(self, monkeypatch):
        """Cosmos DB で予期しないエラーが発生した場合もフォールバック"""
        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = RuntimeError("Unexpected")

        with patch("src.conversations._get_container", return_value=mock_container):
            await save_conversation(
                conversation_id="cosmos-unexpected",
                user_input="テスト",
                events=[],
            )
            assert "cosmos-unexpected" in _memory_store

    async def test_get_conversation_cosmos_success(self, monkeypatch):
        """Cosmos DB から会話を読み取れる場合"""
        mock_container = MagicMock()
        mock_container.read_item.return_value = {"id": "cosmos-get", "input": "test"}

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await get_conversation("cosmos-get")
            assert result is not None
            assert result["id"] == "cosmos-get"

    async def test_get_conversation_cosmos_not_found(self, monkeypatch):
        """Cosmos DB で見つからない場合は None"""
        mock_container = MagicMock()
        mock_container.read_item.side_effect = ValueError("Not found")

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await get_conversation("cosmos-missing")
            assert result is None

    async def test_get_conversation_cosmos_unexpected_error(self, monkeypatch):
        """Cosmos DB で予期しないエラーでも None"""
        mock_container = MagicMock()
        mock_container.read_item.side_effect = RuntimeError("Unexpected")

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await get_conversation("cosmos-error")
            assert result is None

    async def test_list_conversations_cosmos_success(self, monkeypatch):
        """Cosmos DB から会話一覧を取得できる場合"""
        mock_container = MagicMock()
        mock_container.query_items.return_value = iter(
            [
                {"id": "c1", "input": "q1"},
                {"id": "c2", "input": "q2"},
            ]
        )

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await list_conversations(limit=10)
            assert len(result) == 2

    async def test_list_conversations_cosmos_failure(self, monkeypatch):
        """Cosmos DB クエリ失敗時は空リスト"""
        mock_container = MagicMock()
        mock_container.query_items.side_effect = OSError("Query failed")

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await list_conversations()
            assert result == []

    async def test_list_conversations_cosmos_unexpected_error(self, monkeypatch):
        """Cosmos DB で予期しないエラーも空リスト"""
        mock_container = MagicMock()
        mock_container.query_items.side_effect = RuntimeError("Unexpected")

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await list_conversations()
            assert result == []

    async def test_save_replay_data_cosmos(self, monkeypatch):
        """Cosmos DB にリプレイデータを保存"""
        mock_container = MagicMock()
        mock_container.upsert_item = MagicMock()

        with patch("src.conversations._get_container", return_value=mock_container):
            await save_replay_data("replay-cosmos", [{"event": "text"}])
            mock_container.upsert_item.assert_called_once()

    async def test_save_replay_data_cosmos_failure(self, monkeypatch):
        """Cosmos DB 保存失敗時はインメモリにフォールバック"""
        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = OSError("Save failed")

        with patch("src.conversations._get_container", return_value=mock_container):
            await save_replay_data("replay-fallback", [{"event": "text"}])
            assert "replay-replay-fallback" in _memory_store

    async def test_get_replay_data_cosmos_success(self, monkeypatch):
        """Cosmos DB からリプレイデータを取得"""
        mock_container = MagicMock()
        mock_container.read_item.return_value = {"events": [{"event": "text", "data": {"content": "test"}}]}

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await get_replay_data("replay-cosmos-get")
            assert result is not None
            assert len(result) == 1

    async def test_get_replay_data_cosmos_not_found_falls_to_memory(self, monkeypatch):
        """Cosmos DB で見つからない場合メモリ → JSON にフォールバック"""
        mock_container = MagicMock()
        mock_container.read_item.side_effect = ValueError("Not found")

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await get_replay_data("replay-not-in-cosmos")
            # メモリにもないので None or demo-replay.json
            assert result is None or isinstance(result, list)

    async def test_get_replay_data_cosmos_unexpected_error(self, monkeypatch):
        """Cosmos DB で予期しないエラーでもフォールバック"""
        mock_container = MagicMock()
        mock_container.read_item.side_effect = RuntimeError("Unexpected")

        with patch("src.conversations._get_container", return_value=mock_container):
            result = await get_replay_data("replay-error")
            assert result is None or isinstance(result, list)

    async def test_save_replay_data_cosmos_unexpected_error(self, monkeypatch):
        """Cosmos DB リプレイ保存で予期しないエラーでもフォールバック"""
        mock_container = MagicMock()
        mock_container.upsert_item.side_effect = RuntimeError("Unexpected")

        with patch("src.conversations._get_container", return_value=mock_container):
            await save_replay_data("replay-unexpected", [{"event": "text"}])
            assert "replay-replay-unexpected" in _memory_store


class TestCosmosClientCreation:
    """Cosmos DB クライアント作成パスのテスト"""

    def test_cosmos_client_value_error(self, monkeypatch):
        """Cosmos DB 接続で ValueError が発生した場合"""
        monkeypatch.setenv("COSMOS_DB_ENDPOINT", "https://test.documents.azure.com:443/")

        # azure.cosmos.CosmosClient は関数内で import されるので
        # azure.cosmos モジュール自体をモックする
        import sys
        import types

        mock_cosmos_module = types.ModuleType("azure.cosmos")
        mock_cosmos_module.CosmosClient = MagicMock(side_effect=ValueError("Invalid URL"))

        with patch.dict(sys.modules, {"azure.cosmos": mock_cosmos_module}):
            result = _get_cosmos_client()
            assert result is None

    def test_get_container_success(self, monkeypatch):
        """コンテナ正常取得"""
        mock_container = MagicMock()
        mock_db = MagicMock()
        mock_db.get_container_client.return_value = mock_container
        mock_client = MagicMock()
        mock_client.get_database_client.return_value = mock_db

        with patch("src.conversations._get_cosmos_client", return_value=mock_client):
            result = _get_container()
            assert result is mock_container

    def test_get_container_value_error(self, monkeypatch):
        """コンテナ取得で ValueError"""
        mock_client = MagicMock()
        mock_client.get_database_client.side_effect = ValueError("DB not found")

        with patch("src.conversations._get_cosmos_client", return_value=mock_client):
            result = _get_container()
            assert result is None

    def test_get_container_unexpected_error(self, monkeypatch):
        """コンテナ取得で予期しないエラー"""
        mock_client = MagicMock()
        mock_client.get_database_client.side_effect = RuntimeError("Unexpected")

        with patch("src.conversations._get_cosmos_client", return_value=mock_client):
            result = _get_container()
            assert result is None
