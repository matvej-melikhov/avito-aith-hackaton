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
export function WorkspaceWorks({
  ws,
  role,
  coordinatorPool = false,
}: {
  ws: WorkspaceClient;
  role: Role;
  coordinatorPool?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [run, setRun] = useState(
    new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("run") ??
      "",
  );
  const [state, setState] = useState("");
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
        {role !== "student" && (
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
        )}
        <button>Найти</button>
      </form>
      <Resource value={r}>
        {r.data && (
          <Card title={`Работы · ${r.data.total}`}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {role !== "student" && <th>Студент</th>}
                    <th>Задание</th>
                    <th>Статус</th>
                    <th>Срок проверки</th>
                    <th>Оценка</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((w, index) => (
                    <tr key={w.submission_id}>
                      {role !== "student" && (
                        <td>
                          <strong>{w.student_name}</strong>
                          {index === 0 && offset === 0 && (
                            <small>Рекомендуется первой</small>
                          )}
                        </td>
                      )}
                      <td>
                        <strong>{w.title}</strong>
                        <small>
                          {w.course_run_title} · Попытка {w.attempt}
                        </small>
                      </td>
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
                      <td>
                        {role === "student" ? (
                          <a href={`#/submissions/${w.submission_id}`}>
                            Открыть →
                          </a>
                        ) : (
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
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {r.data.items.length === 0 && (
              <Empty>По этим условиям работ нет.</Empty>
            )}
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
          </Card>
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
  const r = useResource(() => ws.statistics(days), String(days));
  return (
    <>
      <ScreenTitle code="Р4" title="Моя статистика" />
      <label>
        Период
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>7 дней</option>
          <option value={30}>30 дней</option>
          <option value={90}>90 дней</option>
        </select>
      </label>
      <Resource value={r}>
        {r.data && (
          <div className="stat-grid">
            <Card title="Опубликовано проверок">
              <p className="stat-number">{r.data.publications}</p>
            </Card>
            <Card title="От начала до публикации">
              <p className="stat-number">
                {r.data.average_elapsed_minutes === null
                  ? "Нет данных"
                  : `${Math.round(r.data.average_elapsed_minutes)} мин`}
              </p>
              <p className="muted">Время между событиями, включая перерывы.</p>
            </Card>
            <Card title="Изменены предложения AI">
              <p className="stat-number">
                {r.data.changed_decisions} / {r.data.compared_decisions}
              </p>
              <p className="muted">
                Считаются только решения, связанные с предложением AI.
              </p>
            </Card>
          </div>
        )}
      </Resource>
    </>
  );
}
