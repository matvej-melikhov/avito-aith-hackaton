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
  Main,
  Pill,
  Sel,
  St,
  Topbar,
  cx,
  dayShort,
  days,
  daysSince,
  isClosed,
} from "../ds";
import { Resource, go, useAction, useResource } from "../ui";
import {
  nextFromPool,
  openQueueWork,
  personalLabel,
  RELEASED_NOTICE,
} from "../reviewQueue";
import { ActionMessage, useActionMessage } from "../ActionMessage";

const LIMIT = 20;
const CLOSED = ["published", "passed", "failed", "needs_changes"];
const HOT_POOL_DAYS = 3;

export function ReviewerQueue({
  ws,
  pool = false,
}: {
  ws: WorkspaceClient;
  pool?: boolean;
}) {
  const [course, setCourse] = useState("");
  const [run, setRun] = useState("");
  const catalog = useResource(() => ws.catalog(), "reviewer-catalog");
  const action = useAction();
  /* Курс сужает список потоков. Работы отбираются по потоку, а когда выбран
     только курс — по всем его потокам. */
  const runs = (catalog.data?.course_runs ?? []).filter(
    (r) => !course || r.course_id === course,
  );
  const courseRunIds = course ? runs.map((r) => r.id) : null;
  const filters = (
    <BtnRow>
      <Sel
        small
        aria-label="Курс"
        value={course}
        onChange={(e) => {
          setCourse(e.target.value);
          setRun("");
        }}
      >
        <option value="">Курс: все</option>
        {catalog.data?.courses.map((c) => (
          <option key={c.id} value={c.id}>
            {c.title}
          </option>
        ))}
      </Sel>
      <Sel
        small
        aria-label="Поток"
        value={run}
        onChange={(e) => setRun(e.target.value)}
      >
        <option value="">Поток: все</option>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.title}
          </option>
        ))}
      </Sel>
    </BtnRow>
  );
  const message = useActionMessage();
  const openNext = () =>
    void action.run(async () => {
      message.clear();
      const id = await nextFromPool(ws);
      if (!id) {
        throw new Error("В пуле нет свободных работ по вашим курсам.");
      }
      go(`/reviews/${id}`);
    });
  return (
    <>
      <Topbar
        title={pool ? "Пул" : "Мои работы"}
        actions={
          <>
            <Btn href="#/preferences" size="s" variant="quiet">
              Настройки
            </Btn>
            <Btn
              size="s"
              variant="pri"
              disabled={action.busy}
              title="Открыть следующую подходящую работу из пула"
              onClick={openNext}
            >
              Открыть работу из пула
            </Btn>
          </>
        }
      />
      <Main data-screen="Р2">
        <ActionMessage />
        {action.feedback}
        <div className="stack">
          {pool ? (
            <QueueSection
              ws={ws}
              id="pool"
              view="pool"
              title=""
              filters={filters}
              run={run}
              courseRunIds={courseRunIds}
            />
          ) : (
            <QueueSection
              ws={ws}
              view="active"
              title="Активные"
              filters={filters}
              run={run}
              courseRunIds={courseRunIds}
              onNextWork={openNext}
              busy={action.busy}
            />
          )}
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
  run = "",
  courseRunIds = null,
  filters,
  onNextWork,
  busy,
}: {
  ws: WorkspaceClient;
  id?: string;
  view: "active" | "pool";
  title: string;
  run?: string;
  /** Потоки выбранного курса, когда сам поток не выбран. */
  courseRunIds?: string[] | null;
  filters?: ReactNode;
  onNextWork?: () => void;
  busy?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  useEffect(() => setOffset(0), [run, courseRunIds?.join(",")]);
  const params = {
    view,
    course_run_id: run || undefined,
    offset,
    limit: LIMIT,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const action = useAction();
  const { setCounts } = useMenuCounts();
  const message = useActionMessage();
  useEffect(() => {
    if (r.data && !run && !courseRunIds)
      setCounts(
        view === "active" ? { works: r.data.total } : { pool: r.data.total },
      );
  }, [r.data, run, courseRunIds, view, setCounts]);

  async function open(w: W<"WorkItem">) {
    message.clear();
    go(`/reviews/${await openQueueWork(ws, w)}`);
  }
  const session = useResource(() => ws.core.session(), "queue-session");

  const active = view === "active";
  const all = r.data?.items ?? [];
  const items =
    run || !courseRunIds
      ? all
      : all.filter((w) => courseRunIds.includes(w.course_run_id));
  return (
    <Card id={id}>
      <CardHead title={title || undefined}>{filters}</CardHead>
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
                    const take = !active && !CLOSED.includes(w.status);
                    const personal = session.data
                      ? personalLabel(w, session.data.user_id)
                      : null;
                    const label =
                      w.status === "pending_review"
                        ? "Начать проверку"
                        : personal
                          ? "Открыть"
                          : take
                            ? "Взять"
                            : "Открыть";
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
                          {personal && <div className="sub">{personal}</div>}
                          {(w.status === "needs_changes" ||
                            (w.attempt > 1 && !closed) ||
                            (w.participant_ids?.length ?? 0) > 1) && (
                            <div className="sub">
                              {[
                                w.status === "needs_changes"
                                  ? "ждём студента"
                                  : w.attempt > 1 && !closed
                                    ? "правки пришли"
                                    : "",
                                (w.participant_ids?.length ?? 0) > 1
                                  ? `вместе с ${w.participant_ids!.length - 1}`
                                  : "",
                              ]
                                .filter(Boolean)
                                .join(", ")}
                            </div>
                          )}
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
                              className={take ? "btn--take" : undefined}
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
                                    message.show(RELEASED_NOTICE);
                                  })
                                }
                              >
                                Снять с себя проверку
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
                  title={active ? "Работ к проверке пока нет" : "В пуле пусто"}
                  action={
                    active && (
                      <Btn size="s" variant="dark" href="#/pool">
                        Перейти в пул работ
                      </Btn>
                    )
                  }
                >
                  {active
                    ? "Можно выбрать работу из общего пула."
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
