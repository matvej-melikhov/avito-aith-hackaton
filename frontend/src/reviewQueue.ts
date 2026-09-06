// Явный переход к следующей работе по нажатию кнопки.
import { WorkspaceClient, type W } from "./api/workspace";

const CLOSED = ["published", "passed", "failed", "needs_changes", "canceled"];

function deadlineOf(w: W<"WorkItem">) {
  const at = Date.parse(
    w.submission_deadline ?? w.review_deadline ?? w.submitted_at,
  );
  return Number.isFinite(at) ? at : Number.MAX_SAFE_INTEGER;
}

/** Кандидаты из пула: сначала свободные, потом остальные незакрытые. */
export async function poolQueue(ws: WorkspaceClient, skip: string[] = []) {
  const list = await ws.works({ view: "all", limit: 50 });
  return list.items
    .filter(
      (w) =>
        !!w.submission_version_id &&
        !CLOSED.includes(w.status) &&
        !skip.includes(w.submission_id),
    )
    .sort(
      (a, b) =>
        Number(b.status === "pending_review") -
          Number(a.status === "pending_review") ||
        deadlineOf(a) - deadlineOf(b) ||
        a.submitted_at.localeCompare(b.submitted_at),
    );
}

/** Берёт следующую работу из пула и возвращает id её ревью, или null. */
export async function nextFromPool(ws: WorkspaceClient, skip: string[] = []) {
  const [w] = await poolQueue(ws, skip);
  if (!w) return null;
  if (
    w.review_iteration_id &&
    w.review_submission_version_id === w.submission_version_id
  ) {
    await ws.core.command(
      "record_review_responsibility",
      w.review_iteration_id,
      w.review_revision,
      { action: "joined" },
    );
    return w.review_iteration_id;
  }
  const result = await ws.command(
    "open_work",
    w.submission_id,
    w.submission_revision,
    { submission_version_id: w.submission_version_id! },
  );
  const opened = await ws.core.review(result.id);
  await ws.core.command(
    "record_review_responsibility",
    result.id,
    opened.revision,
    { action: "started" },
  );
  return result.id;
}
