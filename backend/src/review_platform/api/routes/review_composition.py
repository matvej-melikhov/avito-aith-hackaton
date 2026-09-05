"""Concrete per-request composition for the frozen US5 review routes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.projections.review_detail import (
    ArtifactDownloadGrant,
    ReviewDetailProjectionError,
    read_review_detail,
)
from review_platform.application.request_context import RequestActor
from review_platform.application.services.publication_requests import (
    PublicationRequestError,
    PublicationRequestService,
)
from review_platform.application.services.recommendations import (
    RecommendationService,
    RecommendationServiceError,
)
from review_platform.application.services.review_corrections import (
    CreateReviewCorrectionCommand,
    ReviewCorrectionError,
    ReviewCorrectionService,
)
from review_platform.application.services.review_drafts import (
    ReviewDecisionInput,
    ReviewDraftError,
    ReviewDraftService,
    ReviewNoteInput,
)
from review_platform.application.services.review_publication import (
    ExternalDeliveryIntent,
    PublishReviewCommand,
    ReviewDeliveryScheduler,
    ReviewPublicationError,
    ReviewPublicationService,
)
from review_platform.application.services.review_requirements import (
    MigrateReviewRequirementsCommand,
    ReviewRequirementsError,
    ReviewRequirementsMigrationService,
)
from review_platform.application.services.review_responsibility import (
    ReviewResponsibilityError,
    ReviewResponsibilityService,
)
from review_platform.application.services.review_revisions import (
    ReviewRevisionService,
    SaveReviewRevisionCommand,
)
from review_platform.application.services.reviewer_availability import (
    ReviewerAvailabilityError,
    ReviewerAvailabilityService,
)
from review_platform.application.services.reviewer_course_selections import (
    ReviewerCourseSelectionService,
    ReviewerSelectionError,
)
from review_platform.contracts.commands import (
    CreateReviewCorrectionPayload,
    MigrateReviewRequirementsPayload,
    PublishReviewPayload,
    RecordReviewResponsibilityPayload,
    RequestReviewPublicationPayload,
    SaveReviewRevisionPayload,
    SetReviewerAvailabilityPayload,
    SetReviewerCourseSelectionPayload,
    WireCommand,
)
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.repositories.publications import (
    PublicationRepositoryError,
    SqlPublicationRepository,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewRevisionRepositoryError,
    SqlReviewRevisionRepository,
)
from review_platform.infrastructure.db.repositories.review_work import (
    ReviewWorkRepositoryError,
    SqlReviewWorkRepository,
)


class ReviewCompositionError(RuntimeError):
    """A typed US5 service rejected concrete route dispatch."""


class _UnavailableDeliveryScheduler(ReviewDeliveryScheduler):
    """T141 owns durable delivery intent scheduling; never drop a required intent."""

    async def schedule(
        self,
        intent: ExternalDeliveryIntent,
        *,
        transaction: object,
    ) -> None:
        del intent, transaction
        raise ReviewCompositionError(
            "required delivery scheduling is unavailable until T141 is composed"
        )


async def dispatch_review_mutation(
    *,
    runtime: FoundationRuntime,
    actor: RequestActor,
    command: WireCommand,
    transaction: AsyncSession,
) -> Mapping[str, Any] | None:
    """Map one already validated exact command to its implemented US5 service."""

    try:
        return await _dispatch_review_mutation(
            runtime=runtime,
            actor=actor,
            command=command,
            transaction=transaction,
        )
    except _COMPOSITION_ERRORS as error:
        raise ReviewCompositionError(str(error)) from error
    except (IntegrityError, OperationalError) as error:
        if _mysql_error_code(error) in _MYSQL_CONCURRENCY_CODES:
            raise ReviewCompositionError("concurrent review mutation conflict") from error
        raise


async def _dispatch_review_mutation(
    *,
    runtime: FoundationRuntime,
    actor: RequestActor,
    command: WireCommand,
    transaction: AsyncSession,
) -> Mapping[str, Any] | None:
    authorizer = Authorizer(runtime.user_auth_guard, clock=runtime.clock)
    audit = AuditRecorder(
        SqlAppendOnlyAuditRepository(),
        event_id_factory=runtime.id_factory,
        clock=runtime.clock,
    )
    work = SqlReviewWorkRepository(id_factory=runtime.id_factory)
    publications = SqlPublicationRepository()
    revisions = SqlReviewRevisionRepository(transaction)
    trace_id = runtime.id_factory()
    name = str(command.command_name)

    if name == "set_reviewer_course_selection":
        selection_payload = cast(SetReviewerCourseSelectionPayload, command.payload)
        await ReviewerCourseSelectionService(
            repository=work,
            authorizer=authorizer,
            audit=audit,
        ).replace(
            transaction=transaction,
            organization_id=actor.organization_id,
            membership_id=command.target_id,
            expected_membership_revision=command.expected_revision,
            course_run_ids=selection_payload.course_run_ids,
            actor=actor,
            request_id=command.request_id,
            trace_id=trace_id,
        )
        return None

    if name == "set_reviewer_availability":
        availability_payload = cast(SetReviewerAvailabilityPayload, command.payload)
        availability_result = await ReviewerAvailabilityService(
            repository=work,
            authorizer=authorizer,
            audit=audit,
        ).update(
            transaction=transaction,
            organization_id=actor.organization_id,
            membership_id=command.target_id,
            expected_membership_revision=command.expected_revision,
            planned_minutes=availability_payload.planned_minutes,
            until_at=availability_payload.until_at,
            actor=actor,
            request_id=command.request_id,
            trace_id=trace_id,
        )
        return {
            "id": str(availability_result.availability_plan_id),
            "revision": availability_result.availability_revision,
        }

    if name == "record_review_responsibility":
        responsibility_payload = cast(
            RecordReviewResponsibilityPayload,
            command.payload,
        )
        review_case_id = await _review_case_id(
            transaction,
            organization_id=actor.organization_id,
            review_iteration_id=command.target_id,
            expected_revision=command.expected_revision,
        )
        responsibility_result = await ReviewResponsibilityService(
            repository=work,
            authorizer=authorizer,
            audit=audit,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        ).record(
            transaction=transaction,
            organization_id=actor.organization_id,
            review_case_id=review_case_id,
            review_iteration_id=command.target_id,
            expected_review_iteration_revision=command.expected_revision,
            action=responsibility_payload.action,
            actor=actor,
            request_id=command.request_id,
            trace_id=trace_id,
        )
        return {"id": str(responsibility_result.event_id), "revision": 0}

    if name == "save_review_revision":
        revision_payload = cast(SaveReviewRevisionPayload, command.payload)
        current_revision_id = await _current_revision_id(
            transaction,
            organization_id=actor.organization_id,
            review_iteration_id=command.target_id,
            expected_revision=command.expected_revision,
        )
        revision_result = await ReviewRevisionService(
            ReviewDraftService(
                repository=revisions,
                authorizer=authorizer,
                audit=audit,
                id_factory=runtime.id_factory,
                clock=runtime.clock,
            )
        ).save(
            SaveReviewRevisionCommand(
                organization_id=actor.organization_id,
                review_iteration_id=command.target_id,
                expected_iteration_revision=command.expected_revision,
                expected_current_revision_id=current_revision_id,
                feedback=revision_payload.feedback,
                decisions=tuple(
                    ReviewDecisionInput(
                        criterion_id=item.criterion_id,
                        points=Decimal(str(item.points)),
                        decision=item.decision,
                        reason=item.reason,
                        evidence_ids=tuple(str(value) for value in item.evidence_ids),
                    )
                    for item in revision_payload.criterion_decisions
                ),
                notes=tuple(
                    ReviewNoteInput(text=item.text, criterion_id=item.criterion_id)
                    for item in revision_payload.review_notes
                ),
                request_id=command.request_id,
                trace_id=trace_id,
            ),
            actor=actor,
            transaction=transaction,
        )
        return {
            "review_revision_id": str(revision_result.review_revision_id),
            "review_iteration_id": str(revision_result.review_iteration_id),
            "review_iteration_revision": revision_result.review_iteration_revision,
        }

    if name == "migrate_review_requirements":
        migration_payload = cast(MigrateReviewRequirementsPayload, command.payload)
        migration_result = await ReviewRequirementsMigrationService(
            repository=publications,
            revisions=revisions,
            authorizer=authorizer,
            audit=audit,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        ).migrate(
            MigrateReviewRequirementsCommand(
                organization_id=actor.organization_id,
                predecessor_iteration_id=command.target_id,
                expected_predecessor_revision=command.expected_revision,
                target_homework_version_id=migration_payload.homework_version_id,
                target_criterion_set_id=migration_payload.criterion_set_id,
                request_id=command.request_id,
                trace_id=trace_id,
            ),
            actor=actor,
            transaction=transaction,
        )
        return {
            "review_iteration_id": str(migration_result.successor_iteration_id),
            "review_case_id": str(migration_result.review_case_id),
            "revision": migration_result.successor_iteration_revision,
        }

    if name == "create_review_correction":
        correction_payload = cast(CreateReviewCorrectionPayload, command.payload)
        correction_result = await ReviewCorrectionService(
            successors=publications,
            publications=publications,
            revisions=revisions,
            authorizer=authorizer,
            audit=audit,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        ).create(
            CreateReviewCorrectionCommand(
                organization_id=actor.organization_id,
                predecessor_iteration_id=command.target_id,
                expected_predecessor_revision=command.expected_revision,
                published_review_revision_id=(
                    correction_payload.published_review_revision_id
                ),
                reason=correction_payload.reason,
                request_id=command.request_id,
                trace_id=trace_id,
            ),
            actor=actor,
            transaction=transaction,
        )
        return {
            "review_iteration_id": str(correction_result.successor_iteration_id),
            "review_case_id": str(correction_result.review_case_id),
            "revision": correction_result.successor_iteration_revision,
        }

    if name == "request_review_publication":
        request_payload = cast(RequestReviewPublicationPayload, command.payload)
        request_result = await PublicationRequestService(
            repository=publications,
            authorizer=authorizer,
            audit=audit,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        ).request(
            transaction=transaction,
            organization_id=actor.organization_id,
            review_iteration_id=command.target_id,
            expected_iteration_revision=command.expected_revision,
            review_revision_id=request_payload.review_revision_id,
            expires_at=request_payload.expires_at,
            idempotency_key=command.idempotency_key,
            actor=actor,
            request_id=command.request_id,
            trace_id=trace_id,
        )
        stored = request_result.request
        return {
            "id": str(stored.publication_request_id),
            "review_revision_id": str(stored.review_revision_id),
            "status": stored.status,
            "expires_at": stored.expires_at.isoformat(),
            "revision": stored.revision,
        }

    if name == "publish_review":
        publication_payload = cast(PublishReviewPayload, command.payload)
        publication_result = await ReviewPublicationService(
            repository=publications,
            scheduler=_UnavailableDeliveryScheduler(),
            authorizer=authorizer,
            audit=audit,
            id_factory=runtime.id_factory,
            clock=runtime.clock,
        ).publish(
            PublishReviewCommand(
                organization_id=actor.organization_id,
                review_iteration_id=command.target_id,
                expected_iteration_revision=command.expected_revision,
                expected_current_revision_id=publication_payload.review_revision_id,
                publication_request_id=publication_payload.publication_request_id,
                request_id=command.request_id,
                trace_id=trace_id,
            ),
            actor=actor,
            transaction=transaction,
        )
        assert actor.user_id is not None
        return {
            "id": str(publication_result.publication_id),
            "review_revision_id": str(publication_result.review_revision_id),
            "published_by": str(actor.user_id),
            "publication_request_id": (
                str(publication_result.publication_request_id)
                if publication_result.publication_request_id is not None
                else None
            ),
            "status": "published",
            "deliveries": [],
        }

    raise ReviewCompositionError(f"unsupported concrete review command: {name}")


async def read_recommendation(
    *,
    runtime: FoundationRuntime,
    actor: RequestActor,
    course_run_id: UUID,
    transaction: AsyncSession,
) -> Mapping[str, Any] | None:
    try:
        result = await RecommendationService(
            repository=SqlReviewWorkRepository(id_factory=runtime.id_factory),
            authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
        ).recommend_next(
            organization_id=actor.organization_id,
            course_run_id=course_run_id,
            actor=actor,
            now=runtime.clock(),
            transaction=transaction,
        )
    except _COMPOSITION_ERRORS as error:
        raise ReviewCompositionError(str(error)) from error
    if result is None:
        return None
    return {
        "review_case_id": str(result.review_case_id),
        "review_case_revision": result.review_case_revision,
        "submission_version_id": str(result.submission_version_id),
        "reason": list(result.reason),
    }


async def read_review_detail_response(
    *,
    runtime: FoundationRuntime,
    actor: RequestActor,
    review_iteration_id: UUID,
    transaction: AsyncSession,
) -> Mapping[str, Any]:
    try:
        await Authorizer(runtime.user_auth_guard, clock=runtime.clock).authorize(
            actor=actor,
            organization_id=actor.organization_id,
            policy=AuthorizationPolicy(
                required_roles=frozenset({"reviewer", "methodologist"}),
                required_scopes=frozenset({"reviews:read"}),
            ),
        )
        now = runtime.clock()

        def sign(
            organization_id: UUID,
            artifact_version_id: UUID,
        ) -> ArtifactDownloadGrant:
            return ArtifactDownloadGrant(
                organization_id=organization_id,
                artifact_version_id=artifact_version_id,
                url=runtime.sign_artifact_read(
                    organization_id=str(organization_id),
                    artifact_version_id=str(artifact_version_id),
                    requested_by_organization_id=str(actor.organization_id),
                ),
                expires_at=now
                + timedelta(seconds=runtime.settings.ai_signed_url_ttl_seconds),
            )

        result = await read_review_detail(
            transaction,
            organization_id=actor.organization_id,
            review_iteration_id=review_iteration_id,
            sign_artifact_download=sign,
        )
    except (ReviewDetailProjectionError, ValueError) as error:
        raise ReviewCompositionError(str(error)) from error
    if result is None:
        raise ReviewCompositionError("tenant ReviewIteration was not found")
    return result


async def _review_case_id(
    session: AsyncSession,
    *,
    organization_id: UUID,
    review_iteration_id: UUID,
    expected_revision: int,
) -> UUID:
    value = await session.scalar(
        select(ReviewIteration.review_case_id).where(
            ReviewIteration.organization_id == organization_id,
            ReviewIteration.id == review_iteration_id,
            ReviewIteration.revision == expected_revision,
        )
    )
    if value is None:
        raise ReviewCompositionError("ReviewIteration is missing or revision is stale")
    return value


async def _current_revision_id(
    session: AsyncSession,
    *,
    organization_id: UUID,
    review_iteration_id: UUID,
    expected_revision: int,
) -> UUID | None:
    row = (
        await session.execute(
            select(ReviewIteration.id, ReviewIteration.current_revision_id).where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.id == review_iteration_id,
                ReviewIteration.revision == expected_revision,
            )
        )
    ).one_or_none()
    if row is None:
        raise ReviewCompositionError("ReviewIteration is missing or revision is stale")
    return cast(UUID | None, row.current_revision_id)


_COMPOSITION_ERRORS = (
    PublicationRepositoryError,
    PublicationRequestError,
    RecommendationServiceError,
    ReviewCorrectionError,
    ReviewDraftError,
    ReviewPublicationError,
    ReviewRequirementsError,
    ReviewResponsibilityError,
    ReviewRevisionRepositoryError,
    ReviewerAvailabilityError,
    ReviewerSelectionError,
    ReviewWorkRepositoryError,
)

_MYSQL_CONCURRENCY_CODES = frozenset({1062, 1205, 1213})


def _mysql_error_code(error: IntegrityError | OperationalError) -> int | None:
    arguments = getattr(error.orig, "args", ())
    return arguments[0] if arguments and isinstance(arguments[0], int) else None


__all__ = [
    "ReviewCompositionError",
    "dispatch_review_mutation",
    "read_recommendation",
    "read_review_detail_response",
]
