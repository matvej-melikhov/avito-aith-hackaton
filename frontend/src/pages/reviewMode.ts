import type { WorkspaceClient, W } from "../api/workspace";

export async function openQueueWork(
  ws: WorkspaceClient,
  work: W<"WorkItem">,
  userId: string,
) {
  if (!work.submission_id || !work.submission_version_id)
    throw new Error("У работы пока нет сдачи для проверки.");
  const closed = ["published", "passed", "failed", "needs_changes"].includes(
    work.status,
  );
  let id = work.review_iteration_id;
  if (!id || work.review_submission_version_id !== work.submission_version_id)
    id = (
      await ws.command(
        "open_work",
        work.submission_id,
        work.submission_revision,
        { submission_version_id: work.submission_version_id },
      )
    ).id;
  if (!closed) {
    const detail = await ws.core.review(id);
    const latest = detail.responsibility_events
      .filter((e) => e.reviewer_id === userId)
      .at(-1);
    if (!latest || !["started", "joined"].includes(latest.action))
      await ws.core.command(
        "record_review_responsibility",
        id,
        detail.revision,
        { action: detail.responsibility_events.length ? "joined" : "started" },
      );
  }
  return id;
}

/** Traverse the eligible pool in the server's order; do not rank or widen it. */
export async function nextPoolWork(
  ws: WorkspaceClient,
  currentReviewId: string,
  currentSubmissionId?: string | null,
) {
  let offset = 0;
  for (;;) {
    const page = await ws.works({ view: "pool", limit: 20, offset });
    const next = page.items.find(
      (w) =>
        w.submission_id &&
        w.submission_version_id &&
        w.review_iteration_id !== currentReviewId &&
        w.submission_id !== currentSubmissionId,
    );
    if (next) return next;
    offset += page.items.length;
    if (!page.items.length || offset >= page.total) return null;
  }
}
