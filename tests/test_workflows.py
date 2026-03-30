"""Workflow オーケストレーションのテスト"""

from unittest.mock import MagicMock, patch


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


class TestWorkflowCreation:
    """Workflow 構築のテスト（エージェント作成をモック化）"""

    def test_create_pipeline_workflow_returns_workflow(self):
        """ワークフローが正常に構築されること"""
        mock_agent = MagicMock()
        mock_workflow = MagicMock()

        with patch("src.workflows.create_data_search_agent", return_value=mock_agent) as m1, \
             patch("src.workflows.create_marketing_plan_agent", return_value=mock_agent) as m2, \
             patch("src.workflows.create_regulation_check_agent", return_value=mock_agent) as m3, \
             patch("src.workflows.create_brochure_gen_agent", return_value=mock_agent) as m4, \
             patch("src.workflows.SequentialBuilder") as mock_builder:
            mock_builder.return_value.build.return_value = mock_workflow

            from src.workflows import create_pipeline_workflow

            workflow = create_pipeline_workflow()
            assert workflow is mock_workflow
            m1.assert_called_once()
            m2.assert_called_once()
            m3.assert_called_once()
            m4.assert_called_once()

    def test_create_pipeline_workflow_passes_four_participants(self):
        """4 エージェントが participants として渡されること"""
        agents = [MagicMock(name=f"agent{i}") for i in range(4)]

        with patch("src.workflows.create_data_search_agent", return_value=agents[0]), \
             patch("src.workflows.create_marketing_plan_agent", return_value=agents[1]), \
             patch("src.workflows.create_regulation_check_agent", return_value=agents[2]), \
             patch("src.workflows.create_brochure_gen_agent", return_value=agents[3]), \
             patch("src.workflows.SequentialBuilder") as mock_builder:
            mock_builder.return_value.build.return_value = MagicMock()

            from src.workflows import create_pipeline_workflow

            create_pipeline_workflow()
            call_kwargs = mock_builder.call_args
            participants = call_kwargs.kwargs.get("participants", call_kwargs.args[0] if call_kwargs.args else [])
            assert len(participants) == 4


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

    def test_plan_revision_agent_importable(self):
        from src.agents.plan_revision import create_plan_revision_agent

        assert callable(create_plan_revision_agent)

    def test_brochure_gen_agent_importable(self):
        from src.agents.brochure_gen import create_brochure_gen_agent

        assert callable(create_brochure_gen_agent)

    def test_agents_init_exports_all_four(self):
        """agents/__init__.py が全エージェントをすべて export していること"""
        from src.agents import (
            create_brochure_gen_agent,
            create_data_search_agent,
            create_marketing_plan_agent,
            create_plan_revision_agent,
            create_regulation_check_agent,
        )

        assert all(
            callable(f)
            for f in [
                create_data_search_agent,
                create_marketing_plan_agent,
                create_regulation_check_agent,
                create_plan_revision_agent,
                create_brochure_gen_agent,
            ]
        )
