import { useEffect, useState, type ReactNode } from "react";
import { WorkspaceClient, type W } from "../api/workspace";
import {
  Card,
  Empty,
  Resource,
  Status,
  date,
  go,
  useAction,
  useResource,
} from "../ui";
import { ScreenTitle } from "../workspace-ui";

export function ReviewerQueue({ ws }: { ws: WorkspaceClient }) {
  const [query, setQuery] = useState("");
  const [run, setRun] = useState("");
  const catalog = useResource(() => ws.catalog(), "reviewer-catalog");
  return (
    <>
      <ScreenTitle code="Р2" title="Мои работы">
        <a className="button" href="#/preferences">
          Настройки
        </a>
      </ScreenTitle>
      <div className="stack">
        <QueueSection ws={ws} view="active" title="Активные" />
        <section id="pool">
          <QueueSection
            ws={ws}
            view="all"
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
                  <select value={run} onChange={(e) => setRun(e.target.value)}>
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
  async function open(w: W<"WorkItem">) {
    if (
      w.review_iteration_id &&
      w.review_submission_version_id === w.submission_version_id
    ) {
      if (
        view !== "active" &&
        !["published", "passed", "failed", "needs_changes"].includes(w.status)
      )
        await ws.core.command(
          "record_review_responsibility",
          w.review_iteration_id,
          w.review_revision,
          { action: "joined" },
        );
      go(`/reviews/${w.review_iteration_id}`);
      return;
    }
    const result = await ws.command(
      "open_work",
      w.submission_id,
      w.submission_revision,
      { submission_version_id: w.submission_version_id! },
    );
    if (view !== "active") {
      const opened = await ws.core.review(result.id);
      await ws.core.command(
        "record_review_responsibility",
        result.id,
        opened.revision,
        { action: "started" },
      );
    }
    go(`/reviews/${result.id}`);
  }
  return (
    <Card title={title} actions={filters}>
      {action.feedback}
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
                            <Status value={w.status} />
                            {w.participant_ids?.length
                              ? ` · участников: ${w.participant_ids.length}`
                              : ""}
                          </small>
                        )}
                      </td>
                      {view === "active" ? (
                        <>
                          <td>
                            <Status value={w.status} />
                            {!!w.participant_ids?.length && (
                              <small>
                                Участников: {w.participant_ids.length}
                              </small>
                            )}
                          </td>
                          <td>{w.taken_at ? date(w.taken_at) : "—"}</td>
                        </>
                      ) : (
                        <>
                          <td>{w.course_title}</td>
                          <td>{date(w.submitted_at)}</td>
                        </>
                      )}
                      <td>
                        {view === "active"
                          ? w.review_deadline
                            ? date(w.review_deadline)
                            : "—"
                          : `${Math.max(0, Math.floor((Date.now() - Date.parse(w.submitted_at)) / 86400000))} дн.`}
                      </td>
                      <td>
                        <div className="actions">
                          <button
                            disabled={action.busy || !w.submission_version_id}
                            onClick={() => void action.run(() => open(w))}
                          >
                            {view === "active" ||
                            [
                              "published",
                              "passed",
                              "failed",
                              "needs_changes",
                            ].includes(w.status)
                              ? "Открыть"
                              : w.status === "pending_review"
                                ? "Взять"
                                : "Подключиться"}
                          </button>
                          {view === "active" &&
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
            {view === "all" && (
              <p className="muted">
                Можно открыть любую доступную работу и подключиться к коллегам.
              </p>
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
