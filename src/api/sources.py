"""ユーザー提供ソースの取り込み・レビュー API。"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
import uuid
from typing import Literal

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from src.config import get_settings
from src.mai_transcribe import (
    MaiTranscribeAdapterError,
    MaiTranscribeAdapterNotImplementedError,
    MaiTranscribeRequest,
    MaiTranscribeRequestError,
    MaiTranscribeUnavailableError,
    get_mai_transcribe_availability,
    transcribe_audio,
)
from src.middleware import check_prompt_shield, check_tool_response
from src.model_deployments import parse_bool_setting
from src.request_identity import RequestIdentity, RequestIdentityError, extract_request_identity
from src.source_ingestion import (
    SourceIngestionLimitExceededError,
    SourceIngestionQuotaExceededError,
    build_public_source_payload,
    create_audio_source,
    create_text_source,
    delete_source,
    get_source,
    get_source_ingestion_limits,
    list_sources,
    normalize_source_metadata,
    review_source,
    sanitize_source_text,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])
logger = logging.getLogger(__name__)

_MAX_CONVERSATION_ID_LENGTH = 100
_HARD_MAX_SOURCE_TEXT_LENGTH = 50_000
_HARD_MAX_AUDIO_DURATION_SECONDS = 60 * 60
_HARD_MAX_AUDIO_BYTES = 100 * 1024 * 1024
_HARD_MAX_PDF_BYTES = 25 * 1024 * 1024
_PDF_TEXT_GUARD_LENGTH = 4_000
_ALLOWED_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}


class TextSourceRequest(BaseModel):
    """テキストソース作成リクエスト。"""

    conversation_id: str | None = Field(default=None, max_length=_MAX_CONVERSATION_ID_LENGTH)
    title: str | None = Field(default=None, max_length=120)
    text: str = Field(..., min_length=1, max_length=_HARD_MAX_SOURCE_TEXT_LENGTH)
    metadata: dict | None = None

    @field_validator("conversation_id", "title")
    @classmethod
    def sanitize_optional_fields(cls, value: str | None) -> str | None:
        """任意文字列から制御文字を除く。"""
        sanitized = sanitize_source_text(value, max_length=_MAX_CONVERSATION_ID_LENGTH)
        return sanitized or None

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, value: str) -> str:
        """本文から制御文字を除き、空文字を拒否する。"""
        sanitized = sanitize_source_text(value, max_length=_HARD_MAX_SOURCE_TEXT_LENGTH)
        if not sanitized:
            raise ValueError("text is required")
        return sanitized


class AudioSourceRequest(BaseModel):
    """音声ソース作成リクエスト。raw audio は受け取らない。"""

    conversation_id: str | None = Field(default=None, max_length=_MAX_CONVERSATION_ID_LENGTH)
    audio_url: str | None = Field(default=None, max_length=2048)
    filename: str | None = Field(default=None, max_length=200)
    content_type: str | None = Field(default=None, max_length=100)
    duration_seconds: float | None = Field(default=None, ge=0, le=_HARD_MAX_AUDIO_DURATION_SECONDS)
    size_bytes: int | None = Field(default=None, ge=0, le=_HARD_MAX_AUDIO_BYTES)
    language: str | None = Field(default=None, max_length=20)
    metadata: dict | None = None

    @field_validator("conversation_id", "audio_url", "filename", "content_type", "language")
    @classmethod
    def sanitize_optional_fields(cls, value: str | None) -> str | None:
        """任意文字列から制御文字を除く。"""
        sanitized = sanitize_source_text(value, max_length=2048)
        return sanitized or None


class SourceReviewRequest(BaseModel):
    """ソースレビュー結果の保存リクエスト。"""

    approved: bool
    summary: str | None = Field(default=None, max_length=1200)

    @field_validator("summary")
    @classmethod
    def sanitize_summary(cls, value: str | None) -> str | None:
        """レビュー済み要約から制御文字を除く。"""
        sanitized = sanitize_source_text(value, max_length=1200)
        return sanitized or None


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message, "code": code})


def _owner_identity_or_error(request: Request, *, expected_tenant_id: str = "") -> RequestIdentity | JSONResponse:
    """owner-scoped source API 用に認証境界を適用する。"""
    try:
        return extract_request_identity(
            request,
            expected_tenant_id=expected_tenant_id,
            enforce_owner_boundary=True,
        )
    except RequestIdentityError as exc:
        return _error_response(exc.status_code, exc.code, exc.message)


def _new_conversation_id() -> str:
    return str(uuid.uuid4())


def _source_ingestion_enabled() -> bool:
    """ローカル source ingestion API の feature flag を返す。"""
    return parse_bool_setting(get_settings()["enable_source_ingestion"])


def _quota_error_response() -> JSONResponse:
    return _error_response(429, "SOURCE_QUOTA_EXCEEDED", "owner ごとのソース上限に達しました")


def _limit_error_response(code: str, message: str) -> JSONResponse:
    return _error_response(413, code, message)


def _source_limits_payload() -> dict[str, bool | dict[str, int]]:
    settings = get_settings()
    return {
        "enabled": parse_bool_setting(settings["enable_source_ingestion"]),
        "limits": get_source_ingestion_limits(settings),
    }


async def _reject_if_unsafe(text: str, *, source: Literal["input", "tool"]) -> JSONResponse | None:
    """既存ガードと同じ判定で unsafe なソースを拒否する。"""
    shield_result = await (check_prompt_shield(text) if source == "input" else check_tool_response(text))
    if shield_result.is_safe:
        return None
    return _error_response(400, "SOURCE_GUARD_BLOCKED", "ソースが注入ガードによりブロックされました")


def _safe_pdf_title(filename: str | None) -> str:
    """PDF ファイル名をタイトルとして安全に正規化する。"""
    safe_name = sanitize_source_text(filename or "", max_length=200)
    if not safe_name:
        return "PDF source"
    return safe_name.split("\\")[-1].split("/")[-1] or "PDF source"


def _extract_content_understanding_text(result: object) -> tuple[str, int, int]:
    """Content Understanding の応答から本文候補を抽出する。"""
    if not isinstance(result, dict):
        return "", 0, 0
    root = result.get("result") if isinstance(result.get("result"), dict) else result
    root = root.get("analyzerResult") if isinstance(root.get("analyzerResult"), dict) else root
    pages = root.get("pages") if isinstance(root.get("pages"), list) else []
    paragraphs = root.get("paragraphs") if isinstance(root.get("paragraphs"), list) else []
    parts: list[str] = []

    for para in paragraphs[:80]:
        if not isinstance(para, dict):
            continue
        content = sanitize_source_text(para.get("content"), max_length=1_000)
        if content:
            parts.append(content)

    contents = root.get("contents") if isinstance(root.get("contents"), list) else []
    for item in contents[:10]:
        if not isinstance(item, dict):
            continue
        markdown = sanitize_source_text(item.get("markdown"), max_length=4_000)
        text = sanitize_source_text(item.get("text") or item.get("content"), max_length=4_000)
        if markdown:
            parts.append(markdown)
        elif text:
            parts.append(text)

    return "\n".join(parts)[:_HARD_MAX_SOURCE_TEXT_LENGTH], len(pages), len(paragraphs)


async def _analyze_pdf_content(content: bytes, *, title: str) -> tuple[str, dict[str, str | int | float | bool | None]]:
    """PDF を Content Understanding で解析し、失敗時はレビュー可能なフォールバックを返す。"""
    metadata: dict[str, str | int | float | bool | None] = {
        "filename": title,
        "content_type": "application/pdf",
        "parser": "content_understanding",
    }
    endpoint = get_settings().get("content_understanding_endpoint", "")
    if not endpoint:
        metadata["parse_status"] = "unavailable"
        return (
            f"PDF「{title}」を受け取りました。Content Understanding が未設定のため、本文解析は利用できません。",
            metadata,
        )

    try:
        from src.agent_client import get_shared_credential

        token = await asyncio.to_thread(
            get_shared_credential().get_token, "https://cognitiveservices.azure.com/.default"
        )
        analyze_url = (
            f"{endpoint.rstrip('/')}/contentunderstanding/analyzers/"
            f"prebuilt-document-rag:analyze?api-version=2025-05-01-preview"
        )
        req = urllib.request.Request(
            analyze_url,
            data=content,
            headers={
                "Authorization": f"Bearer {token.token}",
                "Content-Type": "application/pdf",
            },
            method="POST",
        )

        def _post_pdf_for_analysis() -> dict:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))

        result = await asyncio.to_thread(_post_pdf_for_analysis)
        extracted_text, page_count, paragraph_count = _extract_content_understanding_text(result)
        metadata.update(
            {
                "parse_status": "completed" if extracted_text else "empty",
                "page_count": page_count,
                "paragraph_count": paragraph_count,
            }
        )
        if extracted_text:
            return extracted_text, metadata
        return f"PDF「{title}」から本文を抽出できませんでした。", metadata
    except (ImportError, ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.warning("PDF source Content Understanding 解析をスキップ: %s", type(exc).__name__)
        metadata["parse_status"] = "failed"
        return f"PDF「{title}」を受け取りました。Content Understanding 解析に失敗したため、本文解析は利用できません。", metadata
    except Exception as exc:
        logger.warning("PDF source Content Understanding 解析で予期しないエラー: %s", type(exc).__name__)
        metadata["parse_status"] = "failed"
        return f"PDF「{title}」を受け取りました。Content Understanding 解析に失敗したため、本文解析は利用できません。", metadata


async def create_pdf_source_from_upload(
    request: Request,
    file: UploadFile,
    conversation_id: str | None = None,
) -> JSONResponse:
    """PDF を source ingestion のレビュー待ちソースとして取り込む。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    limits = get_source_ingestion_limits()
    identity = _owner_identity_or_error(request, expected_tenant_id=get_settings()["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity

    title = _safe_pdf_title(file.filename)
    if not title.lower().endswith(".pdf"):
        return _error_response(400, "INVALID_PDF_TYPE", "PDF ファイルのみアップロード可能です")
    content_type = sanitize_source_text(file.content_type, max_length=100).lower()
    if content_type not in _ALLOWED_PDF_CONTENT_TYPES:
        return _error_response(400, "INVALID_PDF_TYPE", "PDF ファイルのみアップロード可能です")

    content = await file.read(limits["max_pdf_bytes"] + 1)
    if len(content) > limits["max_pdf_bytes"]:
        return _limit_error_response("PDF_TOO_LARGE", "PDF ファイルサイズが上限を超えています")
    if not content.startswith(b"%PDF-"):
        return _error_response(400, "INVALID_PDF_CONTENT", "有効な PDF ファイルではありません")

    guard_response = await _reject_if_unsafe(title, source="input")
    if guard_response is not None:
        return guard_response

    parsed_text, metadata = await _analyze_pdf_content(content, title=title)
    guard_response = await _reject_if_unsafe(parsed_text[:_PDF_TEXT_GUARD_LENGTH], source="tool")
    if guard_response is not None:
        return guard_response

    try:
        record = await create_text_source(
            owner_id=identity["user_id"],
            conversation_id=sanitize_source_text(conversation_id, max_length=_MAX_CONVERSATION_ID_LENGTH)
            or _new_conversation_id(),
            title=title,
            text=sanitize_source_text(parsed_text, max_length=limits["max_text_chars"]),
            kind="pdf",
            metadata=normalize_source_metadata(metadata),
        )
    except SourceIngestionQuotaExceededError:
        return _quota_error_response()
    except SourceIngestionLimitExceededError:
        return _limit_error_response("SOURCE_TEXT_TOO_LARGE", "抽出テキストが上限を超えています")
    return JSONResponse(status_code=201, content={"source": build_public_source_payload(record)})


def _build_audio_metadata(body: AudioSourceRequest) -> dict[str, str | int | float | bool | None] | None:
    """raw audio URI を除いた安全な音声 metadata だけを残す。"""
    metadata = normalize_source_metadata(body.metadata) or {}
    for key in ("audio_url", "audio_uri", "source_url", "sas_url", "url"):
        metadata.pop(key, None)
    if body.filename:
        metadata["filename"] = body.filename
    if body.content_type:
        metadata["content_type"] = body.content_type
    if body.duration_seconds is not None:
        metadata["duration_seconds"] = body.duration_seconds
    if body.size_bytes is not None:
        metadata["size_bytes"] = body.size_bytes
    if body.language:
        metadata["language"] = body.language
    metadata["transcription_provider"] = "mai_transcribe_1"
    return metadata or None


@router.post("/text")
async def create_text_source_endpoint(request: Request, body: TextSourceRequest) -> JSONResponse:
    """テキストソースをレビュー待ちで取り込む。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    identity = _owner_identity_or_error(request, expected_tenant_id=get_settings()["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity
    limits = get_source_ingestion_limits()
    if len(body.text) > limits["max_text_chars"]:
        return _limit_error_response("SOURCE_TEXT_TOO_LARGE", "テキストが上限を超えています")
    conversation_id = body.conversation_id or _new_conversation_id()
    title = body.title or "Text source"
    guard_response = await _reject_if_unsafe(f"{title}\n{body.text}", source="input")
    if guard_response is not None:
        return guard_response

    try:
        record = await create_text_source(
            owner_id=identity["user_id"],
            conversation_id=conversation_id,
            title=title,
            text=body.text,
            metadata=normalize_source_metadata(body.metadata),
        )
    except SourceIngestionQuotaExceededError:
        return _quota_error_response()
    except SourceIngestionLimitExceededError:
        return _limit_error_response("SOURCE_TEXT_TOO_LARGE", "テキストが上限を超えています")
    return JSONResponse(status_code=201, content={"source": build_public_source_payload(record)})


@router.post("/audio")
async def create_audio_source_endpoint(request: Request, body: AudioSourceRequest) -> JSONResponse:
    """音声ソース取り込みの安全な capability gate。raw audio は保存しない。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    settings = get_settings()
    identity = _owner_identity_or_error(request, expected_tenant_id=settings["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity
    limits = get_source_ingestion_limits(settings)
    if body.duration_seconds is not None and body.duration_seconds > limits["max_audio_seconds"]:
        return _limit_error_response("AUDIO_TOO_LONG", "音声の長さが上限を超えています")
    if body.size_bytes is not None and body.size_bytes > limits["max_audio_bytes"]:
        return _limit_error_response("AUDIO_TOO_LARGE", "音声サイズが上限を超えています")
    availability = get_mai_transcribe_availability(settings)
    if not availability["available"]:
        return _error_response(
            503,
            "AUDIO_TRANSCRIBE_UNAVAILABLE",
            "音声文字起こしアダプターはこの環境では利用できません",
        )

    if not body.audio_url:
        return _error_response(400, "AUDIO_SOURCE_URI_REQUIRED", "audio_url is required")

    conversation_id = body.conversation_id or _new_conversation_id()
    title = body.filename or "Audio source"
    try:
        result = await transcribe_audio(
            MaiTranscribeRequest(
                audio_url=body.audio_url,
                filename=body.filename,
                content_type=body.content_type,
                duration_seconds=body.duration_seconds,
                language=body.language,
            ),
            settings=settings,
        )
    except MaiTranscribeRequestError as exc:
        return _error_response(400, "AUDIO_TRANSCRIBE_BAD_REQUEST", str(exc))
    except MaiTranscribeAdapterNotImplementedError:
        return _error_response(
            501,
            "AUDIO_TRANSCRIBE_ADAPTER_NOT_IMPLEMENTED",
            "音声文字起こし API contract が未設定です",
        )
    except MaiTranscribeUnavailableError:
        return _error_response(
            503,
            "AUDIO_TRANSCRIBE_UNAVAILABLE",
            "音声文字起こしアダプターはこの環境では利用できません",
        )
    except MaiTranscribeAdapterError:
        return _error_response(502, "AUDIO_TRANSCRIBE_FAILED", "音声文字起こしに失敗しました")

    guard_response = await _reject_if_unsafe(result.transcript, source="tool")
    if guard_response is not None:
        return guard_response
    try:
        record = await create_audio_source(
            owner_id=identity["user_id"],
            conversation_id=conversation_id,
            title=title,
            transcript=result.transcript,
            metadata=_build_audio_metadata(body),
        )
    except SourceIngestionQuotaExceededError:
        return _quota_error_response()
    except SourceIngestionLimitExceededError:
        return _limit_error_response("SOURCE_TEXT_TOO_LARGE", "文字起こしテキストが上限を超えています")
    return JSONResponse(status_code=201, content={"source": build_public_source_payload(record)})


@router.post("/pdf")
async def create_pdf_source_endpoint(
    request: Request,
    file: UploadFile,
    conversation_id: str | None = Form(default=None),
) -> JSONResponse:
    """PDF を解析してレビュー待ちソースとして登録する。"""
    return await create_pdf_source_from_upload(request, file, conversation_id)


@router.get("")
async def list_sources_endpoint(request: Request, conversation_id: str | None = None) -> JSONResponse:
    """owner scope 内のソース一覧を返す。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    identity = _owner_identity_or_error(request, expected_tenant_id=get_settings()["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity
    sanitized_conversation_id = sanitize_source_text(conversation_id, max_length=_MAX_CONVERSATION_ID_LENGTH) or None
    records = await list_sources(owner_id=identity["user_id"], conversation_id=sanitized_conversation_id)
    return JSONResponse(content={"sources": [build_public_source_payload(record) for record in records]})


@router.get("/limits")
async def source_limits_endpoint() -> JSONResponse:
    """機密情報を含まない source ingestion の有効状態と運用上限を返す。"""
    return JSONResponse(content=_source_limits_payload())


@router.get("/{source_id}")
async def get_source_endpoint(source_id: str, request: Request) -> JSONResponse:
    """owner scope 内のソース詳細を返す。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    identity = _owner_identity_or_error(request, expected_tenant_id=get_settings()["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity
    sanitized_source_id = sanitize_source_text(source_id, max_length=100)
    record = await get_source(owner_id=identity["user_id"], source_id=sanitized_source_id)
    if record is None:
        return _error_response(404, "SOURCE_NOT_FOUND", "source not found")
    return JSONResponse(content={"source": build_public_source_payload(record)})


@router.post("/{source_id}/review")
async def review_source_endpoint(source_id: str, request: Request, body: SourceReviewRequest) -> JSONResponse:
    """ユーザー確認済みの要約だけをチャット文脈に使える状態へ遷移する。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    identity = _owner_identity_or_error(request, expected_tenant_id=get_settings()["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity
    sanitized_source_id = sanitize_source_text(source_id, max_length=100)
    existing = await get_source(owner_id=identity["user_id"], source_id=sanitized_source_id)
    if existing is None:
        return _error_response(404, "SOURCE_NOT_FOUND", "source not found")

    summary = body.summary if body.summary is not None else existing.summary
    if body.approved:
        guard_response = await _reject_if_unsafe(summary, source="tool")
        if guard_response is not None:
            return guard_response

    record = await review_source(
        owner_id=identity["user_id"],
        source_id=sanitized_source_id,
        approved=body.approved,
        summary=summary,
    )
    if record is None:
        return _error_response(404, "SOURCE_NOT_FOUND", "source not found")
    return JSONResponse(content={"source": build_public_source_payload(record)})


@router.delete("/{source_id}")
async def delete_source_endpoint(source_id: str, request: Request) -> Response:
    """owner scope 内のソースを削除する。"""
    if not _source_ingestion_enabled():
        return _error_response(503, "SOURCE_INGESTION_DISABLED", "ソース取り込み API は無効です")
    identity = _owner_identity_or_error(request, expected_tenant_id=get_settings()["entra_tenant_id"])
    if isinstance(identity, JSONResponse):
        return identity
    sanitized_source_id = sanitize_source_text(source_id, max_length=100)
    deleted = await delete_source(owner_id=identity["user_id"], source_id=sanitized_source_id)
    if not deleted:
        return _error_response(404, "SOURCE_NOT_FOUND", "source not found")
    return Response(status_code=204)
