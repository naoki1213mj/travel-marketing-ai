"""Tests for src/session_cookie.py session-cookie helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Response

from src.session_cookie import (
    SESSION_COOKIE_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_PATH,
    attach_session_cookie,
    generate_new_session_id,
    get_or_create_session_id,
    get_session_cookie,
)


def _fake_request(cookies: dict[str, str] | None = None) -> MagicMock:
    request = MagicMock()
    request.cookies = cookies or {}
    return request


def test_get_session_cookie_returns_existing_value() -> None:
    request = _fake_request({SESSION_COOKIE_NAME: "abc123"})
    assert get_session_cookie(request) == "abc123"


def test_get_session_cookie_returns_empty_when_absent() -> None:
    assert get_session_cookie(_fake_request({})) == ""


def test_get_session_cookie_strips_whitespace() -> None:
    assert get_session_cookie(_fake_request({SESSION_COOKIE_NAME: "  spaced  "})) == "spaced"


def test_generate_new_session_id_is_unique_and_long_enough() -> None:
    a = generate_new_session_id()
    b = generate_new_session_id()
    assert a != b
    # token_urlsafe(32) → ~43 chars base64
    assert len(a) >= 32 and len(b) >= 32


def test_get_or_create_returns_existing_when_present() -> None:
    request = _fake_request({SESSION_COOKIE_NAME: "preexisting"})
    sid, is_new = get_or_create_session_id(request)
    assert sid == "preexisting"
    assert is_new is False


def test_get_or_create_generates_when_absent() -> None:
    request = _fake_request({})
    sid, is_new = get_or_create_session_id(request)
    assert sid != ""
    assert is_new is True


def test_attach_session_cookie_secure_path_httponly() -> None:
    response = Response()
    attach_session_cookie(response, "test-session-token", secure=True)
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    assert "test-session-token" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert f"path={SESSION_COOKIE_PATH}" in set_cookie.lower()
    assert f"max-age={SESSION_COOKIE_MAX_AGE_SECONDS}" in set_cookie.lower()


def test_attach_session_cookie_omits_secure_for_http_dev() -> None:
    response = Response()
    attach_session_cookie(response, "dev-session", secure=False)
    set_cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie
    # Secure attribute should NOT be present
    assert "Secure" not in set_cookie
