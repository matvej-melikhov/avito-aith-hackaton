import { useEffect, useState, type ReactNode } from "react";
import { WorkspaceClient, type W } from "../api/workspace";
import {
  Card,
  Empty,
  ErrorBox,
  Resource,
  Status,
  go,
  useAction,
  useResource,
} from "../ui";
import { ScreenTitle } from "../workspace-ui";

export function ReviewerQueue({
  ws,
  mode = "active",
}: {
  ws: WorkspaceClient;
  mode?: "active" | "pool";
}) {
  const [query, setQuery] = useState("");
  const [run, setRun] = useState("");
  const catalog = useResource(() => ws.catalog(), "reviewer-catalog");
  return (
    <>
      <ScreenTitle code="Р2" title={mode === "active" ? "Мои работы" : "Пул"}>
        <a className="button" href="#/preferences">
          Настройки
        </a>
      </ScreenTitle>
      <div className="stack">
        {mode === "active" ? (
          <QueueSection ws={ws} view="active" title="Мои работы" />
        ) : (
          <section id="pool">
            <QueueSection
              ws={ws}
              view="pool"
              title="Пул"
              filters={
                <div className="filters">
                  <label>
                    Поиск
                    <input
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Студент или задание"
                    />
                  </label>
                  <label>
                    Поток
                    <select
                      value={run}
                      onChange={(e) => setRun(e.target.value)}
                    >
                      <option value="">Все потоки</option>
                      {catalog.data?.course_runs.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.title}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              }
              query={query}
              run={run}
            />
          </section>
        )}
      </div>
    </>
  );
}
function QueueSection({
  ws,
  view,
  title,
  query = "",
  run = "",
  filters,
}: {
  ws: WorkspaceClient;
  view: string;
  title: string;
  query?: string;
  run?: string;
  filters?: ReactNode;
}) {
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [query, run]);
  const params = {
    view,
    q: query,
    course_run_id: run || undefined,
    offset,
    limit: 20,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const action = useAction();
  const session = useResource(() => ws.core.session(), "reviewer-actor");
  async function open(w: W<"WorkItem">) {
    if (!w.submission_id || !w.submission_version_id) return;
    const closed = ["published", "passed", "failed", "needs_changes"].includes(
      w.status,
    );
    let reviewId = w.review_iteration_id;
    if (
      !reviewId ||
      w.review_submission_version_id !== w.submission_version_id
    ) {
      const result = await ws.command(
        "open_work",
        w.submission_id,
        w.submission_revision,
        { submission_version_id: w.submission_version_id! },
      );
      reviewId = result.id;
    }
    if (!closed) {
      const detail = await ws.core.review(reviewId);
      const events = detail.responsibility_events.filter(
        (e) => e.reviewer_id === session.data?.user_id,
      );
      const latest = events.at(-1);
      if (!latest || !["started", "joined"].includes(latest.action))
        await ws.core.command(
          "record_review_responsibility",
          reviewId,
          detail.revision,
          {
            action: detail.responsibility_events.length ? "joined" : "started",
          },
        );
    }
    go(`/reviews/${reviewId}`);
  }
  return (
    <Card title={title} actions={filters}>
      {action.feedback}
      {!!session.error && (
        <ErrorBox error={session.error} retry={session.refresh} />
      )}
      <Resource value={r}>
        {r.data && (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Студент</th>
                    <th>Задание</th>
                    <th>{view === "active" ? "Статус" : "Курс"}</th>
                    <th>{view === "active" ? "Взята" : "Сдана"}</th>
                    <th>{view === "active" ? "Проверить до" : "В пуле"}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((w) => (
                    <tr key={w.submission_id}>
                      <td>{w.student_name}</td>
                      <td>
                        <strong>{w.title}</strong>
                        <small>
                          {w.course_run_title} · попытка {w.attempt}
                        </small>
                        {view !== "active" && (
                          <small>
                            <Status value={w.status} attempt={w.attempt} />
                            {w.participant_ids?.length
                              ? ` · участников: ${w.participant_ids.length}`
                              : ""}
                          </small>
                        )}
                      </td>
                      {view === "active" ? (
                        <>
                          <td>
                            <Status value={w.status} attempt={w.attempt} />
                            {!!w.participant_ids?.length && (
                              <small>
                                Участников: {w.participant_ids.length}
                              </small>
                            )}
                          </td>
                          <td>{w.taken_at ? queueDate(w.taken_at) : "—"}</td>
                        </>
                      ) : (
                        <>
                          <td>{w.course_title}</td>
                          <td>
                            {w.submitted_at ? queueDate(w.submitted_at) : "—"}
                          </td>
                        </>
                      )}
                      <td>
                        {view === "active"
                          ? w.review_deadline
                            ? queueDate(w.review_deadline)
                            : "—"
                          : w.submitted_at
                            ? `${Math.max(0, Math.floor((Date.now() - Date.parse(w.submitted_at)) / 86400000))} дн.`
                            : "—"}
                      </td>
                      <td>
                        <div className="actions">
                          <button
                            disabled={
                              action.busy ||
                              session.loading ||
                              !session.data ||
                              !w.submission_version_id
                            }
                            onClick={() => void action.run(() => open(w))}
                          >
                            {[
                              "published",
                              "passed",
                              "failed",
                              "needs_changes",
                            ].includes(w.status)
                              ? "Посмотреть"
                              : w.participant_ids?.includes(
                                    session.data?.user_id ?? "",
                                  )
                                ? "Продолжить"
                                : "Начать проверку"}
                          </button>
                          {view === "active" &&
                            w.participant_ids?.includes(
                              session.data?.user_id ?? "",
                            ) &&
                            w.review_iteration_id &&
                            ![
                              "published",
                              "passed",
                              "failed",
                              "needs_changes",
                            ].includes(w.status) && (
                              <button
                                disabled={action.busy}
                                onClick={() =>
                                  void action.run(async () => {
                                    await ws.core.command(
                                      "record_review_responsibility",
                                      w.review_iteration_id!,
                                      w.review_revision,
                                      { action: "released" },
                                    );
                                    r.refresh();
                                  })
                                }
                              >
                                Вернуть в пул
                              </button>
                            )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!r.data.items.length && (
              <Empty>
                {view === "active"
                  ? "У вас пока нет активных проверок."
                  : "По этим условиям работ нет."}
              </Empty>
            )}
            {r.data.total > 20 && (
              <div className="pagination">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 20))}
                >
                  Назад
                </button>
                <span>
                  {offset + 1}–{Math.min(offset + 20, r.data.total)} из{" "}
                  {r.data.total}
                </span>
                <button
                  disabled={offset + 20 >= r.data.total}
                  onClick={() => setOffset(offset + 20)}
                >
                  Дальше
                </button>
              </div>
            )}
          </>
        )}
      </Resource>
    </Card>
  );
}

function queueDate(value: string) {
  return new Date(value).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
  });
}
