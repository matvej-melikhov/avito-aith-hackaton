import {
  Sel,
  Inp,
  Btn,
  Chk,
  Field,
  Area,
  Tabs,
  Tab,
  CardFoot,
  BtnRow,
  Seg,
} from "../ds";
import { StudentWorks } from "./StudentWorks";
import { ReviewerQueue } from "./ReviewerQueue";
import { useCallback, useEffect, useState } from "react";
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
  const [homeworkId, setHomeworkId] = useState(initial.get("homework") ?? "");
  const [offset, setOffset] = useState(0);
  const [stuck, setStuck] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [reminding, setReminding] = useState(false);
  const [draftPreview, setDraftPreview] = useState<W<"WorkItem">>();
  const params = {
    q: search,
    course_run_id: run || undefined,
    homework_id: homeworkId || undefined,
    state:
      coordinatorPool &&
      !["pending_review", "in_review", "ready_to_publish"].includes(state)
        ? ""
        : state,
    view: coordinatorPool ? "pool" : "all",
    stuck: coordinatorPool && stuck ? "true" : undefined,
    offset,
    limit: 20,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const draftFromLink = initial.get("draft");
  useEffect(() => {
    if (draftFromLink && r.data)
      setDraftPreview(
        r.data.items.find((work) => work.draft_id === draftFromLink),
      );
  }, [draftFromLink, r.data]);
  const catalog = useResource(() => ws.catalog(), "catalog");
  useEffect(() => {
    if (!run && catalog.data?.course_runs.length)
      setRun(
        catalog.data.course_runs.find((value) => value.status === "active")
          ?.id ?? catalog.data.course_runs[0].id,
      );
  }, [run, catalog.data]);
  const insights = useResource(
    () =>
      run ? ws.insights(run, homeworkId || undefined) : Promise.resolve(null),
    `${run}:${homeworkId}`,
  );
  const homeworks = useResource(async () => {
    if (!run) return null;
    const selected = (await ws.catalog()).course_runs.find(
      (value) => value.id === run,
    );
    return selected ? ws.courseHomeworks(selected.course_id) : null;
  }, run);
  const close = useCallback(() => {
    setExporting(false);
    setReminding(false);
    setDraftPreview(undefined);
  }, []);
  const selectedRun = catalog.data?.course_runs.find(
    (value) => value.id === run,
  );
  const courseTitle = catalog.data?.courses.find(
    (value) => value.id === selectedRun?.course_id,
  )?.title;
  const homeworkFilter = (
    <Sel
      className="btn btn--s btn--quiet"
      aria-label="Задание"
      disabled={!run}
      value={homeworkId}
      onChange={(event) => {
        setHomeworkId(event.target.value);
        setOffset(0);
      }}
    >
      <option value="">Задание: все</option>
      {homeworks.data?.items.map((item) => (
        <option key={item.id} value={item.id}>
          {item.title}
        </option>
      ))}
    </Sel>
  );
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(query);
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  function open(work: W<"WorkItem">) {
    if (!work.submission_id) {
      setDraftPreview(work);
      return;
    }
    if (
      work.review_iteration_id &&
      work.review_submission_version_id === work.submission_version_id
    )
      go(`/reviews/${work.review_iteration_id}`);
    else go(`/submissions/${work.submission_id}`);
  }
  return (
    <>
      <ScreenTitle
        code={coordinatorPool ? "К7" : "К8"}
        title={coordinatorPool ? "Пул проверок" : "Домашки потока"}
        breadcrumbs={
          <>
            <a
              href="#/dashboard"
              className="crumbs__back"
              aria-label="Назад к потокам"
            >
              ←
            </a>
            <a href="#/courses">Курсы</a>
            <span>/</span>
            <span>{courseTitle ?? "Курс"}</span>
            <span>/</span>
            <span className="coordinator-compact-filters">
              <Sel
                className="btn btn--s btn--quiet"
                aria-label="Поток"
                value={run}
                onChange={(event) => {
                  setRun(event.target.value);
                  setHomeworkId("");
                  setOffset(0);
                }}
              >
                {!run && <option value="">Выберите поток</option>}
                {catalog.data?.course_runs.map((value) => (
                  <option key={value.id} value={value.id}>
                    {value.title}
                  </option>
                ))}
              </Sel>
            </span>
          </>
        }
        leading={
          !coordinatorPool ? (
            <Inp
              className="inp inp--s coordinator-header-search"
              aria-label="Поиск"
              placeholder="Поиск по ID студента"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          ) : undefined
        }
      >
        {coordinatorPool ? (
          <Btn
            variant="dark"
            size="s"
            disabled={!run}
            onClick={() => setReminding(true)}
          >
            Напомнить ревьюерам
          </Btn>
        ) : (
          <Btn
            disabled={
              !run ||
              (!!homeworkId &&
                !homeworks.data?.items.some((item) => item.id === homeworkId))
            }
            variant="dark"
            size="s"
            onClick={() => setExporting(true)}
          >
            Выгрузить
          </Btn>
        )}
      </ScreenTitle>
      {coordinatorPool && (
        <Resource value={insights}>
          {insights.data && (
            <div className="tiles">
              <div className="tile">
                <div className="n">{insights.data.pool_metrics.waiting}</div>
                <div className="l">работ ждут ревьюера</div>
                <div className="d">
                  из {insights.data.pool_metrics.submitted} сданных
                </div>
              </div>
              <div className="tile tile--alert">
                <div className="n">{insights.data.pool_metrics.stuck}</div>
                <div className="l">ждут больше 3 дней</div>
              </div>
              <div className="tile">
                <div className="n">
                  {insights.data.pool_metrics.active_reviewers} из{" "}
                  {insights.data.pool_metrics.total_reviewers}
                </div>
                <div className="l">ревьюеров участвуют в проверках</div>
              </div>
              <div className="tile">
                <div className="n">
                  {insights.data.pool_metrics.average_wait_minutes === null
                    ? "Нет данных"
                    : `${(insights.data.pool_metrics.average_wait_minutes / 1440).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} дня`}
                </div>
                <div className="l">среднее ожидание проверки</div>
              </div>
            </div>
          )}
        </Resource>
      )}
      {!coordinatorPool && state === "reviewing" && (
        <div className="actions" aria-label="Дополнительный фильтр">
          <span className="pill">На проверке</span>
          <Btn
            type="button"
            onClick={() => {
              setState("");
              setOffset(0);
            }}
          >
            Сбросить фильтр
          </Btn>
        </div>
      )}
      {!coordinatorPool && (
        <Resource value={insights}>
          <Tabs role="navigation" label="Статусы домашних работ">
            {(
              [
                ["all", "Все"],
                ["draft", "Черновик"],
                ["pending_review", "Сдана"],
                ["in_review", "На ревью"],
                ["needs_changes", "Нужны правки"],
                ["repeat_review", "Повторное ревью"],
                ["passed", "Зачтена"],
                ["failed", "Не зачтена"],
              ] as const
            ).map(([value, label]) => (
              <Tab
                key={value}
                on={(state || "all") === value}
                count={insights.data?.status_counts[value] ?? "—"}
                onClick={() => {
                  setState(value === "all" ? "" : value);
                  setOffset(0);
                }}
              >
                {label}
              </Tab>
            ))}
            <span
              className="coordinator-compact-filters"
              style={{ marginLeft: "auto" }}
            >
              {homeworkFilter}
            </span>
          </Tabs>
        </Resource>
      )}
      <Resource value={r}>
        {r.data && (
          <Card
            title={`${coordinatorPool ? "Незавершённые проверки" : "Домашние работы"} · ${r.data.total}`}
            bodyClassName="card__body--flush"
            subtitle={
              coordinatorPool
                ? "Ожидают проверки или находятся на ревью"
                : "Все статусы и опубликованные результаты"
            }
            actions={
              coordinatorPool ? (
                <div className="btn-row coordinator-compact-filters">
                  {homeworkFilter}
                  <Chk
                    className="btn btn--s btn--quiet"
                    type="checkbox"
                    checked={stuck}
                    onChange={(event) => {
                      setStuck(event.target.checked);
                      setOffset(0);
                    }}
                  >
                    Только зависшие
                  </Chk>
                </div>
              ) : undefined
            }
          >
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Студент</th>
                    <th>Задание</th>
                    <th>Статус</th>
                    {coordinatorPool ? (
                      <>
                        <th>Сдана</th>
                        <th>Срок проверки</th>
                      </>
                    ) : (
                      <>
                        <th>Ревьюер</th>
                        <th>Попытка</th>
                        <th>Сдана</th>
                        <th>Обновлена</th>
                      </>
                    )}
                    <th
                      aria-label={
                        coordinatorPool
                          ? "Участие ревьюеров"
                          : "Опубликованный балл"
                      }
                    >
                      {coordinatorPool ? "Участие ревьюеров" : "Балл"}
                    </th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.items.map((work) => (
                    <tr key={work.submission_id ?? work.draft_id}>
                      <td>{work.student_name}</td>
                      <td>
                        <strong>{work.title}</strong>
                        <small>
                          {work.course_run_title} · Попытка{" "}
                          {work.submission_id ? work.attempt : 1}
                        </small>
                      </td>
                      <td>
                        <Status value={work.status} attempt={work.attempt} />
                      </td>
                      {coordinatorPool ? (
                        <>
                          <td>
                            {work.submitted_at
                              ? shortWorkDate(work.submitted_at)
                              : "—"}
                          </td>
                          <td>
                            {work.review_deadline
                              ? shortWorkDate(work.review_deadline)
                              : "—"}
                          </td>
                        </>
                      ) : (
                        <>
                          <td>{work.reviewer_name ?? "—"}</td>
                          <td>{work.submission_id ? work.attempt : 1}</td>
                          <td>
                            {work.submitted_at
                              ? shortWorkDate(work.submitted_at)
                              : "—"}
                          </td>
                          <td>
                            {work.updated_at
                              ? shortWorkDate(work.updated_at)
                              : "—"}
                          </td>
                        </>
                      )}
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
                          <Btn
                            variant="link"
                            size="s"
                            onClick={() => open(work)}
                          >
                            {coordinatorPool
                              ? "Открыть проверку"
                              : "Открыть работу"}
                          </Btn>
                          {!coordinatorPool && work.submission_id && (
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
              <Btn
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                Назад
              </Btn>
              <span>
                {r.data.total ? offset + 1 : 0}–
                {Math.min(offset + 20, r.data.total)} из {r.data.total}
              </span>
              <Btn
                disabled={offset + 20 >= r.data.total}
                onClick={() => setOffset(offset + 20)}
              >
                Дальше
              </Btn>
            </div>
          </Card>
        )}
      </Resource>
      {coordinatorPool && insights.data && run && (
        <TypicalFailures
          key={`${run}:${homeworkId}`}
          ws={ws}
          runId={run}
          failures={insights.data.typical_failures}
        />
      )}
      {draftPreview && (
        <Modal title="Черновик работы" close={close}>
          <dl>
            <dt>Студент</dt>
            <dd>{draftPreview.student_name}</dd>
            <dt>Задание</dt>
            <dd>{draftPreview.title}</dd>
            <dt>Поток</dt>
            <dd>{draftPreview.course_run_title}</dd>
            <dt>Статус</dt>
            <dd>Черновик · ещё не сдана</dd>
            <dt>Следующая попытка</dt>
            <dd>{Math.max(1, draftPreview.attempt)}</dd>
            <dt>Обновлена</dt>
            <dd>
              {draftPreview.updated_at ? date(draftPreview.updated_at) : "—"}
            </dd>
          </dl>
        </Modal>
      )}
      {exporting && !coordinatorPool && run && catalog.data && (
        <Modal title="Выгрузка" close={close} wide>
          <ExportForm
            ws={ws}
            run={catalog.data.course_runs.find((value) => value.id === run)!}
            close={close}
            homeworkId={homeworkId || undefined}
            homeworkTitle={
              homeworks.data?.items.find((item) => item.id === homeworkId)
                ?.title
            }
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
function TypicalFailures({
  ws,
  runId,
  failures,
}: {
  ws: WorkspaceClient;
  runId: string;
  failures: Awaited<
    ReturnType<WorkspaceClient["insights"]>
  >["typical_failures"];
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const action = useAction();
  return (
    <Card
      title="Типичные ошибки потока"
      subtitle="По завершённым проверкам"
      bodyClassName="card__body--flush"
      actions={
        <Btn
          disabled={action.busy || !selected.length}
          onClick={() =>
            void action.run(async () => {
              const catalog = await ws.catalog();
              const run = catalog.course_runs.find(
                (value) => value.id === runId,
              );
              const course = catalog.courses.find(
                (value) => value.id === run?.course_id,
              );
              if (!course) throw new Error("Курс недоступен.");
              const summary = failures
                .filter((value) => selected.includes(value.criterion_id))
                .map(
                  (value) =>
                    `• ${value.title}: не выполнили ${value.failed} из ${value.reviewed}.`,
                )
                .join("\n");
              await ws.command("update_course", course.id, course.revision, {
                title: course.title,
                owner_id: course.owner_id,
                stepik_url: course.stepik_url ?? null,
                description: `${course.description}${course.description ? "\n\n" : ""}Типичные ошибки потока «${run!.title}»:\n${summary}`,
              });
              setSelected([]);
            }, "Выбранные ошибки добавлены в описание курса.")
          }
        >
          Добавить в описание курса
        </Btn>
      }
    >
      {action.feedback}
      <div className="table-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>Требование</th>
              <th className="n">Не выполнили</th>
              <th>Доля</th>
            </tr>
          </thead>
          <tbody>
            {failures.map((value) => (
              <tr key={value.criterion_id}>
                <td>
                  <Chk
                    type="checkbox"
                    checked={selected.includes(value.criterion_id)}
                    onChange={(event) =>
                      setSelected(
                        event.target.checked
                          ? [...selected, value.criterion_id]
                          : selected.filter((id) => id !== value.criterion_id),
                      )
                    }
                  >
                    {value.title}
                  </Chk>
                </td>
                <td>
                  {value.failed} из {value.reviewed}
                </td>
                <td>
                  <div
                    className="meter coordinator-failure-meter"
                    aria-label={`${Math.round(value.ratio * 100)}%`}
                  >
                    <i
                      className={value.ratio > 0.5 ? "is-low" : ""}
                      style={{
                        width: `${Math.min(100, Math.max(0, value.ratio * 100))}%`,
                      }}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!failures.length && (
        <Empty>
          Для типичных ошибок пока недостаточно завершённых проверок.
        </Empty>
      )}
    </Card>
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
          <Chk
            key={person.id}
            type="checkbox"
            checked={selected.includes(person.id)}
            onChange={(event) =>
              setSelected(
                event.target.checked
                  ? [...selected, person.id]
                  : selected.filter((id) => id !== person.id),
              )
            }
          >
            {person.display_name}
          </Chk>
        ))}
        {r.data?.length === 0 && <Empty>В потоке пока нет ревьюеров.</Empty>}
      </Resource>
      <Field label="Сообщение">
        <Area
          required
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </Field>
      <Btn
        type="submit"
        variant="pri"
        disabled={action.busy || !selected.length}
      >
        Отправить напоминание
      </Btn>
    </form>
  );
}
function ExportForm({
  ws,
  run,
  close,
  homeworkId,
  homeworkTitle,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  close: () => void;
  homeworkId?: string;
  homeworkTitle?: string;
}) {
  const [audience, setAudience] = useState<"team" | "students">("team");
  const [format, setFormat] = useState<"csv" | "xlsx">("csv");
  const [columns, setColumns] = useState<W<"ExportInput">["columns"]>([
    "student_id",
    "score",
    "status",
    "attempt",
  ]);
  const [unfinished, setUnfinished] = useState(false);
  const [job, setJob] = useState<string>();
  const action = useAction();
  const options = [
    ["student_id", "ID студента"],
    ["score", "Итоговый балл"],
    ["status", "Статус"],
    ["attempt", "Номер попытки"],
    ["criterion_points", "Баллы по каждому критерию"],
    ["feedback", "Комментарий ревьюера"],
    ["reviewer_id", "ID ревьюера"],
    ["artifact_url", "Ссылка на работу"],
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
                homework_id: homeworkId,
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
        <div className="field">
          <div className="field__lbl">Что выгружаем</div>
          <div className="export-context">
            {homeworkId
              ? (homeworkTitle ?? "Выбранное задание")
              : "Все задания"}
            , поток «{run.title}»
          </div>
        </div>
        <Field
          label="Кому выгружаем"
          group
          hint="В студенческой копии только ID студента, балл и статус."
        >
          <Seg<"team" | "students">
            label="Кому выгружаем"
            value={audience}
            options={[
              { value: "team", label: "Команде, внутренняя ведомость" },
              { value: "students", label: "Студентам, копия" },
            ]}
            onChange={(value) => {
              setAudience(value);
              if (value === "students")
                setColumns(["student_id", "score", "status"]);
            }}
          />
        </Field>
        <fieldset disabled={action.busy}>
          <legend>Колонки</legend>
          <div className="date-range">
            {options
              .filter(
                ([key]) =>
                  audience === "team" ||
                  ["student_id", "score", "status"].includes(key),
              )
              .map(([key, label]) => (
                <Chk
                  key={key}
                  type="checkbox"
                  checked={columns.includes(key)}
                  onChange={(e) =>
                    setColumns(
                      e.target.checked
                        ? [...columns, key]
                        : columns.filter((c) => c !== key),
                    )
                  }
                >
                  {label}
                </Chk>
              ))}
          </div>
        </fieldset>
        <Field label="Что делать с незакрытыми работами" group>
          <Seg<"omit" | "include">
            label="Что делать с незакрытыми работами"
            disabled={action.busy}
            value={unfinished ? "include" : "omit"}
            options={[
              { value: "omit", label: "Не выгружать" },
              { value: "include", label: "Выгрузить с пустым баллом" },
            ]}
            onChange={(value) => setUnfinished(value === "include")}
          />
        </Field>
        <Field label="Формат" group>
          <Seg<"csv" | "xlsx">
            label="Формат"
            disabled={action.busy}
            value={format}
            options={[
              { value: "csv", label: "CSV" },
              { value: "xlsx", label: "XLSX" },
            ]}
            onChange={setFormat}
          />
        </Field>
        <CardFoot end>
          <BtnRow end>
            <Btn type="button" onClick={close}>
              Отмена
            </Btn>
            <Btn
              type="submit"
              variant="pri"
              disabled={action.busy || !columns.length}
            >
              Подготовить файл
            </Btn>
          </BtnRow>
        </CardFoot>
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
        <div className="btn-row coordinator-compact-filters">
          <Sel
            aria-label="Поток"
            className="btn btn--s btn--quiet"
            value={courseRun}
            onChange={(event) => setCourseRun(event.target.value)}
          >
            <option value="">Все потоки</option>
            {catalog.data?.course_runs.map((run) => (
              <option key={run.id} value={run.id}>
                {run.title}
              </option>
            ))}
          </Sel>
          <Sel
            aria-label="Период"
            className="btn btn--s btn--quiet"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            <option value={7}>7 дней</option>
            <option value={30}>30 дней</option>
            <option value={90}>90 дней</option>
          </Sel>
        </div>
      </ScreenTitle>
      <Tabs role="navigation" label="Кабинет">
        <Tab href="#/preferences">Настройки</Tab>
        <Tab href="#/statistics" on>
          Статистика
        </Tab>
      </Tabs>
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
              <Card
                title="Где вы чаще правите модель"
                subtitle="Доля работ, где вы изменили предложенную оценку"
                bodyClassName="card__body--flush"
              >
                <div className="table-wrap">
                  <table className="tbl">
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
              <Card
                title="Расхождение с другими ревьюерами"
                subtitle={`По одинаковым требованиям на одинаковых работах. Сравнений: ${r.data.peer_comparison?.sample_count ?? 0}.`}
              >
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

function shortWorkDate(value: string) {
  return new Date(value).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
  });
}
