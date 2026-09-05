"""Durable, scoped exports using only published results and explicit audience columns."""

from __future__ import annotations

import asyncio
import csv
import re
from dataclasses import dataclass, field
from datetime import timedelta
from io import BytesIO, StringIO
from urllib.parse import urlsplit
from uuid import UUID
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor, Role
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    require_roles,
    row,
)
from review_platform.application.workspace.projections import WorkspaceQueries
from review_platform.contracts.workspace import DownloadView, ExportInput, ExportView, WorkItem
from review_platform.infrastructure.db.models import (
    Criterion,
    OrganizationMembership,
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewPublication,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    ReviewOutcome,
    WorkspaceArtifact,
    WorkspaceExport,
)

STUDENT_COLUMNS = {"student_id", "score", "status"}
LABELS = {
    "student_id": "ID студента",
    "score": "Балл",
    "status": "Статус",
    "attempt": "Попытка",
    "feedback": "Отзыв",
    "reviewer_id": "ID ревьюера",
    "artifact_url": "Снимок работы",
    "criterion_points": "Баллы по критериям",
}


@dataclass
class ExportDetails:
    artifact_url: str = ""
    attempt: int = 0
    status: str | None = None
    criteria: dict[str, tuple[str, float]] = field(default_factory=dict)


async def export_details(
    session: AsyncSession, org: UUID, items: list[WorkItem], public_base_url: str = ""
) -> dict[UUID, ExportDetails]:
    publications = (
        await session.execute(
            select(ReviewPublication, ReviewIteration)
            .join(
                ReviewIteration,
                (ReviewIteration.id == ReviewPublication.review_iteration_id)
                & (ReviewIteration.organization_id == org),
            )
            .where(
                ReviewPublication.organization_id == org,
                ReviewPublication.status == "published",
                ReviewIteration.review_case_id.in_(
                    [i.review_case_id for i in items if i.review_case_id]
                ),
            )
            .order_by(ReviewPublication.published_at.desc(), ReviewPublication.id.desc())
        )
    ).all()
    by_case: dict[UUID, tuple[ReviewPublication, ReviewIteration]] = {}
    for publication, iteration in publications:
        by_case.setdefault(iteration.review_case_id, (publication, iteration))
    outcomes = {
        outcome.id: outcome.decision
        for outcome in (
            await session.scalars(
                select(ReviewOutcome).where(
                    ReviewOutcome.organization_id == org,
                    ReviewOutcome.id.in_([iteration.id for _, iteration in by_case.values()]),
                )
            )
        ).all()
    }
    revision_ids = [publication.review_revision_id for publication, _ in by_case.values()]
    decisions = (
        await session.execute(
            select(ReviewCriterionDecision, Criterion)
            .join(
                Criterion,
                (Criterion.id == ReviewCriterionDecision.criterion_id)
                & (Criterion.organization_id == org),
            )
            .where(
                ReviewCriterionDecision.organization_id == org,
                ReviewCriterionDecision.review_revision_id.in_(revision_ids),
            )
            .order_by(Criterion.position, Criterion.id)
        )
    ).all()
    by_revision: dict[UUID, dict[str, tuple[str, float]]] = {}
    for decision, criterion in decisions:
        by_revision.setdefault(decision.review_revision_id, {})[str(criterion.id)] = (
            f"{criterion.title} [{str(criterion.id)[:8]}]",
            float(decision.points),
        )
    chosen = {
        item.submission_id: (
            by_case[item.review_case_id][1].submission_version_id
            if item.score is not None and item.review_case_id in by_case
            else item.submission_version_id
        )
        for item in items
        if item.submission_id is not None
    }
    versions = {
        version.id: version
        for version in (
            await session.scalars(
                select(SubmissionVersion).where(
                    SubmissionVersion.organization_id == org,
                    SubmissionVersion.id.in_([v for v in chosen.values() if v]),
                )
            )
        ).all()
    }
    result = {}
    for item in items:
        if item.submission_id is None:
            continue
        version_id = chosen[item.submission_id]
        version = versions.get(version_id) if version_id else None
        publication = (
            by_case[item.review_case_id][0]
            if item.score is not None and item.review_case_id in by_case
            else None
        )
        result[item.submission_id] = ExportDetails(
            artifact_url=f"{public_base_url.rstrip('/')}/api/v2/artifacts/{version.artifact_version_id}/open"
            if version and version.artifact_version_id
            else "",
            attempt=version.sequence if version else item.attempt,
            status=outcomes.get(publication.review_iteration_id, "published")
            if publication
            else None,
            criteria=by_revision.get(publication.review_revision_id, {}) if publication else {},
        )
    return result


