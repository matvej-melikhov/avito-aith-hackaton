import { StudentWorks } from "./StudentWorks";
import { ReviewerQueue } from "./ReviewerQueue";
import {
  useCallback,
  useEffect,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import type { Role } from "../api/client";
import { WorkspaceClient, type W } from "../api/workspace";
import { useMenuCounts } from "../App";
import { ErrorBox, Resource, go, useAction, useResource } from "../ui";
import { CabinetTabs, ExportMonitor, Modal } from "../workspace-ui";
import {
  Area,
  Btn,
  BtnRow,
  Callout,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Chk,
  Crumbs,
  Empty,
  Field,
  Inp,
  Kv,
  Main,
  Pill,
  Seg,
  Sel,
  St,
  Tab,
  Tabs,
  Tile,
  Tiles,
  Topbar,
  cx,
  dayShort,
  days,
  daysSince,
  isClosed,
  num,
  plural,
  short,
} from "../ds";

const LIMIT = 20;
const HOT_DAYS = 3;

export function WorkspaceWorks(props: {
  ws: WorkspaceClient;
  role: Role;
  coordinatorPool?: boolean;
  reviewerPool?: boolean;
}) {
  return props.role === "student" ? (
    <StudentWorks ws={props.ws} />
  ) : props.role === "reviewer" ? (
    <ReviewerQueue ws={props.ws} pool={props.reviewerPool} />
  ) : (
    <WorksList {...props} />
  );
}

const REGISTRY_TABS: [string, string][] = [
  ["", "Все"],
  ["draft", "Черновик"],
  ["pending_review", "Сдана"],
  ["in_review", "На ревью"],
  ["needs_changes", "Нужны правки"],
  ["passed", "Зачтена"],
  ["failed", "Не зачтена"],
];

function hashParam(name: string) {
  return (
    new URLSearchParams(window.location.hash.split("?")[1] ?? "").get(name) ??
    ""
  );
}

/**
 * К7 «Пул проверок» и К8 «Домашки потока» координатора. На «Обзоре» обе
 * таблицы показываются встроенно, без своей шапки экрана.
 */
export function WorksList({
  ws,
  coordinatorPool = false,
  embedded = false,
}: {
  ws: WorkspaceClient;
  role?: Role;
  coordinatorPool?: boolean;
  embedded?: boolean;
}) {
  const pool = coordinatorPool;
  const [query, setQuery] = useState(hashParam("q"));
  const [search, setSearch] = useState(hashParam("q"));
  const [run, setRun] = useState(hashParam("run"));
  const [state, setState] = useState(hashParam("state"));
  const [hotOnly, setHotOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [reminding, setReminding] = useState(false);
  const params = {
    q: search,
    course_run_id: run || undefined,
    state: pool ? "pending_review" : state,
    view: "all",
    offset,
    limit: LIMIT,
  };
  const r = useResource(() => ws.works(params), JSON.stringify(params));
  const catalog = useResource(() => ws.catalog(), "catalog");
  const action = useAction();
  const closeExport = useCallback(() => setExporting(false), []);
  const closeRemind = useCallback(() => setReminding(false), []);
  const { setCounts } = useMenuCounts();
  useEffect(() => {
    if (r.data && !search && !run && (pool || !state))
      setCounts(
        pool ? { coordPool: r.data.total } : { registry: r.data.total },
      );
  }, [r.data, search, run, state, pool, setCounts]);

  const currentRun = catalog.data?.course_runs.find((x) => x.id === run);
  const course = catalog.data?.courses.find(
    (c) => c.id === currentRun?.course_id,
  );
  const items = (r.data?.items ?? []).map((w) => ({
    ...w,
    waited: daysSince(w.submitted_at) ?? 0,
  }));
  const shown =
    pool && hotOnly ? items.filter((w) => w.waited >= HOT_DAYS) : items;
  const hotCount = items.filter((w) => w.waited >= HOT_DAYS).length;
  const averageWait = items.length
    ? items.reduce((sum, w) => sum + w.waited, 0) / items.length
    : null;

  const Frame = ({
    screen,
    children,
  }: {
    screen: string;
    children: ReactNode;
  }) =>
    embedded ? <>{children}</> : <Main data-screen={screen}>{children}</Main>;

  async function openReview(w: W<"WorkItem">) {
    if (
      w.review_iteration_id &&
      w.review_submission_version_id === w.submission_version_id
    ) {
      go(`/reviews/${w.review_iteration_id}`);
      return;
    }
    const history = await ws.core.submission(w.submission_id);
    const current = history.versions.find(
      (v) => v.id === w.submission_version_id,
    );
    if (!current) throw new Error("Версия недоступна.");
    const result = await ws.command(
      "open_work",
      w.submission_id,
      w.submission_revision,
      { submission_version_id: current.id },
    );
    go(`/reviews/${result.id}`);
  }

  function rowOpen(e: MouseEvent, w: W<"WorkItem">) {
    if ((e.target as HTMLElement).closest("button, a, select, input")) return;
    if (!action.busy && w.submission_version_id)
      void action.run(() => openReview(w));
  }

  const runSelect = (
    <Sel
      small
      aria-label="Поток"
      value={run}
      onChange={(e) => {
        setRun(e.target.value);
        setOffset(0);
      }}
    >
      <option value="">Поток: все</option>
      {catalog.data?.course_runs.map((x) => (
        <option key={x.id} value={x.id}>
          {x.title}
        </option>
      ))}
    </Sel>
  );
  const crumbs = (
    <Crumbs
      back="#/dashboard"
      items={[
        { href: "#/courses", label: "Курсы" },
        ...(course ? [{ href: "#/courses", label: course.title }] : []),
      ]}
      current={currentRun?.title ?? "Все потоки"}
    />
  );
  const foot = r.data && (
    <CardFoot>
      <span>
        Показаны {r.data.total ? offset + 1 : 0}–
        {Math.min(offset + LIMIT, r.data.total)} из {r.data.total}
      </span>
      {r.data.total > LIMIT && (
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
      )}
    </CardFoot>
  );

  if (pool)
    return (
      <>
        {!embedded && (
          <Topbar
            crumbs={crumbs}
            title="Пул проверок"
            actions={
              <Btn
                size="s"
                variant="dark"
                disabled={!currentRun}
                title={currentRun ? undefined : "Сначала выберите поток"}
                onClick={() => setReminding(true)}
              >
                Напомнить ревьюерам
              </Btn>
            }
          />
        )}
        <Frame screen="К7">
          {action.feedback}
          <div className="stack">
            {embedded && <h2 className="section-title">Пул проверок</h2>}
            {r.data && !embedded && (
              <Tiles>
                <Tile
                  n={r.data.total}
                  l={`${plural(r.data.total, "работа ждёт", "работы ждут", "работ ждут")} ревьюера`}
                  d={
                    currentRun
                      ? `в потоке «${currentRun.title}»`
                      : "по всем потокам"
                  }
                />
                <Tile
                  n={hotCount}
                  alert={hotCount > 0}
                  l={`${plural(hotCount, "лежит", "лежат", "лежат")} дольше ${HOT_DAYS} дней`}
                  d={
                    r.data.total > items.length
                      ? `среди показанных ${items.length}`
                      : "по всем работам в пуле"
                  }
                />
                <Tile
                  n={averageWait == null ? "—" : num(averageWait, 1)}
                  l="дней ждут в среднем"
                  d="от сдачи до сегодняшнего дня"
                />
              </Tiles>
            )}
            <Card>
              <CardHead title="Работы без ревьюера">
                <BtnRow>
                  {runSelect}
                  <Btn
                    size="s"
                    variant="quiet"
                    aria-pressed={hotOnly}
                    onClick={() => setHotOnly((v) => !v)}
                  >
                    {hotOnly ? "Показать все" : "Только зависшие"}
                  </Btn>
                </BtnRow>
              </CardHead>
              <Resource value={r}>
                {r.data && (
                  <>
                    <CardBody flush>
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>Студент</th>
                            <th>Задание</th>
                            <th className="n">Сдана</th>
                            <th className="n">В пуле</th>
                            <th className="n">Попытка</th>
                            <th className="r"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {shown.map((w) => (
                            <tr
                              key={w.submission_id}
                              className={cx(
                                "is-link",
                                w.waited >= HOT_DAYS && "is-hot",
                              )}
                              onClick={(e) => rowOpen(e, w)}
                            >
                              <td className="n mono">{w.student_name}</td>
                              <td>
                                <div className="who">{w.title}</div>
                                {!currentRun && (
                                  <div className="sub">
                                    {w.course_run_title}
                                  </div>
                                )}
                              </td>
                              <td className="n">{dayShort(w.submitted_at)}</td>
                              <td className="n">
                                {w.waited >= HOT_DAYS ? (
                                  <Pill tone="late">{days(w.waited)}</Pill>
                                ) : (
                                  days(w.waited)
                                )}
                              </td>
                              <td className="n">{w.attempt}</td>
                              <td className="r">
                                <Btn
                                  size="s"
                                  variant="quiet"
                                  disabled={
                                    action.busy || !w.submission_version_id
                                  }
                                  onClick={() =>
                                    void action.run(() => openReview(w))
                                  }
                                >
                                  Открыть проверку
                                </Btn>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {shown.length === 0 && (
                        <Empty title="В пуле пусто">
                          {hotOnly
                            ? "Зависших работ нет: всё берут вовремя."
                            : "Все сданные работы уже у ревьюеров."}
                        </Empty>
                      )}
                    </CardBody>
                    {foot}
                  </>
                )}
              </Resource>
            </Card>
          </div>
        </Frame>
        {reminding && currentRun && (
          <RemindModal ws={ws} run={currentRun} close={closeRemind} />
        )}
      </>
    );

  return (
    <>
      {!embedded && (
        <Topbar
          crumbs={crumbs}
          title="Домашки потока"
          lead={
            <form
              className="topbar__search"
              onSubmit={(e) => {
                e.preventDefault();
                setSearch(query);
                setOffset(0);
              }}
            >
              <Inp
                small
                className="inp--w-200"
                aria-label="Поиск"
                placeholder="Поиск по ID студента"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onBlur={() => {
                  if (query !== search) {
                    setSearch(query);
                    setOffset(0);
                  }
                }}
              />
            </form>
          }
          actions={
            <>
              {!currentRun && (
                <span className="caption">
                  Выгрузка готовится по одному потоку. Выберите его ниже.
                </span>
              )}
              <Btn
                size="s"
                variant="dark"
                disabled={!currentRun}
                title={
                  currentRun
                    ? undefined
                    : "Выберите поток: выгрузка готовится по одному потоку"
                }
                onClick={() => setExporting(true)}
              >
                Выгрузить
              </Btn>
            </>
          }
        />
      )}
      <Frame screen="К8">
        {action.feedback}
        {embedded && <h2 className="section-title">Домашки</h2>}
        <div className="tabs--row">
          <Tabs className="tabs--wrap" label="Статус">
            {REGISTRY_TABS.map(([value, label]) => (
              <Tab
                key={value}
                on={state === value}
                onClick={() => {
                  setState(value);
                  setOffset(0);
                }}
              >
                {label}
              </Tab>
            ))}
          </Tabs>
          {runSelect}
        </div>
        <Card>
          <CardHead
            title={currentRun?.title ?? "Все потоки"}
            sub={course?.title}
          >
            {r.data && (
              <span className="caption">
                Показаны {Math.min(r.data.items.length, r.data.total)} из{" "}
                {r.data.total}
              </span>
            )}
          </CardHead>
          <Resource value={r}>
            {r.data && (
              <>
                <CardBody flush>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Студент</th>
                        <th>Задание</th>
                        <th>Статус</th>
                        <th>Ревьюер</th>
                        <th className="n">Попытка</th>
                        <th className="n">Сдана</th>
                        <th className="n r">Балл</th>
                        <th className="r"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((w) => {
                        const reviewer =
                          w.responsible_reviewer_id ?? w.primary_reviewer_id;
                        return (
                          <tr
                            key={w.submission_id}
                            onClick={(e) => rowOpen(e, w)}
                            className={cx(
                              "is-link",
                              w.status === "pending_review" &&
                                w.waited >= HOT_DAYS &&
                                "is-hot",
                              isClosed(w.status) && "is-done",
                            )}
                          >
                            <td className="n mono">{w.student_name}</td>
                            <td>
                              <div className="who">{w.title}</div>
                              {!currentRun && (
                                <div className="sub">{w.course_run_title}</div>
                              )}
                            </td>
                            <td>
                              <St status={w.status} attempt={w.attempt} />
                            </td>
                            <td className="n mono">
                              {reviewer ? `rev-${short(reviewer)}` : "—"}
                            </td>
                            <td className="n">{w.attempt}</td>
                            <td className="n">{dayShort(w.submitted_at)}</td>
                            <td className="n r">
                              {w.score === null ? "—" : num(w.score)}
                            </td>
                            <td className="r">
                              <Btn
                                size="s"
                                variant="quiet"
                                disabled={
                                  action.busy || !w.submission_version_id
                                }
                                onClick={() =>
                                  void action.run(() => openReview(w))
                                }
                              >
                                Открыть проверку
                              </Btn>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {items.length === 0 && (
                    <Empty title="Работ нет">
                      По этим условиям работ нет. Снимите фильтр статуса или
                      выберите другой поток.
                    </Empty>
                  )}
                </CardBody>
                {foot}
              </>
            )}
          </Resource>
        </Card>
      </Frame>
      {exporting && currentRun && (
        <ExportForm
          ws={ws}
          run={currentRun}
          close={closeExport}
          total={r.data?.total}
        />
      )}
    </>
  );
}

function RemindModal({
  ws,
  run,
  close,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  close: () => void;
}) {
  const [message, setMessage] = useState(
    `В пуле потока «${run.title}» есть работы, которые ждут ревьюера дольше трёх дней. Пожалуйста, возьмите по одной.`,
  );
  const assignments = useResource(
    () => ws.assignments(run.id),
    `remind:${run.id}`,
  );
  const action = useAction();
  const recipients = [
    ...new Set(
      (assignments.data?.items ?? [])
        .map((a) => a.reviewer_id)
        .filter((id): id is string => !!id),
    ),
  ];
  return (
    <Modal title="Напомнить ревьюерам" close={close} flush>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            if (!recipients.length)
              throw new Error(
                "В потоке пока нет ревьюеров с закреплёнными работами.",
              );
            await ws.command("remind_reviewers", run.id, run.revision, {
              reviewer_ids: recipients,
              text: message,
            });
            close();
          });
        }}
      >
        <div className="card__body">
          {action.feedback}
          <Field
            label="Текст напоминания"
            hint={
              assignments.data
                ? `Получат ${recipients.length} ${plural(recipients.length, "ревьюер", "ревьюера", "ревьюеров")} потока.`
                : "Считаем, кто проверяет этот поток…"
            }
          >
            <Area
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              required
            />
          </Field>
        </div>
        <div className="card__foot card__foot--end">
          <BtnRow>
            <Btn variant="quiet" onClick={close}>
              Отмена
            </Btn>
            <Btn
              variant="pri"
              type="submit"
              disabled={action.busy || !assignments.data || !message.trim()}
            >
              Отправить
            </Btn>
          </BtnRow>
        </div>
      </form>
    </Modal>
  );
}

const COLUMNS = [
  ["student_id", "ID студента"],
  ["score", "Итоговый балл"],
  ["status", "Статус"],
  ["attempt", "Номер попытки"],
  ["feedback", "Комментарий ревьюера"],
  ["reviewer_id", "ID ревьюера"],
] as const;
const STUDENT_COLUMNS = ["student_id", "score", "status"];

/** К10: окно выгрузки результатов потока. */
function ExportForm({
  ws,
  run,
  close,
  total,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
  close: () => void;
  total?: number;
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
  const available = COLUMNS.filter(
    ([key]) => audience === "team" || STUDENT_COLUMNS.includes(key),
  );
  return (
    <Modal title="Выгрузка результатов" close={close} wide flush>
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
        <div className="card__body">
          {action.feedback}
          <Field
            label="Что выгружаем"
            hint={
              total !== undefined
                ? `В потоке ${total} ${plural(total, "работа", "работы", "работ")}.`
                : undefined
            }
          >
            <Inp disabled value={`Поток «${run.title}»`} readOnly />
          </Field>
          <Field
            group
            label="Кому выгружаем"
            hint="В студенческой копии нет ID ревьюера и комментариев, только ID студента, балл и статус."
          >
            <Seg
              value={audience}
              disabled={action.busy}
              onChange={(a) => {
                setAudience(a);
                if (a === "students")
                  setColumns(
                    columns.filter((c) => STUDENT_COLUMNS.includes(c)),
                  );
              }}
              options={[
                { value: "team", label: "Команде, внутренняя ведомость" },
                { value: "students", label: "Студентам, копия без ПДн" },
              ]}
            />
          </Field>
          <Field group label="Колонки">
            <div className="chk-grid">
              {available.map(([key, label]) => (
                <Chk
                  key={key}
                  disabled={action.busy}
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
          </Field>
          <Field
            group
            label="Что делать с незакрытыми работами"
            hint="Промежуточные баллы в таблицу лучше не отдавать."
          >
            <Seg
              value={unfinished ? "with" : "skip"}
              disabled={action.busy}
              onChange={(v) => setUnfinished(v === "with")}
              options={[
                { value: "skip", label: "Не выгружать" },
                { value: "with", label: "Выгрузить с пустым баллом" },
              ]}
            />
          </Field>
          <Field group label="Формат">
            <Seg
              value={format}
              disabled={action.busy}
              onChange={setFormat}
              options={[
                { value: "csv", label: "CSV" },
                { value: "xlsx", label: "XLSX" },
              ]}
            />
          </Field>
          {job && (
            <div className="field--after">
              <ExportMonitor ws={ws} id={job} />
            </div>
          )}
        </div>
        <div className="card__foot card__foot--end">
          <BtnRow>
            <Btn variant="quiet" onClick={close}>
              {job ? "Закрыть" : "Отмена"}
            </Btn>
            <Btn
              variant="pri"
              type="submit"
              disabled={action.busy || !columns.length || !!job}
            >
              Подготовить файл
            </Btn>
          </BtnRow>
        </div>
      </form>
    </Modal>
  );
}

function minutes(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 60
    ? `${Math.round(value)} мин`
    : `${num(value / 1440, 1)} ${plural(Math.round(value / 1440), "день", "дня", "дней")}`;
}
function percent(value: number | null | undefined) {
  return value == null ? "—" : `${num(value, 1)}%`;
}

/** Р4: статистика ревьюера, вторая вкладка кабинета. */
export function WorkspaceStatistics({ ws }: { ws: WorkspaceClient }) {
  const [days, setDays] = useState(30);
  const [courseRun, setCourseRun] = useState("");
  const catalog = useResource(() => ws.catalog(), "statistics-catalog");
  const r = useResource(
    () => ws.statistics(days, courseRun || undefined),
    `${days}:${courseRun}`,
  );
  const peer = r.data?.peer_comparison;
  return (
    <>
      <Topbar title="Кабинет" />
      <Main data-screen="Р4">
        <CabinetTabs on="statistics" />
        {/* Фильтры сужают аналитику ниже, поэтому стоят рядом с ней. */}
        <BtnRow className="filter-row">
          <Sel
            small
            aria-label="Поток"
            value={courseRun}
            onChange={(e) => setCourseRun(e.target.value)}
          >
            <option value="">Поток: все</option>
            {catalog.data?.course_runs.map((run) => (
              <option key={run.id} value={run.id}>
                {run.title}
              </option>
            ))}
          </Sel>
          <Sel
            small
            aria-label="Период"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value={7}>Период: 7 дней</option>
            <option value={30}>Период: 30 дней</option>
            <option value={90}>Период: 90 дней</option>
          </Sel>
        </BtnRow>
        <Resource value={r}>
          {r.data && (
            <div className="stack">
              <Tiles>
                <Tile
                  n={r.data.publications ?? 0}
                  l={plural(
                    r.data.publications ?? 0,
                    "работа проверена",
                    "работы проверено",
                    "работ проверено",
                  )}
                  d={`из них ${r.data.repeated_publications ?? 0} ${plural(r.data.repeated_publications ?? 0, "повторная", "повторные", "повторных")}`}
                />
                <Tile
                  n={minutes(r.data.average_elapsed_minutes)}
                  l="в среднем на работу"
                  d={
                    r.data.course_average_elapsed_minutes == null
                      ? "от открытия до публикации"
                      : `по курсу в среднем ${minutes(r.data.course_average_elapsed_minutes)}`
                  }
                />
                <Tile
                  n={minutes(r.data.average_wait_minutes)}
                  l="ждёт студент от сдачи до ответа"
                  d={
                    r.data.course_average_wait_minutes == null
                      ? "нет данных по курсу"
                      : `по курсу в среднем ${minutes(r.data.course_average_wait_minutes)}`
                  }
                />
                <Tile
                  n={percent(r.data.ai_acceptance_percent)}
                  l="вердиктов модели принято без правок"
                  d={
                    r.data.course_ai_acceptance_percent == null
                      ? "нет данных по курсу"
                      : `по курсу ${percent(r.data.course_ai_acceptance_percent)}`
                  }
                />
                <Tile
                  n={r.data.overdue_publications ?? 0}
                  l={`${plural(r.data.overdue_publications ?? 0, "просрочка", "просрочки", "просрочек")} за период`}
                  d={
                    r.data.course_average_overdue_publications == null
                      ? "нет данных по курсу"
                      : `по курсу в среднем ${num(r.data.course_average_overdue_publications, 1)}`
                  }
                />
              </Tiles>
              <div className="row-2">
                <Card>
                  <CardHead
                    title="Где вы чаще правите модель"
                    sub="Доля работ, где вы изменили предложенную оценку"
                  />
                  <CardBody flush>
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>Требование</th>
                          <th className="n">Работ</th>
                          <th className="n r">Правили</th>
                        </tr>
                      </thead>
                      <tbody>
                        {r.data.criterion_changes?.length ? (
                          r.data.criterion_changes.map((c) => (
                            <tr key={c.criterion_id}>
                              <td>{c.title}</td>
                              <td className="n">{c.compared_works}</td>
                              <td className="n r">
                                {percent(c.change_percent)}
                              </td>
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td colSpan={3} className="dim">
                              Нет данных по отдельным требованиям.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </CardBody>
                </Card>
                <Card>
                  <CardHead
                    title="Расхождение с другими ревьюерами"
                    sub="По одинаковым требованиям на одинаковых работах"
                  />
                  <CardBody>
                    {peer?.divergence_percent == null ? (
                      <p className="small dim">
                        Данные для сравнения пока недоступны: сравнений{" "}
                        {peer?.sample_count ?? 0}.
                      </p>
                    ) : (
                      <>
                        <div className="stat-line">
                          <span className="d3">
                            {percent(peer.divergence_percent)}
                          </span>
                          <span className="caption">
                            {peer.course_divergence_percent == null
                              ? `сравнений: ${peer.sample_count}`
                              : `средний разброс по курсу ${percent(peer.course_divergence_percent)}`}
                          </span>
                        </div>
                        <Kv label="Строже коллег">
                          {peer.stricter_criteria?.length
                            ? peer.stricter_criteria
                                .map((c) => `«${c}»`)
                                .join(", ")
                            : "—"}
                        </Kv>
                        <Kv label="Мягче коллег">
                          {peer.softer_criteria?.length
                            ? peer.softer_criteria
                                .map((c) => `«${c}»`)
                                .join(", ")
                            : "—"}
                        </Kv>
                        <Kv label="Совпадение полное">
                          {peer.fully_agreed_criteria}{" "}
                          {plural(
                            peer.fully_agreed_criteria,
                            "требование",
                            "требования",
                            "требований",
                          )}{" "}
                          из {peer.compared_criteria}
                        </Kv>
                      </>
                    )}
                    <Callout className="callout--after">
                      <p>
                        Расхождение само по себе не ошибка. Оно показывает
                        требования, которые сформулированы так, что их можно
                        понять по-разному.
                      </p>
                    </Callout>
                  </CardBody>
                </Card>
              </div>
            </div>
          )}
        </Resource>
        {!!r.error && <ErrorBox error={r.error} retry={r.refresh} />}
      </Main>
    </>
  );
}
