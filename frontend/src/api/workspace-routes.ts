// Generated from workspace v2 OpenAPI.
import type {components} from './workspace-schema';
export type W<K extends keyof components['schemas']> = components['schemas'][K];
export const workspaceRoutes = {
  "save_work_draft": {
    "path": "/v2/course-run-homeworks/{identity}/draft",
    "method": "POST"
  },
  "set_publication_policy": {
    "path": "/v2/course-run-homeworks/{identity}/policy",
    "method": "POST"
  },
  "assign_student": {
    "path": "/v2/course-runs/{identity}/assignments",
    "method": "POST"
  },
  "set_course_membership": {
    "path": "/v2/course-runs/{identity}/memberships",
    "method": "POST"
  },
  "set_run_priority": {
    "path": "/v2/course-runs/{identity}/priority",
    "method": "POST"
  },
  "remind_reviewers": {
    "path": "/v2/course-runs/{identity}/reminders",
    "method": "POST"
  },
  "create_course": {
    "path": "/v2/courses",
    "method": "POST"
  },
  "create_course_run": {
    "path": "/v2/courses/{identity}/course-runs",
    "method": "POST"
  },
  "create_export": {
    "path": "/v2/exports",
    "method": "POST"
  },
  "save_private_homework": {
    "path": "/v2/homework-versions/{identity}/private-details",
    "method": "POST"
  },
  "publish_workspace_homework": {
    "path": "/v2/homework-versions/{identity}/publish",
    "method": "POST"
  },
  "save_editor_draft": {
    "path": "/v2/homeworks/{identity}/editor-draft",
    "method": "POST"
  },
  "read_notification": {
    "path": "/v2/notifications/{identity}/read",
    "method": "POST"
  },
  "save_preferences": {
    "path": "/v2/reviewer/preferences",
    "method": "POST"
  },
  "save_review_outcome": {
    "path": "/v2/reviews/{identity}/outcome",
    "method": "POST"
  },
  "publish_workspace_review": {
    "path": "/v2/reviews/{identity}/publish",
    "method": "POST"
  },
  "release_self_review": {
    "path": "/v2/self-reviews/{identity}/release",
    "method": "POST"
  },
  "open_work": {
    "path": "/v2/submissions/{identity}/open-review",
    "method": "POST"
  },
  "upload_artifact": {
    "path": "/v2/uploads",
    "method": "POST"
  },
  "start_self_review": {
    "path": "/v2/work-drafts/{identity}/self-reviews",
    "method": "POST"
  },
  "submit_uploaded_draft": {
    "path": "/v2/work-drafts/{identity}/submit-upload",
    "method": "POST"
  }
} as const;
export interface WorkspaceInputs {save_work_draft: W<'DraftInput'>;
set_publication_policy: W<'PublicationPolicyInput'>;
assign_student: W<'AssignmentInput'>;
set_course_membership: W<'MembershipInput'>;
set_run_priority: W<'PriorityInput'>;
remind_reviewers: W<'NotificationInput'>;
create_course: W<'CourseInput'>;
create_course_run: W<'CourseRunInput'>;
create_export: W<'ExportInput'>;
save_private_homework: W<'PrivateHomeworkInput'>;
publish_workspace_homework: W<'PublishWithPolicyInput'>;
save_editor_draft: W<'EditorDraftInput'>;
read_notification: W<'EmptyInput'>;
save_preferences: W<'PreferencesInput'>;
save_review_outcome: W<'OutcomeInput'>;
publish_workspace_review: W<'PublishWorkspaceReviewInput'>;
release_self_review: W<'ReleaseInput'>;
open_work: W<'OpenWorkInput'>;
upload_artifact: W<'UploadInput'>;
start_self_review: W<'EmptyInput'>;
submit_uploaded_draft: W<'EmptyInput'>;}
export interface WorkspaceResults {save_work_draft: W<'DraftView'>;
set_publication_policy: W<'ResourceResult'>;
assign_student: W<'ResourceResult'>;
set_course_membership: W<'ResourceResult'>;
set_run_priority: W<'ResourceResult'>;
remind_reviewers: W<'ResourceResult'>;
create_course: W<'ResourceResult'>;
create_course_run: W<'ResourceResult'>;
create_export: W<'ExportView'>;
save_private_homework: W<'PrivateHomeworkView'>;
publish_workspace_homework: W<'PublishedWorkspaceHomework'>;
save_editor_draft: W<'EditorDraftView'>;
read_notification: W<'ResourceResult'>;
save_preferences: W<'PreferencesView'>;
save_review_outcome: W<'ResourceResult'>;
publish_workspace_review: W<'PublishedGradeView'>;
release_self_review: W<'ResourceResult'>;
open_work: W<'ResourceResult'>;
upload_artifact: W<'UploadView'>;
start_self_review: W<'SelfReviewView'>;
submit_uploaded_draft: W<'ResourceResult'>;}
