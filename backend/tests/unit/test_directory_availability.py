"""Directory publishes absence dates without exposing private preference settings."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.catalog import CatalogService
from review_platform.application.workspace.common import WorkspaceFailure


@pytest.mark.anyio
async def test_directory_returns_current_vacation_dates(monkeypatch):
    user_id, org = uuid4(), uuid4()
    preference = SimpleNamespace(
        settings={
            "absent_from": "2026-08-31T21:00:00Z",
            "absent_until": "2026-09-16T20:59:59Z",
            "planned_minutes": 120,
            "notifications": {"pool": False},
        }
    )
    entries = [
        (
            SimpleNamespace(id=user_id, display_name="Reviewer"),
            SimpleNamespace(roles=["reviewer"]),
            preference,
        )
    ]
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: entries)))
    monkeypatch.setattr(
        "review_platform.application.workspace.catalog.student_labels", AsyncMock(return_value={})
    )
    actor = RequestActor(
        organization_id=org,
        actor_type="user",
        user_id=uuid4(),
        roles=frozenset({"methodologist"}),
        membership_revision=0,
        auth_epoch=0,
    )
    result = await CatalogService(None, session).directory(actor)
    assert "LEFT OUTER JOIN" in str(session.execute.call_args.args[0].compile())
    assert result.items[0].absent_from == datetime(2026, 8, 31, 21, tzinfo=UTC)
    assert result.items[0].absent_until == datetime(2026, 9, 16, 20, 59, 59, tzinfo=UTC)
    assert set(result.items[0].model_dump()) == {
        "id",
        "display_name",
        "roles",
        "absent_from",
        "absent_until",
    }
    entries[0] = (*entries[0][:2], None)
    assert (await CatalogService(None, session).directory(actor)).items[0].absent_from is None


@pytest.mark.anyio
async def test_student_cannot_read_directory():
    actor = RequestActor(
        organization_id=uuid4(),
        actor_type="user",
        user_id=uuid4(),
        roles=frozenset({"student"}),
        membership_revision=0,
        auth_epoch=0,
    )
    with pytest.raises(WorkspaceFailure):
        await CatalogService(None, None).directory(actor)
