"""conversations モジュールのユニットテスト（インメモリストア）"""

from pathlib import Path

import pytest

from src.conversations import (
    _memory_store,
    get_conversation,
    get_replay_data,
    list_conversations,
    save_conversation,
)


@pytest.fixture(autouse=True)
def _clear_memory_store(monkeypatch):
    """各テスト前にインメモリストアをクリアし、Cosmos DB を無効化する"""
    _memory_store.clear()
    monkeypatch.delenv("COSMOS_DB_ENDPOINT", raising=False)
    yield
    _memory_store.clear()


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
