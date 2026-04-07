"""エージェントのツール関数テスト"""

import json
from unittest.mock import MagicMock

import pytest

from src import config as config_module
from src.agents.data_search import search_customer_reviews, search_sales_history
from src.agents.regulation_check import check_ng_expressions, check_travel_law_compliance, search_knowledge_base


def _disable_azd_env(monkeypatch) -> None:
    """テスト中は実マシンの azd env を参照しない。"""
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})


class TestDataSearchTools:
    """Agent1 のデータ検索ツールテスト"""

    @pytest.mark.asyncio
    async def test_search_sales_history_returns_json(self):
        """販売履歴検索が JSON 文字列を返すこと"""
        result = await search_sales_history(query="沖縄")
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_search_sales_history_filter_by_season(self):
        """季節フィルタが動作すること"""
        result = await search_sales_history(query="test", season="spring")
        parsed = json.loads(result)
        for item in parsed:
            assert item.get("season") == "spring"

    @pytest.mark.asyncio
    async def test_search_customer_reviews_returns_json(self):
        """顧客レビュー検索が JSON 文字列を返すこと"""
        result = await search_customer_reviews()
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_search_customer_reviews_filter_by_rating(self):
        """最低評価フィルタが動作すること"""
        result = await search_customer_reviews(min_rating=4)
        parsed = json.loads(result)
        for item in parsed:
            assert item.get("rating", 0) >= 4

    @pytest.mark.asyncio
    async def test_query_fabric_falls_back_on_credential_error(self, monkeypatch):
        """Fabric SQL のトークン取得失敗時も CSV フォールバックに流せる"""
        from azure.identity import CredentialUnavailableError

        import src.agents.data_search as ds

        class DummyCredential:
            def get_token(self, _scope):
                raise CredentialUnavailableError("credential unavailable")

        monkeypatch.setattr(ds, "_HAS_PYODBC", True)
        monkeypatch.setattr(ds, "DefaultAzureCredential", DummyCredential)
        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_sql_endpoint": "test.sql.fabric.microsoft.com"})

        result = ds._query_fabric("SELECT 1")
        assert result == []


class TestRegulationCheckTools:
    """Agent3 の規制チェックツールテスト"""

    @pytest.mark.asyncio
    async def test_check_ng_expressions_detects_violation(self):
        """NG 表現が検出されること"""
        result = await check_ng_expressions("このプランは最安値です")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        assert parsed[0]["expression"] == "最安値"

    @pytest.mark.asyncio
    async def test_check_ng_expressions_no_violation(self):
        """NG 表現がない場合は検出なしメッセージを返すこと"""
        result = await check_ng_expressions("安全な旅行プランです")
        assert "検出されませんでした" in result

    @pytest.mark.asyncio
    async def test_check_travel_law_compliance_returns_json(self):
        """旅行業法チェックが JSON 文字列を返すこと"""
        result = await check_travel_law_compliance("旅行業者の登録番号: 東京都知事登録旅行業第1234号")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 5  # 5 項目のチェックリスト

    @pytest.mark.asyncio
    async def test_check_travel_law_compliance_detects_keyword(self):
        """キーワードが含まれていれば適合判定されること"""
        result = await check_travel_law_compliance("書面交付義務を遵守しています。広告表示規制に準拠。")
        parsed = json.loads(result)
        # 少なくとも 1 つは「適合」判定されるはず
        statuses = [item["status"] for item in parsed]
        assert "✅ 適合" in statuses


class TestConfigSettings:
    """設定ロードのテスト"""

    def test_get_settings_returns_dict(self):
        from src.config import get_settings

        settings = get_settings()
        assert "project_endpoint" in settings
        assert "model_name" in settings


class TestKnowledgeBaseTool:
    """Agent3 のナレッジベース検索ツールテスト"""

    @pytest.mark.asyncio
    async def test_search_knowledge_base_returns_json(self):
        """ナレッジベース検索がフォールバック JSON を返すこと"""
        result = await search_knowledge_base(query="景品表示法")
        parsed = json.loads(result)
        assert "query" in parsed
        assert parsed["query"] == "景品表示法"

    @pytest.mark.asyncio
    async def test_search_knowledge_base_contains_regulations(self):
        """フォールバック時に NG 表現リストが含まれること"""
        result = await search_knowledge_base(query="旅行業法")
        parsed = json.loads(result)
        assert "ng_expressions" in parsed or "results" in parsed

    @pytest.mark.asyncio
    async def test_search_knowledge_base_different_query(self):
        """異なるクエリでも動作すること"""
        result = await search_knowledge_base(query="広告規制")
        parsed = json.loads(result)
        assert "query" in parsed
        assert parsed["query"] == "広告規制"

    def test_default_model_name(self, monkeypatch):
        """MODEL_NAME 未設定時のデフォルト値"""
        monkeypatch.delenv("MODEL_NAME", raising=False)
        from src.config import get_settings

        settings = get_settings()
        assert settings["model_name"] == "gpt-5-4-mini"


