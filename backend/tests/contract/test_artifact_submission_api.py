"""Executable RED contract for artifact preflight and submission history."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from jsonschema import ValidationError
from tests.support.contracts import load_fixture, load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.request_context import RequestActor
from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000001")
USER_ID = UUID("00000000-0000-7000-8000-000000000901")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000902")
COURSE_ID = UUID("00000000-0000-7000-8000-000000000903")
COURSE_RUN_A = UUID("00000000-0000-7000-8000-000000000904")
COURSE_RUN_B = UUID("00000000-0000-7000-8000-000000000905")
CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000021")
OTHER_TARGET_ID = UUID("00000000-0000-7000-8000-000000000999")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
AVAILABLE_URL = "https://github.com/example/repository"
UNAVAILABLE_URL = "https://github.com/example/unavailable"


@dataclass(slots=True)
class ArtifactProviderStub:
    """Schema-valid local provider; no network or provider-specific policy."""

    registry: ContractRegistry = field(default_factory=ContractRegistry)
    requests: list[ProviderPayload] = field(default_factory=list)

    @property
    def contract_version(self) -> str:
        return "1.1.0"

    @property
    def schema_name(self) -> str:
        return "artifact-provider.schema.json"

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        self.registry.validate(request, self.schema_name, definition="preflight_request")
        self.requests.append(request)
        if request["url"] == UNAVAILABLE_URL:
            result: ProviderPayload = {
                "contract_version": "1.1.0",
                "organization_id": str(ORGANIZATION_ID),
                "provider": "github",
                "read_capability": "requires_action",
                "feedback_capability": "requires_action",
                "locator": None,
                "error": {
                    "code": "access_denied",
                    "message": "Artifact is not accessible",
                    "retryable": False,
                    "action": "grant_read_access",
                },
            }
        else:
            result = cast(
                ProviderPayload,
                deepcopy(load_fixture("artifact-provider-v1.1.0.json")["available_result"]),
            )
        self.registry.validate(result, self.schema_name, definition="preflight_result")
        return result

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        self.registry.validate(request, self.schema_name, definition="capture_request")
        result = deepcopy(load_fixture("artifact-provider-v1.1.0.json")["success_result"])
        result["artifact_reference_id"] = request["artifact_reference_id"]
        payload = cast(ProviderPayload, result)
        self.registry.validate(payload, self.schema_name, definition="capture_result")
        return payload


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=USER_ID, display_name="Submission Contract User", status="active"))
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_ID,
                    organization_id=ORGANIZATION_ID,
                    user_id=USER_ID,
                    roles=["methodologist", "student"],
                    status="active",
                    revision=3,
                    auth_epoch=2,
                ),
                Course(
                    id=COURSE_ID,
                    organization_id=ORGANIZATION_ID,
                    title="Submission Contract Course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                ExternalCredential(
                    id=CREDENTIAL_ID,
                    organization_id=ORGANIZATION_ID,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-artifact-fixture",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=COURSE_RUN_A,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    external_run_id="submission-contract-a",
                    title="Submission Contract Run A",
                    starts_at=None,
                    ends_at=None,
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=COURSE_RUN_B,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    external_run_id="submission-contract-b",
                    title="Submission Contract Run B",
                    starts_at=None,
                    ends_at=None,
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000000906"),
                    organization_id=ORGANIZATION_ID,
                    course_run_id=COURSE_RUN_A,
                    user_id=USER_ID,
                    kind="student",
                    source="imported",
                    status="active",
                    external_version="fixture-1",
                    joined_at=NOW,
                    removed_at=None,
                ),
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000000907"),
                    organization_id=ORGANIZATION_ID,
                    course_run_id=COURSE_RUN_B,
                    user_id=USER_ID,
                    kind="student",
                    source="imported",
                    status="active",
                    external_version="fixture-1",
                    joined_at=NOW,
                    removed_at=None,
                ),
            ]
        )


@pytest.fixture
async def submission_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    await _seed(foundation_session_factory)
    app = create_app(Settings(), runtime=foundation_runtime)
    provider = ArtifactProviderStub()
    app.state.artifact_provider = provider
    app.state.artifact_provider_factory = lambda _session: provider
    app.state.artifact_credential_binding_id = CREDENTIAL_ID
    app.state.artifact_credential_binding_version = 1
    actor = RequestActor.user(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        roles={"methodologist", "student"},
        membership_revision=3,
        auth_epoch=2,
    )

    @app.middleware("http")
    async def inject_authenticated_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


def _wire_command(
    *,
    command_name: str,
    revision_target: str,
    target_id: UUID,
    expected_revision: int,
    payload: Mapping[str, Any],
    request_number: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_number:012d}",
        "idempotency_key": f"submission-{command_name}-{request_number:08d}",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


def _version_payload() -> dict[str, Any]:
    return {
        "student_text": "Submit the repository",
        "max_score": 10,
        "artifact_kinds": ["github"],
        "estimated_review_minutes": 30,
        "criteria": [
            {
                "key": "correctness",
                "title": "Correctness",
                "description": "The solution is correct",
                "max_points": 10,
            }
        ],
    }


def _validate_component(component_name: str, value: object) -> None:
    openapi = load_openapi()
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{component_name}",
        "components": openapi["components"],
    }
    validator_for(wrapper).validate(value)


def _assert_conflict(response: httpx.Response) -> None:
    assert response.status_code == 409, response.text
    _validate_component("ErrorObject", response.json())


@dataclass(frozen=True, slots=True)
class FlowSetup:
    homework_id: UUID
    homework_version_id: UUID
    relation_a: UUID
    relation_b: UUID


async def _prepare_two_flows(client: httpx.AsyncClient) -> FlowSetup:
    create = await client.post(
        f"/api/v1/course-runs/{COURSE_RUN_A}/homeworks",
        json=_wire_command(
            command_name="create_homework",
            revision_target="course_run",
            target_id=COURSE_RUN_A,
            expected_revision=0,
            payload={"title": "Submission Contract Homework"},
            request_number=901,
        ),
    )
    assert create.status_code == 201, create.text
    homework_id = UUID(create.json()["id"])
    version = await client.post(
        f"/api/v1/homeworks/{homework_id}/versions",
        json=_wire_command(
            command_name="create_homework_version",
            revision_target="homework",
            target_id=homework_id,
            expected_revision=create.json()["revision"],
            payload=_version_payload(),
            request_number=902,
        ),
    )
    assert version.status_code == 201, version.text
    version_id = UUID(version.json()["id"])
    for run_id, request_number in ((COURSE_RUN_A, 903), (COURSE_RUN_B, 904)):
        publish = await client.post(
            f"/api/v1/homework-versions/{version_id}/publish",
            json=_wire_command(
                command_name="publish_homework_version",
                revision_target="homework_version",
                target_id=version_id,
                expected_revision=version.json()["revision"],
                payload={
                    "course_run_id": str(run_id),
                    "submission_deadline": "2026-09-10T12:00:00Z",
                    "review_deadline": "2026-09-12T12:00:00Z",
                },
                request_number=request_number,
            ),
        )
        assert publish.status_code == 201, publish.text

    relations: dict[UUID, UUID] = {}
    for run_id in (COURSE_RUN_A, COURSE_RUN_B):
        response = await client.get(f"/api/v1/course-runs/{run_id}/homeworks")
        assert response.status_code == 200, response.text
        _validate_component("HomeworkList", response.json())
        relations[run_id] = UUID(response.json()["items"][0]["course_run_homework_id"])
    return FlowSetup(
        homework_id=homework_id,
        homework_version_id=version_id,
        relation_a=relations[COURSE_RUN_A],
        relation_b=relations[COURSE_RUN_B],
    )


async def _preflight(
    client: httpx.AsyncClient,
    *,
    course_run_homework_id: UUID,
    artifact_url: str,
    request_number: int,
    target_id: UUID | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/course-run-homeworks/{course_run_homework_id}/submissions/preflight",
        json=_wire_command(
            command_name="preflight_submission",
            revision_target="course_run_homework",
            target_id=target_id or course_run_homework_id,
            expected_revision=1,
            payload={"artifact_url": artifact_url},
            request_number=request_number,
        ),
    )


def test_frozen_contracts_define_conditional_preflight_submit_and_history() -> None:
    openapi = load_openapi()
    preflight = openapi["paths"][
        "/v1/course-run-homeworks/{courseRunHomeworkId}/submissions/preflight"
    ]["post"]
    submit = openapi["paths"]["/v1/submissions/{submissionId}/versions"]["post"]
    history = openapi["paths"]["/v1/submissions/{submissionId}"]["get"]
    assert preflight["x-command-name"] == "preflight_submission"
    assert preflight["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "command.schema.json#/$defs/preflight_submission"
    }
    assert preflight["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ArtifactCapability"
    }
    assert submit["x-command-name"] == "submit_work"
    assert submit["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "command.schema.json#/$defs/submit_work"
    }
    assert submit["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SubmissionVersionCreated"
    }
    assert history["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SubmissionHistory"
    }

    provider_fixture = load_fixture("artifact-provider-v1.1.0.json")
    registry = ContractRegistry()
    registry.validate(
        provider_fixture["preflight_request"],
        "artifact-provider.schema.json",
        definition="preflight_request",
    )
    registry.validate(
        provider_fixture["available_result"],
        "artifact-provider.schema.json",
        definition="preflight_result",
    )
    registry.validate(
        provider_fixture["success_result"],
        "artifact-provider.schema.json",
        definition="capture_result",
    )


def test_frozen_history_shape_preserves_immutable_ids_digest_and_capture_operation() -> None:
    fixture = load_fixture("artifact-provider-v1.1.0.json")["success_result"]
    history = {
        "submission_id": "00000000-0000-7000-8000-000000000910",
        "course_run_id": str(COURSE_RUN_A),
        "homework_id": "00000000-0000-7000-8000-000000000911",
        "current_submission_version_id": "00000000-0000-7000-8000-000000000912",
        "current_publication_id": None,
        "versions": [
            {
                "id": "00000000-0000-7000-8000-000000000912",
                "sequence": 1,
                "homework_version_id": "00000000-0000-7000-8000-000000000913",
                "artifact_reference_id": fixture["artifact_reference_id"],
                "artifact_version_id": "00000000-0000-7000-8000-000000000914",
                "capture_operation_id": "00000000-0000-7000-8000-000000000915",
                "submitted_at": "2026-09-05T12:00:00Z",
                "effective_deadline": "2026-09-10T12:00:00Z",
                "phase": "before_deadline",
                "status": "ready",
            }
        ],
        "artifact_versions": [
            {
                "id": "00000000-0000-7000-8000-000000000914",
                "artifact_reference_id": fixture["artifact_reference_id"],
                "provider": "github",
                "provider_version": fixture["provider_version"],
                "content_digest": fixture["content"]["content_digest"],
                "media_type": fixture["content"]["media_type"],
                "byte_size": fixture["content"]["byte_size"],
                "captured_at": "2026-09-05T12:01:00Z",
            }
        ],
        "review_iterations": [],
        "review_revisions": [],
        "publications": [],
    }
    _validate_component("SubmissionHistory", history)
    assert history["versions"][0]["capture_operation_id"] != history["versions"][0]["id"]
    assert str(history["artifact_versions"][0]["content_digest"]).startswith("sha256:")

    invalid = deepcopy(history)
    invalid["artifact_versions"][0]["content_digest"] = "sha256:NOT-CANONICAL"
    with pytest.raises(ValidationError):
        _validate_component("SubmissionHistory", invalid)


async def test_available_preflight_returns_usable_reference_and_flow_local_submission(
    submission_client: httpx.AsyncClient,
) -> None:
    setup = await _prepare_two_flows(submission_client)
    first = await _preflight(
        submission_client,
        course_run_homework_id=setup.relation_a,
        artifact_url=AVAILABLE_URL,
        request_number=921,
    )
    second = await _preflight(
        submission_client,
        course_run_homework_id=setup.relation_b,
        artifact_url=AVAILABLE_URL,
        request_number=922,
    )
    assert first.status_code == second.status_code == 200
    for response in (first, second):
        _validate_component("ArtifactCapability", response.json())
        assert response.json()["read_capability"] == "available"
        assert response.json()["artifact_reference_id"] is not None
        assert response.json()["error"] is None
        assert response.json()["submission_revision"] >= 0
    assert first.json()["submission_id"] != second.json()["submission_id"]


async def test_unavailable_preflight_withholds_usable_reference(
    submission_client: httpx.AsyncClient,
) -> None:
    setup = await _prepare_two_flows(submission_client)
    response = await _preflight(
        submission_client,
        course_run_homework_id=setup.relation_a,
        artifact_url=UNAVAILABLE_URL,
        request_number=931,
    )
    assert response.status_code == 200, response.text
    _validate_component("ArtifactCapability", response.json())
    assert response.json()["read_capability"] != "available"
    assert response.json()["artifact_reference_id"] is None
    assert response.json()["error"]["action"] == "grant_read_access"
    assert UUID(response.json()["submission_id"])
    assert response.json()["submission_revision"] >= 0


async def test_submit_returns_capture_operation_and_canonical_history(
    submission_client: httpx.AsyncClient,
) -> None:
    setup = await _prepare_two_flows(submission_client)
    capability = await _preflight(
        submission_client,
        course_run_homework_id=setup.relation_a,
        artifact_url=AVAILABLE_URL,
        request_number=941,
    )
    assert capability.status_code == 200, capability.text
    submission_id = UUID(capability.json()["submission_id"])
    reference_id = UUID(capability.json()["artifact_reference_id"])
    submit = await submission_client.post(
        f"/api/v1/submissions/{submission_id}/versions",
        json=_wire_command(
            command_name="submit_work",
            revision_target="submission",
            target_id=submission_id,
            expected_revision=capability.json()["submission_revision"],
            payload={"artifact_reference_id": str(reference_id)},
            request_number=942,
        ),
    )
    assert submit.status_code == 201, submit.text
    _validate_component("SubmissionVersionCreated", submit.json())
    assert submit.json()["submission_id"] == str(submission_id)
    assert submit.json()["submission_revision"] > capability.json()["submission_revision"]
    assert UUID(submit.json()["submission_version_id"])
    assert UUID(submit.json()["capture_operation_id"])

    history = await submission_client.get(f"/api/v1/submissions/{submission_id}")
    assert history.status_code == 200, history.text
    _validate_component("SubmissionHistory", history.json())
    assert history.json()["submission_id"] == str(submission_id)
    assert history.json()["course_run_id"] == str(COURSE_RUN_A)
    assert history.json()["homework_id"] == str(setup.homework_id)
    version = history.json()["versions"][0]
    assert version["id"] == submit.json()["submission_version_id"]
    assert version["homework_version_id"] == str(setup.homework_version_id)
    assert version["artifact_reference_id"] == str(reference_id)
    assert version["capture_operation_id"] == submit.json()["capture_operation_id"]


async def test_preflight_and_submit_reject_wrong_targets_and_stale_submission_revision(
    submission_client: httpx.AsyncClient,
) -> None:
    setup = await _prepare_two_flows(submission_client)
    wrong_command = await submission_client.post(
        f"/api/v1/course-run-homeworks/{setup.relation_a}/submissions/preflight",
        json=_wire_command(
            command_name="submit_work",
            revision_target="submission",
            target_id=setup.relation_a,
            expected_revision=1,
            payload={"artifact_reference_id": str(OTHER_TARGET_ID)},
            request_number=950,
        ),
    )
    _assert_conflict(wrong_command)

    wrong_preflight = await _preflight(
        submission_client,
        course_run_homework_id=setup.relation_a,
        artifact_url=AVAILABLE_URL,
        request_number=951,
        target_id=setup.relation_b,
    )
    _assert_conflict(wrong_preflight)

    capability = await _preflight(
        submission_client,
        course_run_homework_id=setup.relation_a,
        artifact_url=AVAILABLE_URL,
        request_number=952,
    )
    assert capability.status_code == 200, capability.text
    submission_id = UUID(capability.json()["submission_id"])
    reference_id = UUID(capability.json()["artifact_reference_id"])
    wrong_submit = await submission_client.post(
        f"/api/v1/submissions/{submission_id}/versions",
        json=_wire_command(
            command_name="submit_work",
            revision_target="submission",
            target_id=OTHER_TARGET_ID,
            expected_revision=capability.json()["submission_revision"],
            payload={"artifact_reference_id": str(reference_id)},
            request_number=953,
        ),
    )
    _assert_conflict(wrong_submit)

    first = await submission_client.post(
        f"/api/v1/submissions/{submission_id}/versions",
        json=_wire_command(
            command_name="submit_work",
            revision_target="submission",
            target_id=submission_id,
            expected_revision=capability.json()["submission_revision"],
            payload={"artifact_reference_id": str(reference_id)},
            request_number=954,
        ),
    )
    assert first.status_code == 201, first.text
    stale = await submission_client.post(
        f"/api/v1/submissions/{submission_id}/versions",
        json=_wire_command(
            command_name="submit_work",
            revision_target="submission",
            target_id=submission_id,
            expected_revision=capability.json()["submission_revision"],
            payload={"artifact_reference_id": str(reference_id)},
            request_number=955,
        ),
    )
    _assert_conflict(stale)
