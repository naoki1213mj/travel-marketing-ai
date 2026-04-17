"""Work IQ 会話設定と永続化メタデータを正規化する。"""

from typing import NotRequired, TypedDict

from src.request_identity import RequestIdentity

CONVERSATION_SETTINGS_METADATA_KEY = "conversation_settings"
WORK_IQ_SESSION_METADATA_KEY = "work_iq_session"
_DEFAULT_SOURCE_SCOPE = ["meeting_notes", "emails", "teams_chats", "documents_notes"]
_SOURCE_SCOPE_ALIASES = {
    "meeting_notes": "meeting_notes",
    "meeting-notes": "meeting_notes",
    "meetingnotes": "meeting_notes",
    "meetings": "meeting_notes",
    "emails": "emails",
    "email": "emails",
    "teams_chats": "teams_chats",
    "teams-chats": "teams_chats",
    "teams chats": "teams_chats",
    "teams": "teams_chats",
    "documents_notes": "documents_notes",
    "documents-notes": "documents_notes",
    "documents/notes": "documents_notes",
    "documents": "documents_notes",
    "notes": "documents_notes",
}


class ConversationSettings(TypedDict):
    """会話単位で固定する Work IQ 設定。"""

    work_iq_enabled: bool
    source_scope: list[str]


class WorkIQSourceMetadata(TypedDict):
    """Work IQ ソース概要の安全な保存形式。"""

    source: str
    label: NotRequired[str]
    count: NotRequired[int]


class WorkIQSessionMetadata(TypedDict):
    """会話に紐づく Work IQ セッション情報。"""

    enabled: bool
    source_scope: list[str]
    auth_mode: str
    owner_oid: str
    owner_tid: str
    owner_upn: str
    warning_code: NotRequired[str]
    status: NotRequired[str]
    brief_summary: NotRequired[str]
    brief_source_metadata: NotRequired[list[WorkIQSourceMetadata]]


def _sanitize_text(value: object) -> str:
    """永続化前の軽量サニタイズ。"""
    return str(value).strip() if value is not None else ""


