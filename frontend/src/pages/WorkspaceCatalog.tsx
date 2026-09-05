import { useCallback, useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, type W } from "../api/workspace";
import {
  Card,
  Empty,
  Resource,
  Status,
  date,
  useAction,
  useResource,
} from "../ui";
import { WorkspaceSearch } from "./WorkspaceSearch";
import { Modal, ScreenTitle } from "../workspace-ui";
function localDate(value?: string | null) {
  if (!value) return "";
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function WorkspaceCatalog({
  ws,
  mode = "overview",
}: {
  ws: WorkspaceClient;
  mode?: "overview" | "courses";
}) {
  const r = useResource(() => ws.catalog(), "catalog");
  const [modal, setModal] = useState<string>();
  const [editCourse, setEditCourse] = useState<W<"CourseView">>();
  const [selectedCourse, setSelectedCourse] = useState("");
  const close = useCallback(() => setModal(undefined), []);
  const action = useAction();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("active");
  return (
    <>
      <ScreenTitle
        code={mode === "overview" ? "К1" : "К2"}
        title={mode === "overview" ? "Обзор" : "Курсы"}
        leading={mode === "overview" ? <WorkspaceSearch ws={ws} /> : undefined}
      >
        <button className="btn" onClick={() => setModal("course")}>
          Создать курс
        </button>
        {mode === "overview" && (
          <button className="btn btn--dark" onClick={() => setModal("run")}>
            Создать поток
          </button>
        )}
      </ScreenTitle>
      {action.feedback}
      {mode === "overview" && <OverviewMetrics ws={ws} />}
      <Resource value={r}>
        {r.data && mode === "overview" && (
          <Card
            title="Потоки"
            actions={
              <div className="filters">
                <label>
                  Найти курс или поток
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </label>
                <label>
                  Состояние
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value)}
                  >
                    <option value="active">Активные</option>
                    <option value="archived">Архив</option>
                    <option value="">Все</option>
                  </select>
                </label>
              </div>
            }
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Курс / поток</th>
                    <th>Студенты</th>
                    <th>Ревьюеры</th>
                    <th>Ближайший срок</th>
                    <th>Все работы</th>
                    <th>В пуле</th>
                    <th>На проверке</th>
                    <th>Зачтено</th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.course_runs
                    .filter(
                      (run) =>
                        (!status || run.status === status) &&
                        `${run.title} ${r.data!.courses.find((c) => c.id === run.course_id)?.title}`
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                    )
                    .map((run) => (
                      <RunSummary
                        key={run.id}
                        ws={ws}
                        run={run}
                        course={
                          r.data!.courses.find((c) => c.id === run.course_id)
                            ?.title ?? "Курс"
                        }
                      />
                    ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
        {r.data && mode === "courses" && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Курс</th>
                  <th>Заданий</th>
                  <th>Потоков</th>
                  <th>Активных</th>
                  <th>Курс в Stepik</th>
                  <th>Состояние</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {r.data.courses.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <button
                        className="link-button"
                        onClick={() => {
                          setEditCourse(c);
                          setModal("edit-course");
                        }}
                      >
                        {c.title}
                      </button>
                      <small>{c.description}</small>
                    </td>
                    <td>
                      <HomeworkCount ws={ws} courseId={c.id} />
                    </td>
                    <td>
                      {
                        r.data!.course_runs.filter(
                          (run) => run.course_id === c.id,
                        ).length
                      }
                    </td>
                    <td>
                      {
                        r.data!.course_runs.filter(
                          (run) =>
                            run.course_id === c.id && run.status === "active",
                        ).length
                      }
                    </td>
                    <td>
                      {c.stepik_url ? (
                        <a href={c.stepik_url} target="_blank" rel="noreferrer">
                          {c.stepik_url.replace("https://", "")}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <Status value={c.status} />
                    </td>
                    <td>
                      <button
                        disabled={action.busy}
                        onClick={() =>
                          void action.run(async () => {
                            if (c.status === "archived")
                              await ws.core.command(
                                "restore_course",
                                c.id,
                                c.revision,
                                {},
                              );
                            else
                              await ws.core.command(
                                "archive_course",
                                c.id,
                                c.revision,
                                { reason: "Архивирование координатором" },
                              );
                            r.refresh();
                          })
                        }
                      >
                        {c.status === "archived"
                          ? "Восстановить"
                          : "Архивировать"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {r.data?.courses.length === 0 && (
          <Empty>Создайте курс или импортируйте его из Stepik.</Empty>
        )}
      </Resource>
      {modal && (
        <Modal
          title={
            modal === "course"
              ? "Создать курс"
              : modal === "edit-course"
                ? "Редактировать курс"
                : "Создать поток"
          }
          close={close}
        >
          {modal === "course" || modal === "edit-course" ? (
            <CourseForm
              ws={ws}
              course={modal === "edit-course" ? editCourse : undefined}
              done={() => {
                close();
                r.refresh();
              }}
            />
          ) : (
            <>
              <label>
                Курс
                <select
                  value={selectedCourse}
                  onChange={(e) => setSelectedCourse(e.target.value)}
                >
                  <option value="">Выберите курс</option>
                  {r.data?.courses
                    .filter((c) => c.status === "active")
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.title}
                      </option>
                    ))}
                </select>
              </label>
              {selectedCourse && (
                <RunForm
                  key={selectedCourse}
                  ws={ws}
                  course={r.data!.courses.find((c) => c.id === selectedCourse)!}
                  done={() => {
                    close();
                    r.refresh();
                  }}
                />
              )}
            </>
          )}
        </Modal>
      )}
    </>
  );
}
function OverviewMetrics({ ws }: { ws: WorkspaceClient }) {
  const r = useResource(async () => {
    const [all, active, pool, catalog] = await Promise.all([
      ws.works({ limit: 1 }),
      ws.works({ state: "in_review", limit: 1 }),
      ws.works({ state: "pending_review", limit: 1 }),
      ws.catalog(),
    ]);
    const homeworks = await Promise.all(
      catalog.course_runs
        .filter((run) => run.status === "active")
        .map((run) => ws.core.homeworks(run.id)),
    );
    const now = Date.now();
    const deadlines = homeworks
      .flatMap((h) => h.items)
      .filter(
        (h) =>
          h.submission_deadline &&
          new Date(h.submission_deadline).getTime() >= now &&
          new Date(h.submission_deadline).getTime() <= now + 7 * 86400000,
      ).length;
    return {
      all: all.total,
      active: active.total,
      pool: pool.total,
      deadlines,
    };
  }, "overview-metrics");
  return (
    <Resource value={r}>
      {r.data && (
        <div className="tiles">
          <a className="tile" href="#/registry">
            <div className="n">{r.data.all}</div>
            <div className="l">домашек в потоках</div>
          </a>
          <a className="tile" href="#/registry?state=in_review">
            <div className="n">{r.data.active}</div>
            <div className="l">у ревьюеров прямо сейчас</div>
          </a>
          <a
            className="tile tile--alert"
            href="#/registry?state=pending_review"
          >
            <div className="n">{r.data.pool}</div>
            <div className="l">в пуле без проверки</div>
          </a>
          <div className="tile">
            <div className="n">{r.data.deadlines}</div>
            <div className="l">дедлайнов в ближайшие 7 дней</div>
          </div>
        </div>
      )}
    </Resource>
  );
}
function RunSummary({
  ws,
  run,
  course,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  course: string;
}) {
  const r = useResource(async () => {
    const [all, pool, review, accepted, members, homeworks] = await Promise.all(
      [
        ws.works({ course_run_id: run.id, limit: 1 }),
        ws.works({ course_run_id: run.id, state: "pending_review", limit: 1 }),
        ws.works({ course_run_id: run.id, state: "in_review", limit: 1 }),
        ws.works({ course_run_id: run.id, state: "passed", limit: 1 }),
        ws.assignments(run.id),
        ws.core.homeworks(run.id),
      ],
    );
    const deadline = homeworks.items
      .map((h) => h.submission_deadline)
      .filter((d): d is string => !!d && new Date(d).getTime() >= Date.now())
      .sort()[0];
    return {
      all: all.total,
      pool: pool.total,
      review: review.total,
      accepted: accepted.total,
      students: members.items.length,
      deadline,
    };
  }, run.id);
  const link = (count: number, state = "") => (
    <a href={`#/registry?run=${run.id}&state=${state}`}>{count}</a>
  );
  return (
    <tr>
      <td>
        <small>{course}</small>
        <div className="flow-heading">
          <a href={`#/courses/${run.id}`}>{run.title}</a>
          <Status value={run.status} />
        </div>
      </td>
      {r.data ? (
        <>
          <td>{r.data.students}</td>
          <td>{run.reviewer_count}</td>
          <td>{r.data.deadline ? date(r.data.deadline) : "Нет предстоящих"}</td>
          <td>{link(r.data.all)}</td>
          <td>{link(r.data.pool, "pending_review")}</td>
          <td>{link(r.data.review, "in_review")}</td>
          <td>{link(r.data.accepted, "passed")}</td>
        </>
      ) : (
        <td colSpan={7}>
          <Resource value={r}>{null}</Resource>
        </td>
      )}
    </tr>
  );
}
export function RunCard({
  ws,
  run,
  refresh,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  refresh: () => void;
}) {
  const work = useResource(
    () => ws.works({ course_run_id: run.id, limit: 1 }),
    run.id,
  );
  const action = useAction();
  const [priority, setPriority] = useState(run.priority);
  const [editing, setEditing] = useState(false);
  const catalog = useResource(() => ws.catalog(), run.id);
  return (
    <article className="list-item">
      <div className="flow-heading">
        <a href={`#/courses/${run.id}`}>
          <strong>{run.title}</strong>
        </a>
        <Status value={run.status} />
      </div>
      <p className="muted">Ревьюеров в потоке: {run.reviewer_count}</p>
      <p className="muted">
        {run.starts_at ? date(run.starts_at) : "Начало не указано"} —{" "}
        {run.ends_at ? date(run.ends_at) : "Окончание не указано"}
      </p>
      <Resource value={work}>
        {work.data && (
          <p>
            <a href={`#/registry?run=${run.id}`}>Работы: {work.data.total} →</a>
          </p>
        )}
      </Resource>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            await ws.command(
              "set_run_priority",
              run.id,
              run.priority_revision,
              { priority },
            );
            refresh();
          });
        }}
      >
        <label>
          Приоритет рекомендаций
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value as typeof priority)}
          >
            <option value="assigned">Свои студенты сначала</option>
            <option value="deadline">Ближайший срок сначала</option>
          </select>
        </label>
        <button disabled={action.busy || priority === run.priority}>
          Сохранить приоритет
        </button>
      </form>
      {editing && catalog.data && (
        <Modal title="Редактировать поток" close={() => setEditing(false)}>
          <RunForm
            ws={ws}
            course={catalog.data.courses.find((c) => c.id === run.course_id)!}
            run={run}
            done={() => {
              setEditing(false);
              refresh();
            }}
          />
        </Modal>
      )}
      <div className="actions">
        <button onClick={() => setEditing(true)}>Редактировать поток</button>
        <a href={`#/assignments/${run.id}`}>Ревьюеры и студенты</a>
        <button
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              const fresh = (await ws.catalog()).course_runs.find(
                (r) => r.id === run.id,
              )!;
              if (fresh.status === "archived")
                await ws.core.command(
                  "restore_course_run",
                  run.id,
                  fresh.revision,
                  {},
                );
              else
                await ws.core.command(
                  "archive_course_run",
                  run.id,
                  fresh.revision,
                  { reason: "Архивирование потока координатором" },
                );
              refresh();
            })
          }
        >
          {run.status === "archived"
            ? "Восстановить поток"
            : "Архивировать поток"}
        </button>
      </div>
      {action.feedback}
    </article>
  );
}
function CourseForm({
  ws,
  done,
  course,
}: {
  ws: WorkspaceClient;
  done: () => void;
  course?: W<"CourseView">;
}) {
  const [title, setTitle] = useState(course?.title ?? "");
  const [description, setDescription] = useState(course?.description ?? "");
  const [stepikUrl, setStepikUrl] = useState(course?.stepik_url ?? "");
  const [owner, setOwner] = useState(course?.owner_id ?? "");
  const people = useResource(() => ws.directory(), "people");
  const action = useAction();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void action.run(async () => {
          const org = await ws.core.organization();
          await ws.command(
            course ? "update_course" : "create_course",
            course?.id ?? org.id,
            course?.revision ?? org.revision,
            {
              title,
              description,
              owner_id: owner || null,
              stepik_url: stepikUrl || null,
            },
          );
          done();
        });
      }}
    >
      {action.feedback}
      <label>
        Название курса
        <input
          required
          maxLength={512}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label>
        Описание
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label>
        Ссылка на курс в Stepik (необязательно)
        <input
          type="url"
          value={stepikUrl}
          onChange={(e) => setStepikUrl(e.target.value)}
          placeholder="https://stepik.org/course/…"
        />
      </label>
      <label>
        Координатор
        <select value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Не выбран</option>
          {people.data?.items
            .filter((p) => p.roles.includes("methodologist"))
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name}
              </option>
            ))}
        </select>
      </label>
      <button type="button" onClick={done}>
        Отмена
      </button>
      <button className="primary" disabled={action.busy}>
        {course ? "Сохранить курс" : "Создать курс"}
      </button>
    </form>
  );
}
function RunForm({
  ws,
  course,
  done,
  run,
}: {
  ws: WorkspaceClient;
  course: W<"CourseView">;
  done: () => void;
  run?: W<"CourseRunView">;
}) {
  const [title, setTitle] = useState(run?.title ?? "");
  const [start, setStart] = useState(localDate(run?.starts_at));
  const [end, setEnd] = useState(localDate(run?.ends_at));
  const [zone, setZone] = useState(run?.timezone ?? "Europe/Moscow");
  const [priority, setPriority] = useState<"assigned" | "deadline">(
    run?.priority ?? "assigned",
  );
  const action = useAction();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void action.run(async () => {
          await ws.command(
            run ? "update_course_run" : "create_course_run",
            run?.id ?? course.id,
            run?.revision ?? course.revision,
            {
              title,
              starts_at: new Date(start).toISOString(),
              ends_at: new Date(end).toISOString(),
              timezone: zone,
              priority,
            },
          );
          done();
        });
      }}
    >
      {action.feedback}
      <p>Курс: {course.title}</p>
      <label>
        Название потока
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label>
        Начало
        <input
          required
          type="datetime-local"
          value={start}
          onChange={(e) => setStart(e.target.value)}
        />
      </label>
      <label>
        Окончание
        <input
          required
          type="datetime-local"
          min={start}
          value={end}
          onChange={(e) => setEnd(e.target.value)}
        />
      </label>
      <label>
        Часовой пояс
        <input
          required
          value={zone}
          onChange={(e) => setZone(e.target.value)}
        />
      </label>
      <label>
        Порядок рекомендаций
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value as typeof priority)}
        >
          <option value="assigned">Свои студенты сначала</option>
          <option value="deadline">Ближайший срок сначала</option>
        </select>
      </label>
      <p className="muted">
        Порядок рекомендаций не ограничивает доступ к общему пулу.
      </p>
      <p className="muted">
        После создания добавьте участников или импортируйте состав из Stepik.
      </p>
      <button type="button" onClick={done}>
        Отмена
      </button>
      <button className="primary" disabled={action.busy}>
        {run ? "Сохранить поток" : "Создать поток"}
      </button>
    </form>
  );
}
export function WorkspacePreferences({
  ws,
  session,
}: {
  ws: WorkspaceClient;
  session: Model<"Session">;
}) {
  const r = useResource(
    async () => ({
      preferences: await ws.preferences(),
      catalog: await ws.catalog(),
    }),
    "preferences",
  );
  return (
    <Resource value={r}>
      {r.data && (
        <PreferencesForm
          key={r.data.preferences.revision}
          ws={ws}
          session={session}
          {...r.data}
          refresh={r.refresh}
        />
      )}
    </Resource>
  );
}
function PreferencesForm({
  ws,
  session,
  preferences,
  catalog,
  refresh,
}: {
  ws: WorkspaceClient;
  session: Model<"Session">;
  preferences: W<"PreferencesView">;
  catalog: W<"CatalogView">;
  refresh: () => void;
}) {
  const initial = preferences.value;
  const [showPool, setShowPool] = useState(initial?.show_pool ?? true);
  const [notifications, setNotifications] = useState(
    initial?.notifications ?? { deadline: true, revision: true, pool: false },
  );
  const [selected, setSelected] = useState(initial?.course_run_ids ?? []);
  const [from, setFrom] = useState(localDate(initial?.absent_from));
  const [to, setTo] = useState(localDate(initial?.absent_until));
  const action = useAction();
  function reset() {
    setShowPool(initial?.show_pool ?? true);
    setNotifications(
      initial?.notifications ?? { deadline: true, revision: true, pool: false },
    );
    setSelected(initial?.course_run_ids ?? []);
    setFrom(localDate(initial?.absent_from));
    setTo(localDate(initial?.absent_until));
  }
  return (
    <>
      <ScreenTitle code="Р3" title="Кабинет">
        <div className="actions">
          <button type="button" onClick={reset}>
            Отменить
          </button>
          <button
            className="primary"
            form="reviewer-preferences"
            disabled={action.busy}
          >
            Сохранить
          </button>
        </div>
      </ScreenTitle>
      <nav className="tabs">
        <a href="#/preferences" aria-current="page">
          Настройки
        </a>
        <a href="#/statistics">Статистика</a>
      </nav>
      {action.feedback}
      <form
        id="reviewer-preferences"
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            await ws.command(
              "save_preferences",
              session.user_id,
              preferences.revision,
              {
                course_run_ids: selected,
                show_pool: showPool,
                notifications,
                planned_minutes: initial?.planned_minutes ?? 0,
                until_at: initial?.until_at ?? new Date().toISOString(),
                absent_from: from ? new Date(from).toISOString() : null,
                absent_until: from && to ? new Date(to).toISOString() : null,
              },
            );
            refresh();
          });
        }}
      >
        <div className="row-side">
          <Card title="Курсы, которые готов проверять">
            {catalog.courses
              .filter((course) =>
                catalog.course_runs.some(
                  (run) =>
                    run.course_id === course.id && run.status === "active",
                ),
              )
              .map((course) => {
                const runs = catalog.course_runs.filter(
                  (run) =>
                    run.course_id === course.id && run.status === "active",
                );
                return (
                  <label key={course.id} className="check list-item">
                    <input
                      type="checkbox"
                      checked={runs.every((run) => selected.includes(run.id))}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [
                                ...new Set([
                                  ...selected,
                                  ...runs.map((run) => run.id),
                                ]),
                              ]
                            : selected.filter(
                                (id) => !runs.some((run) => run.id === id),
                              ),
                        )
                      }
                    />
                    <span>{course.title}</span>
                    <CoursePoolCount ws={ws} runs={runs} />
                  </label>
                );
              })}
          </Card>
          <div className="stack">
            <Card title="Доступность">
              <label className="check">
                <span>
                  Показывать мне работы из пула
                  <small className="muted">
                    выключите, если временно не берёте новое
                  </small>
                </span>
                <span className="tgl">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={showPool}
                    onChange={(e) => setShowPool(e.target.checked)}
                  />
                  <span className="tgl__t" />
                </span>
              </label>
              <fieldset>
                <legend>Отпуск или отсутствие</legend>
                <div className="actions">
                  <input
                    aria-label="Начало отсутствия"
                    type="datetime-local"
                    value={from}
                    onChange={(e) => {
                      setFrom(e.target.value);
                      if (!e.target.value) setTo("");
                    }}
                  />
                  <span>—</span>
                  <input
                    aria-label="Окончание отсутствия"
                    type="datetime-local"
                    required={!!from}
                    min={from}
                    value={to}
                    onChange={(e) => setTo(e.target.value)}
                  />
                </div>
              </fieldset>
              <p className="muted">
                На это время новые работы не берём, уже взятые остаются за вами.
              </p>
            </Card>
            <Card title="Уведомления">
              {(
                [
                  ["deadline", "Работа, которую я взял, близка к сроку"],
                  ["revision", "Студент прислал правки"],
                  ["pool", "В пуле по моим курсам появилось что-то новое"],
                ] as const
              ).map(([key, label]) => (
                <label key={key} className="check">
                  <input
                    type="checkbox"
                    checked={notifications[key]}
                    onChange={(e) =>
                      setNotifications((current) => ({
                        ...current,
                        [key]: e.target.checked,
                      }))
                    }
                  />
                  {label}
                </label>
              ))}
            </Card>
          </div>
        </div>
      </form>
    </>
  );
}
function CoursePoolCount({
  ws,
  runs,
}: {
  ws: WorkspaceClient;
  runs: W<"CourseRunView">[];
}) {
  const r = useResource(
    async () => {
      const lists = await Promise.all(
        runs.map((run) =>
          ws.works({
            course_run_id: run.id,
            state: "pending_review",
            limit: 1,
          }),
        ),
      );
      return lists.reduce((sum, list) => sum + list.total, 0);
    },
    runs.map((run) => run.id).join(":"),
  );
  return (
    <span className="muted">
      {r.data !== undefined
        ? `в пуле ${r.data} работ`
        : r.error
          ? "Число работ недоступно"
          : "…"}
    </span>
  );
}
export function WorkspaceAssignments({
  ws,
  runId,
}: {
  ws: WorkspaceClient;
  runId: string;
}) {
  const r = useResource(
    async () => ({
      assignments: await ws.assignments(runId),
      people: await ws.directory(),
      catalog: await ws.catalog(),
    }),
    runId,
  );
  const action = useAction();
  const [member, setMember] = useState("");
  const [kind, setKind] = useState<"student" | "reviewer">("student");
  const [message, setMessage] = useState(
    "В потоке есть работы, ожидающие проверки.",
  );
  const [recipients, setRecipients] = useState<string[]>([]);
  return (
    <>
      <ScreenTitle code="К1" title="Студенты и ревьюеры" />
      <p>
        <a href={`#/courses/${runId}`}>← Вернуться к потоку</a>
      </p>
      <p className="notice">
        Основной ревьюер закрепляется за студентом в потоке. Участники
        конкретной проверки могут быть другими: коллеги сохраняют доступ к
        работе, а разовое участие не меняет закрепление.
      </p>
      {action.feedback}
      <Resource value={r}>
        {r.data && (
          <>
            <Card title="Основной ревьюер студента">
              <table>
                <thead>
                  <tr>
                    <th>Студент</th>
                    <th>Ревьюер</th>
                  </tr>
                </thead>
                <tbody>
                  {r.data.assignments.items.map((a) => (
                    <AssignmentRow
                      key={`${a.id}:${a.revision}`}
                      ws={ws}
                      runId={runId}
                      value={a}
                      people={r.data!.people.items}
                      refresh={r.refresh}
                    />
                  ))}
                </tbody>
              </table>
              {r.data.assignments.items.length === 0 && (
                <Empty>В потоке пока нет студентов.</Empty>
              )}
            </Card>
            <div className="two-col">
              <Card title="Добавить участника">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action.run(async () => {
                      const current = (await ws.catalog()).course_runs.find(
                        (r) => r.id === runId,
                      )!;
                      await ws.command(
                        "set_course_membership",
                        runId,
                        current.revision,
                        { user_id: member, kind, active: true },
                      );
                      r.refresh();
                    });
                  }}
                >
                  <label>
                    Роль в потоке
                    <select
                      value={kind}
                      onChange={(e) => {
                        setKind(e.target.value as typeof kind);
                        setMember("");
                      }}
                    >
                      <option value="student">Студент</option>
                      <option value="reviewer">Ревьюер</option>
                    </select>
                  </label>
                  <label>
                    Участник
                    <select
                      required
                      value={member}
                      onChange={(e) => setMember(e.target.value)}
                    >
                      <option value="">Выбрать</option>
                      {r.data.people.items
                        .filter((p) => p.roles.includes(kind))
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.display_name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <button disabled={action.busy}>Добавить</button>
                </form>
              </Card>
              <Card title="Напомнить ревьюерам">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action.run(async () => {
                      const current = (await ws.catalog()).course_runs.find(
                        (r) => r.id === runId,
                      )!;
                      await ws.command(
                        "remind_reviewers",
                        runId,
                        current.revision,
                        { reviewer_ids: recipients, text: message },
                      );
                    }, "Напоминание появится у выбранных ревьюеров в платформе.");
                  }}
                >
                  {r.data.people.items
                    .filter((p) => p.roles.includes("reviewer"))
                    .map((p) => (
                      <label className="check" key={p.id}>
                        <input
                          type="checkbox"
                          checked={recipients.includes(p.id)}
                          onChange={(e) =>
                            setRecipients(
                              e.target.checked
                                ? [...recipients, p.id]
                                : recipients.filter((v) => v !== p.id),
                            )
                          }
                        />
                        {p.display_name}
                      </label>
                    ))}
                  <label>
                    Сообщение
                    <textarea
                      required
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                    />
                  </label>
                  <button disabled={action.busy || !recipients.length}>
                    Отправить напоминание
                  </button>
                </form>
              </Card>
            </div>
          </>
        )}
      </Resource>
    </>
  );
}
function AssignmentRow({
  ws,
  runId,
  value,
  people,
  refresh,
}: {
  ws: WorkspaceClient;
  runId: string;
  value: W<"AssignmentView">;
  people: W<"DirectoryMember">[];
  refresh: () => void;
}) {
  const [reviewer, setReviewer] = useState(value.reviewer_id ?? "");
  const action = useAction();
  return (
    <tr>
      <td>{value.student_name}</td>
      <td>
        <div className="actions">
          <select
            aria-label={`Ревьюер: ${value.student_name}`}
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
          >
            <option value="">Не закреплён</option>
            {people
              .filter((p) => p.roles.includes("reviewer"))
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                </option>
              ))}
          </select>
          <button
            disabled={action.busy}
            onClick={() =>
              void action.run(async () => {
                await ws.command("assign_student", runId, value.revision, {
                  student_id: value.student_id,
                  reviewer_id: reviewer || null,
                  reason: "Изменение закрепления координатором",
                });
                refresh();
              })
            }
          >
            Сохранить
          </button>
        </div>
        {action.feedback}
      </td>
    </tr>
  );
}

