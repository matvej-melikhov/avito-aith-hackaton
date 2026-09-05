"""Tenant-scoped SQL adapters for review successors and human publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.ports.providers import JsonValue
from review_platform.application.services.homeworks import HomeworkRequirementsChanged
from review_platform.application.services.publication_requests import (
    PublicationRequestContext,
    PublicationRequestRecord,
    PublicationRequestRepository,
)
from review_platform.application.services.review_corrections import (
    ExistingReviewCorrection,
    PublishedReviewSnapshot,
    ReviewCorrectionPublicationRepository,
)
from review_platform.application.services.review_publication import (
    DestinationKind,
    DestinationSnapshot,
    ExistingReviewPublication,
    ExternalDeliveryIntent,
    PublicationCriterion,
    ReviewPublicationContext,
    ReviewPublicationRecord,
    ReviewPublicationRepository,
)
from review_platform.application.services.review_requirement_impacts import (
    AffectedReviewContext,
    RequirementsChangeContext,
    ReviewImpactRecord,
    ReviewRequirementImpactRepository,
)
from review_platform.application.services.review_requirements import (
    ExistingRequirementsMigration,
    RequirementsCriterion,
    RequirementsMigrationContext,
    RequirementsTarget,
    ReviewRequirementsRepository,
    SuccessorRevisionRepository,
)
from review_platform.domain.primitives import require_utc
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.learning import (
    Course,
    CourseRun,
    DestinationBinding,
)
from review_platform.infrastructure.db.models.operations import AuditEvent
from review_platform.infrastructure.db.models.publication import (
    ExternalDelivery,
    ReviewImpactEvent,
    ReviewIterationRelation,
)
from review_platform.infrastructure.db.models.publication import (
    PublicationRequest as PublicationRequestModel,
)
from review_platform.infrastructure.db.models.publication import (
    ReviewPublication as ReviewPublicationModel,
)
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionKind,
    ReviewDecisionRecord,
    ReviewNoteRecord,
    ReviewRevisionRecord,
    SqlReviewRevisionRepository,
)

_EDITABLE_ITERATION_STATUSES = ("in_review", "ready_to_publish")
_CURRENT_REPLAY_READ = "review_platform.publications.current_replay_read"


class PublicationRepositoryError(RuntimeError):
    """Persistent publication provenance or concurrency invariant failed."""


class InvalidPublicationTransaction(PublicationRepositoryError):
    pass


class PublicationPersistenceConflict(PublicationRepositoryError):
    pass


class SqlReviewRequirementImpactRepository:
    """Project requirement changes onto exact older ReviewIterations."""

    async def lock_change_context(
        self,
        event: HomeworkRequirementsChanged,
        *,
        transaction: object,
    ) -> RequirementsChangeContext | None:
        session = _session(transaction)
        if event.previous_homework_version_id is None or event.previous_publication_id is None:
            return None
        relation = await session.scalar(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == event.organization_id,
                CourseRunHomework.id == event.course_run_homework_id,
                CourseRunHomework.course_run_id == event.course_run_id,
                CourseRunHomework.homework_id == event.homework_id,
                CourseRunHomework.current_publication_id == event.current_publication_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if relation is None:
            return None
        previous = await session.scalar(
            select(CourseRunHomeworkPublication)
            .where(
                CourseRunHomeworkPublication.organization_id == event.organization_id,
                CourseRunHomeworkPublication.course_run_homework_id == relation.id,
                CourseRunHomeworkPublication.id == event.previous_publication_id,
                CourseRunHomeworkPublication.homework_id == relation.homework_id,
                CourseRunHomeworkPublication.homework_version_id
                == event.previous_homework_version_id,
            )
            .with_for_update()
        )
        current = await session.scalar(
            select(CourseRunHomeworkPublication)
            .where(
                CourseRunHomeworkPublication.organization_id == event.organization_id,
                CourseRunHomeworkPublication.course_run_homework_id == relation.id,
                CourseRunHomeworkPublication.id == event.current_publication_id,
                CourseRunHomeworkPublication.homework_id == relation.homework_id,
                CourseRunHomeworkPublication.homework_version_id
                == event.current_homework_version_id,
                CourseRunHomeworkPublication.publication_sequence == event.publication_sequence,
            )
            .with_for_update()
        )
        criterion_set_id = await session.scalar(
            select(CriterionSet.id)
            .where(
                CriterionSet.organization_id == event.organization_id,
                CriterionSet.homework_version_id == event.current_homework_version_id,
            )
            .with_for_update()
        )
        if previous is None or current is None or criterion_set_id is None:
            return None
        return RequirementsChangeContext(
            organization_id=event.organization_id,
            course_run_id=relation.course_run_id,
            course_run_homework_id=relation.id,
            homework_id=relation.homework_id,
            previous_homework_version_id=previous.homework_version_id,
            current_homework_version_id=current.homework_version_id,
            current_criterion_set_id=criterion_set_id,
            previous_publication_id=previous.id,
            current_publication_id=current.id,
            publication_sequence=current.publication_sequence,
        )

    async def list_affected_reviews(
        self,
        context: RequirementsChangeContext,
        *,
        transaction: object,
    ) -> Sequence[AffectedReviewContext]:
        session = _session(transaction)
        rows = (
            await session.execute(
                select(ReviewIteration, ReviewCase)
                .join(
                    ReviewCase,
                    and_(
                        ReviewCase.organization_id == ReviewIteration.organization_id,
                        ReviewCase.id == ReviewIteration.review_case_id,
                        ReviewCase.course_run_id == ReviewIteration.course_run_id,
                        ReviewCase.homework_id == ReviewIteration.homework_id,
                    ),
                )
                .join(
                    SubmissionVersion,
                    and_(
                        SubmissionVersion.organization_id == ReviewIteration.organization_id,
                        SubmissionVersion.id == ReviewIteration.submission_version_id,
                    ),
                )
                .join(
                    Submission,
                    and_(
                        Submission.organization_id == SubmissionVersion.organization_id,
                        Submission.id == SubmissionVersion.submission_id,
                        Submission.course_run_homework_id == context.course_run_homework_id,
                    ),
                )
                .where(
                    ReviewIteration.organization_id == context.organization_id,
                    ReviewIteration.course_run_id == context.course_run_id,
                    ReviewIteration.homework_id == context.homework_id,
                    ReviewIteration.homework_version_id != context.current_homework_version_id,
                    ReviewIteration.status != "canceled",
                )
                .order_by(ReviewIteration.iteration_number, ReviewIteration.id)
            )
        ).all()
        return tuple(
            AffectedReviewContext(
                organization_id=iteration.organization_id,
                review_case_id=review_case.id,
                review_iteration_id=iteration.id,
                course_run_id=iteration.course_run_id,
                course_run_homework_id=context.course_run_homework_id,
                homework_id=iteration.homework_id,
                effective_homework_version_id=iteration.homework_version_id,
                iteration_number=iteration.iteration_number,
                iteration_status=iteration.status,
            )
            for iteration, review_case in rows
        )

    async def reserve_impact(
        self,
        candidate: ReviewImpactRecord,
        *,
        transaction: object,
    ) -> tuple[ReviewImpactRecord, bool]:
        session = _session(transaction)
        existing = await _impact_by_identity(
            session,
            candidate.organization_id,
            candidate.review_iteration_id,
            candidate.current_homework_version_id,
        )
        if existing is not None:
            return _impact_record(existing), False
        if candidate.resolved_by_iteration_id is not None or candidate.resolved_at is not None:
            raise PublicationPersistenceConflict("new ReviewImpactEvent cannot start resolved")
        row = ReviewImpactEvent(
            id=candidate.impact_id,
            organization_id=candidate.organization_id,
            source_event_id=candidate.source_event_id,
            review_case_id=candidate.review_case_id,
            review_iteration_id=candidate.review_iteration_id,
            course_run_homework_id=candidate.course_run_homework_id,
            course_run_id=candidate.course_run_id,
            homework_id=candidate.homework_id,
            previous_homework_version_id=candidate.previous_homework_version_id,
            current_homework_version_id=candidate.current_homework_version_id,
            previous_publication_id=candidate.previous_publication_id,
            current_publication_id=candidate.current_publication_id,
            publication_sequence=candidate.publication_sequence,
            occurred_at=require_utc(candidate.occurred_at),
            resolved_by_iteration_id=None,
            resolved_at=None,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError:
            existing = await _impact_by_identity(
                session,
                candidate.organization_id,
                candidate.review_iteration_id,
                candidate.current_homework_version_id,
                current_read=True,
            )
            if existing is None:
                raise
            return _impact_record(existing), False
        return _impact_record(row), True


class SqlPublicationRepository:
    """Implement T124-T127 ports without opening or committing transactions."""

    async def find_existing_migration(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        target_homework_version_id: UUID,
        target_criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> ExistingRequirementsMigration | None:
        session = _session(transaction)
        statement = (
            select(ReviewIterationRelation, ReviewIteration)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.organization_id == ReviewIterationRelation.organization_id,
                    ReviewIteration.review_case_id == ReviewIterationRelation.review_case_id,
                    ReviewIteration.id == ReviewIterationRelation.successor_iteration_id,
                ),
            )
            .where(
                ReviewIterationRelation.organization_id == organization_id,
                ReviewIterationRelation.predecessor_iteration_id == predecessor_iteration_id,
                ReviewIterationRelation.kind == "requirements_migration",
                ReviewIteration.homework_version_id == target_homework_version_id,
                ReviewIteration.criterion_set_id == target_criterion_set_id,
            )
            .order_by(ReviewIterationRelation.created_at, ReviewIterationRelation.id)
        )
        current_read = _consume_current_replay_read(session)
        if current_read:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).first()
        if row is None:
            return None
        relation, successor = row
        if successor.current_revision_id is None:
            return None
        revision = await _revision_record(
            session,
            organization_id,
            successor.id,
            successor.current_revision_id,
            current_read=current_read,
        )
        if revision is None:
            return None
        return ExistingRequirementsMigration(
            organization_id=organization_id,
            review_case_id=relation.review_case_id,
            predecessor_iteration_id=predecessor_iteration_id,
            successor_iteration_id=successor.id,
            successor_iteration_revision=successor.revision,
            successor_revision_id=revision.review_revision_id,
            target_homework_version_id=successor.homework_version_id,
            target_criterion_set_id=successor.criterion_set_id,
            transferred_decision_count=len(revision.decisions),
        )

    async def lock_current_predecessor(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        *,
        expected_predecessor_revision: int,
        transaction: object,
    ) -> RequirementsMigrationContext | None:
        session = _session(transaction)
        if expected_predecessor_revision < 0:
            return None
        preliminary = (
            await session.execute(
                select(
                    ReviewIteration.review_case_id,
                    ReviewIteration.course_run_id,
                ).where(
                    ReviewIteration.organization_id == organization_id,
                    ReviewIteration.id == predecessor_iteration_id,
                )
            )
        ).one_or_none()
        if preliminary is None:
            return None
        review_case_id, course_run_id = preliminary
        course_id = await session.scalar(
            select(CourseRun.course_id).where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
            )
        )
        if course_id is None:
            return None

        course = await session.scalar(
            select(Course)
            .where(Course.organization_id == organization_id, Course.id == course_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        course_run = await session.scalar(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
                CourseRun.course_id == course_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        review_case = await session.scalar(
            select(ReviewCase)
            .where(
                ReviewCase.organization_id == organization_id,
                ReviewCase.id == review_case_id,
                ReviewCase.current_iteration_id == predecessor_iteration_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        predecessor = await session.scalar(
            select(ReviewIteration)
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.review_case_id == review_case_id,
                ReviewIteration.id == predecessor_iteration_id,
                ReviewIteration.revision == expected_predecessor_revision,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            course is None
            or course_run is None
            or review_case is None
            or predecessor is None
            or course.status != "active"
            or course_run.status != "active"
        ):
            _mark_current_replay_read(session)
            return None

        current_revision: ReviewRevisionRecord | None = None
        if predecessor.current_revision_id is not None:
            locked_revision = await session.scalar(
                select(ReviewRevision)
                .where(
                    ReviewRevision.organization_id == organization_id,
                    ReviewRevision.review_iteration_id == predecessor.id,
                    ReviewRevision.id == predecessor.current_revision_id,
                )
                .with_for_update()
            )
            if locked_revision is None:
                return None
            current_revision = await _revision_record(
                session,
                organization_id,
                predecessor.id,
                locked_revision.id,
            )
            if current_revision is None:
                return None
        criteria = await _requirements_criteria(
            session,
            organization_id=organization_id,
            criterion_set_id=predecessor.criterion_set_id,
        )
        return RequirementsMigrationContext(
            organization_id=organization_id,
            review_case_id=review_case.id,
            review_case_revision=review_case.revision,
            current_iteration_id=cast(UUID, review_case.current_iteration_id),
            predecessor_iteration_id=predecessor.id,
            predecessor_iteration_revision=predecessor.revision,
            iteration_number=predecessor.iteration_number,
            course_run_id=predecessor.course_run_id,
            homework_id=predecessor.homework_id,
            student_id=predecessor.student_id,
            submission_version_id=predecessor.submission_version_id,
            artifact_version_id=predecessor.artifact_version_id,
            effective_deadline=predecessor.effective_deadline,
            responsible_reviewer_id=predecessor.responsible_reviewer_id,
            status=predecessor.status,
            predecessor_homework_version_id=predecessor.homework_version_id,
            predecessor_criterion_set_id=predecessor.criterion_set_id,
            current_human_revision=current_revision,
            predecessor_criteria=criteria,
        )

    async def load_target(
        self,
        organization_id: UUID,
        homework_id: UUID,
        homework_version_id: UUID,
        criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> RequirementsTarget | None:
        session = _session(transaction)
        target = (
            await session.execute(
                select(HomeworkVersion.id, CriterionSet.id)
                .join(
                    CriterionSet,
                    and_(
                        CriterionSet.organization_id == HomeworkVersion.organization_id,
                        CriterionSet.homework_version_id == HomeworkVersion.id,
                    ),
                )
                .where(
                    HomeworkVersion.organization_id == organization_id,
                    HomeworkVersion.homework_id == homework_id,
                    HomeworkVersion.id == homework_version_id,
                    CriterionSet.id == criterion_set_id,
                )
            )
        ).one_or_none()
        if target is None:
            return None
        criteria = await _requirements_criteria(
            session,
            organization_id=organization_id,
            criterion_set_id=criterion_set_id,
        )
        return RequirementsTarget(
            organization_id=organization_id,
            homework_id=homework_id,
            homework_version_id=target[0],
            criterion_set_id=target[1],
            criteria=criteria,
        )

    async def next_iteration_number(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        value = await session.scalar(
            select(func.max(ReviewIteration.iteration_number)).where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.review_case_id == review_case_id,
            )
        )
        return int(value or 0) + 1

    async def append_successor(
        self,
        successor: ReviewIteration,
        relation: ReviewIterationRelation,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        if (
            successor.organization_id != relation.organization_id
            or successor.review_case_id != relation.review_case_id
            or successor.predecessor_iteration_id != relation.predecessor_iteration_id
            or successor.id != relation.successor_iteration_id
            or successor.origin != relation.kind
            or successor.revision != 0
            or successor.current_revision_id is not None
        ):
            raise PublicationPersistenceConflict("successor and relation provenance disagree")
        try:
            async with session.begin_nested():
                session.add(successor)
                await session.flush([successor])
                session.add(relation)
                await session.flush([relation])
        except IntegrityError as error:
            raise PublicationPersistenceConflict(
                "successor identity or relation conflicts"
            ) from error

    async def compare_and_set_current_iteration(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        expected_review_case_revision: int,
        expected_current_iteration_id: UUID,
        new_iteration_id: UUID,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        successor = await session.scalar(
            select(ReviewIteration.id).where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.review_case_id == review_case_id,
                ReviewIteration.id == new_iteration_id,
                ReviewIteration.predecessor_iteration_id == expected_current_iteration_id,
            )
        )
        if successor is None:
            return False
        result = await session.execute(
            update(ReviewCase)
            .where(
                ReviewCase.organization_id == organization_id,
                ReviewCase.id == review_case_id,
                ReviewCase.revision == expected_review_case_revision,
                ReviewCase.current_iteration_id == expected_current_iteration_id,
            )
            .values(
                current_iteration_id=new_iteration_id,
                revision=expected_review_case_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def find_existing_correction(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        published_review_revision_id: UUID,
        reason: str,
        *,
        transaction: object,
    ) -> ExistingReviewCorrection | None:
        session = _session(transaction)
        statement = (
            select(ReviewIterationRelation, ReviewIteration)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.organization_id == ReviewIterationRelation.organization_id,
                    ReviewIteration.review_case_id == ReviewIterationRelation.review_case_id,
                    ReviewIteration.id == ReviewIterationRelation.successor_iteration_id,
                ),
            )
            .where(
                ReviewIterationRelation.organization_id == organization_id,
                ReviewIterationRelation.predecessor_iteration_id == predecessor_iteration_id,
                ReviewIterationRelation.kind == "correction",
            )
            .order_by(ReviewIterationRelation.created_at, ReviewIterationRelation.id)
        )
        current_read = _consume_current_replay_read(session)
        if current_read:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).first()
        if row is None:
            return None
        relation, successor = row
        if successor.current_revision_id is None:
            return None
        publication_statement = select(ReviewPublicationModel.id).where(
            ReviewPublicationModel.organization_id == organization_id,
            ReviewPublicationModel.review_iteration_id == predecessor_iteration_id,
            ReviewPublicationModel.review_revision_id == published_review_revision_id,
        )
        if current_read:
            publication_statement = publication_statement.with_for_update()
        publication = await session.scalar(publication_statement)
        if publication is None:
            return None
        audit_statement = (
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.action == "create_review_correction",
                AuditEvent.entity_type == "review_iteration",
                AuditEvent.entity_id == successor.id,
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        )
        if current_read:
            audit_statement = audit_statement.with_for_update()
        audit = await session.scalar(audit_statement)
        details = audit.sanitized_details if audit is not None else {}
        if details.get("reason") != reason:
            return None
        return ExistingReviewCorrection(
            organization_id=organization_id,
            review_case_id=relation.review_case_id,
            predecessor_iteration_id=predecessor_iteration_id,
            published_review_revision_id=published_review_revision_id,
            reason=reason,
            successor_iteration_id=successor.id,
            successor_iteration_revision=successor.revision,
            successor_revision_id=successor.current_revision_id,
        )

    async def require_published_revision(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        published_review_revision_id: UUID,
        *,
        transaction: object,
    ) -> PublishedReviewSnapshot | None:
        session = _session(transaction)
        publication_id = await session.scalar(
            select(ReviewPublicationModel.id).where(
                ReviewPublicationModel.organization_id == organization_id,
                ReviewPublicationModel.review_iteration_id == predecessor_iteration_id,
                ReviewPublicationModel.review_revision_id == published_review_revision_id,
                ReviewPublicationModel.status == "published",
            )
        )
        if publication_id is None:
            return None
        revision = await _revision_record(
            session,
            organization_id,
            predecessor_iteration_id,
            published_review_revision_id,
        )
        if revision is None:
            return None
        return PublishedReviewSnapshot(
            organization_id=organization_id,
            review_iteration_id=predecessor_iteration_id,
            publication_id=publication_id,
            revision=revision,
        )

    async def lock_iteration_context(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> PublicationRequestContext | None:
        session = _session(transaction)
        if expected_revision < 0:
            return None
        iteration = await session.scalar(
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
        return PublicationRequestContext(
            organization_id=organization_id,
            review_iteration_id=iteration.id,
            iteration_revision=iteration.revision,
            current_review_revision_id=iteration.current_revision_id,
        )

    async def reserve(
        self,
        candidate: PublicationRequestRecord,
        *,
        transaction: object,
    ) -> tuple[PublicationRequestRecord, bool]:
        session = _session(transaction)
        if (
            candidate.agent_id is None
            or candidate.agent_authorization_id is None
            or candidate.status != "pending"
            or candidate.revision != 0
        ):
            raise PublicationPersistenceConflict(
                "publication request must carry exact pending agent provenance"
            )
        existing = await _request_by_key(
            session,
            candidate.organization_id,
            candidate.idempotency_key,
        )
        if existing is not None:
            return _request_record(existing), False
        row = PublicationRequestModel(
            id=candidate.publication_request_id,
            organization_id=candidate.organization_id,
            review_iteration_id=candidate.review_iteration_id,
            review_revision_id=candidate.review_revision_id,
            requested_by_user_id=candidate.requested_by_user_id,
            agent_id=candidate.agent_id,
            agent_authorization_id=candidate.agent_authorization_id,
            idempotency_key=candidate.idempotency_key,
            status=candidate.status,
            expires_at=require_utc(candidate.expires_at),
            revision=candidate.revision,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError:
            existing = await _request_by_key(
                session,
                candidate.organization_id,
                candidate.idempotency_key,
                populate_existing=True,
            )
            if existing is None:
                raise
            return _request_record(existing), False
        return _request_record(row), True

    async def find_existing_publication(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        review_revision_id: UUID,
        *,
        transaction: object,
    ) -> ExistingReviewPublication | None:
        session = _session(transaction)
        statement = select(ReviewPublicationModel).where(
            ReviewPublicationModel.organization_id == organization_id,
            ReviewPublicationModel.review_iteration_id == review_iteration_id,
            ReviewPublicationModel.review_revision_id == review_revision_id,
        )
        current_read = _consume_current_replay_read(session)
        if current_read:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        if row is None:
            return None
        return await _existing_publication(session, row, current_read=current_read)

    async def lock_publication_context(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID,
        transaction: object,
    ) -> ReviewPublicationContext | None:
        session = _session(transaction)
        preliminary = (
            await session.execute(
                select(
                    ReviewIteration.review_case_id,
                    ReviewIteration.course_run_id,
                ).where(
                    ReviewIteration.organization_id == organization_id,
                    ReviewIteration.id == review_iteration_id,
                )
            )
        ).one_or_none()
        if preliminary is None:
            return None
        review_case_id, course_run_id = preliminary
        course_id = await session.scalar(
            select(CourseRun.course_id).where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
            )
        )
        if course_id is None:
            return None

        course = await session.scalar(
            select(Course)
            .where(Course.organization_id == organization_id, Course.id == course_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        course_run = await session.scalar(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
                CourseRun.course_id == course_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        review_case = await session.scalar(
            select(ReviewCase)
            .where(
                ReviewCase.organization_id == organization_id,
                ReviewCase.id == review_case_id,
                ReviewCase.current_iteration_id == review_iteration_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        iteration = await session.scalar(
            select(ReviewIteration)
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.review_case_id == review_case_id,
                ReviewIteration.id == review_iteration_id,
                ReviewIteration.revision == expected_iteration_revision,
                ReviewIteration.current_revision_id == expected_current_revision_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        revision_row = await session.scalar(
            select(ReviewRevision)
            .where(
                ReviewRevision.organization_id == organization_id,
                ReviewRevision.review_iteration_id == review_iteration_id,
                ReviewRevision.id == expected_current_revision_id,
            )
            .with_for_update()
        )
        if (
            course is None
            or course_run is None
            or review_case is None
            or iteration is None
            or revision_row is None
        ):
            _mark_current_replay_read(session)
            return None
        revision = await _revision_record(
            session,
            organization_id,
            review_iteration_id,
            expected_current_revision_id,
        )
        homework_version = await session.scalar(
            select(HomeworkVersion).where(
                HomeworkVersion.organization_id == organization_id,
                HomeworkVersion.id == iteration.homework_version_id,
                HomeworkVersion.homework_id == iteration.homework_id,
            )
        )
        homework = await session.scalar(
            select(Homework).where(
                Homework.organization_id == organization_id,
                Homework.id == iteration.homework_id,
                Homework.course_id == course.id,
            )
        )
        artifact = await session.scalar(
            select(ArtifactVersion).where(
                ArtifactVersion.organization_id == organization_id,
                ArtifactVersion.id == iteration.artifact_version_id,
            )
        )
        criterion_set = await session.scalar(
            select(CriterionSet.id).where(
                CriterionSet.organization_id == organization_id,
                CriterionSet.id == iteration.criterion_set_id,
                CriterionSet.homework_version_id == iteration.homework_version_id,
            )
        )
        if (
            revision is None
            or homework_version is None
            or homework is None
            or artifact is None
            or criterion_set is None
        ):
            return None
        criteria = (
            await session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == organization_id,
                    Criterion.criterion_set_id == iteration.criterion_set_id,
                    Criterion.active.is_(True),
                )
                .order_by(Criterion.position, Criterion.id)
            )
        ).all()
        destinations = (
            await session.scalars(
                select(DestinationBinding)
                .where(
                    DestinationBinding.organization_id == organization_id,
                    DestinationBinding.course_run_id == iteration.course_run_id,
                    DestinationBinding.required.is_(True),
                    DestinationBinding.status == "active",
                )
                .order_by(DestinationBinding.id, DestinationBinding.binding_version)
            )
        ).all()
        return ReviewPublicationContext(
            organization_id=organization_id,
            review_case_id=review_case.id,
            current_iteration_id=cast(UUID, review_case.current_iteration_id),
            review_iteration_id=iteration.id,
            iteration_revision=iteration.revision,
            current_review_revision_id=iteration.current_revision_id,
            iteration_status=iteration.status,
            course_id=course.id,
            course_status=course.status,
            course_run_id=course_run.id,
            course_run_status=course_run.status,
            homework_version_id=homework_version.id,
            homework_max_score=homework_version.max_score,
            criterion_set_id=iteration.criterion_set_id,
            submission_version_id=iteration.submission_version_id,
            artifact_version_id=artifact.id,
            artifact_content_digest=artifact.content_digest,
            current_revision=revision,
            criteria=tuple(
                PublicationCriterion(item.id, item.position, item.max_points) for item in criteria
            ),
            destinations=tuple(
                DestinationSnapshot(
                    organization_id=organization_id,
                    course_run_id=course_run.id,
                    destination_binding_id=item.id,
                    binding_version=item.binding_version,
                    credential_binding_id=item.credential_id,
                    credential_binding_version=item.credential_binding_version,
                    kind=cast(DestinationKind, item.kind),
                    recipient_ref=item.recipient_ref,
                    required=item.required,
                    status=item.status,
                )
                for item in destinations
            ),
        )

    async def next_publication_version(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        value = await session.scalar(
            select(func.max(ReviewPublicationModel.publication_version)).where(
                ReviewPublicationModel.organization_id == organization_id,
                ReviewPublicationModel.review_iteration_id == review_iteration_id,
            )
        )
        return int(value or 0) + 1

    async def reserve_publication(
        self,
        publication: ReviewPublicationRecord,
        *,
        transaction: object,
    ) -> tuple[ExistingReviewPublication, bool]:
        session = _session(transaction)
        if publication.status != "published" or publication.revision != 0:
            raise PublicationPersistenceConflict("new publication must be terminal revision zero")
        existing = await self.find_existing_publication(
            publication.organization_id,
            publication.review_iteration_id,
            publication.review_revision_id,
            transaction=session,
        )
        if existing is not None:
            return existing, False
        row = ReviewPublicationModel(
            id=publication.publication_id,
            organization_id=publication.organization_id,
            review_iteration_id=publication.review_iteration_id,
            review_revision_id=publication.review_revision_id,
            publication_request_id=publication.publication_request_id,
            publication_version=publication.publication_version,
            published_by=publication.published_by,
            published_at=require_utc(publication.published_at),
            status=publication.status,
            revision=publication.revision,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError:
            existing = await self.find_existing_publication(
                publication.organization_id,
                publication.review_iteration_id,
                publication.review_revision_id,
                transaction=session,
            )
            if existing is None:
                raise
            return existing, False
        current_iteration_revision = await session.scalar(
            select(ReviewIteration.revision).where(
                ReviewIteration.organization_id == publication.organization_id,
                ReviewIteration.id == publication.review_iteration_id,
                ReviewIteration.current_revision_id == publication.review_revision_id,
            )
        )
        if current_iteration_revision is None:
            raise PublicationPersistenceConflict("publication iteration provenance disappeared")
        return (
            ExistingReviewPublication(
                publication=_publication_record(row),
                review_iteration_revision=current_iteration_revision + 1,
                deliveries=(),
            ),
            True,
        )

    async def lock_publication_request(
        self,
        organization_id: UUID,
        publication_request_id: UUID,
        *,
        transaction: object,
    ) -> PublicationRequestRecord | None:
        session = _session(transaction)
        row = await session.scalar(
            select(PublicationRequestModel)
            .where(
                PublicationRequestModel.organization_id == organization_id,
                PublicationRequestModel.id == publication_request_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return None if row is None else _request_record(row)

    async def confirm_publication_request(
        self,
        organization_id: UUID,
        publication_request_id: UUID,
        *,
        expected_revision: int,
        confirmed_by: UUID,
        confirmed_at: datetime,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        result = await session.execute(
            update(PublicationRequestModel)
            .where(
                PublicationRequestModel.organization_id == organization_id,
                PublicationRequestModel.id == publication_request_id,
                PublicationRequestModel.revision == expected_revision,
                PublicationRequestModel.status == "pending",
            )
            .values(
                status="confirmed",
                confirmed_by=confirmed_by,
                confirmed_at=require_utc(confirmed_at),
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def compare_and_set_published(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        result = await session.execute(
            update(ReviewIteration)
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.id == review_iteration_id,
                ReviewIteration.revision == expected_iteration_revision,
                ReviewIteration.current_revision_id == expected_current_revision_id,
                ReviewIteration.status.in_(_EDITABLE_ITERATION_STATUSES),
            )
            .values(
                status="published",
                revision=expected_iteration_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1


async def _requirements_criteria(
    session: AsyncSession,
    *,
    organization_id: UUID,
    criterion_set_id: UUID,
) -> tuple[RequirementsCriterion, ...]:
    rows = (
        await session.scalars(
            select(Criterion)
            .where(
                Criterion.organization_id == organization_id,
                Criterion.criterion_set_id == criterion_set_id,
                Criterion.active.is_(True),
            )
            .order_by(Criterion.position, Criterion.id)
        )
    ).all()
    return tuple(
        RequirementsCriterion(row.id, row.stable_key, row.position, row.max_points) for row in rows
    )


async def _revision_record(
    session: AsyncSession,
    organization_id: UUID,
    review_iteration_id: UUID,
    review_revision_id: UUID,
    *,
    current_read: bool = False,
) -> ReviewRevisionRecord | None:
    if current_read:
        revision = await session.scalar(
            select(ReviewRevision)
            .where(
                ReviewRevision.organization_id == organization_id,
                ReviewRevision.review_iteration_id == review_iteration_id,
                ReviewRevision.id == review_revision_id,
            )
            .with_for_update()
        )
        if revision is None:
            return None
        decision_rows = (
            await session.execute(
                select(ReviewCriterionDecision, Criterion)
                .join(
                    Criterion,
                    and_(
                        Criterion.organization_id == ReviewCriterionDecision.organization_id,
                        Criterion.id == ReviewCriterionDecision.criterion_id,
                    ),
                )
                .where(
                    ReviewCriterionDecision.organization_id == organization_id,
                    ReviewCriterionDecision.review_revision_id == review_revision_id,
                )
                .order_by(Criterion.position, ReviewCriterionDecision.id)
                .with_for_update()
            )
        ).all()
        notes = (
            await session.scalars(
                select(ReviewNote)
                .where(
                    ReviewNote.organization_id == organization_id,
                    ReviewNote.review_revision_id == review_revision_id,
                )
                .order_by(ReviewNote.position, ReviewNote.id)
                .with_for_update()
            )
        ).all()
        return ReviewRevisionRecord(
            organization_id=revision.organization_id,
            review_revision_id=revision.id,
            review_iteration_id=revision.review_iteration_id,
            revision_number=revision.revision_number,
            author_user_id=revision.author_user_id,
            base_revision_id=revision.base_revision_id,
            feedback=revision.feedback,
            total_score=revision.total_score,
            created_at=revision.created_at,
            decisions=tuple(
                ReviewDecisionRecord(
                    decision_id=decision.id,
                    criterion_id=decision.criterion_id,
                    criterion_key=criterion.stable_key,
                    criterion_position=criterion.position,
                    points=decision.points,
                    decision=cast(ReviewDecisionKind, decision.decision),
                    reason=decision.reason,
                    evidence_ids=tuple(cast(Sequence[str], decision.evidence_ids)),
                    ai_suggestion_id=decision.ai_suggestion_id,
                )
                for decision, criterion in decision_rows
            ),
            notes=tuple(
                ReviewNoteRecord(
                    note_id=note.id,
                    criterion_id=note.criterion_id,
                    text=note.text,
                    author_user_id=note.author_user_id,
                    position=note.position,
                )
                for note in notes
            ),
        )
    return await SqlReviewRevisionRepository(session).get_revision(
        organization_id,
        review_iteration_id,
        review_revision_id,
    )


async def _impact_by_identity(
    session: AsyncSession,
    organization_id: UUID,
    review_iteration_id: UUID,
    current_homework_version_id: UUID,
    *,
    current_read: bool = False,
) -> ReviewImpactEvent | None:
    statement = select(ReviewImpactEvent).where(
        ReviewImpactEvent.organization_id == organization_id,
        ReviewImpactEvent.review_iteration_id == review_iteration_id,
        ReviewImpactEvent.current_homework_version_id == current_homework_version_id,
    )
    if current_read:
        statement = statement.with_for_update()
    return cast(ReviewImpactEvent | None, await session.scalar(statement))


def _impact_record(row: ReviewImpactEvent) -> ReviewImpactRecord:
    return ReviewImpactRecord(
        impact_id=row.id,
        organization_id=row.organization_id,
        source_event_id=row.source_event_id,
        review_case_id=row.review_case_id,
        review_iteration_id=row.review_iteration_id,
        course_run_homework_id=row.course_run_homework_id,
        course_run_id=row.course_run_id,
        homework_id=row.homework_id,
        previous_homework_version_id=row.previous_homework_version_id,
        current_homework_version_id=row.current_homework_version_id,
        previous_publication_id=row.previous_publication_id,
        current_publication_id=row.current_publication_id,
        publication_sequence=row.publication_sequence,
        occurred_at=row.occurred_at,
        resolved_by_iteration_id=row.resolved_by_iteration_id,
        resolved_at=row.resolved_at,
    )


async def _request_by_key(
    session: AsyncSession,
    organization_id: UUID,
    idempotency_key: str,
    *,
    populate_existing: bool = False,
) -> PublicationRequestModel | None:
    statement = select(PublicationRequestModel).where(
        PublicationRequestModel.organization_id == organization_id,
        PublicationRequestModel.idempotency_key == idempotency_key,
    )
    if populate_existing:
        statement = statement.execution_options(populate_existing=True)
    return cast(PublicationRequestModel | None, await session.scalar(statement))


def _request_record(row: PublicationRequestModel) -> PublicationRequestRecord:
    return PublicationRequestRecord(
        organization_id=row.organization_id,
        publication_request_id=row.id,
        review_iteration_id=row.review_iteration_id,
        review_revision_id=row.review_revision_id,
        requested_by_user_id=row.requested_by_user_id,
        agent_id=row.agent_id,
        agent_authorization_id=row.agent_authorization_id,
        idempotency_key=row.idempotency_key,
        status=row.status,
        expires_at=row.expires_at,
        revision=row.revision,
    )


def _publication_record(row: ReviewPublicationModel) -> ReviewPublicationRecord:
    return ReviewPublicationRecord(
        organization_id=row.organization_id,
        publication_id=row.id,
        review_iteration_id=row.review_iteration_id,
        review_revision_id=row.review_revision_id,
        publication_request_id=row.publication_request_id,
        publication_version=row.publication_version,
        published_by=row.published_by,
        published_at=row.published_at,
        status="published",
        revision=row.revision,
    )


async def _existing_publication(
    session: AsyncSession,
    row: ReviewPublicationModel,
    *,
    current_read: bool = False,
) -> ExistingReviewPublication:
    iteration_statement = select(ReviewIteration.revision).where(
        ReviewIteration.organization_id == row.organization_id,
        ReviewIteration.id == row.review_iteration_id,
    )
    if current_read:
        iteration_statement = iteration_statement.with_for_update()
    iteration_revision = await session.scalar(iteration_statement)
    if iteration_revision is None:
        raise PublicationPersistenceConflict("publication ReviewIteration is missing")
    delivery_statement = (
        select(ExternalDelivery)
        .where(
            ExternalDelivery.organization_id == row.organization_id,
            ExternalDelivery.publication_id == row.id,
        )
        .order_by(
            ExternalDelivery.destination_binding_id,
            ExternalDelivery.binding_version,
            ExternalDelivery.id,
        )
    )
    if current_read:
        delivery_statement = delivery_statement.with_for_update()
    deliveries = (await session.scalars(delivery_statement)).all()
    return ExistingReviewPublication(
        publication=_publication_record(row),
        review_iteration_revision=iteration_revision,
        deliveries=tuple(_delivery_intent(item) for item in deliveries),
    )


def _delivery_intent(row: ExternalDelivery) -> ExternalDeliveryIntent:
    return ExternalDeliveryIntent(
        organization_id=row.organization_id,
        delivery_id=row.id,
        operation_id=row.operation_id,
        publication_id=row.publication_id,
        delivery_key=row.delivery_key,
        destination=DestinationSnapshot(
            organization_id=row.organization_id,
            course_run_id=row.course_run_id,
            destination_binding_id=row.destination_binding_id,
            binding_version=row.binding_version,
            credential_binding_id=row.credential_binding_id,
            credential_binding_version=row.credential_binding_version,
            kind=cast(DestinationKind, row.destination_kind),
            recipient_ref=row.recipient_ref,
            required=True,
            status="active",
        ),
        course_run_id=row.course_run_id,
        homework_version_id=row.homework_version_id,
        criterion_set_id=row.criterion_set_id,
        submission_version_id=row.submission_version_id,
        artifact_version_id=row.artifact_version_id,
        artifact_content_digest=row.artifact_content_digest,
        review_iteration_id=row.review_iteration_id,
        review_revision_id=row.review_revision_id,
        contract_version=row.contract_version,
        publication_fingerprint=row.publication_fingerprint,
        payload_version=row.payload_version,
        payload_digest=row.payload_digest,
        request=cast(Mapping[str, JsonValue], row.payload),
        requested_at=row.created_at,
    )


def require_successor_revision_repository(
    transaction: object,
) -> SuccessorRevisionRepository:
    session = _session(transaction)
    repository = SqlReviewRevisionRepository(session)
    typed: SuccessorRevisionRepository = repository
    return typed


def _mark_current_replay_read(session: AsyncSession) -> None:
    """Make the immediate winner lookup bypass an older REPEATABLE READ snapshot."""

    session.info[_CURRENT_REPLAY_READ] = True


def _consume_current_replay_read(session: AsyncSession) -> bool:
    return bool(session.info.pop(_CURRENT_REPLAY_READ, False))


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise InvalidPublicationTransaction(
            "publication repository requires caller-owned AsyncSession"
        )
    return transaction


_requirements_port: ReviewRequirementsRepository = SqlPublicationRepository()
_correction_port: ReviewCorrectionPublicationRepository = SqlPublicationRepository()
_request_port: PublicationRequestRepository = SqlPublicationRepository()
_publication_port: ReviewPublicationRepository = SqlPublicationRepository()
_impact_port: ReviewRequirementImpactRepository = SqlReviewRequirementImpactRepository()


__all__ = [
    "InvalidPublicationTransaction",
    "PublicationPersistenceConflict",
    "PublicationRepositoryError",
    "SqlPublicationRepository",
    "SqlReviewRequirementImpactRepository",
    "require_successor_revision_repository",
]
