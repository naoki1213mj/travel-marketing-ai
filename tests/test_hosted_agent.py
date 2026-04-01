"""hosted_agent モジュールのテスト（スタブ確認）"""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_hosted_agent_main_logs_stub_warning():
    """main() がスタブ警告をログ出力し、sleep で待機すること"""
    with (
        patch("src.hosted_agent.logger") as mock_logger,
        patch("src.hosted_agent.asyncio.sleep", side_effect=KeyboardInterrupt),
    ):
        from src.hosted_agent import main

        await main()
        mock_logger.warning.assert_called_once()
