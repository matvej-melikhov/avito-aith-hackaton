"""Concrete tenant-safe AI review start composition."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewCredentialMismatch,
    AIReviewInputSnapshot,
    AIReviewStartArchived,
    AIReviewStartService,
    SignedArtifactGrant,
)
from review_platform.contracts.ai_review import (
    AIArtifactEnvelope,
    AICriteria,
    AIHomework,
)
from review_platform.contracts.registry import CONTRACT_VERSION
from review_platform.domain.primitives import canonical_json_sha256, require_utc
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.homework import (
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.submission import (
    ArtifactReference,
    ArtifactVersion,
)
from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewRepository
from review_platform.infrastructure.tasks.ai_review import SqlAIReviewScheduler


class SqlAIReviewInputRepository:
    """Build the immutable T105 input from one locked ReviewIteration."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_input_snapshot(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> AIReviewInputSnapshot | None:
        if _session(transaction) is not self._session:
            raise TypeError("AI input repository transaction mismatched")
        iteration = await self._session.scalar(
            select(ReviewIteration)
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.id == review_iteration_id,
                ReviewIteration.revision == expected_revision,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if iteration is None:
            return None
        review_case = await self._session.scalar(
            select(ReviewCase)
            .where(
                ReviewCase.organization_id == organization_id,
                ReviewCase.id == iteration.review_case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        artifact_row = (
            await self._session.execute(
                select(ArtifactVersion, ArtifactReference)
                .join(
                    ArtifactReference,
                    (ArtifactReference.organization_id == ArtifactVersion.organization_id)
                    & (ArtifactReference.id == ArtifactVersion.artifact_reference_id),
                )
                .where(
                    ArtifactVersion.organization_id == organization_id,
                    ArtifactVersion.id == iteration.artifact_version_id,
                )
            )
        ).one_or_none()
        homework_row = (
            await self._session.execute(
                select(HomeworkVersion, Homework)
                .join(
                    Homework,
                    (Homework.organization_id == HomeworkVersion.organization_id)
                    & (Homework.id == HomeworkVersion.homework_id),
                )
                .where(
                    HomeworkVersion.organization_id == organization_id,
                    HomeworkVersion.id == iteration.homework_version_id,
                    HomeworkVersion.homework_id == iteration.homework_id,
                )
            )
        ).one_or_none()
        criterion_set = await self._session.scalar(
            select(CriterionSet).where(
                CriterionSet.organization_id == organization_id,
                CriterionSet.id == iteration.criterion_set_id,
                CriterionSet.homework_version_id == iteration.homework_version_id,
            )
        )
        criteria = (
            await self._session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == organization_id,
                    Criterion.criterion_set_id == iteration.criterion_set_id,
                    Criterion.active.is_(True),
                )
                .order_by(Criterion.position, Criterion.id)
            )
        ).all()
        if (
            review_case is None
            or artifact_row is None
            or homework_row is None
            or criterion_set is None
            or not criteria
        ):
            return None
        artifact, reference = artifact_row
        homework_version, homework = homework_row
        criterion_payload = [
            {
                "key": item.stable_key,
                "title": item.title,
                "description": item.description,
                "max_points": _decimal_text(item.max_points),
                "position": item.position,
            }
            for item in criteria
        ]
        homework_digest = canonical_json_sha256(
            {
                "schema_version": CONTRACT_VERSION,
                "student_text": homework_version.student_text,
                "max_score": _decimal_text(homework_version.max_score),
                "artifact_kinds": list(homework_version.artifact_kinds),
                "estimated_review_minutes": homework_version.estimated_review_minutes,
                "criteria": criterion_payload,
            }
        )
        criteria_digest = canonical_json_sha256(
            {"schema_version": CONTRACT_VERSION, "criteria": criterion_payload}
        )
        artifact_envelope = AIArtifactEnvelope.model_validate(
            {
                "contract_version": CONTRACT_VERSION,
                "organization_id": organization_id,
                "artifact_reference_id": artifact.artifact_reference_id,
                "artifact_version_id": artifact.id,
                "provider": reference.provider,
                "provider_version": artifact.provider_version,
                "content_digest": artifact.content_digest,
                "captured_at": require_utc(artifact.captured_at),
                "object": {
                    "key": artifact.object_key,
                    "media_type": artifact.media_type,
                    "byte_size": artifact.byte_size,
                },
                "metadata": cast(dict[str, JsonValue], artifact.artifact_metadata),
            }
        )
        ai_homework = AIHomework(
            version_id=homework_version.id,
            title=homework.title,
            student_text=homework_version.student_text,
            digest=homework_digest,
        )
        ai_criteria = AICriteria.model_validate(
            {
                "set_id": criterion_set.id,
                "digest": criteria_digest,
                "items": [
                    {
                        "id": item.id,
                        "key": item.stable_key,
                        "title": item.title,
                        "description": item.description,
                        "max_points": float(item.max_points),
                    }
                    for item in criteria
                ],
            }
        )
        return AIReviewInputSnapshot(
            organization_id=organization_id,
            review_iteration_id=iteration.id,
            review_iteration_revision=iteration.revision,
            is_current=review_case.current_iteration_id == iteration.id,
            course_run_id=iteration.course_run_id,
            submission_version_id=iteration.submission_version_id,
            artifact=artifact_envelope,
            homework=ai_homework,
            criteria=ai_criteria,
            current_human_revision_id=iteration.current_revision_id,
        )


class SqlAIComponentCredentialRepository:
    async def require_exact_active(
        self,
        binding: AIComponentCredentialBinding,
        *,
        transaction: object,
    ) -> AIComponentCredentialBinding:
        session = _session(transaction)
        row = await session.scalar(
            select(ExternalCredential)
            .where(
                ExternalCredential.organization_id == binding.organization_id,
                ExternalCredential.id == binding.credential_binding_id,
                ExternalCredential.binding_version == binding.credential_binding_version,
                ExternalCredential.provider == "ai_review",
                ExternalCredential.status == "active",
                ExternalCredential.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if row is None:
            raise AIReviewCredentialMismatch("exact active AI component credential was not found")
        return binding


class SqlAIReviewArchivedGuard:
    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        discovered = await session.scalar(
            select(CourseRun).where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
            )
        )
        if discovered is None:
            raise AIReviewStartArchived("tenant CourseRun was not found")
        course = await session.scalar(
            select(Course)
            .where(
                Course.organization_id == organization_id,
                Course.id == discovered.course_id,
            )
            .with_for_update()
        )
        locked_run = await session.scalar(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
                CourseRun.course_id == discovered.course_id,
            )
            .with_for_update()
        )
        if (
            course is None
            or locked_run is None
            or course.status == "archived"
            or locked_run.status == "archived"
        ):
            raise AIReviewStartArchived("archived Course or CourseRun cannot start AI review")


class RuntimeArtifactGrantSigner:
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._runtime = runtime

    def sign_read(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        object_key: str,
        expires_in_seconds: int,
        now: datetime,
    ) -> SignedArtifactGrant:
        selected_at = require_utc(now)
        if expires_in_seconds != self._runtime.settings.ai_signed_url_ttl_seconds:
            raise ValueError("signed artifact TTL differs from server configuration")
        return SignedArtifactGrant(
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
            object_key=object_key,
            url=self._runtime.sign_artifact_read(
                organization_id=str(organization_id),
                artifact_version_id=str(artifact_version_id),
                requested_by_organization_id=str(organization_id),
                object_key=object_key,
            ),
            expires_at=selected_at + timedelta(seconds=expires_in_seconds),
        )


def build_sql_ai_review_start_service_factory(
    runtime: FoundationRuntime,
) -> Callable[[AsyncSession], AIReviewStartService]:
    """Return one explicit per-transaction T105 service factory."""

    def build(session: AsyncSession) -> AIReviewStartService:
        return AIReviewStartService(
            input_repository=SqlAIReviewInputRepository(session),
            run_repository=AIReviewRepository(session),
            credential_repository=SqlAIComponentCredentialRepository(),
            archived_guard=SqlAIReviewArchivedGuard(),
            grant_signer=RuntimeArtifactGrantSigner(runtime),
            scheduler=SqlAIReviewScheduler(
                id_factory=runtime.id_factory,
                clock=runtime.clock,
                max_attempts=runtime.settings.provider_max_attempts,
            ),
            authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
            audit=AuditRecorder(
                SqlAppendOnlyAuditRepository(),
                event_id_factory=runtime.id_factory,
                clock=runtime.clock,
            ),
            signed_url_ttl_seconds=runtime.settings.ai_signed_url_ttl_seconds,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        )

    return build


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise TypeError("AI review composition requires caller-owned AsyncSession")
    return transaction


__all__ = [
    "RuntimeArtifactGrantSigner",
    "SqlAIComponentCredentialRepository",
    "SqlAIReviewArchivedGuard",
    "SqlAIReviewInputRepository",
    "build_sql_ai_review_start_service_factory",
]