export function WorkspaceRunSettings({
  ws,
  runId,
}: {
  ws: WorkspaceClient;
  runId: string;
}) {
  const r = useResource(() => ws.catalog(), runId);
  const run = r.data?.course_runs.find((v) => v.id === runId);
  return (
    <Resource value={r}>
      {run && (
        <RunCard
          key={`${run.id}:${run.revision}`}
          ws={ws}
          run={run}
          refresh={r.refresh}
        />
      )}
    </Resource>
  );
}

function HomeworkCount({
  ws,
  courseId,
}: {
  ws: WorkspaceClient;
  courseId: string;
}) {
  const r = useResource(() => ws.courseHomeworks(courseId), courseId);
  return <Resource value={r}>{r.data?.items.length}</Resource>;
}

export function WorkspaceHomeworkDirectory({ ws }: { ws: WorkspaceClient }) {
  const r = useResource(async () => {
    const catalog = await ws.catalog();
    const lists = await Promise.all(
      catalog.courses.map((course) => ws.courseHomeworks(course.id)),
    );
    return {
      ...catalog,
      homeworks: lists.flatMap((list) =>
        list.items.map((item) => ({ ...item, course_id: list.course_id })),
      ),
    };
  }, "homework-directory");
  const [courseId, setCourseId] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [runId, setRunId] = useState("");
  const [title, setTitle] = useState("");
  const action = useAction();
  const close = useCallback(() => setCreating(false), []);
  return (
    <>
      <ScreenTitle code="К4" title="Задания">
        <button className="primary" onClick={() => setCreating(true)}>
          Создать задание
        </button>
      </ScreenTitle>
      {action.feedback}
      <Resource value={r}>
        <Card
          title="Задания курсов"
          actions={
            <div className="filters">
              <label>
                Поиск
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Название задания"
                />
              </label>
              <label>
                Курс
                <select
                  value={courseId}
                  onChange={(e) => setCourseId(e.target.value)}
                >
                  <option value="">Все курсы</option>
                  {r.data?.courses.map((course) => (
                    <option key={course.id} value={course.id}>
                      {course.title}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          }
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Задание</th>
                  <th>Курс</th>
                  <th>Версия</th>
                  <th>Публикации</th>
                  <th>Поток для настройки</th>
                </tr>
              </thead>
              <tbody>
                {r.data?.homeworks
                  .filter(
                    (item) =>
                      (!courseId || item.course_id === courseId) &&
                      item.title.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((item) => (
                    <HomeworkDirectoryRow
                      key={item.id}
                      item={item}
                      course={
                        r.data!.courses.find(
                          (course) => course.id === item.course_id,
                        )?.title ?? "Курс"
                      }
                      runs={r.data!.course_runs.filter(
                        (run) => run.course_id === item.course_id,
                      )}
                    />
                  ))}
              </tbody>
            </table>
          </div>
          {r.data &&
            !r.data.homeworks.some(
              (item) =>
                (!courseId || item.course_id === courseId) &&
                item.title.toLowerCase().includes(search.toLowerCase()),
            ) && (
              <Empty>
                Заданий не найдено. Создайте задание или измените поиск.
              </Empty>
            )}
        </Card>
      </Resource>
      {creating && (
        <Modal title="Новое задание" close={close}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const run = r.data?.course_runs.find((run) => run.id === runId);
                if (!run) throw new Error("Выберите поток.");
                const fresh = (await ws.catalog()).course_runs.find(
                  (value) => value.id === runId,
                );
                if (!fresh) throw new Error("Поток недоступен.");
                const created = await ws.core.command(
                  "create_homework",
                  runId,
                  fresh.revision,
                  { title },
                );
                setCreating(false);
                window.location.hash = `/homework/${created.id}?run=${runId}`;
              });
            }}
          >
            <label>
              Курс и поток
              <select
                required
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
              >
                <option value="">Выберите поток</option>
                {r.data?.course_runs
                  .filter((run) => run.status === "active")
                  .map((run) => (
                    <option key={run.id} value={run.id}>
                      {
                        r.data!.courses.find(
                          (course) => course.id === run.course_id,
                        )?.title
                      }{" "}
                      · {run.title}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Название задания
              <input
                required
                maxLength={512}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <p className="muted">
              Задание сохраняется в курсе. На последнем шаге мастера задаются
              сроки публикации в выбранном потоке.
            </p>
            <div className="actions">
              <button type="button" onClick={close}>
                Отмена
              </button>
              <button className="primary" disabled={action.busy}>
                Создать задание
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
function HomeworkDirectoryRow({
  item,
  course,
  runs,
}: {
  item: W<"CoordinatorHomeworkItem">;
  course: string;
  runs: W<"CourseRunView">[];
}) {
  const [runId, setRunId] = useState(
    item.published_run_ids[0] ??
      runs.find((run) => run.status === "active")?.id ??
      runs[0]?.id ??
      "",
  );
  return (
    <tr>
      <td>
        <strong>{item.title}</strong>
        {!item.published_run_ids.length && <small>Черновик</small>}
      </td>
      <td>{course}</td>
      <td>{item.latest_version_number ?? "Не сохранена"}</td>
      <td>
        {item.published_run_ids.length
          ? item.published_run_ids
              .map((id) => runs.find((run) => run.id === id)?.title ?? "Поток")
              .join(", ")
          : "Не опубликовано"}
      </td>
      <td>
        <div className="actions">
          <select
            aria-label={`Поток: ${item.title}`}
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
          >
            {!runs.length && <option value="">Нет потоков</option>}
            {runs.map((run) => (
              <option key={run.id} value={run.id}>
                {run.title}
              </option>
            ))}
          </select>
          {runId && (
            <a href={`#/homework/${item.id}?run=${runId}`}>Настроить →</a>
          )}
        </div>
      </td>
    </tr>
  );
}
