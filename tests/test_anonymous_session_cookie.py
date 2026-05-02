"""Tests for cookie-based anonymous user_id derivation in request_identity."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.request_identity import _build_anonymous_user_id


def _fake_request(*, session_id: str = "", forwarded_for: str = "1.2.3.4", user_agent: str = "ua") -> MagicMock:
    request = MagicMock()
    state = MagicMock()
    state.tm_session_id = session_id
    request.state = state
    headers = {}
    if forwarded_for:
        headers["x-forwarded-for"] = forwarded_for
    if user_agent:
        headers["user-agent"] = user_agent
    request.headers = headers
    request.client = None  # client.host is read with getattr, None is OK via getattr default
    return request


def test_anonymous_user_id_uses_session_cookie_when_present() -> None:
    """next-anonymous-auth-replacement: cookie session_id is preferred over fingerprint."""
    sid = "stable-session-token-abcdef0123456789"  # >= 16 chars (production token_urlsafe(32) is ~43 chars)
    request = _fake_request(session_id=sid, forwarded_for="1.2.3.4", user_agent="firefox")
    user_id = _build_anonymous_user_id(request)
    assert user_id.startswith("anon-")
    # 同じ session_id → 同じ user_id (安定性確認)
    request2 = _fake_request(session_id=sid, forwarded_for="9.8.7.6", user_agent="chrome")
    assert _build_anonymous_user_id(request2) == user_id, (
        "session cookie ベースなら fingerprint が変わっても owner_id が安定する"
    )


def test_anonymous_user_id_falls_back_to_fingerprint_when_cookie_absent() -> None:
    """legacy fallback: cookie が無いときは fingerprint で derivation。"""
    request = _fake_request(session_id="", forwarded_for="1.2.3.4", user_agent="ua")
    user_id = _build_anonymous_user_id(request)
    assert user_id.startswith("anon-")
    # 同じ fingerprint → 同じ user_id
    request2 = _fake_request(session_id="", forwarded_for="1.2.3.4", user_agent="ua")
    assert _build_anonymous_user_id(request2) == user_id


def test_cookie_session_yields_different_id_than_fingerprint() -> None:
    """cookie 由来と fingerprint 由来は異なる owner_id になる (sha256 input が違う)。"""
    cookie_request = _fake_request(session_id="my-cookie-with-enough-entropy-1234", forwarded_for="1.1.1.1", user_agent="ua-x")
    fingerprint_request = _fake_request(session_id="", forwarded_for="1.1.1.1", user_agent="ua-x")
    assert _build_anonymous_user_id(cookie_request) != _build_anonymous_user_id(fingerprint_request)


def test_anonymous_user_id_falls_back_to_fingerprint_when_session_id_too_short() -> None:
    """rubber-duck cookie-impl-review: malformed/short session_id は fingerprint fallback。"""
    short_request = _fake_request(session_id="short", forwarded_for="1.2.3.4", user_agent="ua")
    fp_request = _fake_request(session_id="", forwarded_for="1.2.3.4", user_agent="ua")
    assert _build_anonymous_user_id(short_request) == _build_anonymous_user_id(fp_request)


def test_anonymous_user_id_handles_missing_state() -> None:
    """state なしの fake request でも fingerprint fallback で動く。"""
    request = MagicMock()
    request.state = None
    request.headers = {"x-forwarded-for": "1.2.3.4"}
    request.client = None
    user_id = _build_anonymous_user_id(request)
    assert user_id.startswith("anon-")
