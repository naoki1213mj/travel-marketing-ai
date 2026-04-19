"""Tests for Work IQ context retrieval helpers."""

import httpx
import pytest

from src import work_iq_context


class _MockResponse:
    def __init__(self, payload: object, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://graph.microsoft.com/beta/copilot/conversations")
            response = httpx.Response(self.status_code, text=self.text, request=request)
            raise httpx.HTTPStatusError("graph call failed", request=request, response=response)


class _MockClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs):
        self.calls.append({"method": "post", "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append({"method": "stream", "http_method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _MockStreamResponse:
    def __init__(self, chunks: list[str], status_code: int = 200, text: str = "") -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://graph.microsoft.com/beta/copilot/conversations/conv-123/chatOverStream")
            response = httpx.Response(self.status_code, text=self.text, request=request)
            raise httpx.HTTPStatusError("graph call failed", request=request, response=response)

    async def aiter_text(self):
        for chunk in self.chunks:
            yield chunk


def test_parse_brief_summary_prefers_json_payload_and_sanitizes_html() -> None:
    result = work_iq_context._parse_brief_summary(
        '```json {"brief_summary":"<b>家族向け</b> を重視してください","key_points":["ignored"]} ```'
    )

    assert result == "家族向け を重視してください"


def test_build_source_metadata_filters_to_requested_scope() -> None:
    result = work_iq_context._build_source_metadata(
        [
            {
                "seeMoreWebUrl": "https://outlook.office.com/mail/inbox",
                "providerDisplayName": "Mail",
            },
            {
                "seeMoreWebUrl": "https://contoso.sharepoint.com/sites/team/shared.docx",
                "providerDisplayName": "SharePoint",
            },
            {
                "seeMoreWebUrl": "https://teams.microsoft.com/l/chat/0/0",
                "providerDisplayName": "Chat",
            },
        ],
        ["emails", "documents_notes"],
    )

    assert result == [
        {"source": "emails", "count": 1, "label": "メール"},
        {"source": "documents_notes", "count": 1, "label": "文書 / ノート"},
    ]


@pytest.mark.asyncio
async def test_generate_workplace_context_brief_returns_summary_and_sources(monkeypatch) -> None:
    client = _MockClient(
        [
            _MockResponse({"id": "conv-123"}),
            _MockResponse(
                {
                    "messages": [
                        {
                            "text": '{"brief_summary":"営業メールでは春休みの家族旅行需要を重視していました。"}',
                            "attributions": [
                                {
                                    "seeMoreWebUrl": "https://outlook.office.com/mail/inbox",
                                    "providerDisplayName": "Mail",
                                },
                                {
                                    "seeMoreWebUrl": "https://contoso.sharepoint.com/sites/team/briefing.pdf",
                                    "providerDisplayName": "SharePoint",
                                },
                            ],
                        }
                    ]
                }
            ),
        ]
    )
    monkeypatch.setattr(work_iq_context, "get_http_client", lambda: client)
    monkeypatch.setattr(work_iq_context, "get_settings", lambda: {"work_iq_timeout_seconds": "7"})

    result = await work_iq_context.generate_workplace_context_brief(
        "北海道の春プラン",
        ["emails", "documents_notes"],
        "graph-token",
        "Asia/Tokyo",
    )

    assert result == {
        "brief_summary": "営業メールでは春休みの家族旅行需要を重視していました。",
        "brief_source_metadata": [
            {"source": "emails", "count": 1, "label": "メール"},
            {"source": "documents_notes", "count": 1, "label": "文書 / ノート"},
        ],
        "status": "completed",
    }
    assert client.calls[0]["json"] == {}
    assert client.calls[1]["method"] == "post"
    assert str(client.calls[1]["url"]).endswith("/chat")
    assert client.calls[1]["headers"]["Authorization"] == "Bearer graph-token"
    assert client.calls[1]["json"]["contextualResources"]["webContext"]["isWebEnabled"] is False
    assert client.calls[1]["json"]["locationHint"]["timeZone"] == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_generate_workplace_context_brief_maps_consent_errors(monkeypatch) -> None:
    client = _MockClient(
        [
            _MockResponse({"id": "conv-123"}),
            _MockResponse({}, status_code=403, text="Admin consent required for this request"),
        ]
    )
    monkeypatch.setattr(work_iq_context, "get_http_client", lambda: client)

    result = await work_iq_context.generate_workplace_context_brief(
        "北海道の春プラン",
        ["emails"],
        "graph-token",
    )

    assert result == {
        "brief_summary": "",
        "brief_source_metadata": [],
        "status": "consent_required",
        "warning_code": "consent_required",
    }


@pytest.mark.asyncio
async def test_generate_workplace_context_brief_requires_access_token(monkeypatch) -> None:
    client = _MockClient([])
    monkeypatch.setattr(work_iq_context, "get_http_client", lambda: client)

    result = await work_iq_context.generate_workplace_context_brief(
        "北海道の春プラン",
        ["emails"],
        "",
    )

    assert result == {
        "brief_summary": "",
        "brief_source_metadata": [],
        "status": "auth_required",
        "warning_code": "auth_required",
    }
    assert client.calls == []


@pytest.mark.asyncio
async def test_generate_workplace_context_brief_maps_timeout(monkeypatch) -> None:
    client = _MockClient([_MockResponse({"id": "conv-123"}), httpx.TimeoutException("timed out")])
    monkeypatch.setattr(work_iq_context, "get_http_client", lambda: client)

    result = await work_iq_context.generate_workplace_context_brief(
        "北海道の春プラン",
        ["emails"],
        "graph-token",
    )

    assert result == {
        "brief_summary": "",
        "brief_source_metadata": [],
        "status": "timeout",
        "warning_code": "timeout",
    }


@pytest.mark.asyncio
async def test_generate_workplace_context_brief_falls_back_to_stream_when_sync_is_unavailable(monkeypatch) -> None:
    client = _MockClient(
        [
            _MockResponse({"id": "conv-123"}),
            _MockResponse({}, status_code=405, text="Method not allowed"),
            _MockStreamResponse(
                [
                    (
                        "data: {\"messages\":[{\"text\":\""
                        "{\\\"brief_summary\\\":\\\"会議メモでは価格訴求より春の家族体験を重視していました。\\\"}\","
                        "\"attributions\":[{\"seeMoreWebUrl\":\"https://teams.microsoft.com/l/chat/0/0\","
                        "\"providerDisplayName\":\"Chat\"}]}]}\n\n"
                    )
                ]
            ),
        ]
    )
    monkeypatch.setattr(work_iq_context, "get_http_client", lambda: client)

    result = await work_iq_context.generate_workplace_context_brief(
        "北海道の春プラン",
        ["teams_chats"],
        "graph-token",
    )

    assert result == {
        "brief_summary": "会議メモでは価格訴求より春の家族体験を重視していました。",
        "brief_source_metadata": [
            {"source": "teams_chats", "count": 1, "label": "Teams チャット"},
        ],
        "status": "completed",
    }
    assert client.calls[1]["method"] == "post"
    assert str(client.calls[1]["url"]).endswith("/chat")
    assert client.calls[2]["method"] == "stream"
    assert str(client.calls[2]["url"]).endswith("/chatOverStream")
