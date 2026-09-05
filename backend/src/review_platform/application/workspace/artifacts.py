"""Bounded upload and authenticated downloads for public drafts/private references."""

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from pathlib import PurePath
from zipfile import BadZipFile, ZipFile

from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import WorkspaceFailure, require_roles
from review_platform.contracts.workspace import UploadInput, UploadView
from review_platform.infrastructure.db.models.workspace import WorkspaceArtifact


def validate_upload(payload: UploadInput) -> bytes:
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except ValueError as exc:
        raise WorkspaceFailure("invalid_file", "Неверное содержимое файла.", 422) from exc
    if not 0 < len(data) <= 10_000_000:
        raise WorkspaceFailure("file_too_large", "Допустим файл до 10 МБ.", 413)
    if PurePath(payload.filename).name != payload.filename or "\\" in payload.filename:
        raise WorkspaceFailure("invalid_filename", "Неверное имя файла.", 422)
    if payload.media_type == "text/markdown":
        try:
            data.decode("utf-8")
        except UnicodeError as exc:
            raise WorkspaceFailure("invalid_file", "Markdown должен быть в UTF-8.", 422) from exc
    elif payload.media_type == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise WorkspaceFailure("invalid_file", "Файл не является PDF.", 422)
    else:
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if sum(i.file_size for i in entries) > 32_000_000 or len(entries) > 1000:
                    raise WorkspaceFailure(
                        "invalid_file", "Слишком большой распакованный DOCX.", 413
                    )
                if (
                    "[Content_Types].xml" not in archive.namelist()
                    or "word/document.xml" not in archive.namelist()
                ):
                    raise WorkspaceFailure("invalid_file", "Файл не является DOCX.", 422)
                if any(
                    i.flag_bits & 1
                    or ".." in PurePath(i.filename).parts
                    or i.filename.startswith("/")
                    for i in entries
                ):
                    raise WorkspaceFailure("invalid_file", "Неподдерживаемый DOCX.", 422)
        except BadZipFile as exc:
            raise WorkspaceFailure("invalid_file", "Повреждённый DOCX.", 422) from exc
    return data


async def upload(
    runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor, payload: UploadInput
) -> UploadView:
    user_id = require_roles(actor, "student", "reviewer", "methodologist")
    if payload.private:
        require_roles(actor, "methodologist")
    data = validate_upload(payload)
    identity = runtime.id_factory()
    stored = await asyncio.to_thread(
        runtime.object_storage.upload,
        organization_id=str(actor.organization_id),
        artifact_version_id=str(identity),
        source=[data],
        media_type=payload.media_type,
        max_bytes=10_000_000,
    )
    session.add(
        WorkspaceArtifact(
            id=identity,
            organization_id=actor.organization_id,
            owner_id=user_id,
            filename=payload.filename,
            media_type=payload.media_type,
            object_key=stored.key,
            digest=stored.content_digest,
            byte_size=len(data),
            private=payload.private,
        )
    )
    await session.flush()
    return UploadView(
        id=identity,
        filename=payload.filename,
        media_type=payload.media_type,
        byte_size=len(data),
        digest=stored.content_digest,
    )
