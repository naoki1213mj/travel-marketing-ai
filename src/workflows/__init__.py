"""Workflow オーケストレーション。4 エージェントを Sequential に接続する。"""

import logging

from agent_framework.orchestrations import SequentialBuilder

from src.agents import (
    create_brochure_gen_agent,
    create_data_search_agent,
    create_marketing_plan_agent,
    create_regulation_check_agent,
)

logger = logging.getLogger(__name__)


def create_pipeline_workflow():
    """4 エージェントの Sequential Workflow を構築する。

    Agent1(データ検索) → Agent2(施策生成) → Agent3(規制チェック) → Agent4(販促物生成)

    Returns:
        構築済みの Workflow インスタンス
    """
    logger.info("パイプライン Workflow を構築中...")

    agent1 = create_data_search_agent()
    agent2 = create_marketing_plan_agent()
    agent3 = create_regulation_check_agent()
    agent4 = create_brochure_gen_agent()

    workflow = SequentialBuilder(
        participants=[agent1, agent2, agent3, agent4],
    ).build()

    logger.info("パイプライン Workflow 構築完了")
    return workflow
