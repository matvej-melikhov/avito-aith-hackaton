import { useEffect, useState, type ReactNode } from "react";
import { WorkspaceClient, type W } from "../api/workspace";
import { useMenuCounts } from "../App";
import {
  Btn,
  BtnRow,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Empty,
  Inp,
  Main,
  Pill,
  Sel,
  Srch,
  St,
  Topbar,
  cx,
  dayShort,
  days,
  daysSince,
  isClosed,
} from "../ds";
import { Resource, go, useAction, useResource } from "../ui";
import { enterReviewMode, exitReviewMode, nextFromPool } from "../reviewMode";

const LIMIT = 20;
const CLOSED = ["published", "passed", "failed", "needs_changes"];
const HOT_POOL_DAYS = 3;

export function ReviewerQueue({ ws }: { ws: WorkspaceClient }) {
  const [query, setQuery] = useState("");
  const [run, setRun] = useState("");
  const catalog = useResource(() => ws.catalog(), "reviewer-catalog");
  const action = useAction();
  const startMode = () =>
    void action.run(async () => {
      enterReviewMode();
      const id = await nextFromPool(ws);
      if (!id) {
        exitReviewMode();
        throw new Error("В пуле нет свободных работ по вашим курсам.");
      }
      go(`/reviews/${id}`);
    });
  return (
    <>
      <Topbar
        title="Мои работы"
        actions={
          <>
            <Btn href="#/preferences" size="s" variant="quiet">
              Настройки
            </Btn>
            <Btn
              size="s"
              variant="pri"
              disabled={action.busy}
              title="Работы из пула будут открываться одна за другой, ближайший дедлайн первым"
              onClick={startMode}
            >
              Войти в режим проверки
            </Btn>
          </>
        }
      />
      <Main data-screen="Р2">
        {action.feedback}
        <div className="stack">
          <QueueSection
            ws={ws}
            view="active"
            title="Активные"
            onStartMode={startMode}
            busy={action.busy}
          />
          <QueueSection
            ws={ws}
            id="pool"
            view="all"
            title="Пул"
            filters={
              <BtnRow>
                <Srch>
                  <Inp
                    small
                    aria-label="Поиск"
                    placeholder="Студент или задание"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </Srch>
                <Sel
                  small
                  aria-label="Поток"
                  value={run}
                  onChange={(e) => setRun(e.target.value)}
                >
                  <option value="">Поток: все</option>
                  {catalog.data?.course_runs.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.title}
                    </option>
                  ))}
                </Sel>
              </BtnRow>
            }
            query={query}
            run={run}
          />
        </div>
      </Main>
    </>
  );
}

