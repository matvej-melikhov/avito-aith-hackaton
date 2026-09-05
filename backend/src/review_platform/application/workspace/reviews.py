"""Workspace review actions built on the shared core review services."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    require_roles,
    revision,
    row,
)
from review_platform.contracts.commands import WireCommand
from review_platform.contracts.workspace import (
    ExtraRequirementInput,
    ResourceResult,
    WorkspaceCommand,
)
from review_platform.infrastructure.db.models import (
    Course,
    CourseRun,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
    ReviewCase,
    ReviewIteration,
)
from review_platform.infrastructure.db.models.workspace import HomeworkPrivateDetails


async def lock_review_scope(
    session: AsyncSession, actor: RequestActor, identity: UUID
) -> ReviewIteration:
    current = await row(session, ReviewIteration, actor.organization_id, identity)
    await course_scope(session, actor, current.course_run_id, write=True)
    return await lock_review_rows(session, actor.organization_id, identity)


async def lock_review_rows(
    session: AsyncSession, organization_id: UUID, identity: UUID
) -> ReviewIteration:
    """Acquire the shared lock order; callers enforce their route authorization."""
    current = await row(session, ReviewIteration, organization_id, identity)
    run = await row(session, CourseRun, organization_id, current.course_run_id)
    await row(session, Course, organization_id, run.course_id, lock=True)
    await row(session, CourseRun, organization_id, run.id, lock=True)
    await row(session, ReviewCase, organization_id, current.review_case_id, lock=True)
    return await row(session, ReviewIteration, organization_id, identity, lock=True)


async def add_requirement(
    runtime: FoundationRuntime,
    session: AsyncSession,
    actor: RequestActor,
    command: WorkspaceCommand[ExtraRequirementInput],
) -> ResourceResult:
    from review_platform.api.routes.review_composition import dispatch_review_mutation

    require_roles(actor, "reviewer", "methodologist")
    iteration = await lock_review_scope(session, actor, command.target_id)
    revision(iteration.revision, command.expected_revision)
    if iteration.status not in {"in_review", "ready_to_publish"}:
        raise WorkspaceFailure(
            "review_closed", "Добавить требование можно только в открытое ревью."
        )
    original = await row(
        session, HomeworkVersion, actor.organization_id, iteration.homework_version_id
    )
    homework = await row(session, Homework, actor.organization_id, original.homework_id, lock=True)
    number = await session.scalar(
        select(func.max(HomeworkVersion.version_number)).where(
            HomeworkVersion.organization_id == actor.organization_id,
            HomeworkVersion.homework_id == homework.id,
        )
    )
    version = HomeworkVersion(
        id=runtime.id_factory(),
        organization_id=actor.organization_id,
        homework_id=homework.id,
        version_number=(number or 0) + 1,
        review_only=True,
        revision=0,
        student_text=original.student_text,
        max_score=original.max_score + Decimal(str(command.payload.max_points)),
        artifact_kinds=list(original.artifact_kinds),
        estimated_review_minutes=original.estimated_review_minutes,
    )
    session.add(version)
    await session.flush()
    criterion_set = CriterionSet(
        id=runtime.id_factory(),
        organization_id=actor.organization_id,
        homework_version_id=version.id,
    )
    session.add(criterion_set)
    await session.flush()
    criteria = (
        await session.scalars(
            select(Criterion)
            .where(
                Criterion.organization_id == actor.organization_id,
                Criterion.criterion_set_id == iteration.criterion_set_id,
                Criterion.active.is_(True),
            )
            .order_by(Criterion.position)
        )
    ).all()
    for position, criterion in enumerate(criteria, start=1):
        session.add(
            Criterion(
                id=runtime.id_factory(),
                organization_id=actor.organization_id,
                criterion_set_id=criterion_set.id,
                stable_key=criterion.stable_key,
                position=position,
                title=criterion.title,
                description=criterion.description,
                max_points=criterion.max_points,
                active=True,
            )
        )
    session.add(
        Criterion(
            id=runtime.id_factory(),
            organization_id=actor.organization_id,
            criterion_set_id=criterion_set.id,
            stable_key=f"extra-{runtime.id_factory()}",
            position=len(criteria) + 1,
            title=command.payload.title,
            description=command.payload.description,
            max_points=Decimal(str(command.payload.max_points)),
            active=True,
        )
    )
    private = await session.scalar(
        select(HomeworkPrivateDetails).where(
            HomeworkPrivateDetails.organization_id == actor.organization_id,
            HomeworkPrivateDetails.id == original.id,
        )
    )
    if private:
        session.add(
            HomeworkPrivateDetails(
                id=version.id,
                organization_id=actor.organization_id,
                revision=0,
                allowed_sources=private.allowed_sources,
                reviewer_guidance=private.reviewer_guidance,
                reference_upload_id=private.reference_upload_id,
                material_upload_ids=private.material_upload_ids,
                criterion_classes=dict(private.criterion_classes),
                criterion_settings=dict(private.criterion_settings or {}),
            )
        )
    homework.revision += 1
    await session.flush()
    core = WireCommand.model_validate(
        {
            **command.model_dump(mode="json"),
            "command_name": "migrate_review_requirements",
            "revision_target": "review_iteration",
            "payload": {
                "homework_version_id": str(version.id),
                "criterion_set_id": str(criterion_set.id),
            },
        }
    )
    result = await dispatch_review_mutation(
        runtime=runtime, actor=actor, command=core, transaction=session
    )
    assert result is not None
    return ResourceResult(
        id=UUID(str(result["review_iteration_id"])), revision=int(result["revision"])
    )
