import { StudentWorks } from "./StudentWorks";
import { ReviewerQueue } from "./ReviewerQueue";
import { useCallback, useState, type ReactNode } from "react";
import type { Role } from "../api/client";
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
import { ExportMonitor, Modal, ScreenTitle } from "../workspace-ui";
export function WorkspaceWorks(props: {
  ws: WorkspaceClient;
  role: Role;
  coordinatorPool?: boolean;
}) {
  return props.role === "student" ? (
    <StudentWorks ws={props.ws} />
  ) : props.role === "reviewer" ? (
    <ReviewerQueue ws={props.ws} />
  ) : (
    <WorksList {...props} />
  );
}
function WorksList({
  ws,
  role,
  coordinatorPool = false,
}: {
  ws: WorkspaceClient;
  role: Role;
  coordinatorPool?: boolean;
}) {
  const [query, setQuery] = useState(
    new URLSearchParams(window.location.hash.split("?")[1]).get("q") ?? "",
  );
  const [search, setSearch] = useState("");
  const [run, setRun] = useState(
    new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("run") ??
      "",
  );
  const [state, setState] = useState(
    new URLSearchParams(window.location.hash.split("?")[1] ?? "").get(
      "state",
    ) ?? "",
  );
  const [view, setView] = useState("all");
  const [priority, setPriority] = useState("assigned");
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const params = {
    q: search,
    course_run_id: run || undefined,
    state,
    view,
    priority,
    offset,
    limit: 20,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const catalog = useResource(() => ws.catalog(), "catalog");
  const action = useAction();
  const close = useCallback(() => setExporting(false), []);
  const code =
    role === "student"
      ? "С4"
      : role === "reviewer"
        ? "Р2"
        : coordinatorPool
          ? "К6"
          : "К7";
  const title =
    role === "student"
      ? "Мои домашки"
      : role === "reviewer"
        ? "Мои работы и общий пул"
        : coordinatorPool
          ? "Пул проверок"
          : "Реестр домашних работ";
  return (
    <>
      <ScreenTitle code={code} title={title}>
        {role === "methodologist" && (
          <button disabled={!run} onClick={() => setExporting(true)}>
            Выгрузить
          </button>
        )}
      </ScreenTitle>
      {action.feedback}
      {role === "student" && (
        <div className="tabs">
          {[
            ["", "Все"],
            ["in_progress", "В работе"],
            ["completed", "Завершённые"],
          ].map(([value, label]) => (
            <button
              key={value}
              aria-pressed={state === value}
              onClick={() => {
                setState(value);
                setOffset(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {role === "reviewer" && (
        <div className="tabs">
          {[
            ["assigned", "Мои студенты"],
            ["active", "В работе"],
            ["all", "Все работы"],
          ].map(([value, label]) => (
            <button
              key={value}
              aria-pressed={view === value}
              onClick={() => {
                setView(value);
                setOffset(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {role !== "student" && (
        <form
          className="filters"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(query);
            setOffset(0);
          }}
        >
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
              onChange={(e) => {
                setRun(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Все потоки</option>
              {catalog.data?.course_runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Статус
            <select
              value={state}
              onChange={(e) => {
                setState(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Все статусы</option>
              {[
                "draft",
                "pending_review",
                "in_review",
                "ready_to_publish",
                "needs_changes",
                "passed",
                "failed",
                "published",
              ].map((s) => (
                <option key={s} value={s}>
                  {
                    {
                      draft: "Черновик",
                      pending_review: "Ожидает проверки",
                      in_review: "На ревью",
                      ready_to_publish: "Готово к публикации",
                      needs_changes: "Нужны правки",
                      passed: "Зачтена",
                      failed: "Не зачтена",
                      published: "Опубликована",
                    }[s]
                  }
                </option>
              ))}
            </select>
          </label>
          {
            <label>
              Приоритет
              <select
                value={priority}
                onChange={(e) => {
                  setPriority(e.target.value);
                  setOffset(0);
                }}
              >
                <option value="assigned">Свои студенты сначала</option>
                <option value="deadline">Дедлайн сначала</option>
              </select>
            </label>
          }
          <button>Найти</button>
        </form>
      )}
      <Resource value={r}>
        {r.data && (
          <ListFrame
            student={role === "student"}
            title={
              role === "student"
                ? "Домашние работы"
                : `Работы · ${r.data.total}`
            }
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {role !== "student" && <th>Студент</th>}
                    <th>Задание</th>
                    {role === "student" ? (
                      <>
                        <th>Курс</th>
                        <th>Дедлайн</th>
                        <th>Попытка</th>
                        <th>Балл</th>
                        <th>Статус</th>
                      </>
                    ) : (
                      <>
                        <th>Статус</th>
                        <th>Срок проверки</th>
                        <th>Оценка</th>
                      </>
                    )}
                    {role !== "student" && <th></th>}
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((w) => (
                    <tr key={w.submission_id}>
                      {role !== "student" && (
                        <td>
                          <strong>{w.student_name}</strong>
                        </td>
                      )}
                      <td>
                        {role === "student" ? (
                          <a href={`#/submissions/${w.submission_id}`}>
                            <strong>{w.title}</strong>
                          </a>
                        ) : (
                          <strong>{w.title}</strong>
                        )}
                        {role !== "student" && (
                          <small>
                            {w.course_run_title} · Попытка {w.attempt}
                          </small>
                        )}
                      </td>
                      {role === "student" ? (
                        <>
                          <td>{w.course_title}</td>
                          <td>
                            {w.submission_deadline
                              ? date(w.submission_deadline)
                              : "—"}
                          </td>
                          <td>{w.attempt}</td>
                          <td>{w.score?.toLocaleString("ru-RU") ?? "—"}</td>
                          <td>
                            <Status value={w.status} />
                          </td>
                        </>
                      ) : (
                        <>
                          <td>
                            <Status value={w.status} />
                            {w.responsible_reviewer_id && (
                              <small>Есть ответственный</small>
                            )}
                          </td>
                          <td>
                            {w.review_deadline ? date(w.review_deadline) : "—"}
                          </td>
                          <td>{w.score ?? "—"}</td>
                        </>
                      )}
                      {role !== "student" && (
                        <td>
                          <button
                            disabled={action.busy || !w.submission_version_id}
                            onClick={() =>
                              void action.run(async () => {
                                if (
                                  w.review_iteration_id &&
                                  w.review_submission_version_id ===
                                    w.submission_version_id
                                ) {
                                  go(`/reviews/${w.review_iteration_id}`);
                                  return;
                                }
                                const history = await ws.core.submission(
                                  w.submission_id,
                                );
                                const current = history.versions.find(
                                  (v) => v.id === w.submission_version_id,
                                );
                                if (!current)
                                  throw new Error("Версия недоступна.");
                                const result = await ws.command(
                                  "open_work",
                                  w.submission_id,
                                  w.submission_revision,
                                  { submission_version_id: current.id },
                                );
                                go(`/reviews/${result.id}`);
                              })
                            }
                          >
                            Открыть проверку
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {r.data.items.length === 0 && (
              <Empty>По этим условиям работ нет.</Empty>
            )}
            {(role !== "student" || r.data.total > 20) && (
              <div className="pagination">
                <button
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 20))}
                >
                  Назад
                </button>
                <span>
                  {r.data.total ? offset + 1 : 0}–
                  {Math.min(offset + 20, r.data.total)} из {r.data.total}
                </span>
                <button
                  disabled={offset + 20 >= r.data.total}
                  onClick={() => setOffset(offset + 20)}
                >
                  Дальше
                </button>
              </div>
            )}
          </ListFrame>
        )}
      </Resource>
      {role === "student" && <DraftLinks ws={ws} />}{" "}
      {exporting && run && (
        <Modal title="К9 · Выгрузка" close={close}>
          <ExportForm
            ws={ws}
            run={catalog.data!.course_runs.find((r) => r.id === run)!}
          />
        </Modal>
      )}
    </>
  );
}
function DraftLinks({ ws }: { ws: WorkspaceClient }) {
  const r = useResource(() => ws.drafts(), "drafts");
  return (
    <Resource value={r}>
      {!!r.data?.items.length && (
        <Card title="Сохранённые черновики">
          {r.data.items.map((d) => (
            <p key={d.id}>
              <a href={`#/prepare/${d.publication_id}`}>Продолжить работу →</a>
              <small>{d.comment || d.artifact_url || "Загруженный файл"}</small>
            </p>
          ))}
        </Card>
      )}
    </Resource>
  );
}
function ExportForm({
  ws,
  run,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
}) {
  const [audience, setAudience] = useState<"team" | "students">("team");
  const [format, setFormat] = useState<"csv" | "xlsx">("csv");
  const [columns, setColumns] = useState<W<"ExportInput">["columns"]>([
    "student_id",
    "score",
    "status",
  ]);
  const [unfinished, setUnfinished] = useState(false);
  const [job, setJob] = useState<string>();
  const action = useAction();
  const options = [
    ["student_id", "ID студента"],
    ["score", "Итоговый балл"],
    ["status", "Статус"],
    ["attempt", "Номер попытки"],
    ["feedback", "Опубликованный отзыв"],
    ["reviewer_id", "ID ревьюера"],
  ] as const;
  return (
    <>
      {action.feedback}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            const result = await ws.command(
              "create_export",
              run.id,
              run.revision,
              {
                course_run_id: run.id,
                audience,
                columns,
                include_unpublished: unfinished,
                format,
              },
            );
            setJob(result.id);
          });
        }}
      >
        <label>
          Получатель
          <select
            value={audience}
            onChange={(e) => {
              const a = e.target.value as typeof audience;
              setAudience(a);
              if (a === "students")
                setColumns(["student_id", "score", "status"]);
            }}
          >
            <option value="team">Внутренняя ведомость</option>
            <option value="students">Студенческая копия</option>
          </select>
        </label>
        <fieldset disabled={action.busy}>
          {options
            .filter(
              ([key]) =>
                audience === "team" ||
                ["student_id", "score", "status"].includes(key),
            )
            .map(([key, label]) => (
              <label className="check" key={key}>
                <input
                  type="checkbox"
                  checked={columns.includes(key)}
                  onChange={(e) =>
                    setColumns(
                      e.target.checked
                        ? [...columns, key]
                        : columns.filter((c) => c !== key),
                    )
                  }
                />
                {label}
              </label>
            ))}
          <label className="check">
            <input
              type="checkbox"
              checked={unfinished}
              onChange={(e) => setUnfinished(e.target.checked)}
            />
            Включить незакрытые работы с пустым баллом
          </label>
          <label>
            Формат
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value as typeof format)}
            >
              <option>csv</option>
              <option>xlsx</option>
            </select>
          </label>
          <button className="primary" disabled={!columns.length}>
            Подготовить файл
          </button>
        </fieldset>
      </form>
      {job && <ExportMonitor ws={ws} id={job} />}
    </>
  );
}
export function WorkspaceStatistics({ ws }: { ws: WorkspaceClient }) {
  const [days, setDays] = useState(30);
  const [courseRun, setCourseRun] = useState("");
  const catalog = useResource(() => ws.catalog(), "statistics-catalog");
  const r = useResource(
    () => ws.statistics(days, courseRun || undefined),
    `${days}:${courseRun}`,
  );
  return (
    <>
      <ScreenTitle code="Р4" title="Кабинет">
        <div className="actions">
          <label>
            Поток
            <select
              value={courseRun}
              onChange={(e) => setCourseRun(e.target.value)}
            >
              <option value="">Все потоки</option>
              {catalog.data?.course_runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Период
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
            >
              <option value={7}>7 дней</option>
              <option value={30}>30 дней</option>
              <option value={90}>90 дней</option>
            </select>
          </label>
        </div>
      </ScreenTitle>
      <nav className="tabs">
        <a href="#/preferences">Настройки</a>
        <a href="#/statistics" aria-current="page">
          Статистика
        </a>
      </nav>
      <Resource value={r}>
        {r.data && (
          <div className="stack">
            <div className="tiles">
              <div className="tile">
                <div className="n">{r.data.publications}</div>
                <div className="l">работ проверено</div>
                <div className="d">
                  из них {r.data.repeated_publications} повторных
                </div>
              </div>
              <div className="tile">
                <div className="n">
                  {r.data.average_elapsed_minutes == null
                    ? "—"
                    : `${Math.round(r.data.average_elapsed_minutes)} мин`}
                </div>
                <div className="l">в среднем на работу</div>
                <div className="d">
                  От открытия до публикации, включая перерывы
                  {r.data.course_average_elapsed_minutes == null
                    ? ""
                    : `; по курсу ${Math.round(r.data.course_average_elapsed_minutes)} мин`}
                </div>
              </div>
              <div className="tile">
                <div className="n">
                  {r.data.average_wait_minutes == null
                    ? "—"
                    : r.data.average_wait_minutes < 60
                      ? `${Math.round(r.data.average_wait_minutes)} мин`
                      : `${(r.data.average_wait_minutes / 1440).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} дня`}
                </div>
                <div className="l">ждёт студент от сдачи до ответа</div>
                <div className="d">
                  {r.data.course_average_wait_minutes == null
                    ? "Нет данных по курсу"
                    : `по курсу ${(r.data.course_average_wait_minutes / 1440).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} дня`}
                </div>
              </div>
              <div className="tile">
                <div className="n">
                  {r.data.ai_acceptance_percent == null
                    ? "—"
                    : `${r.data.ai_acceptance_percent.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`}
                </div>
                <div className="l">вердиктов модели принято без правок</div>
                <div className="d">
                  {r.data.course_ai_acceptance_percent == null
                    ? "Нет данных по курсу"
                    : `по курсу ${r.data.course_ai_acceptance_percent.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`}
                </div>
              </div>
              <div className="tile">
                <div className="n">{r.data.overdue_publications}</div>
                <div className="l">просрочек за период</div>
                <div className="d">
                  {r.data.course_average_overdue_publications == null
                    ? "Нет данных по курсу"
                    : `по курсу в среднем ${r.data.course_average_overdue_publications.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}`}
                </div>
              </div>
            </div>
            <div className="two-col">
              <Card title="Где вы чаще правите модель">
                <p className="muted">
                  Доля работ, где вы изменили предложенную оценку
                </p>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Требование</th>
                        <th>Работ</th>
                        <th>Правили</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.data.criterion_changes?.length ? (
                        r.data.criterion_changes.map((c) => (
                          <tr key={c.criterion_id}>
                            <td>{c.title}</td>
                            <td>{c.compared_works}</td>
                            <td>
                              {c.change_percent.toLocaleString("ru-RU", {
                                maximumFractionDigits: 1,
                              })}
                              %
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={3}>
                            Нет данных по отдельным требованиям.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card title="Расхождение с другими ревьюерами">
                <p className="muted">
                  По одинаковым требованиям на одинаковых работах. Сравнений:{" "}
                  {r.data.peer_comparison?.sample_count}.
                </p>
                {r.data.peer_comparison?.divergence_percent == null ? (
                  <Empty>Данные для сравнения пока недоступны.</Empty>
                ) : (
                  <>
                    <p className="stat-number">
                      {r.data.peer_comparison?.divergence_percent.toLocaleString(
                        "ru-RU",
                        { maximumFractionDigits: 1 },
                      )}
                      %
                    </p>
                    <dl>
                      <dt>Строже коллег</dt>
                      <dd>
                        {r.data.peer_comparison?.stricter_criteria?.join(
                          ", ",
                        ) || "—"}
                      </dd>
                      <dt>Мягче коллег</dt>
                      <dd>
                        {r.data.peer_comparison?.softer_criteria?.join(", ") ||
                          "—"}
                      </dd>
                      <dt>Совпадение полное</dt>
                      <dd>
                        {r.data.peer_comparison?.fully_agreed_criteria}{" "}
                        требований из{" "}
                        {r.data.peer_comparison?.compared_criteria}
                      </dd>
                    </dl>
                  </>
                )}
                <p className="notice">
                  Расхождение само по себе не ошибка. Оно показывает требования,
                  которые сформулированы так, что их можно понять по-разному.
                </p>
              </Card>
            </div>
          </div>
        )}
      </Resource>
    </>
  );
}

function ListFrame({
  student,
  title,
  children,
}: {
  student: boolean;
  title: string;
  children: ReactNode;
}) {
  return student ? (
    <section className="card">
      <div className="card-body">{children}</div>
    </section>
  ) : (
    <Card title={title}>{children}</Card>
  );
}
