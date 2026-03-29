"""Workflow オーケストレーションのテスト"""


class TestWorkflowImport:
    """Workflow モジュールの import テスト"""

    def test_can_import_create_pipeline_workflow(self):
        """create_pipeline_workflow が import できること"""
        from src.workflows import create_pipeline_workflow

        assert callable(create_pipeline_workflow)

    def test_sequential_builder_available(self):
        """SequentialBuilder が agent_framework.orchestrations から利用可能であること"""
        from agent_framework.orchestrations import SequentialBuilder

        assert SequentialBuilder is not None


class TestAgentCreation:
    """エージェント作成関数のテスト（Azure 接続なしで import レベルの動作を確認）"""

    def test_data_search_agent_importable(self):
        from src.agents.data_search import create_data_search_agent

        assert callable(create_data_search_agent)

    def test_marketing_plan_agent_importable(self):
        from src.agents.marketing_plan import create_marketing_plan_agent

        assert callable(create_marketing_plan_agent)

    def test_regulation_check_agent_importable(self):
        from src.agents.regulation_check import create_regulation_check_agent

        assert callable(create_regulation_check_agent)

    def test_brochure_gen_agent_importable(self):
        from src.agents.brochure_gen import create_brochure_gen_agent

        assert callable(create_brochure_gen_agent)

    def test_agents_init_exports_all_four(self):
        """agents/__init__.py が 4 エージェントをすべて export していること"""
        from src.agents import (
            create_brochure_gen_agent,
            create_data_search_agent,
            create_marketing_plan_agent,
            create_regulation_check_agent,
        )

        assert all(
            callable(f)
            for f in [
                create_data_search_agent,
                create_marketing_plan_agent,
                create_regulation_check_agent,
                create_brochure_gen_agent,
            ]
        )
