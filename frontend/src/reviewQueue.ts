// Явный переход к следующей работе по нажатию кнопки.
import { WorkspaceClient, type W } from "./api/workspace";

export const READY = ["pending_review", "in_review", "ready_to_publish"];

export function personalLabel(w: W<"WorkItem">, userId: string) {
  if (!READY.includes(w.status)) return null;
  if (
    (w.participant_ids ?? []).includes(userId) ||
    (!(w.participant_ids ?? []).length && w.responsible_reviewer_id === userId)
  )
    return "Проверяю";
  return w.primary_reviewer_id === userId ? "Нужно проверить" : null;
}

async function queue(
  ws: WorkspaceClient,
  view: "active" | "pool",
  skip: string[],
) {
  const items: W<"WorkItem">[] = [];
  let offset = 0;
  while (true) {
    const page = await ws.works({ view, offset, limit: 100 });
    items.push(
      ...page.items.filter(
        (w) =>
          !!w.submission_version_id &&
          !!w.submission_id &&
          READY.includes(w.status) &&
          !skip.includes(w.submission_id),
      ),
    );
    offset += page.items.length;
    if (!page.items.length || offset >= page.total) return items;
  }
}

/* Снятие участия не освобождает работу автоматически: в ней могут
   участвовать другие ревьюеры. */
export const RELEASED_NOTICE =
  "Вы сняли с себя проверку. Если других ревьюеров нет, работа вернётся в пул.";

function deadlineOf(w: W<"WorkItem">) {
  const at = Date.parse(
    w.submission_deadline ?? w.review_deadline ?? w.submitted_at ?? "",
  );
  return Number.isFinite(at) ? at : Number.MAX_SAFE_INTEGER;
}

/** Кандидаты из пула: сначала свободные, потом остальные незакрытые. */
export async function poolQueue(ws: WorkspaceClient, skip: string[] = []) {
  return (await queue(ws, "pool", skip)).sort(
    (a, b) =>
      Number(b.status === "pending_review") -
        Number(a.status === "pending_review") ||
      deadlineOf(a) - deadlineOf(b) ||
      (a.submitted_at ?? "").localeCompare(b.submitted_at ?? ""),
  );
}

/** Берёт следующую работу из пула и возвращает id её ревью, или null. */
export async function nextFromPool(ws: WorkspaceClient, skip: string[] = []) {
  const [w] = await poolQueue(ws, skip);
  return w ? openQueueWork(ws, w) : null;
}

export async function nextFromMine(ws: WorkspaceClient, skip: string[] = []) {
  const [w] = await queue(ws, "active", skip);
  return w ? openQueueWork(ws, w) : null;
}

export async function openQueueWork(ws: WorkspaceClient, w: W<"WorkItem">) {
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
    w.submission_id!,
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