def export_rows(
    options: ExportInput, items: list[WorkItem], details: dict[UUID, ExportDetails] | None = None
) -> list[list[str | float | int]]:
    if options.audience == "students" and not set(options.columns) <= STUDENT_COLUMNS:
        raise WorkspaceFailure(
            "private_columns", "Для студенческой копии доступны только ID, балл и статус.", 422
        )
    details = details or {}
    criterion_labels = {
        identity: value[0]
        for item in items
        if item.submission_id is not None
        for identity, value in details.get(item.submission_id, ExportDetails()).criteria.items()
    }
    header: list[str | float | int] = []
    for column in options.columns:
        header.extend(
            [f"Критерий: {label}" for label in criterion_labels.values()] or [LABELS[column]]
        ) if column == "criterion_points" else header.append(LABELS[column])
    rows: list[list[str | float | int]] = [header]
    for item in items:
        if item.course_run_id != options.course_run_id or (
            options.homework_id is not None and item.homework_id != options.homework_id
        ):
            raise WorkspaceFailure("export_scope", "Работа не входит в область экспорта.", 403)
        if item.score is None and not options.include_unpublished:
            continue
        extra = (
            details.get(item.submission_id, ExportDetails(attempt=item.attempt))
            if item.submission_id
            else ExportDetails()
        )
        values: dict[str, str | float | int] = {
            "student_id": str(item.student_id),
            "score": item.score if item.score is not None else "",
            "status": extra.status or item.status,
            "attempt": extra.attempt,
            "feedback": item.feedback or "",
            "reviewer_id": str(item.published_by) if item.published_by else "",
            "artifact_url": extra.artifact_url,
        }
        cells: list[str | float | int] = []
        for column in options.columns:
            if column == "criterion_points":
                cells.extend(
                    [
                        extra.criteria[key][1] if key in extra.criteria else ""
                        for key in criterion_labels
                    ]
                    or [""]
                )
            else:
                cells.append(values[column])
        rows.append(cells)
    return rows


def csv_bytes(rows: list[list[str | float | int]]) -> bytes:
    target = StringIO()
    writer = csv.writer(target)
    for row_values in rows:
        writer.writerow(
            [
                "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
                for v in row_values
            ]
        )
    return target.getvalue().encode("utf-8-sig")


def xlsx_bytes(rows: list[list[str | float | int]]) -> bytes:
    def column(index: int) -> str:
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    contents = []
    for number, values in enumerate(rows, 1):
        cells = []
        for index, value in enumerate(values, 1):
            location = f"{column(index)}{number}"
            if isinstance(value, float | int):
                cells.append(f'<c r="{location}"><v>{value}</v></c>')
            else:
                clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", value)
                cells.append(
                    f'<c r="{location}" t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(clean)}</t></is></c>"
                )
        contents.append(f'<row r="{number}">{"".join(cells)}</row>')
    result = BytesIO()
    with ZipFile(result, "w", ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/p'
            'ackage/2006/content-types"><Default Extension="rels" ContentType="appl'
            'ication/vnd.openxmlformats-package.relationships+xml"/><Default Extens'
            'ion="xml" ContentType="application/xml"/><Override PartName="/xl/workb'
            'ook.xml" ContentType="application/vnd.openxmlformats-officedocument.sp'
            'readsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1'
            '.xml" ContentType="application/vnd.openxmlformats-officedocument.sprea'
            'dsheetml.worksheet+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlforma'
            'ts.org/package/2006/relationships"><Relationship Id="rId1" Type="http:'
            "//schemas.openxmlformats.org/officeDocument/2006/relationships/officeD"
            'ocument" Target="xl/workbook.xml"/></Relationships>',
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.or'
            'g/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships"><sheets><sheet name="Результаты" sh'
            'eetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlforma'
            'ts.org/package/2006/relationships"><Relationship Id="rId1" Type="http:'
            "//schemas.openxmlformats.org/officeDocument/2006/relationships/workshe"
            'et" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + "".join(contents)
            + "</sheetData></worksheet>",
        )
    return result.getvalue()


async def export_view(
    runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor, identity: UUID
) -> ExportView:
    require_roles(actor, "methodologist")
    job = await row(session, WorkspaceExport, actor.organization_id, identity)
    await course_scope(session, actor, job.course_run_id)
    download = None
    if job.artifact_id:
        artifact = await row(session, WorkspaceArtifact, actor.organization_id, job.artifact_id)
        download = DownloadView(
            filename=artifact.filename,
            url=runtime.object_storage.sign_read(
                key=artifact.object_key,
                organization_id=str(actor.organization_id),
                requested_by_organization_id=str(actor.organization_id),
                artifact_version_id=str(artifact.id),
                expires_in_seconds=900,
                download_filename=artifact.filename,
            ),
            expires_at=runtime.clock() + timedelta(minutes=15),
        )
    return ExportView.model_validate(
        {
            "id": job.id,
            "status": job.status,
            "rows": job.row_count,
            "download": download,
            "error": job.error,
        }
    )


