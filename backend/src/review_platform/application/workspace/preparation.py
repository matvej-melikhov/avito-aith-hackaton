"""Immutable draft snapshots shared by optional self-review and final submission."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    require_roles,
    revision,
    row,
    student_epoch_valid,
)
from review_platform.application.workspace.ports import (
    ArtifactPreparer,
    DefinitivePreparationFailure,
)
from review_platform.application.workspace.source_policy import require_publication_source
from review_platform.contracts.workspace import PreparationView
from review_platform.infrastructure.db.models import (
    CourseMembership,
    CourseRunHomework,
    OrganizationMembership,
)
from review_platform.infrastructure.db.models.workspace import (
    ArtifactPreparation,
    WorkDraft,
    WorkspaceArtifact,
)
from review_platform.infrastructure.object_storage.s3 import S3ObjectStorage


class PreparationService:
    def __init__(self, runtime: FoundationRuntime, session: AsyncSession):
        self.runtime, self.session = runtime, session

    async def start(self, actor: RequestActor, identity: UUID, expected: int) -> PreparationView:
        user = require_roles(actor, "student")
        draft = await row(self.session, WorkDraft, actor.organization_id, identity)
        if draft.student_id != user:
            raise WorkspaceFailure("not_found", "Черновик недоступен.", 404)
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, draft.publication_id, lock=True
        )
        await course_scope(self.session, actor, publication.course_run_id, write=True)
        draft = await row(self.session, WorkDraft, actor.organization_id, identity, lock=True)
        revision(draft.revision, expected)
        await require_publication_source(
            self.session, publication, draft.artifact_url, draft.upload_id
        )
        if not publication.current_publication_id:
            raise WorkspaceFailure("not_published", "Задание ещё не опубликовано.")
        current = await self.session.scalar(
            select(ArtifactPreparation)
            .where(
                ArtifactPreparation.organization_id == actor.organization_id,
                ArtifactPreparation.draft_id == identity,
                ArtifactPreparation.draft_revision == expected,
            )
            .with_for_update()
        )
        if current is None:
            if not draft.upload_id and not self.runtime.settings.workspace_enabled:
                raise WorkspaceFailure(
                    "configuration_required", "Подготовка ссылок ещё не настроена.", 503
                )
            current = ArtifactPreparation(
                id=self.runtime.id_factory(),
                organization_id=actor.organization_id,
                draft_id=identity,
                draft_revision=expected,
                source_url=draft.artifact_url,
                artifact_id=draft.upload_id,
                status="succeeded" if draft.upload_id else "pending",
            )
            self.session.add(current)
            await self.session.flush()
        elif current.status == "failed":
            current.status, current.error_code, current.attempts = "pending", None, 0
        return await self.view(current)

    async def view(self, value: ArtifactPreparation) -> PreparationView:
        artifact = (
            await row(self.session, WorkspaceArtifact, value.organization_id, value.artifact_id)
            if value.artifact_id
            else None
        )
        return PreparationView(
            id=value.id,
            draft_revision=value.draft_revision,
            status=cast(Literal["pending", "processing", "succeeded", "failed"], value.status),
            artifact_id=value.artifact_id,
            filename=artifact.filename if artifact else None,
            error_code=value.error_code,
        )

    async def get(self, actor: RequestActor, identity: UUID) -> PreparationView:
        value = await row(self.session, ArtifactPreparation, actor.organization_id, identity)
        draft = await row(self.session, WorkDraft, actor.organization_id, value.draft_id)
        if draft.student_id != actor.user_id:
            raise WorkspaceFailure("not_found", "Подготовка недоступна.", 404)
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, draft.publication_id
        )
        await course_scope(self.session, actor, publication.course_run_id)
        return await self.view(value)


async def prepared_artifact(session: AsyncSession, draft: WorkDraft) -> UUID:
    publication = await row(session, CourseRunHomework, draft.organization_id, draft.publication_id)
    await require_publication_source(session, publication, draft.artifact_url, draft.upload_id)
    if draft.upload_id:
        return draft.upload_id
    value = await session.scalar(
        select(ArtifactPreparation).where(
            ArtifactPreparation.organization_id == draft.organization_id,
            ArtifactPreparation.draft_id == draft.id,
            ArtifactPreparation.draft_revision == draft.revision,
            ArtifactPreparation.status == "succeeded",
        )
    )
    if value is None or value.artifact_id is None:
        raise WorkspaceFailure("artifact_not_prepared", "Сначала дождитесь подготовки работы.", 409)
    return value.artifact_id


class PreparationWorker:
    def __init__(
        self, runtime: FoundationRuntime, preparer: ArtifactPreparer, storage: S3ObjectStorage
    ):
        self.runtime, self.preparer, self.storage = runtime, preparer, storage

    async def tick(self) -> bool:
        now, token = self.runtime.clock(), self.runtime.id_factory()
        async with self.runtime.transaction() as session:
            value = await session.scalar(
                select(ArtifactPreparation)
                .where(
                    ArtifactPreparation.status.in_(["pending", "processing"]),
                    or_(
                        ArtifactPreparation.lease_until.is_(None),
                        ArtifactPreparation.lease_until <= now,
                    ),
                )
                .order_by(ArtifactPreparation.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if value is None:
                return False
            value.status, value.lease_token, value.lease_until = (
                "processing",
                token,
                now + timedelta(seconds=60),
            )
            value.attempts += 1
            identity, org, source = value.id, value.organization_id, value.source_url
            draft = await row(session, WorkDraft, org, value.draft_id)
            owner = draft.student_id
            publication = await row(session, CourseRunHomework, org, draft.publication_id)
            membership = await session.scalar(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == org,
                    OrganizationMembership.user_id == owner,
                    OrganizationMembership.status == "active",
                )
            )
            enrolled = await session.scalar(
                select(CourseMembership.id).where(
                    CourseMembership.organization_id == org,
                    CourseMembership.user_id == owner,
                    CourseMembership.course_run_id == publication.course_run_id,
                    CourseMembership.status == "active",
                    CourseMembership.kind == "student",
                )
            )
            if membership is None or "student" not in membership.roles or not enrolled:
                value.status, value.error_code = "failed", "access_revoked"
                return True
            try:
                await require_publication_source(
                    session, publication, source, None
                )
            except WorkspaceFailure as error:
                value.status, value.error_code = "failed", error.code
                return True
            membership_revision, auth_epoch, run_id = (
                membership.revision,
                membership.auth_epoch,
                publication.course_run_id,
            )
        try:
            prepared = await self.preparer.prepare(source)
            if not prepared.content:
                raise DefinitivePreparationFailure("Empty artifact")
            stored = await asyncio.to_thread(
                self.storage.upload,
                organization_id=str(org),
                artifact_version_id=str(identity),
                source=[prepared.content],
                media_type=prepared.media_type,
                max_bytes=self.runtime.settings.workspace_snapshot_max_bytes,
            )
            async with self.runtime.transaction() as session:
                value = await row(session, ArtifactPreparation, org, identity, lock=True)
                if value.lease_token != token:
                    return True
                if not await student_epoch_valid(
                    session, org, owner, run_id, membership_revision, auth_epoch
                ):
                    value.status, value.error_code, value.lease_until = (
                        "failed",
                        "access_revoked",
                        None,
                    )
                    return True
                session.add(
                    WorkspaceArtifact(
                        id=identity,
                        organization_id=org,
                        owner_id=owner,
                        filename=prepared.filename,
                        media_type=prepared.media_type,
                        object_key=stored.key,
                        digest=stored.content_digest,
                        byte_size=stored.byte_size,
                        private=False,
                        provenance={
                            "source_kind": prepared.source_kind,
                            "source_url": source,
                            "source_version": prepared.source_version,
                            "fixture": self.runtime.settings.workspace_fixtures,
                        },
                    )
                )
                await session.flush()
                value.artifact_id, value.status, value.lease_until = identity, "succeeded", None
        except (DefinitivePreparationFailure, OSError, httpx.HTTPError) as error:
            async with self.runtime.transaction() as session:
                value = await row(session, ArtifactPreparation, org, identity, lock=True)
                if value.lease_token != token:
                    return True
                terminal = (
                    isinstance(error, DefinitivePreparationFailure)
                    or value.attempts >= self.runtime.settings.workspace_max_attempts
                )
                value.status = "failed" if terminal else "pending"
                value.error_code = (
                    error.code
                    if isinstance(error, DefinitivePreparationFailure)
                    else "source_unavailable"
                )
                value.lease_until = (
                    None if terminal else self.runtime.clock() + timedelta(seconds=10)
                )
        return True
