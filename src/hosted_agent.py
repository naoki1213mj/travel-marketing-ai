"""Hosted Agent エントリポイント（将来用スタブ）。

Foundry Agent Service に Hosted Agent としてデプロイする際のエントリポイント。
現行アーキテクチャでは FastAPI (src.main) がオーケストレーションを担っており、
このモジュールは使用されていない。Hosted Agent 化を検討する際の雛形として残す。
"""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    """Hosted Agent を起動する（スタブ）。"""
    logger.info("Hosted Agent を起動中...")
    logger.warning("現行はスタブです。実装は src.main (FastAPI) を参照してください。")

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