class ExportWorker:
    def __init__(self, runtime: FoundationRuntime):
        self.runtime = runtime

    async def tick(self) -> bool:
        async with self.runtime.transaction() as session:
            job = await session.scalar(
                select(WorkspaceExport)
                .where(
                    WorkspaceExport.status.in_(["queued", "processing"]),
                    or_(
                        WorkspaceExport.lease_until.is_(None),
                        WorkspaceExport.lease_until <= self.runtime.clock(),
                    ),
                )
                .order_by(WorkspaceExport.created_at, WorkspaceExport.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            job.status = "processing"
            job.lease_until = self.runtime.clock() + timedelta(minutes=2)
            identity, org, owner, options = (
                job.id,
                job.organization_id,
                job.owner_id,
                ExportInput.model_validate(job.options),
            )
        try:
            async with self.runtime.transaction() as session:
                # Hold current membership read lock through the snapshot and store.
                member = await session.scalar(
                    select(OrganizationMembership)
                    .where(
                        OrganizationMembership.organization_id == org,
                        OrganizationMembership.user_id == owner,
                        OrganizationMembership.status == "active",
                    )
                    .with_for_update()
                )
                if member is None or "methodologist" not in member.roles:
                    raise WorkspaceFailure("forbidden", "Доступ отозван.", 403)
                actor = RequestActor(
                    organization_id=org,
                    actor_type="user",
                    user_id=owner,
                    roles=frozenset[Role]({"methodologist"}),
                    membership_revision=member.revision,
                    auth_epoch=member.auth_epoch,
                )
                await course_scope(session, actor, options.course_run_id)
                items = []
                offset = 0
                while True:
                    page = await WorkspaceQueries(self.runtime, session).works(
                        actor,
                        run_id=options.course_run_id,
                        homework_id=options.homework_id,
                        limit=100,
                        offset=offset,
                    )
                    if page.total > 10000:
                        raise WorkspaceFailure(
                            "export_too_large", "Уточните фильтры: максимум 10000 строк.", 413
                        )
                    items.extend(page.items)
                    offset += len(page.items)
                    if offset >= page.total or not page.items:
                        break
                public_base = self.runtime.settings.workspace_public_base_url or ""
                if "artifact_url" in options.columns:
                    parsed_base = urlsplit(public_base)
                    if (
                        parsed_base.scheme not in {"http", "https"}
                        or not parsed_base.netloc
                        or parsed_base.username
                        or parsed_base.password
                        or parsed_base.query
                        or parsed_base.fragment
                    ):
                        raise WorkspaceFailure(
                            "configuration_required",
                            "Для ссылок экспорта настройте публичный адрес приложения.",
                            503,
                        )
                rows = export_rows(
                    options, items, await export_details(session, org, items, public_base)
                )
                data = csv_bytes(rows) if options.format == "csv" else xlsx_bytes(rows)
                media = (
                    "text/csv"
                    if options.format == "csv"
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                stored = await asyncio.to_thread(
                    self.runtime.object_storage.upload,
                    organization_id=str(org),
                    artifact_version_id=str(identity),
                    source=[data],
                    media_type=media,
                    max_bytes=10_000_000,
                )
                current = await row(session, WorkspaceExport, org, identity, lock=True)
                artifact = await session.scalar(
                    select(WorkspaceArtifact).where(
                        WorkspaceArtifact.organization_id == org, WorkspaceArtifact.id == identity
                    )
                )
                if artifact is None:
                    session.add(
                        WorkspaceArtifact(
                            id=identity,
                            organization_id=org,
                            owner_id=owner,
                            filename=f"results.{options.format}",
                            media_type=media,
                            object_key=stored.key,
                            digest=stored.content_digest,
                            byte_size=len(data),
                            private=True,
                        )
                    )
                    await session.flush()
                current.artifact_id = identity
                current.status = "succeeded"
                current.row_count = len(rows) - 1
                current.lease_until = None
        except WorkspaceFailure as error:
            async with self.runtime.transaction() as session:
                current = await row(session, WorkspaceExport, org, identity, lock=True)
                current.status = "failed"
                current.error = error.code
                current.lease_until = None
        return True