class TestBrochureGenTools:
    """Agent4 のブローシャ生成ツールテスト"""

    @pytest.mark.asyncio
    async def test_generate_hero_image_fallback(self, monkeypatch):
        """OpenAI クライアント未初期化時にフォールバック画像を返す"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        # シングルトンをリセットして未初期化にする
        monkeypatch.setattr(bg, "_image_openai_client", None)
        monkeypatch.setattr(bg, "_image_client_initialized", False)
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
        bg.set_current_conversation_id("hero-test")

        result = await bg.generate_hero_image(
            prompt="beautiful beach",
            destination="Okinawa",
            style="photorealistic",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "generated"
        assert parsed["type"] == "hero"
        # side-channel に保存されていること
        images = bg.pop_pending_images("hero-test")
        assert "hero" in images
        assert images["hero"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_generate_banner_image_fallback(self, monkeypatch):
        """OpenAI クライアント未初期化時にフォールバック画像を返す"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_image_openai_client", None)
        monkeypatch.setattr(bg, "_image_client_initialized", False)
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        result = await bg.generate_banner_image(
            prompt="travel banner",
            platform="instagram",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "generated"
        assert parsed["platform"] == "instagram"
        assert parsed["size"] == "1024x1024"

    @pytest.mark.asyncio
    async def test_generate_banner_image_twitter_size(self, monkeypatch):
        """twitter 指定は X 用バナーへ正規化される"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_image_openai_client", None)
        monkeypatch.setattr(bg, "_image_client_initialized", False)
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        result = await bg.generate_banner_image(
            prompt="travel banner",
            platform="twitter",
        )
        parsed = json.loads(result)
        assert parsed["size"] == "1536x1024"
        assert parsed["platform"] == "x"
        assert parsed["display_aspect_ratio"] == "1.91:1"

    def test_pop_pending_images_returns_and_clears(self):
        """pop_pending_images が保存済み画像を返しクリアする"""
        import src.agents.brochure_gen as bg

        bg._pending_images = {
            "test-conv": {"hero": "data:image/png;base64,abc", "banner_instagram": "data:image/png;base64,def"}
        }
        result = bg.pop_pending_images("test-conv")
        assert result == {"hero": "data:image/png;base64,abc", "banner_instagram": "data:image/png;base64,def"}
        assert "test-conv" not in bg._pending_images

    def test_pop_pending_images_empty(self):
        """画像がない場合は空辞書を返す"""
        import src.agents.brochure_gen as bg

        bg._pending_images = {}
        result = bg.pop_pending_images("nonexistent")
        assert result == {}

    def test_pop_pending_video_job_returns_and_clears(self):
        """pop_pending_video_job がジョブ情報を返しクリアする"""
        import src.agents.video_gen as vg

        vg._pending_video_jobs = {"video-test": {"job_id": "promo-123", "status": "submitted"}}
        result = vg.pop_pending_video_job("video-test")
        assert result == {"job_id": "promo-123", "status": "submitted"}
        assert vg._pending_video_jobs == {}

    def test_pop_pending_video_job_none(self):
        """ジョブがない場合は None を返す"""
        import src.agents.video_gen as vg

        vg._pending_video_jobs = {}
        result = vg.pop_pending_video_job("missing")
        assert result is None

    def test_get_image_openai_client_no_endpoint(self, monkeypatch):
        """project_endpoint 未設定時は None を返す"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_image_client_initialized", False)
        monkeypatch.setattr(bg, "_image_openai_client", None)
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        result = bg._get_image_openai_client()
        assert result is None
        assert bg._image_client_initialized is True

    def test_get_image_openai_client_cached(self, monkeypatch):
        """2 回目以降はキャッシュされた結果を返す"""
        import src.agents.brochure_gen as bg

        monkeypatch.setattr(bg, "_image_client_initialized", True)
        monkeypatch.setattr(bg, "_image_openai_client", "cached-client")

        result = bg._get_image_openai_client()
        assert result == "cached-client"

    @pytest.mark.asyncio
    async def test_generate_image_returns_fallback_on_no_client(self, monkeypatch):
        """クライアントが None の場合フォールバック画像を返す"""
        import src.agents.brochure_gen as bg

        monkeypatch.setattr(bg, "_image_client_initialized", True)
        monkeypatch.setattr(bg, "_image_openai_client", None)

        result = await bg._generate_image("test prompt")
        assert result == bg._FALLBACK_IMAGE

    @pytest.mark.asyncio
    async def test_generate_image_returns_fallback_on_exception(self, monkeypatch):
        """画像生成中にエラーが発生した場合フォールバック画像を返す"""

        import src.agents.brochure_gen as bg

        mock_client = MagicMock()
        mock_client.responses.create.side_effect = Exception("API error")
        monkeypatch.setattr(bg, "_image_client_initialized", True)
        monkeypatch.setattr(bg, "_image_openai_client", mock_client)

        result = await bg._generate_image("test prompt")
        assert result == bg._FALLBACK_IMAGE

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_no_endpoint(self, monkeypatch):
        """CONTENT_UNDERSTANDING_ENDPOINT 未設定時に警告を返す"""
        # data/ ディレクトリ内のパスを使う（パストラバーサル防止ガードを通過させる）
        from pathlib import Path

        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        allowed_path = str(Path(bg.__file__).resolve().parent.parent.parent / "data" / "dummy.pdf")
        monkeypatch.delenv("CONTENT_UNDERSTANDING_ENDPOINT", raising=False)
        result = await bg.analyze_existing_brochure(allowed_path)
        assert "見つかりません" in result or "利用できません" in result

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_file_not_found(self, monkeypatch):
        """ファイルが見つからない場合のエラー"""
        from pathlib import Path

        import src.agents.brochure_gen as bg

        allowed_path = str(Path(bg.__file__).resolve().parent.parent.parent / "data" / "nonexistent.pdf")
        monkeypatch.setenv("CONTENT_UNDERSTANDING_ENDPOINT", "https://test.cognitiveservices.azure.com")
        result = await bg.analyze_existing_brochure(allowed_path)
        assert "見つかりません" in result

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_path_traversal(self):
        """パストラバーサル攻撃を拒否する"""
        import src.agents.brochure_gen as bg

        result = await bg.analyze_existing_brochure("../../etc/passwd")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "許可されていません" in parsed["error"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_no_endpoint(self, monkeypatch):
        """SPEECH_SERVICE_ENDPOINT 未設定時に unavailable を返す"""
        import src.agents.video_gen as vg

        monkeypatch.delenv("SPEECH_SERVICE_ENDPOINT", raising=False)
        monkeypatch.delenv("SPEECH_SERVICE_REGION", raising=False)
        result = await vg.generate_promo_video("テストサマリ", "concierge")
        parsed = json.loads(result)
        assert parsed["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_generate_promo_video_uses_hd_ssml_payload(self, monkeypatch):
        """動画生成は avatar 互換の簡素な SSML と既定 avatar 設定で送信する"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-123"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.delenv("VIDEO_GEN_VOICE", raising=False)
        monkeypatch.delenv("VIDEO_GEN_AVATAR_CHARACTER", raising=False)
        monkeypatch.delenv("VIDEO_GEN_AVATAR_STYLE", raising=False)
        monkeypatch.delenv("VIDEO_GEN_BACKGROUND_COLOR", raising=False)
        monkeypatch.delenv("VIDEO_GEN_BITRATE_KBPS", raising=False)
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("春の北海道旅。温泉と絶景を楽しめます。", "concierge")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert parsed["job_id"] == "promo-job-123"
        assert payload["inputKind"] == "SSML"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "lisa"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "casual-sitting"
        assert payload["avatarConfig"]["bitrateKbps"] == 4000
        ssml_content = payload["inputs"][0]["content"]
        assert "ja-JP-Nanami:DragonHDLatestNeural" in ssml_content
        assert "gesture.show-front-1" in ssml_content
        assert "詳しくはブローシャをご確認のうえ" in ssml_content
        assert "mstts:express-as" not in ssml_content
        assert "mstts:paralinguistic" not in ssml_content
        assert "<prosody" not in ssml_content
        assert "<emphasis" not in ssml_content

    @pytest.mark.asyncio
    async def test_generate_promo_video_guide_uses_valid_avatar_style(self, monkeypatch):
        """guide 指定時は Lori の有効な casual スタイルを使う"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-guide"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("沖縄の海と文化を体験できる旅です。", "guide")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "lori"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "casual"
        assert "gesture.hello" in payload["inputs"][0]["content"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_respects_env_overrides(self, monkeypatch):
        """動画生成は env の voice / avatar / bitrate 上書きを反映する"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-456"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.setenv("VIDEO_GEN_VOICE", "ja-JP-Masaru:DragonHDLatestNeural")
        monkeypatch.setenv("VIDEO_GEN_AVATAR_CHARACTER", "meg")
        monkeypatch.setenv("VIDEO_GEN_AVATAR_STYLE", "business-standing")
        monkeypatch.setenv("VIDEO_GEN_BACKGROUND_COLOR", "#11223344")
        monkeypatch.setenv("VIDEO_GEN_BITRATE_KBPS", "5500")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("沖縄の海と文化を体験できる旅です。", "guide")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "meg"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "business-standing"
        assert payload["avatarConfig"]["backgroundColor"] == "#11223344"
        assert payload["avatarConfig"]["bitrateKbps"] == 5500
        assert "ja-JP-Masaru:DragonHDLatestNeural" in payload["inputs"][0]["content"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_falls_back_when_bitrate_is_invalid(self, monkeypatch):
        """bitrate の env が不正でも既定値で送信する"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-789"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.setenv("VIDEO_GEN_BITRATE_KBPS", "invalid")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("札幌のグルメを巡る旅です。", "concierge")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["bitrateKbps"] == 4000

    def test_create_brochure_gen_agent_with_mock(self, monkeypatch):
        """ブローシャ生成エージェントが正しいツール数で作成されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.brochure_gen as bg

        mock_agent = MagicMock()
        mock_client = MagicMock()
        mock_client.as_agent.return_value = mock_agent

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        agent = bg.create_brochure_gen_agent()
        assert agent is mock_agent
        call_kwargs = mock_client.as_agent.call_args.kwargs
        assert call_kwargs["name"] == "brochure-gen-agent"
        assert len(call_kwargs["tools"]) == 3

    def test_create_brochure_gen_agent_with_model_settings(self, monkeypatch):
        """model_settings が agent_kwargs に反映されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.brochure_gen as bg

        mock_client = MagicMock()
        mock_client.as_agent.return_value = MagicMock()

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        bg.create_brochure_gen_agent(model_settings={"temperature": 0.5, "max_tokens": 2000, "top_p": 0.9})
        call_kwargs = mock_client.as_agent.call_args.kwargs
        opts = call_kwargs["default_options"]
        assert opts["temperature"] == 0.5
        assert opts["max_output_tokens"] == 2000
        assert opts["top_p"] == 0.9


class TestMarketingPlanAgent:
    """Agent2 のマーケ施策エージェント作成テスト"""

    def test_create_marketing_plan_agent_with_mock(self, monkeypatch):
        """マーケ施策エージェントが作成されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.marketing_plan as mp

        mock_agent = MagicMock()
        mock_client = MagicMock()
        mock_client.as_agent.return_value = mock_agent
        mock_client.get_web_search_tool.return_value = MagicMock()

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        agent = mp.create_marketing_plan_agent()
        assert agent is mock_agent
        call_kwargs = mock_client.as_agent.call_args.kwargs
        assert call_kwargs["name"] == "marketing-plan-agent"
        assert "instructions" in call_kwargs
        assert len(call_kwargs["tools"]) == 1

    def test_create_marketing_plan_agent_with_settings(self, monkeypatch):
        """model_settings が正しく渡されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.marketing_plan as mp

        mock_client = MagicMock()
        mock_client.as_agent.return_value = MagicMock()
        mock_client.get_web_search_tool.return_value = MagicMock()

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        mp.create_marketing_plan_agent(model_settings={"temperature": 0.3})
        call_kwargs = mock_client.as_agent.call_args.kwargs
        opts = call_kwargs["default_options"]
        assert opts["temperature"] == 0.3
        assert opts["max_output_tokens"] == 16384

    def test_instructions_contains_required_sections(self):
        """INSTRUCTIONS に必要な構成要素が含まれること"""
        from src.agents.marketing_plan import INSTRUCTIONS

        assert "キャッチコピー" in INSTRUCTIONS
        assert "ターゲット" in INSTRUCTIONS
        assert "KPI" in INSTRUCTIONS
        assert "景品表示法" in INSTRUCTIONS
