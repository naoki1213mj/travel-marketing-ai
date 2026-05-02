"""Per-session HttpOnly cookie for stable anonymous owner_id.

Replaces the fingerprint-based `anon-{sha256(IP+UA)}` owner derivation, which
was unstable across requests due to Connection reuse, X-Forwarded-For order
shifts, and Envoy header normalization (documented in src/api/chat.py:554-557
and reproduced multiple times as APPROVAL_CONTEXT_NOT_FOUND in production).

Design (rubber-duck audit `harden-plan` 2026-05-02):
- Cookie name: `tm_session_id`
- Value: 32-byte urlsafe random token (256-bit entropy, server-issued)
- Cookie attributes — HttpOnly, Secure on HTTPS, SameSite Lax, Path /api,
  Max-Age 86400 (24 h)
- SameSite Lax (NOT Strict) so the Vite dev proxy on localhost works and
  legitimate cross-tab navigation keeps the cookie
- Path /api scopes the cookie to API endpoints only (not /static)
- Set by middleware ONCE per session; subsequent /api requests reuse it
- Bearer-authenticated users IGNORE this cookie (oid+tid from JWT wins)

Migration: existing in-flight conversations keyed by `anon-{fingerprint}`
become unreachable for cookie users. Per rubber-duck #11 we accept this
break for hackathon scope; conversations are short-lived and the production
demo doesn't have persistent multi-session anonymous users.

Cookie size: ~50 bytes. No PII, no privileged data.
"""
from __future__ import annotations

import secrets

from fastapi import Request, Response

SESSION_COOKIE_NAME = "tm_session_id"
SESSION_COOKIE_MAX_AGE_SECONDS = 86_400  # 24 h
SESSION_COOKIE_PATH = "/api"
SESSION_COOKIE_SAMESITE = "lax"  # NOT 'strict' (Vite dev proxy + cross-tab nav)


def get_session_cookie(request: Request) -> str:
    """既存の session cookie を返す。なければ空文字列。"""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    return (raw or "").strip()


def generate_new_session_id() -> str:
    """新しい session_id を発行する (32-byte urlsafe = 256 bit エントロピー)。"""
    return secrets.token_urlsafe(32)


def attach_session_cookie(response: Response, session_id: str, *, secure: bool) -> None:
    """response に session cookie を attach する。

    `secure=True` を強く推奨 (HTTPS 専用)。HTTP 接続でも安全に動かせるよう
    middleware から `request.url.scheme == 'https'` を渡す。
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite=SESSION_COOKIE_SAMESITE,
        path=SESSION_COOKIE_PATH,
    )


def get_or_create_session_id(request: Request) -> tuple[str, bool]:
    """request から session_id を読み出す。なければ生成。

    Returns:
        (session_id, is_newly_generated)
    """
    existing = get_session_cookie(request)
    if existing:
        return existing, False
    return generate_new_session_id(), True
