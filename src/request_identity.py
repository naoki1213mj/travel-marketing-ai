"""リクエストから会話所有者識別子を導出する。"""

import base64
import hashlib
import json
from typing import Literal, TypedDict

from fastapi import Request

IdentityErrorCode = Literal["missing_token", "invalid_token", "identity_mismatch"]


class RequestIdentity(TypedDict):
    """リクエスト単位の呼び出し元情報。"""

    user_id: str
    auth_mode: Literal["delegated", "anonymous"]
    oid: str
    tid: str
    upn: str
    auth_error: IdentityErrorCode | None


def _sanitize_text(value: object) -> str:
    """ヘッダー・claim 由来文字列を軽量に正規化する。"""
    return str(value).strip() if value is not None else ""


def _build_user_id(oid: str, tid: str) -> str:
    """oid/tid 由来の安定した user_id を返す。"""
    digest = hashlib.sha256(f"{tid}:{oid}".encode("utf-8")).hexdigest()[:32]
    return f"user-{digest}"


def _build_anonymous_user_id(request: Request) -> str:
    """認証なしリクエスト向けの匿名 user_id を返す。"""
    forwarded_for = _sanitize_text(request.headers.get("x-forwarded-for"))
    client_host = _sanitize_text(getattr(request.client, "host", ""))
    user_agent = _sanitize_text(request.headers.get("user-agent"))
    accept_language = _sanitize_text(request.headers.get("accept-language"))
    fingerprint = "|".join(value for value in (forwarded_for, client_host, user_agent, accept_language) if value)
    if not fingerprint:
        fingerprint = "anonymous"
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    return f"anon-{digest}"


def _decode_jwt_payload(token: str) -> dict[str, object]:
    """署名検証済み前提の JWT payload をデコードする。"""
    parts = token.split(".")
    if len(parts) != 3:
        return {}

    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}".encode("utf-8")).decode("utf-8")
        data = json.loads(decoded)
    except (ValueError, TypeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def request_has_bearer_token(request: Request) -> bool:
    """Authorization Bearer の有無を返す。"""
    authorization = _sanitize_text(request.headers.get("authorization"))
    return authorization.lower().startswith("bearer ")


def extract_request_identity(request: Request, *, expected_tenant_id: str = "") -> RequestIdentity:
    """Bearer token または匿名フォールバックから呼び出し元を解決する。"""
    anonymous_identity: RequestIdentity = {
        "user_id": _build_anonymous_user_id(request),
        "auth_mode": "anonymous",
        "oid": "",
        "tid": "",
        "upn": "",
        "auth_error": "missing_token",
    }

    if not request_has_bearer_token(request):
        return anonymous_identity

    authorization = _sanitize_text(request.headers.get("authorization"))
    token = authorization.split(" ", 1)[1] if " " in authorization else ""
    claims = _decode_jwt_payload(token)
    oid = _sanitize_text(claims.get("oid"))
    tid = _sanitize_text(claims.get("tid"))
    upn = _sanitize_text(claims.get("preferred_username") or claims.get("upn") or claims.get("email"))

    if expected_tenant_id and tid and tid != expected_tenant_id:
        return {**anonymous_identity, "auth_error": "identity_mismatch"}

    if not oid or not tid:
        return {**anonymous_identity, "auth_error": "invalid_token"}

    return {
        "user_id": _build_user_id(oid, tid),
        "auth_mode": "delegated",
        "oid": oid,
        "tid": tid,
        "upn": upn,
        "auth_error": None,
    }
