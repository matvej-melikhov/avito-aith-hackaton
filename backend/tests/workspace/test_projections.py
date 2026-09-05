"""Regression coverage for current-work state and export boundaries."""

from datetime import timedelta
from io import BytesIO
from uuid import UUID, uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from sqlalchemy import select
from tests.workspace.conftest import IDS, NOW
from tests.workspace.test_http import assert_ok, client_for, command, upload_and_open

from review_platform.application.workspace.common import WorkspaceFailure
from review_platform.application.workspace.exports import csv_bytes, export_rows, xlsx_bytes
from review_platform.contracts.workspace import ExportInput, WorkItem
from review_platform.infrastructure.db.models import ReviewResponsibility, SubmissionVersion


def test_spreadsheet_text_is_not_executable_and_xml_controls_are_removed():
    value = ' \t=HYPERLINK("https://example.invalid")\x00'
    assert b"' \t=HYPERLINK" in csv_bytes([[value]])
    with ZipFile(BytesIO(xlsx_bytes([[value]]))) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    assert root.find(".//s:f", ns) is None
    assert root.find(".//s:t", ns).text == value.replace("\x00", "")


def test_student_export_rejects_private_columns():
    with pytest.raises(WorkspaceFailure, match="Для студенческой"):
        export_rows(
            ExportInput(
                course_run_id=IDS["run"], audience="students", columns=["feedback"], format="csv"
            ),
            [],
        )


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_current_version_status_keeps_previous_grade_and_participation(
    workspace_runtime, student
):
    sub, opened = await upload_and_open(workspace_runtime)
    async with await client_for(workspace_runtime, "reviewer") as client:
        # Another responsible reviewer does not remove this reviewer's participation.
        async with workspace_runtime.transaction() as session:
            from review_platform.infrastructure.db.models import ReviewIteration

            iteration = await session.get(ReviewIteration, UUID(opened["id"]))
            iteration.responsible_reviewer_id = IDS["methodologist"]
            session.add(
                ReviewResponsibility(
                    id=uuid4(),
                    organization_id=IDS["org"],
                    review_case_id=iteration.review_case_id,
                    review_iteration_id=iteration.id,
                    reviewer_id=IDS["reviewer"],
                    actor_id=IDS["reviewer"],
                    action="joined",
                    occurred_at=NOW,
                )
            )
        assert assert_ok(await client.get("/api/v2/works?view=active"))["total"] == 1
        async with workspace_runtime.transaction() as session:
            iteration = await session.get(ReviewIteration, UUID(opened["id"]))
            session.add(
                ReviewResponsibility(
                    id=uuid4(),
                    organization_id=IDS["org"],
                    review_case_id=iteration.review_case_id,
                    review_iteration_id=iteration.id,
                    reviewer_id=IDS["reviewer"],
                    actor_id=IDS["reviewer"],
                    action="released",
                    occurred_at=NOW + timedelta(seconds=1),
                )
            )
        assert assert_ok(await client.get("/api/v2/works?view=active"))["total"] == 0
        saved = assert_ok(
            await client.post(
                f"/api/v1/review-iterations/{opened['id']}/revisions",
                json={
                    **command(
                        "save_review_revision",
                        opened["id"],
                        {
                            "feedback": "Published",
                            "criterion_decisions": [
                                {
                                    "criterion_id": str(IDS["criterion"]),
                                    "points": 8,
                                    "decision": "manual",
                                    "reason": "Checked",
                                }
                            ],
                            "review_notes": [],
                        },
                        opened["revision"],
                    ),
                    "revision_target": "review_iteration",
                },
            )
        )
        assert_ok(
            await client.post(
                f"/api/v2/reviews/{opened['id']}/publish",
                json=command(
                    "publish_workspace_review",
                    opened["id"],
                    {"review_revision_id": saved["review_revision_id"], "apply_penalty": False},
                    saved["review_iteration_revision"],
                ),
            )
        )
        async with workspace_runtime.transaction() as session:
            previous = await session.scalar(
                select(SubmissionVersion).where(SubmissionVersion.submission_id == UUID(sub["id"]))
            )
            fields = {
                column.name: getattr(previous, column.name)
                for column in SubmissionVersion.__table__.columns
            }
            fields.update(id=uuid4(), sequence=previous.sequence + 1)
            session.add(SubmissionVersion(**fields))
        from dataclasses import replace

        from review_platform.application.workspace.projections import WorkspaceQueries

        async with workspace_runtime.transaction() as session:
            measured = await WorkspaceQueries(workspace_runtime, session).statistics(
                replace(student, user_id=IDS["reviewer"], roles=frozenset({"reviewer"})),
                NOW - timedelta(days=1),
                NOW + timedelta(seconds=1),
                IDS["run"],
            )
            assert measured.publications == 1
            assert measured.compared_decisions == 0
            assert measured.ai_acceptance_percent is None
            assert measured.peer_comparison.sample_count == 0
            assert measured.wait_sample_count == 1
        item = WorkItem.model_validate(assert_ok(await client.get("/api/v2/works"))["items"][0])
        assert item.status == "pending_review"
        assert item.score == 8
        with pytest.raises(WorkspaceFailure, match="область экспорта"):
            export_rows(
                ExportInput(
                    course_run_id=uuid4(), audience="team", columns=["score"], format="xlsx"
                ),
                [item],
            )