function QueueSection({
  ws,
  id,
  view,
  title,
  query = "",
  run = "",
  filters,
  onStartMode,
  busy,
}: {
  ws: WorkspaceClient;
  id?: string;
  view: "active" | "all";
  title: string;
  query?: string;
  run?: string;
  filters?: ReactNode;
  onStartMode?: () => void;
  busy?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [query, run]);
  const params = {
    view,
    q: query,
    course_run_id: run || undefined,
    offset,
    limit: LIMIT,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const action = useAction();
  const { setCounts } = useMenuCounts();
  useEffect(() => {
    if (r.data && !query && !run)
      setCounts(
        view === "active" ? { works: r.data.total } : { pool: r.data.total },
      );
  }, [r.data, query, run, view, setCounts]);

  async function open(w: W<"WorkItem">) {
    if (
      w.review_iteration_id &&
      w.review_submission_version_id === w.submission_version_id
    ) {
      if (view !== "active" && !CLOSED.includes(w.status))
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

  const active = view === "active";
  const items = r.data?.items ?? [];
  return (
    <Card id={id}>
      <CardHead title={title}>{filters}</CardHead>
      {!!action.error && <CardBody>{action.feedback}</CardBody>}
      <Resource value={r}>
        {r.data && (
          <>
            <CardBody flush>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Студент</th>
                    <th>Задание</th>
                    <th>{active ? "Статус" : "Курс"}</th>
                    <th className="n">{active ? "Взята" : "Сдана"}</th>
                    <th className="n">{active ? "Проверить до" : "В пуле"}</th>
                    <th className="r"></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((w) => {
                    const closed = isClosed(w.status);
                    const waited = daysSince(w.submitted_at) ?? 0;
                    const due = w.review_deadline
                      ? Date.parse(w.review_deadline) - Date.now() < 86_400_000
                      : false;
                    const hot = active
                      ? due && !closed
                      : waited >= HOT_POOL_DAYS &&
                        w.status === "pending_review";
                    const canRelease =
                      active &&
                      !!w.review_iteration_id &&
                      !CLOSED.includes(w.status);
                    const label =
                      active || CLOSED.includes(w.status)
                        ? "Открыть"
                        : w.status === "pending_review"
                          ? "Взять"
                          : "Подключиться";
                    return (
                      <tr
                        key={w.submission_id}
                        className={cx(
                          "is-link",
                          hot && "is-hot",
                          closed && "is-done",
                        )}
                        onClick={(e) => {
                          if ((e.target as HTMLElement).closest("button, a"))
                            return;
                          if (!action.busy && w.submission_version_id)
                            void action.run(() => open(w));
                        }}
                      >
                        <td className="mono">{w.student_name}</td>
                        <td>
                          <div className="who">{w.title}</div>
                          <div className="sub">
                            {w.status === "needs_changes"
                              ? "ждём студента"
                              : w.attempt > 1 && !closed
                                ? `попытка ${w.attempt}, правки пришли`
                                : `попытка ${w.attempt}`}
                            {w.participant_ids && w.participant_ids.length > 1
                              ? ` · вместе с ${w.participant_ids.length - 1}`
                              : ""}
                          </div>
                        </td>
                        {active ? (
                          <>
                            <td>
                              <St status={w.status} attempt={w.attempt} />
                            </td>
                            <td className="n">{dayShort(w.taken_at)}</td>
                            <td className={cx("n", due && !closed && "late")}>
                              {closed ? "—" : dayShort(w.review_deadline)}
                            </td>
                          </>
                        ) : (
                          <>
                            <td>{w.course_title}</td>
                            <td className="n">{dayShort(w.submitted_at)}</td>
                            <td className="n">
                              {hot ? (
                                <Pill tone="late">{days(waited)}</Pill>
                              ) : (
                                days(waited)
                              )}
                            </td>
                          </>
                        )}
                        <td className="r">
                          <BtnRow end>
                            <Btn
                              size="s"
                              variant={closed ? "quiet" : undefined}
                              disabled={action.busy || !w.submission_version_id}
                              onClick={() => void action.run(() => open(w))}
                            >
                              {label}
                            </Btn>
                            {canRelease && (
                              <Btn
                                size="s"
                                variant="quiet"
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
                              </Btn>
                            )}
                          </BtnRow>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {!items.length && (
                <Empty
                  title={active ? "Активных проверок нет" : "В пуле пусто"}
                  action={
                    active &&
                    !query && (
                      <Btn
                        size="s"
                        variant="dark"
                        disabled={busy}
                        onClick={onStartMode}
                      >
                        Войти в режим проверки
                      </Btn>
                    )
                  }
                >
                  {active
                    ? "У вас пока нет активных проверок."
                    : "По этим условиям работ нет."}
                </Empty>
              )}
            </CardBody>
            {r.data.total > LIMIT && (
              <CardFoot>
                <span>
                  Показаны {offset + 1}–{Math.min(offset + LIMIT, r.data.total)}{" "}
                  из {r.data.total}
                </span>
                <BtnRow>
                  <Btn
                    size="s"
                    variant="quiet"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                  >
                    Назад
                  </Btn>
                  <Btn
                    size="s"
                    variant="link"
                    disabled={offset + LIMIT >= r.data.total}
                    onClick={() => setOffset(offset + LIMIT)}
                  >
                    Показать ещё
                  </Btn>
                </BtnRow>
              </CardFoot>
            )}
          </>
        )}
      </Resource>
    </Card>
  );
}
