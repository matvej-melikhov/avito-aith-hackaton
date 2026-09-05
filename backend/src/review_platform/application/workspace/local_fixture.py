"""Explicit, idempotent local database fixture. Never overwrites an existing installation."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid5

import boto3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import (
    FoundationRuntime,
    build_foundation_runtime,
)
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.submissions import submit_uploaded_draft
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
    Organization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.workspace import (
    PublicationPolicy,
    SelfReviewQuota,
    StudentReviewerAssignment,
    WorkDraft,
    WorkspaceArtifact,
    WorkspaceCourseDetails,
    WorkspaceRunSettings,
)
from review_platform.settings import get_settings

NAMESPACE = UUID("de0e8fad-8fe5-4b8b-bc85-df5383f7f7bb")
IDENTITIES = (
    ("student-1", "Алексей Иванов — студент", "student"),
    ("student-2", "Мария Петрова — студент", "student"),
    ("reviewer-1", "Елена Смирнова — ревьюер", "reviewer"),
    ("reviewer-2", "Дмитрий Волков — ревьюер", "reviewer"),
    ("coordinator", "Анна Соколова — координатор", "methodologist"),
)


def fixture_id(key: str) -> UUID:
    return uuid5(NAMESPACE, key)


async def seed(runtime: FoundationRuntime) -> bool:
    if runtime.settings.environment != "local" or not runtime.settings.workspace_fixtures:
        raise RuntimeError("Fixture seed requires environment=local and workspace_fixtures=true")
    org, now = fixture_id("org"), runtime.clock()
    async with runtime.transaction() as session:
        organizations = (await session.scalars(select(Organization).with_for_update())).all()
        if organizations:
            if (
                len(organizations) == 1
                and organizations[0].id == org
                and organizations[0].slug == "workspace-local-fixture-v2"
            ):
                await _ensure_fixture_quotas(session, org)
                return False
            raise RuntimeError("Refusing to seed a database containing unrelated organizations")
        if await session.scalar(select(User.id).limit(1)):
            raise RuntimeError("Refusing to seed a database containing unrelated users")
        session.add(
            Organization(
                id=org, slug="workspace-local-fixture-v2", name="Локальная учебная организация"
            )
        )
        for key, label, _ in IDENTITIES:
            session.add(User(id=fixture_id(key), display_name=label.split(" — ")[0]))
        await session.flush()
        for key, _, role in IDENTITIES:
            session.add(
                OrganizationMembership(
                    id=fixture_id(key + "-member"),
                    organization_id=org,
                    user_id=fixture_id(key),
                    roles=[role],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                )
            )
        session.add(
            Course(
                id=fixture_id("course"),
                organization_id=org,
                title="Backend-разработка на Python",
                description="Локальный учебный курс. Демонстрационные данные.",
                source_kind="standalone",
                status="active",
                revision=0,
            )
        )
        await session.flush()
        session.add(
            WorkspaceCourseDetails(
                id=fixture_id("course"), organization_id=org, owner_id=fixture_id("coordinator")
            )
        )
        for n in (1, 2):
            session.add(
                CourseRun(
                    id=fixture_id(f"run-{n}"),
                    organization_id=org,
                    course_id=fixture_id("course"),
                    title=f"Поток {n} · сентябрь",
                    timezone="Europe/Moscow",
                    starts_at=now - timedelta(days=7),
                    ends_at=now + timedelta(days=60),
                    status="active",
                    revision=0,
                )
            )
            session.add(
                Homework(
                    id=fixture_id(f"homework-{n}"),
                    organization_id=org,
                    course_id=fixture_id("course"),
                    title=f"Задание {n}: HTTP API",
                    revision=0,
                )
            )
        await session.flush()
        for n in (1, 2):
            session.add(
                WorkspaceRunSettings(
                    id=fixture_id(f"run-{n}"), organization_id=org, priority="assigned", revision=0
                )
            )
            for key, _, role in IDENTITIES:
                if role == "methodologist":
                    continue
                session.add(
                    CourseMembership(
                        id=fixture_id(f"{n}-{key}-enrollment"),
                        organization_id=org,
                        course_run_id=fixture_id(f"run-{n}"),
                        user_id=fixture_id(key),
                        kind=role,
                        status="active",
                        source="invitation",
                        joined_at=now,
                    )
                )
            session.add(
                HomeworkVersion(
                    id=fixture_id(f"version-{n}"),
                    organization_id=org,
                    homework_id=fixture_id(f"homework-{n}"),
                    version_number=1,
                    revision=0,
                    student_text=(
                        "Реализуйте HTTP API для списка задач. Опишите решения и проверки в README."
                    ),
                    max_score=Decimal("10"),
                    artifact_kinds=["github", "google_docs"],
                    estimated_review_minutes=30,
                )
            )
            session.add(
                CourseRunHomework(
                    id=fixture_id(f"publication-{n}"),
                    organization_id=org,
                    course_run_id=fixture_id(f"run-{n}"),
                    homework_id=fixture_id(f"homework-{n}"),
                    status="active",
                    revision=0,
                )
            )
        await session.flush()
        for n in (1, 2):
            session.add(
                CriterionSet(
                    id=fixture_id(f"criteria-{n}"),
                    organization_id=org,
                    homework_version_id=fixture_id(f"version-{n}"),
                )
            )
            session.add(
                CourseRunHomeworkPublication(
                    id=fixture_id(f"history-{n}"),
                    organization_id=org,
                    course_run_homework_id=fixture_id(f"publication-{n}"),
                    homework_id=fixture_id(f"homework-{n}"),
                    homework_version_id=fixture_id(f"version-{n}"),
                    publication_sequence=1,
                    submission_deadline=now + timedelta(days=7),
                    review_deadline=now + timedelta(days=10),
                    published_at=now,
                )
            )
        await session.flush()
        for n in (1, 2):
            publication = await session.get(CourseRunHomework, fixture_id(f"publication-{n}"))
            assert publication
            publication.current_publication_id = fixture_id(f"history-{n}")
            session.add(
                PublicationPolicy(
                    id=publication.id,
                    organization_id=org,
                    revision=0,
                    self_review_limit=n,
                    policy={
                        "self_review_limit": n,
                        "pass_score": 5,
                        "revision_days": 7,
                        "penalty_per_day": 0.5,
                        "max_resubmissions": 3,
                    },
                )
            )
            for pos, title in enumerate(("API выполняет требования", "README и проверки"), 1):
                session.add(
                    Criterion(
                        id=fixture_id(f"criterion-{n}-{pos}"),
                        organization_id=org,
                        criterion_set_id=fixture_id(f"criteria-{n}"),
                        stable_key=f"criterion-{pos}",
                        position=pos,
                        title=title,
                        description="Оцените полноту реализации и доказательства проверки.",
                        max_points=Decimal("5"),
                        active=True,
                    )
                )
            for student in (1, 2):
                session.add(
                    StudentReviewerAssignment(
                        id=fixture_id(f"assignment-{n}-{student}"),
                        organization_id=org,
                        course_run_id=fixture_id(f"run-{n}"),
                        student_id=fixture_id(f"student-{student}"),
                        reviewer_id=fixture_id(f"reviewer-{student}"),
                        revision=0,
                    )
                )
                artifact_id = fixture_id(f"artifact-{n}-{student}")
                content = (
                    f"# Учебная работа\nГруппа {n}, студент {student}.\n"  # noqa: RUF001
                    "GET /tasks возвращает список задач.\n"
                ).encode()
                stored = await asyncio.to_thread(
                    runtime.object_storage.upload,
                    organization_id=str(org),
                    artifact_version_id=str(artifact_id),
                    source=[content],
                    media_type="text/markdown",
                )
                session.add(
                    WorkspaceArtifact(
                        id=artifact_id,
                        organization_id=org,
                        owner_id=fixture_id(f"student-{student}"),
                        filename="README.md",
                        media_type="text/markdown",
                        object_key=stored.key,
                        digest=stored.content_digest,
                        byte_size=stored.byte_size,
                        private=False,
                        provenance={"fixture": True},
                    )
                )
                await session.flush()
                session.add(
                    WorkDraft(
                        id=fixture_id(f"draft-{n}-{student}"),
                        organization_id=org,
                        publication_id=publication.id,
                        student_id=fixture_id(f"student-{student}"),
                        upload_id=artifact_id,
                        artifact_url="",
                        comment="Локальная демонстрационная работа.",
                        revision=0,
                    )
                )
        await session.flush()
        await _ensure_fixture_quotas(session, org)
        for n in (1, 2):
            for student in (1, 2):
                actor = RequestActor(
                    organization_id=org,
                    actor_type="user",
                    user_id=fixture_id(f"student-{student}"),
                    roles=frozenset({"student"}),
                    membership_revision=0,
                    auth_epoch=0,
                )
                await submit_uploaded_draft(
                    runtime, session, actor, fixture_id(f"draft-{n}-{student}"), 0
                )
    return True


async def _ensure_fixture_quotas(session: AsyncSession, org: UUID) -> None:
    drafts = (
        await session.scalars(
            select(WorkDraft)
            .outerjoin(
                SelfReviewQuota,
                (SelfReviewQuota.draft_id == WorkDraft.id)
                & (SelfReviewQuota.organization_id == WorkDraft.organization_id),
            )
            .where(WorkDraft.organization_id == org, SelfReviewQuota.id.is_(None))
        )
    ).all()
    for draft in drafts:
        session.add(
            SelfReviewQuota(
                id=uuid5(draft.id, "fixture-quota"),
                organization_id=org,
                draft_id=draft.id,
                used=0,
                reserved=0,
            )
        )
    await session.flush()


async def _main() -> None:
    settings = get_settings()
    if settings.environment != "local" or not settings.workspace_fixtures:
        raise RuntimeError("Local fixture mode must be explicitly enabled")
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value()
        if settings.s3_access_key_id
        else None,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value()
        if settings.s3_secret_access_key
        else None,
    )
    from botocore.exceptions import ClientError  # type: ignore[import-untyped]

    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
            raise
        client.create_bucket(Bucket=settings.s3_bucket)
    runtime = build_foundation_runtime(settings)
    try:
        changed = await seed(runtime)
        print("Local fixture created." if changed else "Local fixture already exists; preserved.")
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(_main())