def _to_bool(value: object) -> bool:
    """入力値を bool に正規化する。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _normalize_scope_value(raw_scope: object) -> list[str]:
    """source scope を canonical form に正規化する。"""
    if isinstance(raw_scope, str):
        candidates = [part.strip() for part in raw_scope.split(",")]
    elif isinstance(raw_scope, list):
        candidates = [_sanitize_text(value) for value in raw_scope]
    else:
        candidates = []

    normalized: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        key = _SOURCE_SCOPE_ALIASES.get(candidate.strip().lower())
        if key and key not in normalized:
            normalized.append(key)
    return normalized


def sanitize_conversation_settings(value: object) -> ConversationSettings:
    """conversation_settings を canonical form に整える。"""
    if not isinstance(value, dict):
        return {"work_iq_enabled": False, "source_scope": []}

    enabled = _to_bool(value.get("work_iq_enabled") or value.get("workIqEnabled"))
    raw_scope = (
        value.get("source_scope")
        or value.get("sourceScope")
        or value.get("work_iq_source_scope")
        or value.get("workIqSourceScope")
    )
    source_scope = _normalize_scope_value(raw_scope)
    if enabled and not source_scope:
        source_scope = list(_DEFAULT_SOURCE_SCOPE)
    if not enabled:
        source_scope = []
    return {
        "work_iq_enabled": enabled,
        "source_scope": source_scope,
    }


def normalize_conversation_settings(
    raw_conversation_settings: dict | None,
    raw_settings: dict | None,
) -> ConversationSettings:
    """新旧 payload から immutable conversation_settings を抽出する。"""
    if isinstance(raw_conversation_settings, dict):
        return sanitize_conversation_settings(raw_conversation_settings)
    if isinstance(raw_settings, dict):
        return sanitize_conversation_settings(raw_settings)
    return {"work_iq_enabled": False, "source_scope": []}


def has_work_iq_overrides(raw_conversation_settings: dict | None, raw_settings: dict | None) -> bool:
    """payload に Work IQ 固有設定が含まれるかを返す。"""
    candidates = [raw_conversation_settings, raw_settings]
    tracked_keys = {"work_iq_enabled", "workIqEnabled", "source_scope", "sourceScope", "work_iq_source_scope", "workIqSourceScope"}
    return any(isinstance(candidate, dict) and any(key in candidate for key in tracked_keys) for candidate in candidates)


def conversation_settings_conflict(
    requested: ConversationSettings,
    stored: ConversationSettings,
) -> bool:
    """会話途中に immutable 設定が変更されていないか判定する。"""
    return requested["work_iq_enabled"] != stored["work_iq_enabled"] or requested["source_scope"] != stored["source_scope"]


def get_conversation_settings_from_metadata(metadata: dict | None) -> ConversationSettings:
    """metadata から conversation_settings を安全に読み出す。"""
    if not isinstance(metadata, dict):
        return {"work_iq_enabled": False, "source_scope": []}
    return sanitize_conversation_settings(metadata.get(CONVERSATION_SETTINGS_METADATA_KEY))


def _sanitize_source_metadata_item(value: object) -> WorkIQSourceMetadata | None:
    """brief_source_metadata の 1 要素を allow-list で整形する。"""
    if not isinstance(value, dict):
        return None
    source = _sanitize_text(value.get("source"))
    if not source:
        return None

    item: WorkIQSourceMetadata = {"source": source}
    label = _sanitize_text(value.get("label"))
    if label:
        item["label"] = label
    count = value.get("count")
    if isinstance(count, int):
        item["count"] = count
    return item


def sanitize_work_iq_session_for_storage(value: object) -> WorkIQSessionMetadata | None:
    """永続化用の Work IQ session metadata を allow-list で整える。"""
    if not isinstance(value, dict):
        return None

    enabled = _to_bool(value.get("enabled"))
    source_scope = _normalize_scope_value(value.get("source_scope") or value.get("sourceScope"))
    if enabled and not source_scope:
        source_scope = list(_DEFAULT_SOURCE_SCOPE)
    if not enabled:
        source_scope = []

    session: WorkIQSessionMetadata = {
        "enabled": enabled,
        "source_scope": source_scope,
        "auth_mode": _sanitize_text(value.get("auth_mode")) or "anonymous",
        "owner_oid": _sanitize_text(value.get("owner_oid")),
        "owner_tid": _sanitize_text(value.get("owner_tid")),
        "owner_upn": _sanitize_text(value.get("owner_upn")),
    }

    warning_code = _sanitize_text(value.get("warning_code"))
    if warning_code:
        session["warning_code"] = warning_code

    status = _sanitize_text(value.get("status") or value.get("status_code") or warning_code)
    if status:
        session["status"] = status

    brief_summary = _sanitize_text(value.get("brief_summary"))
    if brief_summary:
        session["brief_summary"] = brief_summary

    raw_source_metadata = value.get("brief_source_metadata")
    if isinstance(raw_source_metadata, list):
        sanitized_source_metadata = [
            item for raw_item in raw_source_metadata if (item := _sanitize_source_metadata_item(raw_item)) is not None
        ]
        if sanitized_source_metadata:
            session["brief_source_metadata"] = sanitized_source_metadata

    return session


def sanitize_work_iq_session_for_response(value: object) -> dict | None:
    """API レスポンス向けに owner claim を除去した Work IQ session を返す。"""
    session = sanitize_work_iq_session_for_storage(value)
    if not session:
        return None
    return {
        key: item
        for key, item in session.items()
        if key not in {"owner_oid", "owner_tid", "owner_upn"}
    }


def build_work_iq_session_metadata(
    conversation_settings: ConversationSettings,
    identity: RequestIdentity,
    existing_session: object = None,
) -> WorkIQSessionMetadata:
    """新規会話向けの sanitized Work IQ session metadata を構築する。"""
    session = sanitize_work_iq_session_for_storage(existing_session) or {
        "enabled": False,
        "source_scope": [],
        "auth_mode": "anonymous",
        "owner_oid": "",
        "owner_tid": "",
        "owner_upn": "",
    }

    session.update(
        {
            "enabled": conversation_settings["work_iq_enabled"],
            "source_scope": list(conversation_settings["source_scope"]),
            "auth_mode": identity["auth_mode"],
            "owner_oid": identity["oid"],
            "owner_tid": identity["tid"],
            "owner_upn": identity["upn"],
        }
    )

    if conversation_settings["work_iq_enabled"] and identity["auth_mode"] != "delegated":
        session["warning_code"] = "identity_mismatch" if identity["auth_error"] == "identity_mismatch" else "auth_required"
        session["status"] = session["warning_code"]
    else:
        session.pop("warning_code", None)
        session.pop("status", None)

    return session
