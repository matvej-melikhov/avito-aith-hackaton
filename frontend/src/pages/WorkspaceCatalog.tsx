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
import { Modal, ScreenTitle } from "../workspace-ui";
export function WorkspaceCatalog({ ws }: { ws: WorkspaceClient }) {
  const r = useResource(() => ws.catalog(), "catalog");
  const [modal, setModal] = useState<"course" | string>();
  const close = useCallback(() => setModal(undefined), []);
  const action = useAction();
  const [search, setSearch] = useState("");
  return (
    <>
      <ScreenTitle code="К1" title="Курсы и потоки">
        <button className="primary" onClick={() => setModal("course")}>
          Создать курс
        </button>
      </ScreenTitle>
      {action.feedback}
      <label>
        Найти курс
        <input value={search} onChange={(e) => setSearch(e.target.value)} />
      </label>
      <Resource value={r}>
        <div className="cards">
          {r.data?.courses
            .filter((c) => c.title.toLowerCase().includes(search.toLowerCase()))
            .map((c) => (
              <Card
                title={c.title}
                key={c.id}
                actions={<Status value={c.status} />}
              >
                <p>{c.description}</p>
                {r
                  .data!.course_runs.filter((run) => run.course_id === c.id)
                  .map((run) => (
                    <RunCard key={run.id} ws={ws} run={run} />
                  ))}
                <div className="actions">
                  <button
                    disabled={c.status === "archived"}
                    onClick={() => setModal(c.id)}
                  >
                    Создать поток
                  </button>
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
                      : "Архивировать курс"}
                  </button>
                </div>
              </Card>
            ))}
        </div>
        {r.data?.courses.length === 0 && (
          <Empty>Создайте курс или импортируйте его из Stepik.</Empty>
        )}
      </Resource>
      {modal && (
        <Modal
          title={
            modal === "course" ? "К2 · Создать курс" : "К3 · Создать поток"
          }
          close={close}
        >
          {modal === "course" ? (
            <CourseForm
              ws={ws}
              done={() => {
                close();
                r.refresh();
              }}
            />
          ) : (
            <RunForm
              ws={ws}
              course={r.data!.courses.find((c) => c.id === modal)!}
              done={() => {
                close();
                r.refresh();
              }}
            />
          )}
        </Modal>
      )}
      <Card title="Курс из Stepik">
        <a href="#/courses">Открыть импорт курса →</a>
      </Card>
    </>
  );
}
function RunCard({
  ws,
  run,
}: {
  ws: WorkspaceClient;
  run: W<"CourseRunView">;
}) {
  const work = useResource(
    () => ws.works({ course_run_id: run.id, limit: 1 }),
    run.id,
  );
  const action = useAction();
  return (
    <article className="list-item">
      <div className="row">
        <a href={`#/courses/${run.id}`}>
          <strong>{run.title}</strong>
        </a>
        <Status value={run.status} />
      </div>
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
      <div className="actions">
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
              window.location.reload();
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
function CourseForm({ ws, done }: { ws: WorkspaceClient; done: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const people = useResource(() => ws.directory(), "people");
  const action = useAction();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void action.run(async () => {
          const org = await ws.core.organization();
          await ws.command("create_course", org.id, org.revision, {
            title,
            description,
            owner_id: owner || null,
          });
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
      <button className="primary" disabled={action.busy}>
        Создать курс
      </button>
    </form>
  );
}
function RunForm({
  ws,
  course,
  done,
}: {
  ws: WorkspaceClient;
  course: W<"CourseView">;
  done: () => void;
}) {
  const [title, setTitle] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [zone, setZone] = useState("Europe/Moscow");
  const action = useAction();
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void action.run(async () => {
          await ws.command("create_course_run", course.id, course.revision, {
            title,
            starts_at: new Date(start).toISOString(),
            ends_at: new Date(end).toISOString(),
            timezone: zone,
            priority: "assigned",
          });
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
      <p className="muted">
        После создания добавьте участников или импортируйте состав из Stepik.
      </p>
      <button className="primary" disabled={action.busy}>
        Создать поток
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
  const [selected, setSelected] = useState(initial?.course_run_ids ?? []);
  const [minutes, setMinutes] = useState(initial?.planned_minutes ?? 0);
  const [until, setUntil] = useState(initial?.until_at?.slice(0, 16) ?? "");
  const [from, setFrom] = useState(initial?.absent_from?.slice(0, 16) ?? "");
  const [to, setTo] = useState(initial?.absent_until?.slice(0, 16) ?? "");
  const action = useAction();
  return (
    <>
      <ScreenTitle code="Р3" title="Настройки ревьюера" />
      {action.feedback}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(async () => {
            await ws.command(
              "save_preferences",
              session.user_id,
              preferences.revision,
              {
                course_run_ids: selected,
                planned_minutes: minutes,
                until_at: new Date(until).toISOString(),
                absent_from: from ? new Date(from).toISOString() : null,
                absent_until: to ? new Date(to).toISOString() : null,
              },
            );
            refresh();
          });
        }}
      >
        <div className="two-col">
          <Card title="Курсы и потоки">
            {catalog.course_runs
              .filter((r) => r.status === "active")
              .map((run) => (
                <label key={run.id} className="check">
                  <input
                    type="checkbox"
                    checked={selected.includes(run.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, run.id]
                          : selected.filter((id) => id !== run.id),
                      )
                    }
                  />
                  {run.title}
                </label>
              ))}
          </Card>
          <div>
            <Card title="Плановое время">
              <label>
                Минут на проверку
                <input
                  type="number"
                  min={0}
                  required
                  value={minutes}
                  onChange={(e) => setMinutes(e.target.valueAsNumber)}
                />
              </label>
              <label>
                До
                <input
                  type="datetime-local"
                  required
                  value={until}
                  onChange={(e) => setUntil(e.target.value)}
                />
              </label>
              <p className="muted">
                План помогает рекомендовать работы и не ограничивает доступ к
                ним.
              </p>
            </Card>
            <Card title="Отпуск или отсутствие">
              <label>
                С
                <input
                  type="datetime-local"
                  value={from}
                  onChange={(e) => setFrom(e.target.value)}
                />
              </label>
              <label>
                По
                <input
                  type="datetime-local"
                  required={!!from}
                  min={from}
                  value={to}
                  onChange={(e) => setTo(e.target.value)}
                />
              </label>
            </Card>
          </div>
        </div>
        <button className="primary" disabled={action.busy}>
          Сохранить настройки
        </button>
      </form>
    </>
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
      <ScreenTitle code="К6" title="Студенты и ревьюеры" />
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
