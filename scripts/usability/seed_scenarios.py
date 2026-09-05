#!/usr/bin/env python3
"""Isolated usability fixtures and guarded baseline restore; no production backend changes.

From the repository root:
  python3 scripts/usability/seed_scenarios.py setup
  python3 scripts/usability/seed_scenarios.py status
  python3 scripts/usability/seed_scenarios.py restore

setup refuses an unrelated/nonempty database and never resets existing study work.
restore requires the exact study containers, database marker and baseline digest.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5
from urllib.parse import urlsplit

PROJECT = "workspace-usability-study"
DATABASE = "workspace_usability"
BUCKET = PROJECT
NAMESPACE = UUID("6471c9ad-b5fb-42e1-9432-a3de89c25ad4")
ORG_NAMESPACE = UUID("de0e8fad-8fe5-4b8b-bc85-df5383f7f7bb")
ORG_NAME = "Практикум API · исследование интерфейса"
MARKER = "workspace-usability-v1"
ROOT = (
    Path("/study")
    if os.environ.get("WORKSPACE_USABILITY_STUDY") == "isolated-v1"
    else Path(__file__).resolve().parents[2]
)
CACHE = ROOT / ".cache/usability"
IDENTITIES = (
    ("student-1", "Алексей Иванов", "student"),
    ("student-2", "Мария Петрова", "student"),
    ("reviewer-1", "Елена Смирнова", "reviewer"),
    ("reviewer-2", "Дмитрий Волков", "reviewer"),
    ("coordinator", "Анна Соколова", "methodologist"),
)


def fid(key: str) -> UUID:
    return uuid5(ORG_NAMESPACE, key)


def sid(key: str) -> UUID:
    return uuid5(NAMESPACE, key)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def compose(
    *args: str, capture: bool = False, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess:
    cmd = [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        str(ROOT / "deploy/compose.yaml"),
        "-f",
        str(ROOT / "deploy/compose.usability.yaml"),
        *args,
    ]
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        input=input_bytes,
    )


def guard_containers() -> None:
    for service in ("mysql", "api", "minio", "redis"):
        name = f"{PROJECT}-{service}-1"
        raw = subprocess.check_output(["docker", "inspect", name])
        info = json.loads(raw)[0]
        labels = info["Config"]["Labels"]
        if (
            labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.service") != service
        ):
            raise RuntimeError(f"Refusing unexpected container {name}")
        if service in {"mysql", "minio"}:
            expected_volume = f"{PROJECT}_{service}-data"
            if not any(m.get("Name") == expected_volume for m in info["Mounts"]):
                raise RuntimeError("Refusing unexpected persistent study volume")
        if (
            service == "mysql"
            and "MYSQL_DATABASE=" + DATABASE not in info["Config"]["Env"]
        ):
            raise RuntimeError(
                "Refusing a database other than the fixed usability database"
            )
        if service == "api":
            env = info["Config"]["Env"]
            if (
                "WORKSPACE_USABILITY_STUDY=isolated-v1" not in env
                or "REVIEW_PLATFORM_S3_BUCKET=" + BUCKET not in env
            ):
                raise RuntimeError("Study marker or bucket mismatch")


def inside(*args: str) -> bytes:
    return compose(
        "exec", "-T", "api", "python", "/study/seed_scenarios.py", *args, capture=True
    ).stdout


def mysql(script: str) -> bytes:
    return compose(
        "exec",
        "-T",
        "mysql",
        "sh",
        "-ec",
        f'exec env MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --default-character-set=utf8mb4 -uroot --batch --skip-column-names {DATABASE}',
        capture=True,
        input_bytes=script.encode(),
    ).stdout


def guard_database() -> None:
    expected = fid("org").hex
    value = mysql('SELECT CONCAT(id, ":", name) FROM organization;').decode().strip()
    if value != f"{expected}:{ORG_NAME}":
        raise RuntimeError("Refusing non-study organization data")


def dump_baseline() -> None:
    guard_containers()
    guard_database()
    # An API session or audit mutation means someone already started a journey.
    if (
        mysql(
            "SELECT (SELECT COUNT(*) FROM session)+(SELECT COUNT(*) FROM audit_event);"
        ).strip()
        != b"0"
    ):
        raise RuntimeError(
            "Baseline may only be captured immediately after seed, before journeys"
        )
    target = CACHE / "baseline.sql"
    if target.exists():
        raise RuntimeError("Baseline already exists; refusing to overwrite it")
    dump = compose(
        "exec",
        "-T",
        "mysql",
        "sh",
        "-ec",
        f'exec env MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump --default-character-set=utf8mb4 -uroot --single-transaction --set-gtid-purged=OFF --no-tablespaces --skip-comments {DATABASE}',
        capture=True,
    ).stdout
    target.write_bytes(dump)
    write_json(
        CACHE / "baseline.json",
        {
            "project": PROJECT,
            "database": DATABASE,
            "marker": MARKER,
            "sha256": hashlib.sha256(dump).hexdigest(),
            "created_at": datetime.now(UTC).isoformat(),
            "seed_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )


def restore() -> None:
    guard_containers()
    guard_database()
    meta = json.loads((CACHE / "baseline.json").read_text())
    dump = (CACHE / "baseline.sql").read_bytes()
    if (meta.get("project"), meta.get("database"), meta.get("marker")) != (
        PROJECT,
        DATABASE,
        MARKER,
    ):
        raise RuntimeError("Baseline identity mismatch")
    if hashlib.sha256(dump).hexdigest() != meta.get("sha256"):
        raise RuntimeError("Baseline digest mismatch")
    # Only this exact study API is stopped. No other compose project is addressed.
    compose("stop", "api")
    try:
        compose(
            "exec",
            "-T",
            "mysql",
            "sh",
            "-ec",
            f'exec env MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --default-character-set=utf8mb4 -uroot {DATABASE}',
            input_bytes=dump,
        )
    finally:
        compose("start", "--wait", "api")
    guard_database()
    print(
        "Study database restored; immutable baseline objects remain in the study bucket."
    )


async def existing_manifest(session) -> dict:
    """Recover the deterministic scenario map without changing existing study rows."""
    from review_platform.infrastructure.db.models import (
        Organization,
        Course,
        CourseRun,
        Homework,
        SubmissionVersion,
    )
    from review_platform.infrastructure.db.models.workspace import ReviewOutcome

    org = await session.get(Organization, fid("org"))
    result = {
        "marker": MARKER,
        "project": PROJECT,
        "database": DATABASE,
        "organization_id": str(org.id),
        "seeded_at": org.created_at.isoformat(),
        "api_url": "http://127.0.0.1:18010",
        "frontend_url": "http://127.0.0.1:5180",
        "scenarios": [],
    }
    definitions = [
        (
            "S1",
            "s1",
            "student-1",
            "course-learning",
            "run-s1",
            "Опубликовано; нет черновика и сдачи; доступны 2 самопроверки.",
            "Подготовить и сдать работу.",
            None,
            "new-api.md",
        ),
        (
            "S2",
            "s2",
            "student-2",
            "course-learning",
            "run-s2",
            "Первая попытка возвращена с замечаниями; срок исправлений через 3 дня.",
            "Отправить исправленную попытку, сохранив историю.",
            1,
            "revised-api.md",
        ),
        (
            "R1",
            "r1",
            "reviewer-1",
            "course-review",
            "run-r1",
            "Первая сдача ожидает открытия ревью.",
            "Проверить и опубликовать результат.",
            1,
            None,
        ),
        (
            "R2",
            "r2",
            "reviewer-2",
            "course-review",
            "run-r2",
            "Вторая сдача после возврата; замечания к первой опубликованы.",
            "Проверить исправления и опубликовать результат второй попытки.",
            2,
            None,
        ),
        (
            "C2",
            "c2",
            "coordinator",
            "course-team",
            "run-team",
            "Работа взята reviewer1 четыре дня назад; есть отдельная опубликованная работа.",
            "Обнаружить задержку, передать работу/закрепление reviewer2 и выгрузить ведомость.",
            1,
            None,
        ),
    ]
    for (
        code,
        key,
        actor,
        course_key,
        run_key,
        initial,
        expected,
        number,
        fixture,
    ) in definitions:
        course = await session.get(Course, sid(course_key))
        run = await session.get(CourseRun, sid(run_key))
        homework = await session.get(Homework, sid(key + "-homework"))
        if not course or not run or not homework:
            raise RuntimeError(
                "The study fixture is incomplete; refusing to infer missing rows"
            )
        value = {
            "code": code,
            "actor": actor,
            "course_id": str(course.id),
            "course_name": course.title,
            "course_run_id": str(run.id),
            "course_run_name": run.title,
            "homework_id": str(homework.id),
            "homework_name": homework.title,
            "publication_id": str(sid(key + "-publication")),
            "initial_state": initial,
            "expected_business_result": expected,
        }
        if number:
            version = await session.get(
                SubmissionVersion, sid(key + f"-attempt-{number}")
            )
            if version is None:
                raise RuntimeError("The study submission fixture is missing")
            value.update(
                submission_id=str(version.submission_id),
                submission_version_id=str(version.id),
                artifact_id=str(version.artifact_version_id),
                draft_id=str(sid(key + "-draft")),
            )
        if code in {"S2", "R2", "C2"}:
            value.update(
                review_case_id=str(sid(key + "-case")),
                review_iteration_id=str(sid(key + "-iteration")),
            )
        if code == "S1":
            value["self_review_remaining"] = 2
        if code == "S2":
            outcome = await session.get(ReviewOutcome, sid(key + "-iteration"))
            value["revision_deadline"] = outcome.revision_deadline.isoformat()
        if code == "R2":
            value["previous_attempt_id"] = str(sid("r2-attempt-1"))
        if code == "C2":
            value.update(
                stuck_days=4,
                completed_homework_name="Итоги каталога",
                completed_submission_id=str(sid("c2-completed-submission")),
            )
        if fixture:
            value["fixture_file"] = fixture
        result["scenarios"].append(value)
    course = await session.get(Course, sid("course-management"))
    result["scenarios"].insert(
        4,
        {
            "code": "C1",
            "actor": "coordinator",
            "course_id": str(course.id),
            "course_name": course.title,
            "initial_state": "Существует курс без потоков и заданий.",
            "expected_business_result": "Создать поток и опубликовать задание.",
        },
    )
    return result


async def internal_seed() -> None:
    from sqlalchemy import select
    from review_platform.settings import get_settings
    from review_platform.application.foundation_runtime import build_foundation_runtime
    from review_platform.infrastructure.db.models import (
        Organization,
        User,
        OrganizationMembership,
        Course,
        CourseRun,
        CourseMembership,
        Homework,
        HomeworkVersion,
        CriterionSet,
        Criterion,
        CourseRunHomework,
        CourseRunHomeworkPublication,
        ArtifactReference,
        ArtifactVersion,
        Submission,
        SubmissionVersion,
        ReviewCase,
        ReviewIteration,
        ReviewRevision,
        ReviewCriterionDecision,
        ReviewPublication,
        ReviewResponsibility,
    )
    from review_platform.infrastructure.db.models.workspace import (
        WorkspaceCourseDetails,
        WorkspaceRunSettings,
        PublicationPolicy,
        HomeworkPrivateDetails,
        WorkspaceArtifact,
        WorkDraft,
        SelfReviewQuota,
        SubmissionPolicySnapshot,
        ReviewOutcome,
        WorkspacePublishedGrade,
        StudentReviewerAssignment,
    )
    import boto3
    from botocore.exceptions import ClientError

    settings = get_settings()
    if (
        os.environ.get("WORKSPACE_USABILITY_STUDY") != "isolated-v1"
        or settings.environment != "local"
        or not settings.workspace_fixtures
        or settings.live_providers_enabled
        or settings.s3_bucket != BUCKET
        or not settings.database_url
        or urlsplit(settings.database_url).hostname != "mysql"
        or urlsplit(settings.database_url).path != "/" + DATABASE
        or settings.s3_endpoint_url != "http://minio:9000"
    ):
        raise RuntimeError(
            "This fixture can only run inside the fixed local study configuration"
        )
    storage = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name=settings.s3_region,
    )
    try:
        storage.head_bucket(Bucket=BUCKET)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
            raise
        storage.create_bucket(Bucket=BUCKET)
    runtime = build_foundation_runtime(settings)
    org = fid("org")
    now = datetime.now(UTC).replace(microsecond=0)
    manifest = {
        "marker": MARKER,
        "project": PROJECT,
        "database": DATABASE,
        "organization_id": str(org),
        "seeded_at": now.isoformat(),
        "api_url": "http://127.0.0.1:18010",
        "frontend_url": "http://127.0.0.1:5180",
        "scenarios": [],
    }
    try:
        async with runtime.transaction() as session:
            organizations = (await session.scalars(select(Organization))).all()
            if organizations:
                if (
                    len(organizations) == 1
                    and organizations[0].id == org
                    and organizations[0].name == ORG_NAME
                ):
                    recovered = await existing_manifest(session)
                    print(
                        json.dumps(
                            {"created": False, "manifest": recovered},
                            ensure_ascii=False,
                        )
                    )
                    return
                raise RuntimeError(
                    "Refusing to seed a database containing unrelated organizations"
                )
            if await session.scalar(select(User.id).limit(1)):
                raise RuntimeError(
                    "Refusing to seed a database containing unrelated users"
                )
            session.add(
                Organization(id=org, slug="workspace-local-fixture-v2", name=ORG_NAME)
            )
            for key, name, role in IDENTITIES:
                session.add(User(id=fid(key), display_name=name, status="active"))
            await session.flush()
            for key, _, role in IDENTITIES:
                session.add(
                    OrganizationMembership(
                        id=sid(key + "-membership"),
                        organization_id=org,
                        user_id=fid(key),
                        roles=[role],
                        status="active",
                        revision=0,
                        auth_epoch=0,
                    )
                )
            await session.flush()

            async def course(key: str, title: str) -> UUID:
                identity = sid(key)
                session.add(
                    Course(
                        id=identity,
                        organization_id=org,
                        title=title,
                        description="Учебный практикум. Локальные данные исследования.",
                        source_kind="standalone",
                        status="active",
                        revision=0,
                    )
                )
                await session.flush()
                session.add(
                    WorkspaceCourseDetails(
                        id=identity, organization_id=org, owner_id=fid("coordinator")
                    )
                )
                return identity

            async def run(key: str, course_id: UUID, title: str) -> UUID:
                identity = sid(key)
                session.add(
                    CourseRun(
                        id=identity,
                        organization_id=org,
                        course_id=course_id,
                        title=title,
                        timezone="Europe/Moscow",
                        starts_at=now - timedelta(days=14),
                        ends_at=now + timedelta(days=45),
                        status="active",
                        revision=0,
                    )
                )
                await session.flush()
                session.add(
                    WorkspaceRunSettings(
                        id=identity,
                        organization_id=org,
                        priority="assigned",
                        revision=0,
                    )
                )
                for actor, _, role in IDENTITIES:
                    if role == "methodologist":
                        continue
                    session.add(
                        CourseMembership(
                            id=sid(key + "-" + actor),
                            organization_id=org,
                            course_run_id=identity,
                            user_id=fid(actor),
                            kind=role,
                            source="invitation",
                            status="active",
                            joined_at=now - timedelta(days=14),
                        )
                    )
                for number in (1, 2):
                    session.add(
                        StudentReviewerAssignment(
                            id=sid(key + f"-assignment-{number}"),
                            organization_id=org,
                            course_run_id=identity,
                            student_id=fid(f"student-{number}"),
                            reviewer_id=fid(f"reviewer-{number}"),
                            revision=0,
                        )
                    )
                return identity

            async def homework(
                key: str, course_id: UUID, run_id: UUID, title: str
            ) -> dict:
                ids = {
                    name: sid(key + "-" + name)
                    for name in (
                        "homework",
                        "version",
                        "criteria",
                        "publication",
                        "history",
                    )
                }
                session.add(
                    Homework(
                        id=ids["homework"],
                        organization_id=org,
                        course_id=course_id,
                        title=title,
                        revision=0,
                    )
                )
                await session.flush()
                session.add(
                    HomeworkVersion(
                        id=ids["version"],
                        organization_id=org,
                        homework_id=ids["homework"],
                        version_number=1,
                        revision=0,
                        student_text="Реализуйте HTTP API списка задач. Опишите основные сценарии и проверки ошибочных запросов в README.",
                        max_score=Decimal("10"),
                        artifact_kinds=["github", "google_docs"],
                        estimated_review_minutes=30,
                    )
                )
                session.add(
                    CourseRunHomework(
                        id=ids["publication"],
                        organization_id=org,
                        course_run_id=run_id,
                        homework_id=ids["homework"],
                        status="active",
                        revision=0,
                    )
                )
                await session.flush()
                session.add(
                    CriterionSet(
                        id=ids["criteria"],
                        organization_id=org,
                        homework_version_id=ids["version"],
                    )
                )
                session.add(
                    CourseRunHomeworkPublication(
                        id=ids["history"],
                        organization_id=org,
                        course_run_homework_id=ids["publication"],
                        homework_id=ids["homework"],
                        homework_version_id=ids["version"],
                        publication_sequence=1,
                        submission_deadline=now + timedelta(days=7),
                        review_deadline=now + timedelta(days=10),
                        published_at=now - timedelta(days=10),
                    )
                )
                await session.flush()
                relation = await session.get(CourseRunHomework, ids["publication"])
                relation.current_publication_id = ids["history"]
                policy = {
                    "self_review_limit": 2,
                    "pass_score": 7,
                    "revision_days": 3,
                    "penalty_per_day": 0,
                    "max_resubmissions": 3,
                }
                session.add(
                    PublicationPolicy(
                        id=ids["publication"],
                        organization_id=org,
                        revision=0,
                        self_review_limit=2,
                        policy=policy,
                    )
                )
                session.add(
                    HomeworkPrivateDetails(
                        id=ids["version"],
                        organization_id=org,
                        revision=0,
                        allowed_sources=["upload", "github", "google_docs"],
                        reviewer_guidance="Проверьте реальные подтверждения выполнения требований.",
                        criterion_classes={},
                    )
                )
                for number, title_ in (
                    (1, "Основной сценарий API"),
                    (2, "Ошибки и воспроизводимые проверки"),
                ):
                    session.add(
                        Criterion(
                            id=sid(key + f"-criterion-{number}"),
                            organization_id=org,
                            criterion_set_id=ids["criteria"],
                            stable_key=f"criterion-{number}",
                            position=number,
                            title=title_,
                            description="Выполнено, если поведение подтверждено примерами входа и ожидаемого ответа.",
                            max_points=Decimal("5"),
                            active=True,
                        )
                    )
                await session.flush()
                return {
                    **ids,
                    "run": run_id,
                    "course": course_id,
                    "key": key,
                    "title": title,
                    "policy": policy,
                }

            async def attempt(
                info: dict,
                student: str,
                number: int,
                fixture: str,
                days_ago: int,
                comment: str,
            ) -> dict:
                key = info["key"]
                artifact_id = sid(key + f"-artifact-{number}")
                content = (Path("/study/fixtures") / fixture).read_bytes()
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
                        owner_id=fid(student),
                        filename=fixture,
                        media_type="text/markdown",
                        object_key=stored.key,
                        digest=stored.content_digest,
                        byte_size=len(content),
                        private=False,
                        provenance={"fixture": MARKER},
                    )
                )
                reference_id = sid(key + f"-reference-{number}")
                session.add(
                    ArtifactReference(
                        id=reference_id,
                        organization_id=org,
                        provider="upload",
                        credential_binding_id=None,
                        credential_binding_version=None,
                        original_url="upload:" + str(artifact_id),
                        locator={"workspace_artifact_id": str(artifact_id)},
                        read_capability="available",
                        feedback_capability="not_supported",
                        last_checked_at=now,
                        revision=0,
                    )
                )
                await session.flush()
                session.add(
                    ArtifactVersion(
                        id=artifact_id,
                        organization_id=org,
                        artifact_reference_id=reference_id,
                        provider_version=stored.content_digest,
                        content_digest=stored.content_digest,
                        object_key=stored.key,
                        media_type="text/markdown",
                        byte_size=len(content),
                        captured_at=now - timedelta(days=days_ago),
                        artifact_metadata={"source": "upload", "filename": fixture},
                    )
                )
                submission_id = sid(key + "-submission")
                submission = await session.get(Submission, submission_id)
                if not submission:
                    submission = Submission(
                        id=submission_id,
                        organization_id=org,
                        course_run_homework_id=info["publication"],
                        course_run_id=info["run"],
                        homework_id=info["homework"],
                        student_id=fid(student),
                        revision=0,
                    )
                    session.add(submission)
                await session.flush()
                version_id = sid(key + f"-attempt-{number}")
                session.add(
                    SubmissionVersion(
                        id=version_id,
                        organization_id=org,
                        submission_id=submission_id,
                        course_run_id=info["run"],
                        homework_id=info["homework"],
                        comment=comment,
                        sequence=number,
                        homework_version_id=info["version"],
                        artifact_reference_id=reference_id,
                        artifact_version_id=artifact_id,
                        submitted_at=now - timedelta(days=days_ago),
                        effective_deadline=now + timedelta(days=7),
                        phase="before_deadline",
                        status="ready",
                        revision=0,
                    )
                )
                await session.flush()
                submission.current_predeadline_version_id = version_id
                submission.revision = number
                session.add(
                    SubmissionPolicySnapshot(
                        id=version_id,
                        organization_id=org,
                        policy=info["policy"],
                        policy_revision=0,
                    )
                )
                draft = await session.get(WorkDraft, sid(key + "-draft"))
                if draft is None:
                    draft = WorkDraft(
                        id=sid(key + "-draft"),
                        organization_id=org,
                        publication_id=info["publication"],
                        student_id=fid(student),
                        artifact_url="",
                        upload_id=artifact_id,
                        comment=comment,
                        revision=number,
                    )
                    session.add(draft)
                    await session.flush()
                    session.add(
                        SelfReviewQuota(
                            id=sid(key + "-quota"),
                            organization_id=org,
                            draft_id=draft.id,
                            used=0,
                            reserved=0,
                        )
                    )
                else:
                    draft.upload_id, draft.comment, draft.revision = (
                        artifact_id,
                        comment,
                        number,
                    )
                await session.flush()
                return {
                    "submission": submission_id,
                    "version": version_id,
                    "artifact": artifact_id,
                    "student": student,
                    "number": number,
                    "draft": draft.id,
                }

            async def review(info: dict, work: dict, reviewer: str, state: str) -> dict:
                key = info["key"]
                case_id, iteration_id = sid(key + "-case"), sid(key + "-iteration")
                session.add(
                    ReviewCase(
                        id=case_id,
                        organization_id=org,
                        course_run_id=info["run"],
                        homework_id=info["homework"],
                        student_id=fid(work["student"]),
                        revision=1,
                    )
                )
                await session.flush()
                iteration = ReviewIteration(
                    id=iteration_id,
                    organization_id=org,
                    review_case_id=case_id,
                    course_run_id=info["run"],
                    homework_id=info["homework"],
                    student_id=fid(work["student"]),
                    iteration_number=1,
                    submission_version_id=work["version"],
                    artifact_version_id=work["artifact"],
                    homework_version_id=info["version"],
                    criterion_set_id=info["criteria"],
                    effective_deadline=now + timedelta(days=7),
                    responsible_reviewer_id=fid(reviewer),
                    status="in_review" if state == "stuck" else "published",
                    origin="initial",
                    revision=1,
                    created_at=now - timedelta(days=4),
                    updated_at=now - timedelta(days=4),
                )
                session.add(iteration)
                await session.flush()
                case = await session.get(ReviewCase, case_id)
                case.current_iteration_id = iteration_id
                session.add(
                    ReviewResponsibility(
                        id=sid(key + "-participation"),
                        organization_id=org,
                        review_case_id=case_id,
                        review_iteration_id=iteration_id,
                        reviewer_id=fid(reviewer),
                        actor_id=fid(reviewer),
                        action="started",
                        occurred_at=now - timedelta(days=4),
                    )
                )
                if state != "stuck":
                    revision_id = sid(key + "-review-revision")
                    score = Decimal("4") if state == "returned" else Decimal("8")
                    feedback = (
                        "Добавьте проверку пустого заголовка и неверного значения фильтра. В README нужны воспроизводимые шаги."
                        if state == "returned"
                        else "Основные сценарии и проверки выполнены."
                    )
                    session.add(
                        ReviewRevision(
                            id=revision_id,
                            organization_id=org,
                            review_iteration_id=iteration_id,
                            revision_number=1,
                            author_user_id=fid(reviewer),
                            feedback=feedback,
                            total_score=score,
                            created_at=now - timedelta(days=2),
                        )
                    )
                    await session.flush()
                    iteration.current_revision_id = revision_id
                    for number in (1, 2):
                        session.add(
                            ReviewCriterionDecision(
                                id=sid(key + f"-decision-{number}"),
                                organization_id=org,
                                review_revision_id=revision_id,
                                criterion_id=sid(key + f"-criterion-{number}"),
                                points=score / 2,
                                decision="manual",
                                reason=feedback,
                                evidence_ids=[],
                            )
                        )
                    publication_id = sid(key + "-review-publication")
                    session.add(
                        ReviewPublication(
                            id=publication_id,
                            organization_id=org,
                            review_iteration_id=iteration_id,
                            review_revision_id=revision_id,
                            publication_version=1,
                            published_by=fid(reviewer),
                            published_at=now - timedelta(days=2),
                            status="published",
                            revision=0,
                        )
                    )
                    session.add(
                        ReviewOutcome(
                            id=iteration_id,
                            organization_id=org,
                            revision=0,
                            decision="needs_changes"
                            if state == "returned"
                            else "passed",
                            revision_deadline=now + timedelta(days=3)
                            if state == "returned"
                            else None,
                            reason=feedback,
                        )
                    )
                    await session.flush()
                    session.add(
                        WorkspacePublishedGrade(
                            id=publication_id,
                            organization_id=org,
                            details={
                                "raw_score": float(score),
                                "penalty_days": 0,
                                "penalty_rate": 0,
                                "penalty": 0,
                                "final_score": float(score),
                                "pass_score": 7,
                                "policy_revision": 0,
                            },
                        )
                    )
                await session.flush()
                return {
                    "review_case_id": str(case_id),
                    "review_iteration_id": str(iteration_id),
                }

            def scenario(
                code: str,
                actor: str,
                info: dict,
                initial: str,
                expected: str,
                work: dict | None = None,
                **extra,
            ) -> None:
                value = {
                    "code": code,
                    "actor": actor,
                    "course_id": str(info["course"]),
                    "course_name": info["course_name"],
                    "course_run_id": str(info["run"]),
                    "course_run_name": info["run_name"],
                    "homework_id": str(info["homework"]),
                    "homework_name": info["title"],
                    "publication_id": str(info["publication"]),
                    "initial_state": initial,
                    "expected_business_result": expected,
                    **extra,
                }
                if work:
                    value.update(
                        {
                            "submission_id": str(work["submission"]),
                            "submission_version_id": str(work["version"]),
                            "artifact_id": str(work["artifact"]),
                            "draft_id": str(work["draft"]),
                        }
                    )
                manifest["scenarios"].append(value)

            learning = await course(
                "course-learning", "Практикум API · задания студентов"
            )
            s1_run = await run(
                "run-s1", learning, "Самостоятельная работа · новые задания"
            )
            s1 = await homework("s1", learning, s1_run, "Первый API списка задач")
            s1.update(
                course_name="Практикум API · задания студентов",
                run_name="Самостоятельная работа · новые задания",
            )
            scenario(
                "S1",
                "student-1",
                s1,
                "Опубликовано; у студента нет черновика и сдачи; доступны 2 самопроверки.",
                "Студент подготовил работу, при желании получил самопроверку и явно сдал её.",
                fixture_file="new-api.md",
                self_review_remaining=2,
            )
            s2_run = await run("run-s2", learning, "Самостоятельная работа · доработка")
            s2 = await homework("s2", learning, s2_run, "Надёжная обработка ошибок")
            s2.update(
                course_name=s1["course_name"],
                run_name="Самостоятельная работа · доработка",
            )
            s2_work = await attempt(
                s2,
                "student-2",
                1,
                "returned-api.md",
                5,
                "Первая версия. Ошибочные запросы пока не проверены.",
            )
            s2_review = await review(s2, s2_work, "reviewer-1", "returned")
            scenario(
                "S2",
                "student-2",
                s2,
                "Первая попытка опубликована с needs_changes; файл, комментарий и замечания доступны; срок исправлений через 3 дня.",
                "Исправленная попытка отправлена до срока; история первой попытки сохранена.",
                s2_work,
                fixture_file="revised-api.md",
                revision_deadline=(now + timedelta(days=3)).isoformat(),
                **s2_review,
            )
            reviewing = await course("course-review", "Практикум API · проверка работ")
            r1_run = await run(
                "run-r1", reviewing, "Основная очередь · первые проверки"
            )
            r1 = await homework("r1", reviewing, r1_run, "Пагинация списка задач")
            r1.update(
                course_name="Практикум API · проверка работ",
                run_name="Основная очередь · первые проверки",
            )
            r1_work = await attempt(
                r1, "student-1", 1, "new-api.md", 1, "Готово к первой проверке."
            )
            scenario(
                "R1",
                "reviewer-1",
                r1,
                "Новая сдача в очереди; ревью ещё не открыто.",
                "Ревьюер проверил работу по критериям, сохранил и опубликовал согласованный результат.",
                r1_work,
            )
            r2_run = await run(
                "run-r2", reviewing, "Повторная очередь · исправленные работы"
            )
            r2 = await homework(
                "r2", reviewing, r2_run, "Повторная проверка фильтрации"
            )
            r2.update(
                course_name=r1["course_name"],
                run_name="Повторная очередь · исправленные работы",
            )
            r2_first = await attempt(
                r2,
                "student-2",
                1,
                "returned-api.md",
                5,
                "Первая попытка: фильтр ещё без валидации.",
            )
            r2_review = await review(r2, r2_first, "reviewer-2", "returned")
            r2_work = await attempt(
                r2,
                "student-2",
                2,
                "revised-api.md",
                0,
                "Исправлены пустой заголовок и неизвестное значение фильтра.",
            )
            scenario(
                "R2",
                "reviewer-2",
                r2,
                "Вторая сдача после возврата; предыдущие замечания опубликованы; новая попытка ждёт ревью.",
                "Ревьюер сверил исправления с прошлым результатом и опубликовал новую оценку.",
                r2_work,
                previous_attempt_id=str(r2_first["version"]),
                **r2_review,
            )
            management = await course(
                "course-management", "Практикум · учебная программа"
            )
            manifest["scenarios"].append(
                {
                    "code": "C1",
                    "actor": "coordinator",
                    "course_id": str(management),
                    "course_name": "Практикум · учебная программа",
                    "initial_state": "Существует курс без потоков и заданий.",
                    "expected_business_result": "Координатор создал поток и опубликовал задание с условиями и настройками.",
                }
            )
            team = await course("course-team", "Практикум · команда и нагрузка")
            team_run = await run("run-team", team, "Осенний поток · распределение")
            c2 = await homework("c2", team, team_run, "API каталога")
            c2.update(
                course_name="Практикум · команда и нагрузка",
                run_name="Осенний поток · распределение",
            )
            c2_work = await attempt(
                c2,
                "student-1",
                1,
                "new-api.md",
                5,
                "Ожидаю результат проверки каталога.",
            )
            c2_review = await review(c2, c2_work, "reviewer-1", "stuck")
            completed = await homework("c2-completed", team, team_run, "Итоги каталога")
            completed_work = await attempt(
                completed,
                "student-2",
                1,
                "revised-api.md",
                5,
                "Все проверки каталога завершены.",
            )
            await review(completed, completed_work, "reviewer-2", "passed")
            scenario(
                "C2",
                "coordinator",
                c2,
                "Работа студента1 взята reviewer1 четыре дня назад; primary reviewer1. В потоке есть отдельный опубликованный результат студента2.",
                "Координатор обнаружил задержку, передал ответственность/закрепление reviewer2 и выгрузил ведомость выбранного потока.",
                c2_work,
                stuck_days=4,
                completed_homework_name="Итоги каталога",
                completed_submission_id=str(completed_work["submission"]),
                **c2_review,
            )
        print(json.dumps({"created": True, "manifest": manifest}, ensure_ascii=False))
    finally:
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["setup", "status", "snapshot", "restore", "inside-seed"]
    )
    args = parser.parse_args()
    if args.action == "inside-seed":
        asyncio.run(internal_seed())
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / ".gitignore").write_text("*\n")
    if args.action == "setup":
        compose("up", "--build", "-d", "--wait", "mysql", "redis", "minio", "api")
        guard_containers()
        seeded = json.loads(inside("inside-seed"))
        if not (CACHE / "scenarios.json").exists():
            write_json(CACHE / "scenarios.json", seeded["manifest"])
        print(
            "Study fixtures created."
            if seeded["created"]
            else "Study fixtures already exist; preserved."
        )
        if not (CACHE / "baseline.sql").exists():
            dump_baseline()
        print(f"Manifest: {CACHE / 'scenarios.json'}")
    elif args.action == "snapshot":
        dump_baseline()
    elif args.action == "restore":
        restore()
    else:
        guard_containers()
        guard_database()
        print(
            json.dumps(
                {
                    "project": PROJECT,
                    "database": DATABASE,
                    "api": "http://127.0.0.1:18010",
                    "frontend": "http://127.0.0.1:5180",
                    "manifest": str(CACHE / "scenarios.json"),
                    "baseline": (CACHE / "baseline.sql").exists(),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
