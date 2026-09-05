from __future__ import annotations
import base64
from datetime import timedelta
from uuid import UUID,uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.models import Session
from review_platform.infrastructure.db.models.workspace import WorkspaceArtifact, SelfReviewRun
from review_platform.main import create_app
from review_platform.application.workspace.worker import SelfReviewWorker,FixtureSelfReviewProvider
from tests.workspace.conftest import IDS,NOW

pytestmark=[pytest.mark.anyio,pytest.mark.infrastructure]

async def client_for(runtime,role):
    secret=('workspace-'+role+'-')*4
    async with runtime.transaction() as s:
        existing=await s.scalar(select(Session).where(Session.token_digest==sha256_digest(secret)))
        if not existing:s.add(Session(id=uuid4(),organization_id=IDS['org'],user_id=IDS[role],membership_id=UUID(int=IDS[role].int+100),membership_revision=0,auth_epoch=0,token_digest=sha256_digest(secret),expires_at=NOW+timedelta(hours=1),status='active'))
    app=create_app(runtime=runtime)
    client=AsyncClient(transport=ASGITransport(app=app),base_url='https://testserver')
    client.cookies.set('review_session',secret)
    return client

def command(name,target,payload,revision=0):
    return {'request_id':str(uuid4()),'idempotency_key':str(uuid4()),'command_name':name,'target_id':str(target),'expected_revision':revision,'payload':payload}

def assert_ok(response):
    assert response.status_code<300,response.text
    return response.json()

async def test_cookie_actor_and_typed_student_context(workspace_runtime):
    async with await client_for(workspace_runtime,'student') as c:
        current=assert_ok(await c.get('/api/v1/session'));assert current['user_id']==str(IDS['student'])
        context=assert_ok(await c.get(f"/api/v2/course-run-homeworks/{IDS['publication']}/student-context"))
        assert context['quota']['remaining']==1
        assert 'PRIVATE' not in str(context)
        assert (await c.get('/api/v2/directory')).status_code==403
        assert (await c.post('/api/v2/courses',json=command('create_course',IDS['org'],{'title':'bad'}))).status_code==403

async def test_full_upload_self_review_then_human_open(workspace_runtime):
    async with await client_for(workspace_runtime,'student') as c:
        uploaded=assert_ok(await c.post('/api/v2/uploads',json=command('upload_artifact',IDS['student'],{'filename':'work.md','media_type':'text/markdown','content_base64':base64.b64encode(b'# Work\nPublic solution').decode(),'private':False})))
        draft=assert_ok(await c.post(f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",json=command('save_work_draft',IDS['publication'],{'artifact_url':'','upload_id':uploaded['id'],'comment':'My work'})))
        start=command('start_self_review',draft['id'],{},draft['revision'])
        run=assert_ok(await c.post(f"/api/v2/work-drafts/{draft['id']}/self-reviews",json=start))
        replayed=assert_ok(await c.post(f"/api/v2/work-drafts/{draft['id']}/self-reviews",json=start));assert run['id']==replayed['id']
        assert await SelfReviewWorker(workspace_runtime,FixtureSelfReviewProvider(),workspace_runtime.object_storage).tick()
        result=assert_ok(await c.get(f"/api/v2/self-reviews/{run['id']}"));assert result['disposition']=='consumed';assert result['quota']['remaining']==0
        submitted=assert_ok(await c.post(f"/api/v2/work-drafts/{draft['id']}/submit-upload",json=command('submit_uploaded_draft',draft['id'],{},draft['revision'])))
        detail=assert_ok(await c.get(f"/api/v2/submissions/{submitted['id']}"));assert len(detail['attempts'])==1 and not detail['reviews']
    async with await client_for(workspace_runtime,'reviewer') as c:
        works=assert_ok(await c.get('/api/v2/works'));assert works['total']==1
        item=works['items'][0]
        opened=assert_ok(await c.post(f"/api/v2/submissions/{submitted['id']}/open-review",json=command('open_work',submitted['id'],{'submission_version_id':item['submission_version_id']},item['submission_revision'])))
        context=assert_ok(await c.get(f"/api/v2/reviews/{opened['id']}/context"));assert len(context['self_reviews'])==1
        assert context['criteria'][0]['description']=='PRIVATE rubric'

async def test_coordinator_creates_course_and_run(workspace_runtime):
    async with await client_for(workspace_runtime,'methodologist') as c:
        course=assert_ok(await c.post('/api/v2/courses',json=command('create_course',IDS['org'],{'title':'New course','description':'Description','owner_id':str(IDS['methodologist'])})))
        run=assert_ok(await c.post(f"/api/v2/courses/{course['id']}/course-runs",json=command('create_course_run',course['id'],{'title':'Autumn','starts_at':NOW.isoformat(),'ends_at':(NOW+timedelta(days=90)).isoformat(),'timezone':'UTC'})))
        catalog=assert_ok(await c.get('/api/v2/catalog'))
        assert any(v['id']==course['id'] and v['owner_id']==str(IDS['methodologist']) for v in catalog['courses'])
        assert any(v['id']==run['id'] for v in catalog['course_runs'])

async def test_mutation_rejects_cross_origin(workspace_runtime):
    async with await client_for(workspace_runtime,'student') as c:
        response=await c.post(f"/api/v2/course-run-homeworks/{IDS['publication']}/draft",headers={'Origin':'https://evil.invalid'},json=command('save_work_draft',IDS['publication'],{'artifact_url':'https://github.com/example/demo'}))
        assert response.status_code==403
