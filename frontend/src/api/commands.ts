// Generated from frozen contracts. Run npm run generate:api.
export type Uuid = string;
export type AgentScope =
  | "courses:read"
  | "review_preferences:write"
  | "review_queue:read"
  | "reviews:read"
  | "reviews:write"
  | "ai_reviews:start"
  | "operations:read"
  | "publication_requests:write";

export interface CommandPayloads {
  activate_bootstrap: ActivateBootstrapPayload;
  create_invitation: CreateInvitationPayload;
  revoke_invitation: ReasonPayload;
  start_course_import: StartCourseImportPayload;
  archive_course: ReasonPayload;
  restore_course: EmptyPayload;
  archive_course_run: ReasonPayload;
  restore_course_run: EmptyPayload;
  change_membership_roles: ChangeMembershipRolesPayload;
  create_homework: CreateHomeworkPayload;
  create_homework_version: CreateHomeworkVersionPayload;
  publish_homework_version: PublishHomeworkVersionPayload;
  set_reviewer_course_selection: SetReviewerCourseSelectionPayload;
  set_reviewer_availability: SetReviewerAvailabilityPayload;
  preflight_submission: PreflightSubmissionPayload;
  submit_work: SubmitWorkPayload;
  open_review_iteration: OpenReviewIterationPayload;
  migrate_review_requirements: MigrateReviewRequirementsPayload;
  create_review_correction: CreateReviewCorrectionPayload;
  record_review_responsibility: RecordReviewResponsibilityPayload;
  save_review_revision: SaveReviewRevisionPayload;
  request_review_publication: RequestReviewPublicationPayload;
  publish_review: PublishReviewPayload;
  start_ai_review: EmptyPayload;
  retry_delivery: RetryDeliveryPayload;
  grant_agent_authorization: GrantAgentAuthorizationPayload;
  revoke_agent_authorization: ReasonPayload;
  recover_methodologist: RecoverMethodologistPayload;
}
export interface ActivateBootstrapPayload {
  external_identity: ExternalIdentity;
}
export interface ExternalIdentity {
  provider: string;
  issuer: string;
  subject: string;
}
export interface CreateInvitationPayload {
  email: string;
  role: "methodologist" | "reviewer";
  expires_at: string;
}
export interface ReasonPayload {
  reason: string;
}
export interface StartCourseImportPayload {
  provider: string;
  external_url: string;
}
export interface EmptyPayload {}
export interface ChangeMembershipRolesPayload {
  roles: ("methodologist" | "reviewer" | "student")[];
}
export interface CreateHomeworkPayload {
  title: string;
}
export interface CreateHomeworkVersionPayload {
  student_text: string;
  max_score: number;
  /**
   * @minItems 1
   * @maxItems 2
   */
  artifact_kinds: ("github" | "google_docs")[];
  estimated_review_minutes: number;
  /**
   * @minItems 1
   * @maxItems 500
   */
  criteria: CriterionInput[];
}
export interface CriterionInput {
  key: string;
  title: string;
  description: string;
  max_points: number;
}
export interface PublishHomeworkVersionPayload {
  course_run_id: Uuid;
  submission_deadline: string;
  review_deadline: string;
}
export interface SetReviewerCourseSelectionPayload {
  /**
   * @maxItems 1000
   */
  course_run_ids: Uuid[];
}
export interface SetReviewerAvailabilityPayload {
  planned_minutes: number;
  until_at: string;
}
export interface PreflightSubmissionPayload {
  artifact_url: string;
}
export interface SubmitWorkPayload {
  artifact_reference_id: Uuid;
}
export interface OpenReviewIterationPayload {
  submission_version_id: Uuid;
}
export interface MigrateReviewRequirementsPayload {
  homework_version_id: Uuid;
  criterion_set_id: Uuid;
}
export interface CreateReviewCorrectionPayload {
  published_review_revision_id: Uuid;
  reason: string;
}
export interface RecordReviewResponsibilityPayload {
  action: "started" | "joined" | "released" | "completed";
}
export interface SaveReviewRevisionPayload {
  feedback: string;
  /**
   * @maxItems 500
   */
  criterion_decisions: ReviewDecision[];
  /**
   * @maxItems 500
   */
  review_notes: ReviewNote[];
}
export interface ReviewDecision {
  criterion_id: Uuid;
  /**
   * MUST NOT exceed max_points of the matching criterion in the ReviewIteration snapshot.
   */
  points: number;
  decision: "accepted" | "changed" | "manual";
  reason: string;
  /**
   * @maxItems 100
   */
  evidence_ids?: Uuid[];
}
export interface ReviewNote {
  criterion_id?: Uuid | null;
  text: string;
}
export interface RequestReviewPublicationPayload {
  review_revision_id: Uuid;
  expires_at: string;
}
export interface PublishReviewPayload {
  review_revision_id: Uuid;
  publication_request_id?: Uuid | null;
}
export interface RetryDeliveryPayload {
  reconcile_first: true;
}
export interface GrantAgentAuthorizationPayload {
  agent_id: Uuid;
  /**
   * @minItems 1
   */
  scopes: AgentScope[];
  expires_at: string;
}
export interface RecoverMethodologistPayload {
  organization_id: Uuid;
  provider: string;
  issuer: string;
  subject: string;
  reason: string;
}

export const revisionTargets = {
  "activate_bootstrap": "organization",
  "create_invitation": "organization",
  "revoke_invitation": "invitation",
  "start_course_import": "organization",
  "archive_course": "course",
  "restore_course": "course",
  "archive_course_run": "course_run",
  "restore_course_run": "course_run",
  "change_membership_roles": "membership",
  "create_homework": "course_run",
  "create_homework_version": "homework",
  "publish_homework_version": "homework_version",
  "set_reviewer_course_selection": "membership",
  "set_reviewer_availability": "membership",
  "preflight_submission": "course_run_homework",
  "submit_work": "submission",
  "open_review_iteration": "review_case",
  "migrate_review_requirements": "review_iteration",
  "create_review_correction": "review_iteration",
  "record_review_responsibility": "review_iteration",
  "save_review_revision": "review_iteration",
  "request_review_publication": "review_iteration",
  "publish_review": "review_iteration",
  "start_ai_review": "review_iteration",
  "retry_delivery": "external_delivery",
  "grant_agent_authorization": "membership",
  "revoke_agent_authorization": "agent_authorization",
  "recover_methodologist": "organization"
} as const;
export type CommandName = keyof CommandPayloads;
export type WireCommand<K extends CommandName = CommandName> = { request_id: string; idempotency_key: string; command_name: K; revision_target: typeof revisionTargets[K]; target_id: string; expected_revision: number; payload: CommandPayloads[K] };
