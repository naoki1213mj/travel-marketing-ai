"""Hosted Agent エントリポイント。

Foundry Agent Service に Hosted Agent としてデプロイする際のエントリポイント。
SequentialBuilder で 4 エージェントの Workflow を構築し、
Foundry のランタイムに登録する。
"""

import asyncio
import logging

from src.workflows import create_pipeline_workflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    """Hosted Agent を起動する。"""
    logger.info("Hosted Agent を起動中...")
    workflow = create_pipeline_workflow()
    logger.info("パイプライン Workflow 構築完了 (%s)。Foundry Agent Service に登録します。", type(workflow).__name__)

    # Foundry Agent Service のランタイムがこのプロセスを管理する
    # ワークフローは Conversations API 経由でトリガーされる
    # ここではワークフローが利用可能であることを確認するのみ
    logger.info("Hosted Agent 起動完了。リクエストを待機中...")

    # プロセスを維持（Foundry ランタイムが管理）
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Hosted Agent を停止中...")


if __name__ == "__main__":
    asyncio.run(main())
