import { HeaderProfile } from "../workspace-ui";
import { WorkspaceClient } from "../api/workspace";
import { WorkspaceRunSettings } from "./WorkspaceCatalog";
import { useState } from "react";
import type { ApiClient, Model, Role } from "../api/client";
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
import { OperationPanel } from "./Operations";
export function CoursesPage({ api, role }: { api: ApiClient; role: Role }) {
  const state = useResource(() => api.courses(), "courses");
  const action = useAction();
  const [url, setUrl] = useState("");
  const [operation, setOperation] = useState<string>();
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Учебная работа</p>
          <h1>{role === "student" ? "Мои курсы" : "Курсы и потоки"}</h1>
        </div>
      </div>
      {action.feedback}
      <Resource value={state}>
        <div className="cards">
          {state.data?.items.map((c) => (
            <Card
              key={c.id}
              title={c.title}
              actions={<Status value={c.status} />}
            >
              {state.data?.course_runs
                .filter((r) => r.course_id === c.id)
                .map((r) => (
                  <a
                    className="course-row"
                    key={r.id}
                    href={`#/courses/${r.id}`}
                  >
                    <div>
                      <strong>{r.title}</strong>
                      <small>{r.timezone}</small>
                    </div>
                    <Status value={r.status} />
                    <span>→</span>
                  </a>
                ))}
              {state.data?.course_runs.every((r) => r.course_id !== c.id) && (
                <Empty>Потоков пока нет.</Empty>
              )}
            </Card>
          ))}
        </div>
        {state.data?.items.length === 0 && <Empty>Нет доступных курсов.</Empty>}
      </Resource>
      {role === "methodologist" && (
        <Card title="Импорт курса из Stepik">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const org = await api.organization();
                const op = await api.command(
                  "start_course_import",
                  org.id,
                  org.revision,
                  { provider: "stepik", external_url: url },
                );
                setOperation(op.id);
              });
            }}
          >
            <label>
              Ссылка на курс
              <input
                type="url"
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://stepik.org/course/…"
              />
            </label>
            <button className="primary" disabled={action.busy}>
              Импортировать
            </button>
          </form>
        </Card>
      )}
      {operation && <OperationPanel api={api} id={operation} />}
    </>
  );
}
export function CoursePage({
  api,
  id,
  role,
}: {
  api: ApiClient;
  id: string;
  role: Role;
}) {
  const state = useResource(async () => {
    const [courses, homeworks] = await Promise.all([
      api.courses(),
      api.homeworks(id),
    ]);
    const run = courses.course_runs.find((r) => r.id === id);
    const allHomeworks =
      role === "methodologist" && run
        ? await new WorkspaceClient(api).courseHomeworks(run.course_id)
        : undefined;
    return { run, homeworks, allHomeworks };
  }, id);
  const action = useAction();
  const [title, setTitle] = useState("");
  return (
    <Resource value={state}>
      {state.data && (
        <>
          <div className="page-heading">
            <div>
              <a href={role === "methodologist" ? "#/dashboard" : "#/courses"}>
                Курсы /
              </a>
              <h1>{state.data.run?.title ?? "Поток"}</h1>
            </div>
            <div className="actions">
              {state.data.run && <Status value={state.data.run.status} />}
              <HeaderProfile />
            </div>
          </div>
          {action.feedback}
          <Card title="Задания" bodyClassName="card__body--flush">
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Задание</th>
                    <th>Сдать до</th>
                    <th>Формат</th>
                    <th>Баллы</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {state.data.allHomeworks?.items
                    .filter(
                      (h) =>
                        !state.data!.homeworks.items.some(
                          (p) => p.homework_id === h.id,
                        ),
                    )
                    .map((h) => (
                      <tr key={h.id}>
                        <td>
                          <strong>{h.title}</strong>
                          <small>
                            Черновик ·{" "}
                            {h.latest_version_number
                              ? `Версия ${h.latest_version_number}`
                              : "Версия ещё не сохранена"}
                          </small>
                        </td>
                        <td>Не опубликовано в потоке</td>
                        <td>—</td>
                        <td>—</td>
                        <td>
                          <a href={`#/homework/${h.id}?run=${id}`}>
                            Настроить →
                          </a>
                        </td>
                      </tr>
                    ))}
                  {state.data.homeworks.items.map((h) => (
                    <tr key={h.course_run_homework_id}>
                      <td>
                        <strong>{h.title}</strong>
                      </td>
                      <td>{date(h.submission_deadline)}</td>
                      <td>{h.artifact_kinds.join(", ")}</td>
                      <td>{h.max_score}</td>
                      <td>
                        <a
                          href={
                            role === "student"
                              ? `#/submit/${id}/${h.course_run_homework_id}`
                              : `#/homework/${h.homework_id}?run=${id}`
                          }
                        >
                          {role === "student" ? "Сдать работу" : "Открыть"} →
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {state.data.homeworks.items.length === 0 &&
              !state.data.allHomeworks?.items.length && (
                <Empty>Опубликованных заданий пока нет.</Empty>
              )}
          </Card>
          {role === "reviewer" && (
            <a className="button primary" href={`#/queue/${id}`}>
              Следующая работа →
            </a>
          )}
          {role === "methodologist" && (
            <>
              <Card title="Новое задание">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action.run(async () => {
                      const run = state.data!.run;
                      if (!run) throw new Error("Не удалось загрузить поток.");
                      const created = await api.command(
                        "create_homework",
                        id,
                        run.revision,
                        { title },
                      );
                      go(`/homework/${created.id}?run=${id}`);
                    });
                  }}
                >
                  <label>
                    Название
                    <input
                      required
                      maxLength={512}
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                    />
                  </label>
                  <button disabled={action.busy || !state.data.run}>
                    Создать задание
                  </button>
                </form>
              </Card>
              <WorkspaceRunSettings ws={new WorkspaceClient(api)} runId={id} />
              <CourseMembers api={api} id={id} />
            </>
          )}
        </>
      )}
    </Resource>
  );
}
function CourseMembers({ api, id }: { api: ApiClient; id: string }) {
  const s = useResource(async () => {
    const [members, people] = await Promise.all([
      api.courseMembers(id),
      new WorkspaceClient(api).directory(),
    ]);
    return { ...members, people: people.items };
  }, id);
  return (
    <Card title="Участники потока">
      <Resource value={s}>
        {s.data?.items.map((m) => (
          <p key={`${m.user_id}:${m.kind}`}>
            <span>
              {s.data?.people.find((p) => p.id === m.user_id)?.display_name ??
                "Участник"}
            </span>{" "}
            · {m.kind === "student" ? "Студент" : "Ревьюер"} · {m.status}
          </p>
        ))}
        {s.data?.items.length === 0 && <Empty>Участников пока нет.</Empty>}
      </Resource>
    </Card>
  );
}
export function QueuePage({ api, id }: { api: ApiClient; id: string }) {
  const s = useResource(() => api.next(id), id);
  const action = useAction();
  return (
    <>
      <h1>Следующая работа</h1>
      <p className="muted">
        Рекомендация учитывает выбранный поток и доступность ревьюера.
      </p>
      {action.feedback}
      <Resource value={s}>
        {s.data ? (
          <Card title="Работа готова к проверке">
            <p>{s.data.reason.join(" · ")}</p>
            <button
              className="primary"
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  const item = s.data!;
                  const result = await api.command(
                    "open_review_iteration",
                    item.review_case_id,
                    item.review_case_revision,
                    { submission_version_id: item.submission_version_id },
                  );
                  go(`/reviews/${result.review_iteration_id}`);
                })
              }
            >
              Открыть проверку
            </button>
          </Card>
        ) : (
          <Empty>В этом потоке пока нет подходящих работ.</Empty>
        )}
      </Resource>
      <button onClick={s.refresh}>Обновить рекомендацию</button>
    </>
  );
}