def test_statistics_use_measured_ai_and_matching_peer_inputs():
    from dataclasses import replace

    from review_platform.application.workspace.statistics import (
        PublishedMeasurement,
        summarize_statistics,
    )

    first = PublishedMeasurement(
        publication_id=uuid4(),
        reviewer_id=IDS["reviewer"],
        artifact_id=uuid4(),
        rubric_id=IDS["set"],
        published_at=NOW,
        opened_at=NOW - timedelta(minutes=30),
        submitted_at=NOW - timedelta(days=2),
        review_deadline=NOW - timedelta(hours=1),
        attempt=2,
        decisions={IDS["criterion"]: ("API correctness", 10, 8, 8)},
    )
    peer = replace(
        first,
        publication_id=uuid4(),
        reviewer_id=IDS["methodologist"],
        decisions={IDS["criterion"]: ("API correctness", 10, 6, None)},
    )
    wrong_artifact = replace(
        peer,
        publication_id=uuid4(),
        artifact_id=uuid4(),
        decisions={IDS["criterion"]: ("API correctness", 10, 0, None)},
    )
    wrong_rubric = replace(
        peer,
        publication_id=uuid4(),
        rubric_id=uuid4(),
        decisions={IDS["criterion"]: ("API correctness", 10, 0, None)},
    )
    result = summarize_statistics(
        [first, peer, wrong_artifact, wrong_rubric],
        IDS["reviewer"],
        NOW - timedelta(days=7),
        NOW + timedelta(seconds=1),
    )
    assert result.publications == result.repeated_publications == result.overdue_publications == 1
    assert result.average_elapsed_minutes == 30
    assert result.average_wait_minutes == 2880
    assert result.ai_acceptance_percent == 100
    assert result.compared_decisions == 1 and result.changed_decisions == 0
    assert result.criterion_changes[0].compared_works == 1
    assert result.peer_comparison.sample_count == 1
    assert result.peer_comparison.divergence_percent == 20
    assert result.peer_comparison.softer_criteria == ["API correctness"]
    no_match = summarize_statistics(
        [first, wrong_artifact, wrong_rubric],
        IDS["reviewer"],
        NOW - timedelta(days=7),
        NOW + timedelta(seconds=1),
    )
    assert no_match.peer_comparison.sample_count == 0
    assert no_match.peer_comparison.divergence_percent is None
    manual = summarize_statistics(
        [peer], IDS["methodologist"], NOW - timedelta(days=7), NOW + timedelta(seconds=1)
    )
    assert manual.ai_acceptance_percent is None and manual.compared_decisions == 0
