// Generated from frozen OpenAPI.
import type { components } from './schema';
export const commandRoutes = {
  "create_invitation": {
    "path": "/v1/invitations",
    "method": "POST"
  },
  "revoke_invitation": {
    "path": "/v1/invitations/{invitationId}/revoke",
    "method": "POST"
  },
  "start_course_import": {
    "path": "/v1/courses/imports",
    "method": "POST"
  },
  "create_homework": {
    "path": "/v1/course-runs/{courseRunId}/homeworks",
    "method": "POST"
  },
  "archive_course_run": {
    "path": "/v1/course-runs/{courseRunId}/archive",
    "method": "POST"
  },
  "restore_course_run": {
    "path": "/v1/course-runs/{courseRunId}/restore",
    "method": "POST"
  },
  "archive_course": {
    "path": "/v1/courses/{courseId}/archive",
    "method": "POST"
  },
  "restore_course": {
    "path": "/v1/courses/{courseId}/restore",
    "method": "POST"
  },
  "change_membership_roles": {
    "path": "/v1/memberships/{membershipId}/roles",
    "method": "PUT"
  },
  "create_homework_version": {
    "path": "/v1/homeworks/{homeworkId}/versions",
    "method": "POST"
  },
  "publish_homework_version": {
    "path": "/v1/homework-versions/{homeworkVersionId}/publish",
    "method": "POST"
  },
  "set_reviewer_course_selection": {
    "path": "/v1/reviewer/course-selections",
    "method": "POST"
  },
  "set_reviewer_availability": {
    "path": "/v1/reviewer/availability",
    "method": "PUT"
  },
  "preflight_submission": {
    "path": "/v1/course-run-homeworks/{courseRunHomeworkId}/submissions/preflight",
    "method": "POST"
  },
  "submit_work": {
    "path": "/v1/submissions/{submissionId}/versions",
    "method": "POST"
  },
  "open_review_iteration": {
    "path": "/v1/review-cases/{reviewCaseId}/iterations",
    "method": "POST"
  },
  "save_review_revision": {
    "path": "/v1/review-iterations/{reviewIterationId}/revisions",
    "method": "POST"
  },
  "migrate_review_requirements": {
    "path": "/v1/review-iterations/{reviewIterationId}/requirements-migrations",
    "method": "POST"
  },
  "create_review_correction": {
    "path": "/v1/review-iterations/{reviewIterationId}/corrections",
    "method": "POST"
  },
  "record_review_responsibility": {
    "path": "/v1/review-iterations/{reviewIterationId}/responsibility-events",
    "method": "POST"
  },
  "publish_review": {
    "path": "/v1/review-iterations/{reviewIterationId}/publish",
    "method": "POST"
  },
  "request_review_publication": {
    "path": "/v1/review-iterations/{reviewIterationId}/publication-requests",
    "method": "POST"
  },
  "start_ai_review": {
    "path": "/v1/review-iterations/{reviewIterationId}/ai-review",
    "method": "POST"
  },
  "grant_agent_authorization": {
    "path": "/v1/memberships/{membershipId}/agent-authorizations",
    "method": "POST"
  },
  "revoke_agent_authorization": {
    "path": "/v1/agent-authorizations/{agentAuthorizationId}/revoke",
    "method": "POST"
  },
  "retry_delivery": {
    "path": "/v1/deliveries/{deliveryId}/retry",
    "method": "POST"
  }
} as const;
export interface CommandResults { create_invitation: components['schemas']['CreatedResource'];
revoke_invitation: void;
start_course_import: components['schemas']['Operation'];
create_homework: components['schemas']['CreatedResource'];
archive_course_run: void;
restore_course_run: void;
archive_course: void;
restore_course: void;
change_membership_roles: components['schemas']['UpdatedResource'];
create_homework_version: components['schemas']['CreatedResource'];
publish_homework_version: components['schemas']['CreatedResource'];
set_reviewer_course_selection: void;
set_reviewer_availability: components['schemas']['UpdatedResource'];
preflight_submission: components['schemas']['ArtifactCapability'];
submit_work: components['schemas']['SubmissionVersionCreated'];
open_review_iteration: components['schemas']['ReviewIterationCreated'];
save_review_revision: components['schemas']['ReviewRevisionSaved'];
migrate_review_requirements: components['schemas']['ReviewIterationCreated'];
create_review_correction: components['schemas']['ReviewIterationCreated'];
record_review_responsibility: components['schemas']['CreatedResource'];
publish_review: components['schemas']['Publication'];
request_review_publication: components['schemas']['PublicationRequest'];
start_ai_review: components['schemas']['Operation'];
grant_agent_authorization: components['schemas']['AgentAuthorizationCreated'];
revoke_agent_authorization: void;
retry_delivery: components['schemas']['Operation']; }
