import { useCallback, useEffect, useRef, useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, type W } from "../api/workspace";
import { useMenuCounts } from "../App";
import { Resource, go, useAction, useResource } from "../ui";
import { WorkspaceSearch } from "./WorkspaceSearch";
import { WorksList } from "./WorkspaceLists";
import { CabinetTabs, Modal } from "../workspace-ui";
import {
  Area,
  Btn,
  BtnRow,
  Callout,
  Card as DsCard,
  CardBody,
  CardFoot,
  CardHead,
  Chk,
  Crumbs,
  Empty as DsEmpty,
  Field,
  Inp,
  Kv,
  Main,
  OpPill,
  Pill,
  Seg,
  Sel,
  Tgl,
  Tile,
  Tiles,
  Topbar,
  cx,
  dayLong,
  dayShort,
  daysSince,
  plural,
} from "../ds";

const HOT_DAYS = 3;
const WEEK = 7 * 86_400_000;

function localDate(value?: string | null) {
  if (!value) return "";
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

type RunStats = {
  all: number;
  pool: number;
  stuck: number;
  review: number;
  accepted: number;
  students: number;
  homeworks: number;
  deadline?: string;
  upcoming: { title: string; deadline: string }[];
};

function useRunStats(ws: WorkspaceClient, run: W<"CourseRunView">) {
  return useResource(async (): Promise<RunStats> => {
    const [all, pool, review, accepted, members, homeworks] = await Promise.all(
      [
        ws.works({ course_run_id: run.id, limit: 1 }),
        ws.works({ course_run_id: run.id, state: "pending_review", limit: 50 }),
        ws.works({ course_run_id: run.id, state: "in_review", limit: 1 }),
        ws.works({ course_run_id: run.id, state: "passed", limit: 1 }),
        ws.assignments(run.id),
        ws.core.homeworks(run.id),
      ],
    );
    const now = Date.now();
    const upcoming = homeworks.items
      .filter(
        (h): h is typeof h & { submission_deadline: string } =>
          !!h.submission_deadline &&
          new Date(h.submission_deadline).getTime() >= now,
      )
      .map((h) => ({ title: h.title, deadline: h.submission_deadline }))
      .sort((a, b) => a.deadline.localeCompare(b.deadline));
    return {
      all: all.total,
      pool: pool.total,
      stuck: pool.items.filter(
        (w) => (daysSince(w.submitted_at) ?? 0) >= HOT_DAYS,
      ).length,
      review: review.total,
      accepted: accepted.total,
      students: members.items.length,
      homeworks: homeworks.items.length,
      deadline: upcoming[0]?.deadline,
      upcoming,
    };
  }, `run-stats:${run.id}:${run.revision}`);
}

/** К1 «Обзор» и К2 «Курсы» координатора; модалки К3 и К4. */
export function WorkspaceCatalog({
  ws,
  mode = "overview",
}: {
  ws: WorkspaceClient;
  mode?: "overview" | "courses" | "runs";
}) {
  const r = useResource(() => ws.catalog(), "catalog");
  const [modal, setModal] = useState<string>();
  const [editCourse, setEditCourse] = useState<W<"CourseView">>();
  const close = useCallback(() => setModal(undefined), []);
  const action = useAction();
  const [courseFilter, setCourseFilter] = useState("");
  const [status, setStatus] = useState("");
  const [runsCollapsed, setRunsCollapsed] = useState(false);
  const [stats, setStats] = useState<Record<string, RunStats>>({});
  const { setCounts } = useMenuCounts();
  useEffect(() => {
    if (r.data) setCounts({ courses: r.data.courses.length });
  }, [r.data, setCounts]);
  const report = useCallback(
    (id: string, value: RunStats) =>
      setStats((prev) =>
        prev[id] === value ? prev : { ...prev, [id]: value },
      ),
    [],
  );
  const runs = r.data?.course_runs ?? [];
  const courses = r.data?.courses ?? [];
  const activeRuns = runs.filter((run) => run.status === "active");
  const shownRuns = runs
    .filter(
      (run) =>
        (!status || run.status === status) &&
        (!courseFilter || run.course_id === courseFilter),
    )
    .sort((a, b) =>
      a.status === b.status ? 0 : a.status === "active" ? -1 : 1,
    );
  const known = Object.values(stats);
  const totals = activeRuns.reduce(
    (sum, run) => {
      const s = stats[run.id];
      if (!s) return sum;
      return {
        all: sum.all + s.all,
        review: sum.review + s.review,
        pool: sum.pool + s.pool,
        stuck: sum.stuck + s.stuck,
      };
    },
    { all: 0, review: 0, pool: 0, stuck: 0 },
  );
  const now = Date.now();
  const upcoming = activeRuns
    .flatMap((run) =>
      (stats[run.id]?.upcoming ?? []).map((u) => ({
        ...u,
        course: courses.find((c) => c.id === run.course_id)?.title ?? "Курс",
      })),
    )
    .sort((a, b) => a.deadline.localeCompare(b.deadline));
  const thisWeek = upcoming.filter(
    (u) => new Date(u.deadline).getTime() <= now + WEEK,
  ).length;
  const coursesWithWork = new Set(
    activeRuns
      .filter((run) => (stats[run.id]?.all ?? 0) > 0)
      .map((run) => run.course_id),
  ).size;
  const loadingStats = activeRuns.some((run) => !stats[run.id]);

  const courseSelect = (
    <Sel
      small
      aria-label="Курс"
      value={courseFilter}
      onChange={(e) => setCourseFilter(e.target.value)}
    >
      <option value="">Курс: все</option>
      {courses.map((c) => (
        <option key={c.id} value={c.id}>
          {c.title}
        </option>
      ))}
    </Sel>
  );

  if (mode === "runs")
    return (
      <>
        <Topbar
          title="Потоки"
          actions={
            <Btn size="s" variant="dark" onClick={() => setModal("run")}>
              + Создать поток
            </Btn>
          }
        />
        <Main data-screen="К1">
          {action.feedback}
          <Resource value={r}>
            {r.data && (
              <div className="stack">
                <DsCard>
                  <CardHead
                    title="Потоки"
                    sub={`Показаны ${shownRuns.length} из ${runs.length}, сначала активные`}
                  >
                    <BtnRow>
                      {courseSelect}
                      <Sel
                        small
                        aria-label="Статус"
                        value={status}
                        onChange={(e) => setStatus(e.target.value)}
                      >
                        <option value="">Статус: все</option>
                        <option value="active">Активные</option>
                        <option value="archived">Архив</option>
                      </Sel>
                      <Btn
                        size="s"
                        variant="link"
                        aria-expanded={!runsCollapsed}
                        onClick={() => setRunsCollapsed((v) => !v)}
                      >
                        {runsCollapsed ? "Развернуть" : "Свернуть"}
                      </Btn>
                    </BtnRow>
                  </CardHead>
                  {!runsCollapsed && (
                    <CardBody flush>
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Курс</th>
                            <th>Поток</th>
                            <th className="n">Студентов</th>
                            <th className="n">Ближайший дедлайн</th>
                            <th className="n">Сдано</th>
                            <th className="n">На ревью</th>
                            <th className="n">Зависло</th>
                            <th className="n">Зачтено</th>
                          </tr>
                        </thead>
                        <tbody>
                          {shownRuns.map((run) => (
                            <RunRow
                              key={run.id}
                              ws={ws}
                              run={run}
                              course={
                                courses.find((c) => c.id === run.course_id)
                                  ?.title ?? "Курс"
                              }
                              report={report}
                            />
                          ))}
                        </tbody>
                      </table>
                      {shownRuns.length === 0 && (
                        <DsEmpty
                          title="Потоков нет"
                          action={
                            <Btn
                              size="s"
                              variant="dark"
                              onClick={() => setModal("run")}
                            >
                              Создать поток
                            </Btn>
                          }
                        >
                          {runs.length
                            ? "По этим фильтрам потоков нет."
                            : "Поток всегда принадлежит курсу и открывает задания студентам."}
                        </DsEmpty>
                      )}
                    </CardBody>
                  )}
                </DsCard>
              </div>
            )}
          </Resource>
        </Main>
        {modal === "run" && (
          <Modal title="Новый поток" close={close} flush>
            <RunForm
              ws={ws}
              courses={courses}
              done={() => {
                close();
                r.refresh();
              }}
              cancel={close}
            />
          </Modal>
        )}
      </>
    );

  if (mode === "courses")
    return (
      <>
        <Topbar
          title="Курсы"
          actions={
            <Btn size="s" variant="dark" onClick={() => setModal("course")}>
              + Создать курс
            </Btn>
          }
        />
        <Main data-screen="К2">
          {action.feedback}
          <DsCard>
            <Resource value={r}>
              {r.data && (
                <CardBody flush>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Курс</th>
                        <th className="n">Заданий</th>
                        <th className="n">Потоков</th>
                        <th>Курс в Stepik</th>
                        <th className="r"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {courses.map((c) => (
                        <tr
                          key={c.id}
                          className={cx(
                            "is-link",
                            c.status === "archived" && "is-done",
                          )}
                          onClick={(e) => {
                            if ((e.target as HTMLElement).closest("a, button"))
                              return;
                            go(`/homeworks?course=${c.id}`);
                          }}
                        >
                          <td>
                            <div className="who">{c.title}</div>
                            {c.description && (
                              <div className="sub">{c.description}</div>
                            )}
                          </td>
                          <td className="n">
                            <HomeworkCount ws={ws} courseId={c.id} />
                          </td>
                          <td className="n">
                            {
                              runs.filter((run) => run.course_id === c.id)
                                .length
                            }
                          </td>
                          <td>
                            {c.stepik_url ? (
                              <a
                                className="mono"
                                href={c.stepik_url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {c.stepik_url.replace(/^https?:\/\//, "")}
                              </a>
                            ) : (
                              <span className="mono dim">—</span>
                            )}
                          </td>
                          <td className="r">
                            <Btn
                              size="s"
                              variant="quiet"
                              aria-label={`Редактировать курс «${c.title}»`}
                              title="Редактировать"
                              onClick={() => {
                                setEditCourse(c);
                                setModal("edit-course");
                              }}
                            >
                              ✎
                            </Btn>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {courses.length === 0 && (
                    <DsEmpty
                      title="Курсов пока нет"
                      action={
                        <Btn
                          size="s"
                          variant="dark"
                          onClick={() => setModal("course")}
                        >
                          Создать курс
                        </Btn>
                      }
                    >
                      Курс — верхний уровень: внутри него живут задания и
                      потоки.
                    </DsEmpty>
                  )}
                </CardBody>
              )}
            </Resource>
          </DsCard>
        </Main>
        {modal && (
          <CatalogModal
            ws={ws}
            modal={modal}
            course={editCourse}
            courses={courses}
            close={close}
            refresh={r.refresh}
          />
        )}
      </>
    );

  return (
    <>
      <Topbar center title="Обзор" lead={<WorkspaceSearch ws={ws} />} />
      <Main data-screen="К1">
        {action.feedback}
        <Resource value={r}>
          {r.data && (
            <div className="stack">
              {activeRuns.map((run) => (
                <RunStatsProbe key={run.id} ws={ws} run={run} report={report} />
              ))}
              <Tiles>
                <Tile
                  href="#/registry"
                  n={loadingStats ? "…" : totals.all}
                  l="домашек в активных потоках"
                  d={
                    coursesWithWork
                      ? `по ${coursesWithWork} ${plural(coursesWithWork, "курсу", "курсам", "курсам")}`
                      : `${activeRuns.length} ${plural(activeRuns.length, "активный поток", "активных потока", "активных потоков")}`
                  }
                />
                <Tile
                  href="#/registry?state=in_review"
                  n={loadingStats ? "…" : totals.review}
                  l="у ревьюеров прямо сейчас"
                  d="взяты из пула и проверяются"
                />
                <Tile
                  href="#/coord-pool"
                  n={loadingStats ? "…" : totals.pool}
                  alert={totals.pool > 0}
                  l="в пуле без ревьюера"
                  d={
                    totals.stuck
                      ? `${totals.stuck} ${plural(totals.stuck, "лежит", "лежат", "лежат")} дольше ${HOT_DAYS} дней`
                      : "ни одна не лежит дольше 3 дней"
                  }
                />
                <Tile
                  href="#/homeworks"
                  n={loadingStats ? "…" : thisWeek}
                  l={`${plural(thisWeek, "дедлайн", "дедлайна", "дедлайнов")} на этой неделе`}
                  d={
                    upcoming[0]
                      ? `ближайший ${dayShort(upcoming[0].deadline)}, ${upcoming[0].course}`
                      : "предстоящих сроков нет"
                  }
                />
              </Tiles>
              <WorksList ws={ws} coordinatorPool embedded />
              <WorksList ws={ws} embedded />

              <div className="row-2">
                <DsCard>
                  <CardHead title="Требует внимания" />
                  <CardBody tight>
                    {totals.stuck > 0 ? (
                      activeRuns
                        .filter((run) => (stats[run.id]?.stuck ?? 0) > 0)
                        .map((run) => (
                          <Kv
                            key={run.id}
                            ink
                            label={`${stats[run.id].stuck} ${plural(stats[run.id].stuck, "работа", "работы", "работ")} в пуле дольше ${HOT_DAYS} дней, поток «${run.title}»`}
                          >
                            <Btn
                              variant="link"
                              href={`#/coord-pool?run=${run.id}`}
                            >
                              Открыть пул
                            </Btn>
                          </Kv>
                        ))
                    ) : (
                      <p className="small dim">
                        {loadingStats
                          ? "Считаем работы в пулах…"
                          : "Зависших работ нет: всё берут вовремя."}
                      </p>
                    )}
                    {known.length > 0 &&
                      activeRuns
                        .filter((run) => (stats[run.id]?.homeworks ?? 1) === 0)
                        .map((run) => (
                          <Kv
                            key={`hw:${run.id}`}
                            ink
                            label={`В потоке «${run.title}» нет опубликованных заданий`}
                          >
                            <Btn variant="link" href="#/homeworks">
                              Задания
                            </Btn>
                          </Kv>
                        ))}
                  </CardBody>
                </DsCard>
                <DsCard>
                  <CardHead title="Ближайшие дедлайны" />
                  <CardBody tight>
                    {upcoming.slice(0, 5).map((u, i) => (
                      <Kv key={i} ink label={`${u.course}, ${u.title}`}>
                        <Pill
                          tone={
                            new Date(u.deadline).getTime() - now <
                            3 * 86_400_000
                              ? "late"
                              : undefined
                          }
                        >
                          {dayShort(u.deadline)}
                        </Pill>
                      </Kv>
                    ))}
                    {upcoming.length === 0 && (
                      <p className="small dim">
                        {loadingStats
                          ? "Собираем сроки…"
                          : "Предстоящих дедлайнов нет."}
                      </p>
                    )}
                  </CardBody>
                </DsCard>
              </div>
            </div>
          )}
        </Resource>
      </Main>
      {modal && (
        <CatalogModal
          ws={ws}
          modal={modal}
          course={editCourse}
          courses={courses}
          close={close}
          refresh={r.refresh}
        />
      )}
    </>
  );
}

/** Сбор метрик потока без разметки: «Обзор» считает плитки без таблицы. */
function RunStatsProbe({
  ws,
  run,
  report,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  report: (id: string, value: RunStats) => void;
}) {
  const r = useRunStats(ws, run);
  useEffect(() => {
    if (r.data) report(run.id, r.data);
  }, [r.data, run.id, report]);
  return null;
}

function RunRow({
  ws,
  run,
  course,
  report,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  course: string;
  report: (id: string, value: RunStats) => void;
}) {
  const r = useRunStats(ws, run);
  useEffect(() => {
    if (r.data) report(run.id, r.data);
  }, [r.data, run.id, report]);
  const s = r.data;
  const hot =
    !!s?.deadline &&
    new Date(s.deadline).getTime() - Date.now() < 3 * 86_400_000;
  const href = `#/registry?run=${run.id}`;
  return (
    <tr
      className={cx(
        "is-link",
        hot && "is-hot",
        run.status === "archived" && "is-done",
      )}
      onClick={() => {
        window.location.hash = href;
      }}
    >
      <td>
        <div className="who">{course}</div>
        <div className="sub">
          {s
            ? `${s.homeworks} ${plural(s.homeworks, "задание", "задания", "заданий")}`
            : "…"}
        </div>
      </td>
      <td>
        {run.title}
        {run.status === "archived" && <span className="caption"> · архив</span>}
      </td>
      {s ? (
        <>
          <td className="n">{s.students}</td>
          <td className={cx("n", hot && "late")}>
            {s.deadline ? dayShort(s.deadline) : "—"}
          </td>
          <td className="n">{s.all}</td>
          <td className="n">{s.review}</td>
          <td className={cx("n", s.stuck > 0 && "bad")}>{s.stuck}</td>
          <td className="n">{s.accepted}</td>
        </>
      ) : (
        <td className="n dim" colSpan={6}>
          {r.error ? "не удалось посчитать" : "…"}
        </td>
      )}
    </tr>
  );
}

function CatalogModal({
  ws,
  modal,
  course,
  courses,
  close,
  refresh,
}: {
  ws: WorkspaceClient;
  modal: string;
  course?: W<"CourseView">;
  courses: W<"CourseView">[];
  close: () => void;
  refresh: () => void;
}) {
  const done = () => {
    close();
    refresh();
  };
  if (modal === "course" || modal === "edit-course")
    return (
      <Modal title={course ? "Курс" : "Новый курс"} close={close} flush>
        <CourseForm
          ws={ws}
          course={modal === "edit-course" ? course : undefined}
          done={done}
          cancel={close}
        />
      </Modal>
    );
  return (
    <Modal title="Новый поток" close={close} flush>
      <RunForm
        ws={ws}
        courses={courses.filter((c) => c.status === "active")}
        done={done}
        cancel={close}
      />
    </Modal>
  );
}

/** К3: форма курса. */
function CourseForm({
  ws,
  done,
  cancel,
  course,
}: {
  ws: WorkspaceClient;
  done: () => void;
  cancel: () => void;
  course?: W<"CourseView">;
}) {
  const [title, setTitle] = useState(course?.title ?? "");
  const [description, setDescription] = useState(course?.description ?? "");
  const [stepikUrl, setStepikUrl] = useState(course?.stepik_url ?? "");
  const owner = course?.owner_id ?? "";
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
      <div className="card__body">
        {action.feedback}
        <Field label="Название">
          <Inp
            required
            maxLength={512}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Field>
        <Field label="Короткое описание, видно студентам и ревьюерам">
          <Area
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field
          label="Ссылка на курс в Stepik"
          opt="необязательно"
          hint="Нужна, чтобы открыть курс на Stepik из справочника."
        >
          <Inp
            mono
            type="url"
            value={stepikUrl}
            onChange={(e) => setStepikUrl(e.target.value)}
            placeholder="https://stepik.org/course/…"
          />
        </Field>
      </div>
      <div className="card__foot card__foot--end">
        <BtnRow>
          <Btn variant="quiet" onClick={cancel}>
            Отмена
          </Btn>
          <Btn variant="pri" type="submit" disabled={action.busy}>
            {course ? "Сохранить курс" : "Создать курс"}
          </Btn>
        </BtnRow>
      </div>
    </form>
  );
}

/** К4: форма потока. */
/** Название потока по датам, когда координатор его не задал. */
function runTitleFromDates(start: string, end: string) {
  const from = start ? new Date(start) : null;
  const to = end ? new Date(end) : null;
  const label = (at: Date) =>
    at.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  if (!from) return "Поток";
  const a = label(from);
  const b = to ? label(to) : a;
  return a === b ? a : `${a} — ${b}`;
}

function RunForm({
  ws,
  courses,
  course: fixedCourse,
  done,
  cancel,
  run,
}: {
  ws: WorkspaceClient;
  courses?: W<"CourseView">[];
  course?: W<"CourseView">;
  done: () => void;
  cancel: () => void;
  run?: W<"CourseRunView">;
}) {
  const [courseId, setCourseId] = useState(
    fixedCourse?.id ?? run?.course_id ?? "",
  );
  const [title, setTitle] = useState(run?.title ?? "");
  const [start, setStart] = useState(localDate(run?.starts_at));
  const [end, setEnd] = useState(localDate(run?.ends_at));
  const [zone, setZone] = useState(run?.timezone ?? "Europe/Moscow");
  // Порядок рекомендаций один для всех потоков: свои студенты сначала.
  const priority = run?.priority ?? "assigned";
  const action = useAction();
  const course = fixedCourse ?? courses?.find((c) => c.id === courseId);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void action.run(async () => {
          if (!course) throw new Error("Выберите курс.");
          await ws.command(
            run ? "update_course_run" : "create_course_run",
            run?.id ?? course.id,
            run?.revision ?? course.revision,
            {
              title: title.trim() || runTitleFromDates(start, end),
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
      <div className="card__body">
        {action.feedback}
        <Field label="Курс">
          {fixedCourse || run ? (
            <Inp disabled readOnly value={course?.title ?? ""} />
          ) : (
            <Sel
              required
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
            >
              <option value="">Выберите курс</option>
              {courses?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </Sel>
          )}
        </Field>
        <div className="date-range">
          <Field label="Дата начала">
            <Inp
              required
              type="datetime-local"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
          <Field label="Дата окончания">
            <Inp
              required
              type="datetime-local"
              min={start}
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </Field>
        </div>
        <Field
          label="Название потока"
          opt="необязательно"
          hint="Например, «Февраль 2026». Без названия поток называется по датам."
        >
          <Inp value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Часовой пояс">
          <Inp
            mono
            required
            value={zone}
            onChange={(e) => setZone(e.target.value)}
          />
        </Field>
        <Callout tone="warn">
          <p>
            Студентов добавлять не нужно. Зачисление происходит при первом
            переходе по ссылке задания: платформа узнаёт студента по Stepik ID.
          </p>
        </Callout>
      </div>
      <div className="card__foot card__foot--end">
        <BtnRow>
          <Btn variant="quiet" onClick={cancel}>
            Отмена
          </Btn>
          <Btn variant="pri" type="submit" disabled={action.busy}>
            {run ? "Сохранить поток" : "Создать поток"}
          </Btn>
        </BtnRow>
      </div>
    </form>
  );
}

/** Карточка потока на странице потока: даты, приоритет, архив. Экрана в паке нет. */
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
    <DsCard>
      <CardHead title="Поток" sub={run.title}>
        <OpPill status={run.status} />
      </CardHead>
      <CardBody tight>
        <Kv label="Начало">{dayLong(run.starts_at)}</Kv>
        <Kv label="Окончание">{dayLong(run.ends_at)}</Kv>
        <Kv label="Часовой пояс">
          <span className="mono">{run.timezone}</span>
        </Kv>
        <Kv label="Работ в потоке">
          {work.data ? (
            <Btn variant="link" href={`#/registry?run=${run.id}`}>
              {work.data.total}
            </Btn>
          ) : (
            "…"
          )}
        </Kv>
        <Kv label="Порядок рекомендаций">
          <BtnRow>
            <Seg
              value={priority}
              onChange={setPriority}
              options={[
                { value: "assigned", label: "Свои студенты" },
                { value: "deadline", label: "Ближайший срок" },
              ]}
            />
            <Btn
              size="s"
              disabled={action.busy || priority === run.priority}
              onClick={() =>
                void action.run(async () => {
                  await ws.command(
                    "set_run_priority",
                    run.id,
                    run.priority_revision,
                    {
                      priority,
                    },
                  );
                  refresh();
                })
              }
            >
              Сохранить
            </Btn>
          </BtnRow>
        </Kv>
      </CardBody>
      {!!action.error && <CardBody>{action.feedback}</CardBody>}
      <CardFoot>
        <BtnRow>
          <Btn size="s" onClick={() => setEditing(true)}>
            Редактировать поток
          </Btn>
          <Btn size="s" variant="quiet" href={`#/assignments/${run.id}`}>
            Ревьюеры и студенты
          </Btn>
        </BtnRow>
        <Btn
          size="s"
          variant="quiet"
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
                  {
                    reason: "Архивирование потока координатором",
                  },
                );
              refresh();
            })
          }
        >
          {run.status === "archived"
            ? "Восстановить поток"
            : "Архивировать поток"}
        </Btn>
      </CardFoot>
      {editing && catalog.data && (
        <Modal title="Поток" close={() => setEditing(false)} flush>
          <RunForm
            ws={ws}
            course={catalog.data.courses.find((c) => c.id === run.course_id)!}
            run={run}
            cancel={() => setEditing(false)}
            done={() => {
              setEditing(false);
              refresh();
            }}
          />
        </Modal>
      )}
    </DsCard>
  );
}

const PREFERENCES_AUTOSAVE_DELAY = 1000;

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
    <>
      {r.loading && <Topbar title="Кабинет" />}
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
    </>
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
  const defaults = { deadline: true, revision: true, pool: false };
  const [showPool, setShowPool] = useState(initial?.show_pool ?? true);
  const [notifications, setNotifications] = useState(
    initial?.notifications ?? defaults,
  );
  const [selected, setSelected] = useState(initial?.course_run_ids ?? []);
  const [from, setFrom] = useState(localDate(initial?.absent_from));
  const [to, setTo] = useState(localDate(initial?.absent_until));
  const action = useAction();
  const [savedNote, setSavedNote] = useState("");
  /* Настройки сохраняются сами через секунду после последней правки: отдельных
     кнопок «Сохранить» и «Отменить» на экране нет. */
  const payload = JSON.stringify({
    course_run_ids: selected,
    show_pool: showPool,
    notifications,
    absent_from: from ? new Date(from).toISOString() : null,
    absent_until: from && to ? new Date(to).toISOString() : null,
  });
  const known = useRef(payload);
  useEffect(() => {
    if (payload === known.current) return;
    if (from && !to) return;
    const timer = window.setTimeout(() => {
      known.current = payload;
      setSavedNote("Сохраняем…");
      void action.run(async () => {
        await ws.command(
          "save_preferences",
          session.user_id,
          preferences.revision,
          {
            ...JSON.parse(payload),
            planned_minutes: initial?.planned_minutes ?? 0,
            until_at: initial?.until_at ?? new Date().toISOString(),
          },
        );
        setSavedNote("Настройки сохранены");
        refresh();
      });
    }, PREFERENCES_AUTOSAVE_DELAY);
    return () => window.clearTimeout(timer);
  }, [payload, from, to]);
  const courses = catalog.courses
    .map((course) => ({
      course,
      runs: catalog.course_runs.filter(
        (run) => run.course_id === course.id && run.status === "active",
      ),
    }))
    .filter(({ runs }) => runs.length > 0);
  return (
    <>
      <Topbar
        title="Кабинет"
        actions={
          savedNote && (
            <span className="caption" role="status">
              {savedNote}
            </span>
          )
        }
      />
      <Main data-screen="Р3">
        <CabinetTabs on="preferences" />
        {action.feedback}
        <form id="reviewer-preferences" onSubmit={(e) => e.preventDefault()}>
          <div className="row-side">
            <DsCard>
              <CardHead title="Курсы, которые готов проверять" />
              <CardBody>
                {courses.length === 0 ? (
                  <DsEmpty title="Активных потоков нет">
                    Когда координатор откроет поток, курс появится здесь.
                  </DsEmpty>
                ) : (
                  <div className="stack--chk">
                    {courses.map(({ course, runs }) => (
                      <Chk
                        key={course.id}
                        row
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
                        trail={<CoursePoolCount ws={ws} runs={runs} />}
                      >
                        {course.title}
                      </Chk>
                    ))}
                  </div>
                )}
              </CardBody>
            </DsCard>
            <div className="stack">
              <DsCard>
                <CardHead title="Доступность" />
                <CardBody>
                  <label className="tgl-row">
                    <div>
                      <div id="pref-show-pool">
                        Показывать мне работы из пула
                      </div>
                      <div className="caption">
                        выключите, если временно не берёте новое
                      </div>
                    </div>
                    <Tgl
                      aria-labelledby="pref-show-pool"
                      checked={showPool}
                      onChange={(e) => setShowPool(e.target.checked)}
                    />
                  </label>
                  <div className="field field--after">
                    <span className="field__lbl" id="pref-absence">
                      Отпуск или отсутствие
                    </span>
                    <div className="date-range">
                      <Inp
                        aria-label="Начало отсутствия"
                        type="datetime-local"
                        value={from}
                        onChange={(e) => {
                          setFrom(e.target.value);
                          if (!e.target.value) setTo("");
                        }}
                      />
                      <span className="date-range__dash" aria-hidden="true">
                        —
                      </span>
                      <Inp
                        aria-label="Окончание отсутствия"
                        type="datetime-local"
                        required={!!from}
                        min={from}
                        value={to}
                        onChange={(e) => setTo(e.target.value)}
                      />
                    </div>
                    <span className="field__hint">
                      На это время новые работы не берём, уже взятые остаются за
                      вами.
                    </span>
                  </div>
                </CardBody>
              </DsCard>
              <DsCard>
                <CardHead title="Уведомления" />
                <CardBody>
                  <div className="stack--chk stack--chk-tight">
                    {(
                      [
                        ["deadline", "Работа, которую я взял, близка к сроку"],
                        ["revision", "Студент прислал правки"],
                        [
                          "pool",
                          "В пуле по моим курсам появилось что-то новое",
                        ],
                      ] as const
                    ).map(([key, label]) => (
                      <Chk
                        key={key}
                        checked={notifications[key]}
                        onChange={(e) =>
                          setNotifications((current) => ({
                            ...current,
                            [key]: e.target.checked,
                          }))
                        }
                      >
                        {label}
                      </Chk>
                    ))}
                  </div>
                </CardBody>
              </DsCard>
            </div>
          </div>
        </form>
      </Main>
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
    <span className="caption">
      {r.data !== undefined
        ? `в пуле ${r.data} ${plural(r.data, "работа", "работы", "работ")}`
        : r.error
          ? "число работ недоступно"
          : "…"}
    </span>
  );
}

/** Закрепления студентов за ревьюерами и напоминания. Экрана в паке нет. */
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
  const run = r.data?.catalog.course_runs.find((x) => x.id === runId);
  const course = r.data?.catalog.courses.find((c) => c.id === run?.course_id);
  return (
    <>
      <Topbar
        crumbs={
          <Crumbs
            back={`#/courses/${runId}`}
            items={[
              { href: "#/courses", label: "Курсы" },
              ...(course ? [{ href: "#/courses", label: course.title }] : []),
              ...(run
                ? [{ href: `#/courses/${runId}`, label: run.title }]
                : []),
            ]}
            current="Студенты и ревьюеры"
          />
        }
        title="Студенты и ревьюеры"
      />
      <Main>
        {action.feedback}
        <Resource value={r}>
          {r.data && (
            <div className="stack">
              <Callout>
                <p>
                  Основной ревьюер закрепляется за студентом в потоке. Участники
                  конкретной проверки могут быть другими: коллеги сохраняют
                  доступ к работе, а разовое участие не меняет закрепление.
                </p>
              </Callout>
              <DsCard>
                <CardHead title="Основной ревьюер студента" />
                {r.data.assignments.items.length === 0 ? (
                  <DsEmpty title="В потоке пока нет студентов">
                    Студенты появятся после первого перехода по ссылке задания.
                  </DsEmpty>
                ) : (
                  <CardBody flush>
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>Студент</th>
                          <th>Ревьюер</th>
                          <th className="r"></th>
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
                  </CardBody>
                )}
              </DsCard>
              <div className="row-2">
                <DsCard>
                  <CardHead title="Добавить участника" />
                  <CardBody>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void action.run(async () => {
                          const current = (await ws.catalog()).course_runs.find(
                            (x) => x.id === runId,
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
                      <Field label="Роль в потоке">
                        <Sel
                          value={kind}
                          onChange={(e) => {
                            setKind(e.target.value as typeof kind);
                            setMember("");
                          }}
                        >
                          <option value="student">Студент</option>
                          <option value="reviewer">Ревьюер</option>
                        </Sel>
                      </Field>
                      <Field label="Участник">
                        <Sel
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
                        </Sel>
                      </Field>
                      <BtnRow>
                        <Btn size="s" type="submit" disabled={action.busy}>
                          Добавить
                        </Btn>
                      </BtnRow>
                    </form>
                  </CardBody>
                </DsCard>
                <DsCard>
                  <CardHead title="Напомнить ревьюерам" />
                  <CardBody>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        void action.run(async () => {
                          const current = (await ws.catalog()).course_runs.find(
                            (x) => x.id === runId,
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
                      <Field group label="Кому">
                        <div className="stack--chk stack--chk-tight">
                          {r.data.people.items
                            .filter((p) => p.roles.includes("reviewer"))
                            .map((p) => (
                              <Chk
                                key={p.id}
                                checked={recipients.includes(p.id)}
                                onChange={(e) =>
                                  setRecipients(
                                    e.target.checked
                                      ? [...recipients, p.id]
                                      : recipients.filter((v) => v !== p.id),
                                  )
                                }
                              >
                                {p.display_name}
                              </Chk>
                            ))}
                        </div>
                      </Field>
                      <Field label="Сообщение">
                        <Area
                          required
                          value={message}
                          onChange={(e) => setMessage(e.target.value)}
                        />
                      </Field>
                      <BtnRow>
                        <Btn
                          size="s"
                          variant="dark"
                          type="submit"
                          disabled={action.busy || !recipients.length}
                        >
                          Отправить напоминание
                        </Btn>
                      </BtnRow>
                    </form>
                  </CardBody>
                </DsCard>
              </div>
            </div>
          )}
        </Resource>
      </Main>
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
      <td className="mono">{value.student_name}</td>
      <td>
        <Sel
          small
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
        </Sel>
        {action.feedback}
      </td>
      <td className="r">
        <Btn
          size="s"
          disabled={action.busy || reviewer === (value.reviewer_id ?? "")}
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
        </Btn>
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
  return <>{r.data ? r.data.items.length : "…"}</>;
}

/** Список заданий всех курсов. В паке экрана нет, собран по образцу К2. */
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
  // Задания открываются с курса, поэтому фильтр берётся из адреса.
  const [courseId, setCourseId] = useState(
    () =>
      new URLSearchParams(window.location.hash.split("?")[1] ?? "").get(
        "course",
      ) ?? "",
  );
  const [search, setSearch] = useState("");
  const action = useAction();
  const { setCounts } = useMenuCounts();
  useEffect(() => {
    if (r.data) setCounts({ homeworks: r.data.homeworks.length });
  }, [r.data, setCounts]);
  const shown =
    r.data?.homeworks.filter(
      (item) =>
        (!courseId || item.course_id === courseId) &&
        item.title.toLowerCase().includes(search.toLowerCase()),
    ) ?? [];
  return (
    <>
      <Topbar
        center
        title="Задания"
        lead={
          <Inp
            small
            className="inp--w-200"
            aria-label="Поиск"
            placeholder="Название задания"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        }
        actions={
          <Btn
            size="s"
            variant="dark"
            href={`#/homework-new${courseId ? `?course=${courseId}` : ""}`}
          >
            + Новое задание
          </Btn>
        }
      />
      <Main>
        {action.feedback}
        <DsCard>
          <CardHead
            title="Задания курсов"
            sub={
              r.data
                ? `Показаны ${shown.length} из ${r.data.homeworks.length}`
                : undefined
            }
          >
            <Sel
              small
              aria-label="Курс"
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
            >
              <option value="">Курс: все</option>
              {r.data?.courses.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </Sel>
          </CardHead>
          <Resource value={r}>
            <CardBody flush>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Задание</th>
                    <th>Курс</th>
                    <th className="n">Версия</th>
                    <th>Публикации</th>
                    <th className="r">Настройка</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((item) => (
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
              {r.data && shown.length === 0 && (
                <DsEmpty
                  title="Заданий не найдено"
                  action={
                    <Btn
                      size="s"
                      variant="dark"
                      href={`#/homework-new${courseId ? `?course=${courseId}` : ""}`}
                    >
                      + Новое задание
                    </Btn>
                  }
                >
                  Создайте задание или измените поиск.
                </DsEmpty>
              )}
            </CardBody>
          </Resource>
        </DsCard>
      </Main>
    </>
  );
}
/**
 * Создание задания. Задание принадлежит курсу, поэтому спрашиваем только курс и
 * название: поток подставляется сам, активный поток курса всегда один.
 */
export function WorkspaceHomeworkNew({ ws }: { ws: WorkspaceClient }) {
  const r = useResource(() => ws.catalog(), "catalog");
  const action = useAction();
  const [courseId, setCourseId] = useState(
    () =>
      new URLSearchParams(window.location.hash.split("?")[1] ?? "").get(
        "course",
      ) ?? "",
  );
  const [title, setTitle] = useState("");
  const runs = r.data?.course_runs ?? [];
  const activeRun = runs.find(
    (run) => run.course_id === courseId && run.status === "active",
  );
  return (
    <>
      <Topbar
        crumbs={
          <Crumbs
            back="#/courses"
            items={[{ href: "#/courses", label: "Курсы" }]}
            current="Новое задание"
          />
        }
        title="Новое задание"
      />
      <Main>
        <Resource value={r}>
          {r.data && (
            <DsCard>
              <CardHead
                title="Задание"
                sub="Условие и критерии заполняются на следующем шаге"
              />
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void action.run(async () => {
                    if (!activeRun)
                      throw new Error(
                        "У курса нет активного потока. Создайте поток на странице «Потоки».",
                      );
                    const fresh = (await ws.catalog()).course_runs.find(
                      (value) => value.id === activeRun.id,
                    );
                    if (!fresh) throw new Error("Поток недоступен.");
                    const created = await ws.core.command(
                      "create_homework",
                      activeRun.id,
                      fresh.revision,
                      { title },
                    );
                    go(`/homework/${created.id}?run=${activeRun.id}`);
                  });
                }}
              >
                <CardBody>
                  {action.feedback}
                  <Field label="Курс">
                    <Sel
                      required
                      value={courseId}
                      onChange={(e) => setCourseId(e.target.value)}
                    >
                      <option value="">Выберите курс</option>
                      {r.data.courses.map((course) => (
                        <option key={course.id} value={course.id}>
                          {course.title}
                        </option>
                      ))}
                    </Sel>
                  </Field>
                  <Field
                    label="Название задания"
                    hint={
                      courseId && !activeRun
                        ? "У курса нет активного потока: задание некуда опубликовать."
                        : undefined
                    }
                  >
                    <Inp
                      required
                      maxLength={512}
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                    />
                  </Field>
                </CardBody>
                <div className="card__foot card__foot--end">
                  <BtnRow>
                    <Btn variant="quiet" href="#/courses">
                      Отмена
                    </Btn>
                    <Btn
                      variant="pri"
                      type="submit"
                      disabled={action.busy || !activeRun}
                    >
                      Создать задание
                    </Btn>
                  </BtnRow>
                </div>
              </form>
            </DsCard>
          )}
        </Resource>
      </Main>
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
  /* Задание настраивается для курса целиком. Поток нужен только адресу и
     берётся сам: активный поток курса всегда один. */
  const runId =
    runs.find((run) => run.status === "active")?.id ??
    item.published_run_ids[0] ??
    runs[0]?.id ??
    "";
  const href = `#/homework/${item.id}${runId ? `?run=${runId}` : ""}`;
  return (
    <tr>
      <td>
        <a className="who" href={href}>
          {item.title}
        </a>
        {!item.published_run_ids.length && <div className="sub">Черновик</div>}
      </td>
      <td>{course}</td>
      <td className="n">{item.latest_version_number ?? "—"}</td>
      <td>
        {item.published_run_ids.length
          ? item.published_run_ids
              .map((id) => runs.find((run) => run.id === id)?.title ?? "Поток")
              .join(", ")
          : "Не опубликовано"}
      </td>
      <td className="r">
        <BtnRow end>
          <Btn size="s" href={href}>
            Настроить →
          </Btn>
        </BtnRow>
      </td>
    </tr>
  );
}
