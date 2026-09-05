import { StudentWorks } from "./StudentWorks";
import { ReviewerQueue } from "./ReviewerQueue";
import { useCallback, useState } from "react";
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
  reviewerMode?: "active" | "pool";
}) {
  return props.role === "student" ? (
    <StudentWorks ws={props.ws} />
  ) : props.role === "reviewer" ? (
    <ReviewerQueue ws={props.ws} mode={props.reviewerMode} />
  ) : (
    <WorksList
      key={`${props.coordinatorPool ? "pool" : "registry"}:${window.location.hash}`}
      {...props}
    />
  );
}
function WorksList({
  ws,
  coordinatorPool = false,
}: {
  ws: WorkspaceClient;
  role: Role;
  coordinatorPool?: boolean;
}) {
  const initial = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  const [query, setQuery] = useState(initial.get("q") ?? "");
  const [search, setSearch] = useState(initial.get("q") ?? "");
  const [run, setRun] = useState(initial.get("run") ?? "");
  const [state, setState] = useState(initial.get("state") ?? "");
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [reminding, setReminding] = useState(false);
  const params = {
    q: search,
    course_run_id: run || undefined,
    state:
      coordinatorPool &&
      !["pending_review", "in_review", "ready_to_publish"].includes(state)
        ? ""
        : state,
    view: coordinatorPool ? "pool" : "all",
    offset,
    limit: 20,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const catalog = useResource(() => ws.catalog(), "catalog");
  const action = useAction();
  const close = useCallback(() => {
    setExporting(false);
    setReminding(false);
  }, []);
  async function open(work: W<"WorkItem">) {
    if (
      work.review_iteration_id &&
      work.review_submission_version_id === work.submission_version_id
    ) {
      go(`/reviews/${work.review_iteration_id}`);
      return;
    }
    if (!work.submission_version_id) {
      go(`/submissions/${work.submission_id}`);
      return;
    }
    const result = await ws.command(
      "open_work",
      work.submission_id,
      work.submission_revision,
      { submission_version_id: work.submission_version_id },
    );
    go(`/reviews/${result.id}`);
  }
  return (
    <>
      <ScreenTitle
        code={coordinatorPool ? "К7" : "К8"}
        title={coordinatorPool ? "Пул проверок" : "Домашки"}
      >
        {coordinatorPool ? (
          <button disabled={!run} onClick={() => setReminding(true)}>
            Напомнить ревьюерам
          </button>
        ) : (
          <button disabled={!run} onClick={() => setExporting(true)}>
            Выгрузить
          </button>
        )}
      </ScreenTitle>
      <p className="muted">
        {coordinatorPool
          ? "Работы, ожидающие проверки и находящиеся на ревью. Здесь можно открыть проверку и напомнить ревьюерам."
          : "Все домашние работы: от черновика до опубликованного результата, с баллами, историей и выгрузкой."}
      </p>
      {action.feedback}
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
            placeholder="ID студента или задание"
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
            {catalog.data?.course_runs.map((run) => (
              <option key={run.id} value={run.id}>
                {run.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Статус
          <select
            value={params.state}
            onChange={(e) => {
              setState(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">
              {coordinatorPool ? "Все незавершённые" : "Все статусы"}
            </option>
            {(coordinatorPool
              ? ["pending_review", "in_review", "ready_to_publish"]
              : [
                  "draft",
                  "pending_review",
                  "in_review",
                  "ready_to_publish",
                  "needs_changes",
                  "passed",
                  "failed",
                  "published",
                ]
            ).map((value) => (
              <option key={value} value={value}>
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
                  }[value]
                }
              </option>
            ))}
          </select>
        </label>
        <button>Найти</button>
      </form>
      <Resource value={r}>
        {r.data && (
          <Card
            title={`${coordinatorPool ? "Незавершённые проверки" : "Домашние работы"} · ${r.data.total}`}
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Студент</th>
                    <th>Задание</th>
                    <th>Статус</th>
                    <th>Срок проверки</th>
                    <th>
                      {coordinatorPool
                        ? "Участие ревьюеров"
                        : "Опубликованный балл"}
                    </th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((work) => (
                    <tr key={work.submission_id}>
                      <td>{work.student_name}</td>
                      <td>
                        <strong>{work.title}</strong>
                        <small>
                          {work.course_run_title} · Попытка {work.attempt}
                        </small>
                      </td>
                      <td>
                        <Status value={work.status} attempt={work.attempt} />
                      </td>
                      <td>
                        {work.review_deadline
                          ? date(work.review_deadline)
                          : "—"}
                      </td>
                      <td>
                        {coordinatorPool
                          ? work.participant_ids?.length
                            ? work.participant_ids.length === 1
                              ? "1 участник"
                              : work.participant_ids.length < 5
                                ? `${work.participant_ids.length} участника`
                                : `${work.participant_ids.length} участников`
                            : "Пока нет участников"
                          : (work.score?.toLocaleString("ru-RU") ?? "—")}
                      </td>
                      <td>
                        <div className="actions">
                          <button
                            disabled={action.busy}
                            onClick={() => void action.run(() => open(work))}
                          >
                            {coordinatorPool
                              ? "Открыть проверку"
                              : "Открыть работу"}
                          </button>
                          {!coordinatorPool && (
                            <a href={`#/submissions/${work.submission_id}`}>
                              История и результат
                            </a>
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
                {coordinatorPool
                  ? "Незавершённых проверок по этим условиям нет."
                  : "Домашних работ по этим условиям нет."}
              </Empty>
            )}
            <div className="pagination">
              <button
                disabled={!offset}
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
          </Card>
        )}
      </Resource>
      {exporting && !coordinatorPool && run && catalog.data && (
        <Modal title="К10 · Выгрузка" close={close}>
          <ExportForm
            ws={ws}
            run={catalog.data.course_runs.find((value) => value.id === run)!}
          />
        </Modal>
      )}
      {reminding && coordinatorPool && run && (
        <Modal title="Напомнить ревьюерам" close={close}>
          <PoolReminder ws={ws} runId={run} done={close} />
        </Modal>
      )}
    </>
  );
}
function PoolReminder({
  ws,
  runId,
  done,
}: {
  ws: WorkspaceClient;
  runId: string;
  done: () => void;
}) {
  const r = useResource(async () => {
    const [members, people] = await Promise.all([
      ws.core.courseMembers(runId),
      ws.directory(),
    ]);
    return people.items.filter((person) =>
      members.items.some(
        (member) =>
          member.user_id === person.id &&
          member.kind === "reviewer" &&
          member.status === "active",
      ),
    );
  }, runId);
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("В потоке есть работы, ожидающие проверки.");
  const action = useAction();
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void action.run(async () => {
          const run = (await ws.catalog()).course_runs.find(
            (value) => value.id === runId,
          );
          if (!run) throw new Error("Поток недоступен.");
          await ws.command("remind_reviewers", runId, run.revision, {
            reviewer_ids: selected,
            text,
          });
          done();
        });
      }}
    >
      {action.feedback}
      <Resource value={r}>
        {r.data?.map((person) => (
          <label key={person.id} className="check">
            <input
              type="checkbox"
              checked={selected.includes(person.id)}
              onChange={(event) =>
                setSelected(
                  event.target.checked
                    ? [...selected, person.id]
                    : selected.filter((id) => id !== person.id),
                )
              }
            />
            {person.display_name}
          </label>
        ))}
        {r.data?.length === 0 && <Empty>В потоке пока нет ревьюеров.</Empty>}
      </Resource>
      <label>
        Сообщение
        <textarea
          required
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <button className="primary" disabled={action.busy || !selected.length}>
        Отправить напоминание
      </button>
    </form>
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
