// Explicit scenario adapter for UI development without any backend or AI service.
import { ApiClient, type Model, type Transport } from "../api/client";
import type { W, WorkspaceInputs } from "../api/workspace-routes";
import type { createDemoCore } from "./transport";
import * as fixture from "./fixtures";
type Core = ReturnType<typeof createDemoCore>;
type Command = {
  [K in keyof WorkspaceInputs]: {
    command_name: K;
    target_id: string;
    expected_revision: number;
    request_id: string;
    idempotency_key: string;
    payload: WorkspaceInputs[K];
  };
}[keyof WorkspaceInputs];
export function secondFixture(): typeof fixture {
  const mapping = Object.fromEntries(
    Object.values(fixture.ids).map((id) => [
      id,
      id.slice(0, -4) + String(Number(id.slice(-4)) + 500).padStart(4, "0"),
    ]),
  );
  mapping[fixture.ids.organization] = fixture.ids.organization;
  function copy<T>(v: T): T {
    return JSON.parse(
      JSON.stringify(v, (_key, value) =>
        typeof value === "string" ? (mapping[value] ?? value) : value,
      ),
    ) as T;
  }
  const seed = {
    ...fixture,
    ids: Object.fromEntries(
      Object.entries(fixture.ids).map(([k, v]) => [k, mapping[v]]),
    ),
    session: structuredClone(fixture.session),
    organization: fixture.organization,
    course: copy(fixture.course),
    run: copy(fixture.run),
    homework: copy(fixture.homework),
    version: copy(fixture.version),
    history: copy(fixture.history),
    review: copy(fixture.review),
    submission: copy(fixture.submission),
  };
  seed.course.title = "Системный дизайн";
  seed.run.title = "Системный дизайн · осень 2026";
  seed.homework.title = "Декомпозиция сервиса";
  seed.version.student_text =
    "Опишите границы компонентов, их контракты и обработку ошибок.";
  seed.history.versions = [seed.version];
  return seed;
}
export function enhanceWorkspace(first: Core, second: Core): Transport {
  const cores = [first, second];
  const user = first.session.user_id;
  const courses: W<"CourseView">[] = cores.map((c) => ({
    ...c.seed.course,
    description:
      c === first
        ? "HTTP, тесты и инженерная практика"
        : "Архитектура и контракты",
    owner_id: user,
  }));
  const runs: W<"CourseRunView">[] = cores.map((c) => ({
    ...c.run,
    starts_at: "2026-09-01T09:00:00Z",
    ends_at: "2026-12-01T18:00:00Z",
    priority: "assigned",
    priority_revision: 0,
  }));
  const people: W<"DirectoryMember">[] = [
    {
      id: user,
      display_name: "Алексей Смирнов",
      roles: ["reviewer", "methodologist", "student"],
    },
    {
      id: second.seed.ids.user,
      display_name: "Мария Иванова",
      roles: ["student"],
    },
    {
      id: second.seed.ids.membership,
      display_name: "Дмитрий Соколов",
      roles: ["reviewer"],
    },
  ];
  const drafts = new Map<string, W<"DraftView">>();
  const quotas = new Map<string, W<"QuotaView">>();
  const policies = new Map<string, W<"PublicationPolicyView">>();
  const selfRuns = new Map<string, W<"SelfReviewView">>();
  const editors = new Map<string, W<"EditorDraftView">>();
  const privateDetails = new Map<string, W<"PrivateHomeworkView">>();
  const uploads = new Map<string, { view: W<"UploadView">; url: string }>();
  const exports = new Map<string, W<"ExportView">>();
  const notices: W<"NotificationView">[] = [];
  const outcomes = new Map<
    string,
    { value: W<"OutcomeInput">; revision: number }
  >();
  const assignments = new Map<string, W<"AssignmentsView">>();
  let preferences: W<"PreferencesView"> = {
    revision: 0,
    value: {
      course_run_ids: runs.map((r) => r.id),
      planned_minutes: 120,
      until_at: "2026-12-01T18:00:00Z",
      absent_from: null,
      absent_until: null,
    },
  };
  const runDrafts = new Map<string, string>();
  const receipts = new Map<string, { body: string; value: unknown }>();
  let initialized = false;
  const response = (value: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const error = (message: string, status = 409) =>
    response({ code: "workspace_demo_error", message, action: null }, status);
  const policy = (n: number): W<"PublicationPolicyView"> => ({
    self_review_limit: n,
    pass_score: 5,
    revision_days: 7,
    penalty_per_day: 0,
    max_resubmissions: 3,
    revision: 0,
  });
  for (const c of cores) {
    policies.set(c.seed.ids.publication, policy(3));
    quotas.set(c.seed.ids.publication, {
      limit: 3,
      used: 0,
      reserved: 0,
      remaining: 3,
      policy_revision: 0,
      active_run_id: null,
    });
    assignments.set(c.run.id, {
      items: [
        {
          id: c.seed.ids.user,
          student_id: c.seed.ids.user,
          student_name: c === first ? "Алексей Смирнов" : "Мария Иванова",
          reviewer_id: user,
          reviewer_name: "Алексей Смирнов",
          revision: 0,
        },
      ],
    });
  }
  function owner(identity: string): Core {
    return (
      cores.find(
        (c) =>
          Object.values(c.seed.ids).includes(identity) ||
          !!c.histories[identity] ||
          c.homeworks.some((h) => h.course_run_homework_id === identity),
      ) ?? first
    );
  }
  function publication(identity: string) {
    const c = owner(identity);
    const h = c.homeworks.find((h) => h.course_run_homework_id === identity);
    if (!h) throw new Error("Задание не найдено в демо.");
    return {
      c,
      h,
      history: c.histories[h.homework_id],
      version: c.histories[h.homework_id].versions.find(
        (v) => v.id === h.current_version_id,
      )!,
    };
  }
  function quota(pub: string): W<"QuotaView"> {
    let q = quotas.get(pub);
    if (!q) {
      const p = policies.get(pub);
      q = {
        limit: p?.self_review_limit ?? 0,
        used: 0,
        reserved: 0,
        remaining: p?.self_review_limit ?? 0,
        policy_revision: p?.revision ?? 0,
        active_run_id: null,
      };
      quotas.set(pub, q);
    }
    return q;
  }
  function initialize() {
    if (initialized) return;
    initialized = true;
    for (const c of cores) {
      if (!c.submission.versions.length) {
        c.submission.versions.push({
          id: c.seed.ids.submissionVersion,
          sequence: 1,
          homework_version_id: c.seed.ids.homeworkVersion,
          artifact_reference_id: c.seed.ids.artifactRef,
          artifact_version_id: c.seed.ids.artifact,
          capture_operation_id: null,
          submitted_at: "2026-09-04T10:00:00Z",
          effective_deadline: c.seed.homework.submission_deadline,
          phase: "before_deadline",
          status: "ready",
        });
        c.submission.current_submission_version_id =
          c.seed.ids.submissionVersion;
      }
    }
  }
  function workItems(): W<"WorkItem">[] {
    return cores.map((c) => {
      const current = c.submission.versions.at(-1);
      const pub = c.submission.publications.at(-1);
      const rev = c.submission.review_revisions.find(
        (r) => r.id === pub?.review_revision_id,
      );
      const outcome = outcomes.get(c.review.review_iteration_id);
      return {
        submission_id: c.submission.submission_id,
        submission_revision: Math.max(0, c.submission.versions.length - 1),
        publication_id: c.seed.ids.publication,
        course_run_id: c.run.id,
        homework_id: c.seed.ids.homework,
        title: c.seed.homework.title,
        course_run_title: c.run.title,
        student_id: c.seed.ids.user,
        student_name: c === first ? "Алексей Смирнов" : "Мария Иванова",
        submitted_at: current?.submitted_at ?? "2026-09-04T10:00:00Z",
        submission_version_id: current?.id ?? null,
        attempt: current?.sequence ?? 0,
        review_case_id: c.seed.ids.reviewCase,
        review_case_revision: 0,
        review_iteration_id: c.review.review_iteration_id,
        review_revision: c.review.revision,
        review_submission_version_id:
          c.review.immutable_inputs.submission_version_id,
        responsible_reviewer_id: c.review.responsibility_events.some(
          (e) => e.action === "started",
        )
          ? user
          : null,
        primary_reviewer_id:
          assignments
            .get(c.run.id)
            ?.items.find((a) => a.student_id === c.seed.ids.user)
            ?.reviewer_id ?? null,
        status:
          c.review.status === "published"
            ? (outcome?.value.decision ?? "published")
            : c.review.status,
        score: pub?.total_score ?? null,
        feedback: rev?.feedback ?? null,
        published_by: pub?.published_by ?? null,
        review_deadline: c.seed.homework.review_deadline,
      };
    });
  }
  return async (input, init = {}) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "");
    const method = init.method ?? "GET";
    const parts = path.split("/");
    const identity = parts[3] ?? "";
    if (!path.startsWith("/v2/")) {
      if (path === "/v1/courses" && method === "GET")
        return response({
          items: courses.map(({ id, title, status, revision }) => ({
            id,
            title,
            status,
            revision,
          })),
          course_runs: runs.map(
            ({ id, course_id, title, timezone, status, revision }) => ({
              id,
              course_id,
              title,
              timezone,
              status,
              revision,
            }),
          ),
        });
      if (method === "GET" && /^\/v1\/course-runs\/.+\/homeworks$/.test(path)) {
        const all = cores.flatMap((c) =>
          c.homeworks.filter((h) =>
            c.histories[h.homework_id].course_run_publications.some(
              (p) => p.course_run_id === identity && p.is_current,
            ),
          ),
        );
        return response({ items: all });
      }
      const core = owner(identity);
      const result = await core.transport(input, init);
      if (result.ok && init.body) {
        const body = JSON.parse(String(init.body));
        if (
          body.command_name === "archive_course" ||
          body.command_name === "restore_course"
        ) {
          const c = courses.find((c) => c.id === body.target_id);
          if (c) {
            c.status =
              body.command_name === "archive_course" ? "archived" : "active";
            c.revision++;
          }
        }
        if (
          body.command_name === "archive_course_run" ||
          body.command_name === "restore_course_run"
        ) {
          const r = runs.find((r) => r.id === body.target_id);
          if (r) {
            r.status =
              body.command_name === "archive_course_run"
                ? "archived"
                : "active";
            r.revision++;
          }
        }
      }
      return result;
    }
    initialize();
    try {
      if (method === "GET") {
        if (path === "/v2/catalog")
          return response({ courses, course_runs: runs });
        if (path === "/v2/directory") return response({ items: people });
        if (path === "/v2/reviewer/preferences") return response(preferences);
        if (path === "/v2/drafts")
          return response({ items: [...drafts.values()] });
        if (path === "/v2/notifications") return response({ items: notices });
        if (path.endsWith("/assignments"))
          return response(assignments.get(identity) ?? { items: [] });
        if (path.endsWith("/editor-draft"))
          return response(
            editors.get(
              `${identity}:${url.searchParams.get("course_run_id")}`,
            ) ?? { revision: 0, value: null },
          );
        if (path.endsWith("/private-details"))
          return response(
            privateDetails.get(identity) ?? {
              revision: 0,
              reviewer_guidance: "",
              reference_upload_id: null,
              criterion_classes: {},
            },
          );
        if (path.endsWith("/policy"))
          return response(policies.get(identity) ?? null);
        if (path.endsWith("/student-context")) {
          const { c, h, version } = publication(identity);
          return response({
            publication_id: identity,
            homework_id: h.homework_id,
            homework_version_id: version.id,
            course_run_id:
              c.histories[h.homework_id].course_run_publications.find(
                (p) => p.is_current,
              )?.course_run_id ?? c.run.id,
            title: h.title,
            student_text: version.student_text,
            criteria: version.criteria.map(({ id, key, title }) => ({
              id,
              key,
              title,
            })),
            submission_deadline: h.submission_deadline,
            draft: drafts.get(identity) ?? null,
            quota: policies.has(identity) ? quota(identity) : null,
            self_reviews: [...selfRuns.values()].filter(
              (r) => drafts.get(identity)?.id === runDrafts.get(r.id),
            ),
            policy: policies.get(identity) ?? null,
          });
        }
        if (path.startsWith("/v2/self-reviews/")) {
          const r = selfRuns.get(identity);
          return r ? response(r) : error("Запуск не найден.", 404);
        }
        if (path === "/v2/works") {
          let items = workItems();
          const run = url.searchParams.get("course_run_id"),
            q = url.searchParams.get("q")?.toLowerCase(),
            state = url.searchParams.get("state"),
            view = url.searchParams.get("view");
          if (run) items = items.filter((w) => w.course_run_id === run);
          if (q)
            items = items.filter((w) =>
              `${w.title} ${w.student_name}`.toLowerCase().includes(q),
            );
          if (state) items = items.filter((w) => w.status === state);
          if (view === "assigned")
            items = items.filter((w) => w.primary_reviewer_id === user);
          if (view === "active")
            items = items.filter(
              (w) =>
                w.responsible_reviewer_id === user && w.status !== "published",
            );
          const offset = Number(url.searchParams.get("offset") ?? 0),
            limit = Number(url.searchParams.get("limit") ?? 30);
          return response({
            items: items.slice(offset, offset + limit),
            total: items.length,
            offset,
            limit,
          });
        }
        if (path === "/v2/statistics") {
          const pubs = cores.flatMap((c) => c.submission.publications);
          return response({
            publications: pubs.length,
            average_elapsed_minutes: null,
            changed_decisions: 0,
            compared_decisions: 0,
            from_date: "2026-08-01T00:00:00Z",
            until_date: new Date().toISOString(),
          });
        }
        if (path.startsWith("/v2/reviews/") && path.endsWith("/context")) {
          const c = owner(identity),
            v =
              Object.values(c.histories)
                .flatMap((h) => h.versions)
                .find(
                  (v) => v.id === c.review.immutable_inputs.homework_version_id,
                ) ?? c.seed.version;
          return response({
            homework_id: c.seed.ids.homework,
            criterion_set_id: v.criterion_set_id,
            homework_version_id: v.id,
            student_text: v.student_text,
            max_score: v.max_score,
            criteria: v.criteria,
            private_details: privateDetails.get(v.id) ?? null,
            self_reviews: [...selfRuns.values()],
            outcome: outcomes.get(identity)?.value ?? null,
            outcome_revision: outcomes.get(identity)?.revision ?? 0,
          });
        }
        if (path.startsWith("/v2/artifacts/") && path.endsWith("/download"))
          return response({
            url:
              uploads.get(identity)?.url ??
              `${location.origin}/demo-artifact.txt`,
            expires_at: new Date(Date.now() + 900000).toISOString(),
          });
        if (path.startsWith("/v2/submissions/")) {
          const c = owner(identity);
          return response({
            id: c.submission.submission_id,
            title: c.seed.homework.title,
            publication_id: c.seed.ids.publication,
            course_run_id: c.run.id,
            attempts: c.submission.versions.map((v) => ({
              id: v.id,
              sequence: v.sequence,
              submitted_at: v.submitted_at,
              status: v.status,
              artifact_id: v.artifact_version_id,
              capture_operation_id: v.capture_operation_id,
            })),
            reviews: c.submission.publications.map((p) => ({
              id: p.id,
              iteration_id: p.review_iteration_id,
              submission_version_id:
                c.review.immutable_inputs.submission_version_id,
              published_at: p.published_at,
              score: p.total_score,
              feedback:
                c.submission.review_revisions.find(
                  (v) => v.id === p.review_revision_id,
                )?.feedback ?? "",
              decision:
                outcomes.get(p.review_iteration_id)?.value.decision ?? null,
              revision_deadline:
                outcomes.get(p.review_iteration_id)?.value.revision_deadline ??
                null,
              criteria: c.review.criterion_decisions.map((d) => ({
                title:
                  c.seed.version.criteria.find((v) => v.id === d.criterion_id)
                    ?.title ?? "Критерий",
                points: d.points,
                max_points: 10,
                reason: d.reason,
              })),
            })),
            current_publication_id: c.submission.current_publication_id,
          });
        }
        if (path.startsWith("/v2/exports/"))
          return exports.has(identity)
            ? response(exports.get(identity))
            : error("Выгрузка не найдена.", 404);
        return error("Демо-ресурс не найден.", 404);
      }
      const bodyText = String(init.body ?? "");
      const cmd = JSON.parse(bodyText) as Command;
      const previous = receipts.get(cmd.idempotency_key);
      if (previous)
        return previous.body === bodyText
          ? response(previous.value)
          : error("Ключ запроса уже использован.");
      let result: unknown;
      switch (cmd.command_name) {
        case "create_course": {
          const id = crypto.randomUUID();
          courses.push({
            id,
            title: cmd.payload.title,
            description: cmd.payload.description ?? "",
            owner_id: cmd.payload.owner_id ?? null,
            status: "active",
            revision: 0,
          });
          first.org.revision++;
          result = { id, revision: 0 };
          break;
        }
        case "create_course_run": {
          const id = crypto.randomUUID();
          runs.push({
            id,
            course_id: cmd.target_id,
            title: cmd.payload.title,
            starts_at: cmd.payload.starts_at,
            ends_at: cmd.payload.ends_at,
            timezone: cmd.payload.timezone,
            priority: cmd.payload.priority,
            priority_revision: 0,
            status: "active",
            revision: 0,
          });
          result = { id, revision: 0 };
          break;
        }
        case "save_preferences": {
          if (cmd.expected_revision !== preferences.revision)
            return error("Настройки изменились.");
          preferences = {
            revision: preferences.revision + 1,
            value: cmd.payload,
          };
          result = preferences;
          break;
        }
        case "save_work_draft": {
          const old = drafts.get(cmd.target_id);
          if (cmd.expected_revision !== (old?.revision ?? 0))
            return error("Черновик изменился.");
          const value: W<"DraftView"> = {
            id: old?.id ?? crypto.randomUUID(),
            revision: (old?.revision ?? 0) + 1,
            publication_id: cmd.target_id,
            artifact_url: cmd.payload.artifact_url ?? "",
            upload_id: cmd.payload.upload_id ?? null,
            comment: cmd.payload.comment ?? "",
          };
          drafts.set(cmd.target_id, value);
          result = value;
          break;
        }
        case "start_self_review": {
          const draft = [...drafts.values()].find(
            (d) => d.id === cmd.target_id,
          );
          if (!draft) return error("Черновик не найден.", 404);
          const q = quota(draft.publication_id);
          if (q.remaining === 0) return error("Лимит самопроверок исчерпан.");
          if (q.reserved) return error("Самопроверка уже выполняется.");
          q.reserved = 1;
          q.remaining--;
          const id = crypto.randomUUID();
          q.active_run_id = id;
          const v: W<"SelfReviewView"> = {
            id,
            draft_revision: draft.revision,
            status: "running",
            disposition: "reserved",
            artifact_id: draft.upload_id,
            created_at: new Date().toISOString(),
            result: null,
            error_code: null,
            quota: structuredClone(q),
          };
          selfRuns.set(id, v);
          runDrafts.set(id, draft.id);
          setTimeout(() => {
            if (v.disposition !== "reserved") return;
            const p = publication(draft.publication_id);
            q.reserved = 0;
            q.used++;
            q.active_run_id = null;
            v.status = "succeeded";
            v.disposition = "consumed";
            v.result = {
              findings: p.version.criteria.map((c) => ({
                criterion_id: c.id,
                status: "needs_attention",
                feedback: `Демо: проверьте требование «${c.title}». Внешняя AI-модель не вызывалась.`,
                evidence: "",
              })),
            };
            v.quota = structuredClone(q);
          }, 500);
          result = structuredClone(v);
          break;
        }
        case "set_publication_policy": {
          const current = policies.get(cmd.target_id);
          if (cmd.expected_revision !== (current?.revision ?? 0))
            return error("Политика изменилась.");
          const p = { ...cmd.payload, revision: (current?.revision ?? 0) + 1 };
          policies.set(cmd.target_id, p);
          const q = quota(cmd.target_id);
          q.limit = p.self_review_limit;
          q.policy_revision = p.revision;
          q.remaining = Math.max(0, q.limit - q.used - q.reserved);
          result = { id: cmd.target_id, revision: p.revision };
          break;
        }
        case "save_editor_draft": {
          const key = `${cmd.target_id}:${cmd.payload.course_run_id}`,
            old = editors.get(key);
          if (cmd.expected_revision !== (old?.revision ?? 0))
            return error("Черновик задания изменился.");
          const next = {
            revision: (old?.revision ?? 0) + 1,
            value: cmd.payload,
          };
          editors.set(key, next);
          result = next;
          break;
        }
        case "save_private_homework": {
          const next = {
            ...cmd.payload,
            revision: (privateDetails.get(cmd.target_id)?.revision ?? 0) + 1,
          };
          privateDetails.set(cmd.target_id, next);
          result = next;
          break;
        }
        case "publish_workspace_homework": {
          const c = owner(cmd.target_id);
          const core = new ApiClient(c.transport);
          const published = await core.command(
            "publish_homework_version",
            cmd.target_id,
            cmd.expected_revision,
            {
              course_run_id: cmd.payload.course_run_id,
              submission_deadline: cmd.payload.submission_deadline,
              review_deadline: cmd.payload.review_deadline,
            },
          );
          const h = c.homeworks.find(
            (h) => h.course_run_homework_id === published.id,
          )!;
          const history = c.histories[h.homework_id];
          history.course_run_publications.at(-1)!.course_run_id =
            cmd.payload.course_run_id;
          const p = {
            ...cmd.payload.policy,
            revision: (policies.get(published.id)?.revision ?? 0) + 1,
          };
          policies.set(published.id, p);
          const q = quota(published.id);
          q.limit = p.self_review_limit;
          q.remaining = Math.max(0, q.limit - q.used - q.reserved);
          q.policy_revision = p.revision;
          result = {
            publication_id: published.id,
            history_publication_id: history.course_run_publications.at(-1)!.id,
            revision: published.revision,
            policy_revision: p.revision,
          };
          break;
        }
        case "upload_artifact": {
          const id = crypto.randomUUID();
          const bytes = Uint8Array.from(atob(cmd.payload.content_base64), (c) =>
            c.charCodeAt(0),
          );
          const view: W<"UploadView"> = {
            id,
            filename: cmd.payload.filename,
            media_type: cmd.payload.media_type,
            byte_size: bytes.length,
            digest: `sha256:${"f".repeat(64)}`,
          };
          uploads.set(id, {
            view,
            url: URL.createObjectURL(
              new Blob([bytes], { type: cmd.payload.media_type }),
            ),
          });
          result = view;
          break;
        }
        case "submit_uploaded_draft": {
          const draft = [...drafts.values()].find(
            (d) => d.id === cmd.target_id,
          );
          if (!draft) return error("Черновик не найден.", 404);
          const c = publication(draft.publication_id).c;
          const core = new ApiClient(c.transport);
          const cap = await core.command(
            "preflight_submission",
            draft.publication_id,
            0,
            { artifact_url: "https://github.com/local-fixture/upload" },
          );
          const submitted = await core.command(
            "submit_work",
            cap.submission_id,
            cap.submission_revision,
            { artifact_reference_id: cap.artifact_reference_id! },
          );
          c.submission.versions.at(-1)!.artifact_version_id = draft.upload_id;
          result = {
            id: submitted.submission_id,
            revision: submitted.submission_revision,
          };
          break;
        }
        case "open_work": {
          const c = owner(cmd.target_id);
          result = {
            id: c.review.review_iteration_id,
            revision: c.review.revision,
          };
          break;
        }
        case "assign_student": {
          const list = assignments.get(cmd.target_id) ?? { items: [] };
          let item = list.items.find(
            (a) => a.student_id === cmd.payload.student_id,
          );
          if (!item) {
            item = {
              id: crypto.randomUUID(),
              student_id: cmd.payload.student_id,
              student_name:
                people.find((p) => p.id === cmd.payload.student_id)
                  ?.display_name ?? "Студент",
              reviewer_id: null,
              reviewer_name: null,
              revision: 0,
            };
            list.items.push(item);
            assignments.set(cmd.target_id, list);
          }
          if (cmd.expected_revision !== item.revision)
            return error("Закрепление изменилось.");
          item.reviewer_id = cmd.payload.reviewer_id;
          item.reviewer_name =
            people.find((p) => p.id === cmd.payload.reviewer_id)
              ?.display_name ?? null;
          item.revision++;
          result = { id: item.id, revision: item.revision };
          break;
        }
        case "set_course_membership": {
          const run = runs.find((r) => r.id === cmd.target_id)!;
          run.revision++;
          if (cmd.payload.kind === "student" && cmd.payload.active) {
            const list = assignments.get(run.id) ?? { items: [] };
            if (!list.items.some((a) => a.student_id === cmd.payload.user_id))
              list.items.push({
                id: crypto.randomUUID(),
                student_id: cmd.payload.user_id,
                student_name:
                  people.find((p) => p.id === cmd.payload.user_id)
                    ?.display_name ?? "Студент",
                reviewer_id: null,
                reviewer_name: null,
                revision: 0,
              });
            assignments.set(run.id, list);
          }
          result = { id: run.id, revision: run.revision };
          break;
        }
        case "set_run_priority": {
          const r = runs.find((r) => r.id === cmd.target_id)!;
          r.priority = cmd.payload.priority;
          r.priority_revision++;
          result = { id: r.id, revision: r.priority_revision };
          break;
        }
        case "save_review_outcome": {
          const value = {
            value: cmd.payload,
            revision: (outcomes.get(cmd.target_id)?.revision ?? 0) + 1,
          };
          outcomes.set(cmd.target_id, value);
          result = { id: cmd.target_id, revision: value.revision };
          break;
        }
        case "remind_reviewers": {
          for (const _recipient of cmd.payload.reviewer_ids)
            notices.push({
              id: crypto.randomUUID(),
              course_run_id: cmd.target_id,
              text: cmd.payload.text,
              created_at: new Date().toISOString(),
              read: false,
            });
          result = { id: cmd.request_id, revision: 0 };
          break;
        }
        case "read_notification": {
          const n = notices.find((n) => n.id === cmd.target_id);
          if (n) n.read = true;
          result = { id: cmd.target_id, revision: 0 };
          break;
        }
        case "release_self_review": {
          const run = selfRuns.get(cmd.target_id);
          if (!run) return error("Запуск не найден.", 404);
          const draft = [...drafts.values()].find(
            (d) => d.id === runDrafts.get(run.id),
          )!;
          const q = quota(draft.publication_id);
          q.reserved = 0;
          q.active_run_id = null;
          q.remaining = Math.max(0, q.limit - q.used);
          run.status = "failed";
          run.disposition = "released";
          run.quota = structuredClone(q);
          result = { id: run.id, revision: 0 };
          break;
        }
        case "create_export": {
          const id = crypto.randomUUID();
          const items = workItems().filter(
            (w) =>
              w.course_run_id === cmd.payload.course_run_id &&
              (cmd.payload.include_unpublished || w.score !== null),
          );
          const text = [
            cmd.payload.columns.join(","),
            ...items.map((w) =>
              cmd.payload.columns
                .map((c) =>
                  JSON.stringify(
                    c === "score"
                      ? (w.score ?? "")
                      : c === "student_id"
                        ? w.student_id
                        : c === "status"
                          ? w.status
                          : c === "attempt"
                            ? w.attempt
                            : c === "feedback"
                              ? (w.feedback ?? "")
                              : (w.published_by ?? ""),
                  ),
                )
                .join(","),
            ),
          ].join("\n");
          const url = URL.createObjectURL(
            new Blob([text], { type: "text/csv;charset=utf-8" }),
          );
          const value: W<"ExportView"> = {
            id,
            status: "succeeded",
            rows: items.length,
            download: {
              url,
              expires_at: new Date(Date.now() + 900000).toISOString(),
            },
            error: null,
          };
          exports.set(id, value);
          result = value;
          break;
        }
        default:
          return error("Действие не поддерживается имитатором.", 501);
      }
      receipts.set(cmd.idempotency_key, {
        body: bodyText,
        value: structuredClone(result),
      });
      return response(result);
    } catch (e) {
      return error(e instanceof Error ? e.message : "Ошибка сценария.", 422);
    }
  };
}
