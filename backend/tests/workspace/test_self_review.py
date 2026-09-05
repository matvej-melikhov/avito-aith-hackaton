from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import WorkspaceFailure
from review_platform.application.workspace.self_review import SelfReviewService
from review_platform.contracts.workspace import DraftInput, SelfReviewEvent, SelfReviewFinding, SelfReviewResult
from review_platform.infrastructure.db.models.workspace import SelfReviewQuota, SelfReviewQuotaEvent, SelfReviewRun
from tests.workspace.conftest import IDS

pytestmark=[pytest.mark.anyio,pytest.mark.infrastructure]

async def draft(runtime: FoundationRuntime, actor: RequestActor):
    async with runtime.transaction() as s:
        return await SelfReviewService(runtime,s).save_draft(actor,IDS['publication'],0,DraftInput(artifact_url='https://github.com/example/work'))

async def dispatched(runtime: FoundationRuntime, actor: RequestActor):
    d=await draft(runtime,actor)
    async with runtime.transaction() as s:
        run=await SelfReviewService(runtime,s).start(actor,d.id,d.revision)
    async with runtime.transaction() as s:
        stored=await s.get(SelfReviewRun,run.id);assert stored
        stored.attempt=1;stored.input_fingerprint='sha256:'+'a'*64;stored.status='running'
    return d,run

def event(run_id, status='succeeded', finding_status='met'):
    return SelfReviewEvent(contract_version='2.0.0',event_id=uuid4(),run_id=run_id,attempt=1,sequence=1,input_fingerprint='sha256:'+'a'*64,status=status,result=SelfReviewResult(findings=[SelfReviewFinding(criterion_id=IDS['criterion'],status=finding_status,feedback='Checked')]) if status=='succeeded' else None,error_code='unavailable' if status=='failed' else None)

async def test_last_slot_is_atomic(workspace_runtime,student):
    d=await draft(workspace_runtime,student)
    async def start():
        try:
            async with workspace_runtime.transaction() as s:
                return await SelfReviewService(workspace_runtime,s).start(student,d.id,d.revision)
        except WorkspaceFailure as e:return e
    results=await asyncio.gather(start(),start())
    assert sum(not isinstance(r,WorkspaceFailure) for r in results)==1
    async with workspace_runtime.transaction() as s:
        quota=await s.scalar(select(SelfReviewQuota));assert quota
        assert quota.used==0 and quota.reserved==1
        stored=await s.scalar(select(SelfReviewRun));assert stored
        assert 'PRIVATE' not in str(stored.criteria)

async def test_final_result_charges_once_and_event_replay_is_idempotent(workspace_runtime,student):
    _,run=await dispatched(workspace_runtime,student);e=event(run.id)
    for _ in range(2):
        async with workspace_runtime.transaction() as s:
            view=await SelfReviewService(workspace_runtime,s).accept(IDS['org'],e)
            assert view.quota.used==1 and view.quota.reserved==0 and view.disposition=='consumed'
    async with workspace_runtime.transaction() as s:
        entries=(await s.scalars(select(SelfReviewQuotaEvent))).all()
        assert sorted(x.action for x in entries)==['consume','reserve']

@pytest.mark.parametrize('kind',['failure','all_not_checked'])
async def test_technical_failure_returns_slot(workspace_runtime,student,kind):
    _,run=await dispatched(workspace_runtime,student)
    e=event(run.id,status='failed') if kind=='failure' else event(run.id,finding_status='not_checked')
    async with workspace_runtime.transaction() as s:
        result=await SelfReviewService(workspace_runtime,s).accept(IDS['org'],e)
        assert result.quota.used==0 and result.quota.reserved==0 and result.quota.remaining==1

async def test_draft_changes_do_not_reset_consumed_quota(workspace_runtime,student):
    d,run=await dispatched(workspace_runtime,student)
    async with workspace_runtime.transaction() as s:await SelfReviewService(workspace_runtime,s).accept(IDS['org'],event(run.id))
    async with workspace_runtime.transaction() as s:
        updated=await SelfReviewService(workspace_runtime,s).save_draft(student,IDS['publication'],d.revision,DraftInput(artifact_url='https://github.com/example/revision'))
        assert updated.id==d.id
    with pytest.raises(WorkspaceFailure,match='Лимит'):
        async with workspace_runtime.transaction() as s:await SelfReviewService(workspace_runtime,s).start(student,d.id,updated.revision)

async def test_late_result_after_release_is_rejected(workspace_runtime,student):
    _,run=await dispatched(workspace_runtime,student)
    async with workspace_runtime.transaction() as s:await SelfReviewService(workspace_runtime,s).accept(IDS['org'],event(run.id,status='failed'))
    with pytest.raises(WorkspaceFailure,match='no longer'):
        async with workspace_runtime.transaction() as s:await SelfReviewService(workspace_runtime,s).accept(IDS['org'],event(run.id))

async def test_student_cannot_read_another_students_run(workspace_runtime,student):
    _,run=await dispatched(workspace_runtime,student)
    foreign=RequestActor(organization_id=uuid4(),actor_type='user',user_id=uuid4(),roles=frozenset({'student'}),membership_revision=0,auth_epoch=0)
    with pytest.raises(WorkspaceFailure):
        async with workspace_runtime.transaction() as s:await SelfReviewService(workspace_runtime,s).get(foreign,run.id)

def test_result_schema_rejects_private_fields():
    with pytest.raises(ValidationError):SelfReviewFinding(criterion_id=IDS['criterion'],status='met',feedback='x',reviewer_note='secret')
