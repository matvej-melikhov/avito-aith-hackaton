// Explicit local demo adapter. It never calls the network or live providers.
import type { Transport, Model } from "../api/client";
import type { CommandName, WireCommand } from "../api/commands";
import * as fixture from "./fixtures";
import { enhanceWorkspace, secondFixture } from "./workspace";
type AnyCommand = { [K in CommandName]: WireCommand<K> }[CommandName];
export function createDemoCore(seed: typeof fixture = fixture) {
  const session = structuredClone(seed.session),
    org = structuredClone(seed.organization),
    run = structuredClone(seed.run),
    review = structuredClone(seed.review),
    submission = structuredClone(seed.submission);
  const homeworks = [structuredClone(seed.homework)];
  const titles: Record<string, string> = {
    [seed.ids.homework]: seed.homework.title,
  };
  const histories: Record<string, Model<"HomeworkHistory">> = {
    [seed.ids.homework]: structuredClone(seed.history),
  };
  const operations: Record<string, Model<"Operation">> = {};
  const invitations: Model<"InvitationList">["items"] = [];
  let authenticated = true;
  let submissionRevision = 0;
  const receipts = new Map<string, { body: string; result: unknown }>();
  const response = (data: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const error = (status: number, message: string) =>
    response({ code: `demo_${status}`, message, action: null }, status);
  const transport: Transport = async (input, init = {}) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "");
    const method = init.method ?? "GET";
    if (
      path === "/v1/auth/reviewer/magic-link" ||
      path === "/v1/auth/stepik/callback"
    ) {
      authenticated = true;
      return response(undefined, 204);
    }
    if (!authenticated)
      return error(
        401,
        "Демо-сессия завершена. Перезагрузите вкладку для сброса.",
      );
    if (path === "/v1/session") {
      if (method === "DELETE") {
        authenticated = false;
        return response(undefined, 204);
      }
      return response(session);
    }
    if (method === "GET") {
      if (path === "/v1/organization") return response(org);
      if (path === "/v1/courses")
        return response({ items: [seed.course], course_runs: [run] });
      if (path === "/v1/course-runs") return response({ items: [run] });
      if (path === `/v1/course-runs/${run.id}/homeworks`)
        return response({ items: homeworks });
      if (path.startsWith("/v1/homeworks/"))
        return histories[path.split("/")[3]]
          ? response(histories[path.split("/")[3]])
          : error(404, "Задание не найдено.");
      if (path === `/v1/review-iterations/${review.review_iteration_id}`)
        return response(review);
      if (path === "/v1/review-queue/next")
        return response(
          review.status === "published"
            ? null
            : {
                review_case_id: seed.ids.reviewCase,
                review_case_revision: 0,
                submission_version_id: seed.ids.submissionVersion,
                reason: [
                  "Работа из выбранного потока",
                  "Оценка времени: 30 минут",
                ],
              },
        );
      if (path === `/v1/submissions/${submission.submission_id}`)
        return response(submission);
      if (path.startsWith("/v1/operations/"))
        return operations[path.split("/")[3]]
          ? response(operations[path.split("/")[3]])
          : error(404, "Операция не найдена.");
      if (path === "/v1/deliveries")
        return response({ items: review.deliveries });
      if (path === "/v1/organization/memberships")
        return response({
          items: [
            {
              id: session.membership_id,
              user_id: session.user_id,
              roles: session.roles,
              status: "active",
              revision: session.membership_revision,
              auth_epoch: session.auth_epoch,
            },
          ],
        });
      if (path === "/v1/invitations") return response({ items: invitations });
      if (path === `/v1/course-runs/${run.id}/memberships`)
        return response({
          items: [
            {
              user_id: session.user_id,
              kind: "reviewer",
              status: "active",
              source: "self_selected",
            },
          ],
        });
      return error(404, "Нет такого demo-ресурса.");
    }
    const body = String(init.body ?? "");
    const command = JSON.parse(body) as AnyCommand;
    const prior = receipts.get(command.idempotency_key);
    if (prior)
      return prior.body === body
        ? response(prior.result, prior.result === undefined ? 204 : 200)
        : error(409, "Ключ повтора уже использован.");
    if (
      command.revision_target === "review_iteration" &&
      command.expected_revision !== review.revision
    )
      return error(409, "Ревизия изменилась.");
    if (
      command.revision_target === "membership" &&
      command.expected_revision !== session.membership_revision
    )
      return error(409, "Роли изменились.");
    let result: unknown;
    switch (command.command_name) {
      case "open_review_iteration":
        result = {
          review_iteration_id: review.review_iteration_id,
          review_case_id: seed.ids.reviewCase,
          revision: review.revision,
        };
        break;
      case "save_review_revision": {
        if (["published", "canceled"].includes(review.status))
          return error(409, "Ревью закрыто.");
        review.revision++;
        review.criterion_decisions = command.payload.criterion_decisions;
        review.current_review_revision_id = crypto.randomUUID();
        review.current_review_revision = {
          id: review.current_review_revision_id,
          review_iteration_id: review.review_iteration_id,
          revision_number: review.revision,
          author_user_id: session.user_id,
          feedback: command.payload.feedback,
          total_score: command.payload.criterion_decisions.reduce(
            (s, d) => s + d.points,
            0,
          ),
          created_at: new Date().toISOString(),
        };
        review.review_notes = command.payload.review_notes.map((n, i) => ({
          ...n,
          id: crypto.randomUUID(),
          author_user_id: session.user_id,
          position: i,
        }));
        review.status = "ready_to_publish";
        result = {
          review_revision_id: review.current_review_revision_id,
          review_iteration_id: review.review_iteration_id,
          review_iteration_revision: review.revision,
        };
        break;
      }
      case "publish_review": {
        if (
          command.payload.review_revision_id !==
            review.current_review_revision_id ||
          !review.current_review_revision
        )
          return error(409, "Сохранённая версия не совпала.");
        review.status = "published";
        review.revision++;
        const publicationId = crypto.randomUUID();
        submission.review_revisions.push(review.current_review_revision);
        submission.publications.push({
          id: publicationId,
          review_iteration_id: review.review_iteration_id,
          review_revision_id: review.current_review_revision_id,
          published_by: session.user_id,
          published_at: new Date().toISOString(),
          total_score: review.current_review_revision.total_score,
        });
        submission.current_publication_id = publicationId;
        result = {
          id: publicationId,
          review_revision_id: review.current_review_revision_id,
          published_by: session.user_id,
          publication_request_id: null,
          status: "published",
          deliveries: [],
        };
        break;
      }
      case "record_review_responsibility":
        review.revision++;
        review.responsibility_events.push({
          id: crypto.randomUUID(),
          reviewer_id: session.user_id,
          actor_id: session.user_id,
          action: command.payload.action,
          occurred_at: new Date().toISOString(),
        });
        result = { id: review.review_iteration_id, revision: review.revision };
        break;
      case "start_ai_review": {
        const op = seed.operation();
        operations[op.id] = op;
        review.ai_review = {
          run_id: crypto.randomUUID(),
          input_fingerprint: `sha256:${"a".repeat(64)}`,
          contract_version: "1.1.0",
          state: "succeeded",
          attempts: [],
          suggestions: [
            {
              id: crypto.randomUUID(),
              criterion_id: seed.ids.criterion,
              status: "suggested",
              proposed_points: 8,
              reason:
                "Демонстрационное предложение: проверьте обработку неизвестной ссылки и тесты для ошибок.",
              evidence: [],
              confidence: "medium",
              reviewer_note: "Это локальный пример, модель не вызывалась.",
              student_feedback: null,
              flags: [],
            },
          ],
          signal: null,
          error: null,
        };
        result = op;
        break;
      }
      case "preflight_submission":
        result = {
          provider: command.payload.artifact_url.includes("docs.google.com")
            ? "google_docs"
            : "github",
          read_capability: "available",
          feedback_capability: "not_supported",
          submission_id: submission.submission_id,
          submission_revision: submissionRevision,
          artifact_reference_id: seed.ids.artifactRef,
          error: null,
        };
        break;
      case "submit_work": {
        if (command.expected_revision !== submissionRevision)
          return error(409, "Сдача изменилась.");
        submissionRevision++;
        const op = seed.operation("artifact_capture");
        operations[op.id] = op;
        const versionId = crypto.randomUUID();
        submission.current_submission_version_id = versionId;
        submission.versions.push({
          id: versionId,
          sequence: submission.versions.length + 1,
          homework_version_id: seed.ids.homeworkVersion,
          artifact_reference_id: seed.ids.artifactRef,
          artifact_version_id: seed.ids.artifact,
          capture_operation_id: op.id,
          submitted_at: new Date().toISOString(),
          effective_deadline: seed.homework.submission_deadline,
          phase: "before_deadline",
          status: "pending_review",
        });
        result = {
          submission_id: submission.submission_id,
          submission_revision: submissionRevision,
          submission_version_id: versionId,
          submission_version_revision: 0,
          capture_operation_id: op.id,
        };
        break;
      }
      case "create_invitation": {
        const item = {
          id: crypto.randomUUID(),
          normalized_email: command.payload.email.toLowerCase(),
          role: command.payload.role,
          status: "active" as const,
          expires_at: command.payload.expires_at,
          revision: 0,
        };
        invitations.push(item);
        result = { id: item.id, revision: 0 };
        break;
      }
      case "revoke_invitation": {
        const i = invitations.find((i) => i.id === command.target_id);
        if (!i) return error(404, "Приглашение не найдено.");
        i.status = "revoked";
        i.revision++;
        break;
      }
      case "change_membership_roles":
        session.roles = command.payload.roles;
        session.membership_revision++;
        result = {
          id: session.membership_id,
          revision: session.membership_revision,
        };
        break;
      case "set_reviewer_course_selection":
        session.membership_revision++;
        break;
      case "set_reviewer_availability":
        session.membership_revision++;
        result = {
          id: session.membership_id,
          revision: session.membership_revision,
        };
        break;
      case "create_homework": {
        const id = crypto.randomUUID();
        titles[id] = command.payload.title;
        histories[id] = {
          homework_id: id,
          homework_revision: 0,
          versions: [],
          course_run_publications: [],
        };
        result = { id, revision: 0 };
        break;
      }
      case "create_homework_version": {
        const h = histories[command.target_id];
        if (!h) return error(404, "Задание не найдено.");
        if (h.homework_revision !== command.expected_revision)
          return error(409, "Задание изменилось.");
        const v: Model<"HomeworkVersionSummary"> = {
          ...command.payload,
          id: crypto.randomUUID(),
          revision: 0,
          version_number: h.versions.length + 1,
          criterion_set_id: crypto.randomUUID(),
          criteria: command.payload.criteria.map((c, i) => ({
            ...c,
            id: crypto.randomUUID(),
            position: i,
          })),
        };
        h.versions.push(v);
        h.homework_revision++;
        result = { id: v.id, revision: 0 };
        break;
      }
      case "publish_homework_version": {
        const h = Object.values(histories).find((h) =>
          h.versions.some((v) => v.id === command.target_id),
        );
        if (!h) return error(404, "Версия не найдена.");
        const v = h.versions.find((v) => v.id === command.target_id)!;
        const existing = homeworks.find((w) => w.homework_id === h.homework_id);
        const publicationId =
          existing?.course_run_homework_id ?? crypto.randomUUID();
        h.course_run_publications.forEach((p) => {
          p.is_current = false;
        });
        h.course_run_publications.push({
          id: crypto.randomUUID(),
          course_run_homework_id: publicationId,
          course_run_id: run.id,
          homework_version_id: v.id,
          publication_sequence: h.course_run_publications.length + 1,
          submission_deadline: command.payload.submission_deadline,
          review_deadline: command.payload.review_deadline,
          published_at: new Date().toISOString(),
          is_current: true,
        });
        const summary = {
          course_run_homework_id: publicationId,
          revision: (existing?.revision ?? -1) + 1,
          homework_id: h.homework_id,
          current_version_id: v.id,
          title: titles[h.homework_id],
          max_score: v.max_score,
          artifact_kinds: v.artifact_kinds,
          submission_deadline: command.payload.submission_deadline,
          review_deadline: command.payload.review_deadline,
        };
        if (existing) Object.assign(existing, summary);
        else homeworks.push(summary);
        result = { id: publicationId, revision: summary.revision };
        break;
      }
      case "start_course_import":
        return error(
          501,
          "Импорт требует backend с настроенным адаптером. В демо доступны готовые курсы.",
        );
      default:
        return error(501, "Это действие не реализовано в demo-адаптере.");
    }
    receipts.set(command.idempotency_key, { body, result });
    return response(result, result === undefined ? 204 : 200);
  };
  return {
    transport,
    seed,
    session,
    org,
    run,
    review,
    submission,
    homeworks,
    histories,
    operations,
    invitations,
    titles,
  };
}
export function createDemoTransport(): Transport {
  return enhanceWorkspace(createDemoCore(), createDemoCore(secondFixture()));
}
