import type { Model } from "../api/client";
export const ids = Object.fromEntries(
  [
    "user",
    "organization",
    "membership",
    "course",
    "run",
    "homework",
    "homeworkVersion",
    "publication",
    "criterion",
    "submission",
    "submissionVersion",
    "artifactRef",
    "artifact",
    "reviewCase",
    "review",
    "revision",
    "operation",
  ].map((key, index) => [
    key,
    `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
  ]),
);
export const session: Model<"Session"> = {
  user_id: ids.user,
  organization_id: ids.organization,
  membership_id: ids.membership,
  roles: ["reviewer", "methodologist", "student"],
  membership_revision: 0,
  auth_epoch: 0,
  actor_type: "user",
};
export const organization: Model<"Organization"> = {
  id: ids.organization,
  name: "Академия · демо",
  status: "active",
  revision: 0,
};
export const course: Model<"Course"> = {
  id: ids.course,
  title: "Разработка на Go",
  status: "active",
  revision: 0,
};
export const run: Model<"CourseRun"> = {
  id: ids.run,
  course_id: ids.course,
  title: "Go · осень 2026",
  timezone: "Europe/Moscow",
  status: "active",
  revision: 0,
};
export const homework: Model<"HomeworkSummary"> = {
  course_run_homework_id: ids.publication,
  revision: 0,
  homework_id: ids.homework,
  current_version_id: ids.homeworkVersion,
  title: "HTTP-сервис коротких ссылок",
  max_score: 10,
  artifact_kinds: ["github", "google_docs"],
  submission_deadline: "2026-10-20T20:59:00Z",
  review_deadline: "2026-10-24T20:59:00Z",
};
export const version: Model<"HomeworkVersionSummary"> = {
  id: ids.homeworkVersion,
  revision: 0,
  version_number: 1,
  criterion_set_id: ids.criterion,
  student_text:
    "Разработайте HTTP-сервис для создания коротких ссылок. Опишите запуск, обработайте ошибки валидации и добавьте тесты для основных сценариев.",
  max_score: 10,
  artifact_kinds: ["github", "google_docs"],
  estimated_review_minutes: 30,
  criteria: [
    {
      id: ids.criterion,
      key: "http",
      title: "HTTP API и обработка ошибок",
      description:
        "Сервис создаёт ссылки, выполняет редирект и возвращает корректные коды ответа для невалидных запросов.",
      max_points: 10,
      position: 0,
    },
  ],
};
export const history: Model<"HomeworkHistory"> = {
  homework_id: ids.homework,
  homework_revision: 0,
  versions: [version],
  course_run_publications: [
    {
      id: ids.publication,
      course_run_homework_id: ids.publication,
      course_run_id: ids.run,
      homework_version_id: ids.homeworkVersion,
      publication_sequence: 1,
      submission_deadline: homework.submission_deadline,
      review_deadline: homework.review_deadline,
      published_at: "2026-09-01T09:00:00Z",
      is_current: true,
    },
  ],
};
export const review: Model<"ReviewDetail"> = {
  review_iteration_id: ids.review,
  immutable_inputs: {
    course_run_id: ids.run,
    submission_version_id: ids.submissionVersion,
    effective_deadline: homework.submission_deadline,
    artifact_version_id: ids.artifact,
    artifact_content_digest: `sha256:${"a".repeat(64)}`,
    artifact_download_url: "http://localhost:5173/demo-artifact.txt",
    artifact_download_expires_at: "2026-10-24T21:00:00Z",
    homework_version_id: ids.homeworkVersion,
    criterion_set_id: ids.criterion,
    contract_version: "1.1.0",
  },
  current_review_revision_id: null,
  current_review_revision: null,
  revision: 0,
  status: "in_review",
  criterion_decisions: [],
  review_notes: [],
  ai_review: null,
  responsibility_events: [],
  publication_request: null,
  deliveries: [],
};
export const submission: Model<"SubmissionHistory"> = {
  submission_id: ids.submission,
  course_run_id: ids.run,
  homework_id: ids.homework,
  current_submission_version_id: null,
  current_publication_id: null,
  versions: [],
  artifact_versions: [],
  review_iterations: [],
  review_revisions: [],
  publications: [],
};
export function operation(
  kind: Model<"Operation">["kind"] = "ai_review",
): Model<"Operation"> {
  return {
    id: crypto.randomUUID(),
    kind,
    input_version: "demo-v1",
    state: "succeeded",
    attempts: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
    error: null,
  };
}
