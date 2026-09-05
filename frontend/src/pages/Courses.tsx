import { WorkspaceClient } from "../api/workspace";
import { WorkspaceRunSettings } from "./WorkspaceCatalog";
import { useState } from "react";
import type { ApiClient, Model, Role } from "../api/client";
import {
  Btn,
  Callout,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Crumbs,
  Empty,
  Field,
  Inp,
  Kv,
  Main,
  OpPill,
  Topbar,
  dayLong,
  num,
} from "../ds";
import { Resource, go, useAction, useResource } from "../ui";
import { OperationPanel } from "./Operations";

const kindLabel = (kind: string) =>
  kind === "github" ? "GitHub" : kind === "google_docs" ? "Google Docs" : kind;

/** Курсы и потоки для студента и ревьюера. Экрана в паке нет. */
export function CoursesPage({ api, role }: { api: ApiClient; role: Role }) {
  const state = useResource(() => api.courses(), "courses");
  const action = useAction();
  const [url, setUrl] = useState("");
  const [operation, setOperation] = useState<string>();
  return (
    <>
      <Topbar
        page={role === "student"}
        title={role === "student" ? "Мои курсы" : "Курсы и потоки"}
      />
      <Main page={role === "student"}>
        {action.feedback}
        <div className="stack">
          <Resource value={state}>
            {state.data?.items.map((c) => (
              <Card key={c.id}>
                <CardHead title={c.title}>
                  <OpPill status={c.status} />
                </CardHead>
                {state.data?.course_runs.every((r) => r.course_id !== c.id) ? (
                  <Empty title="Потоков пока нет">
                    Когда координатор откроет поток, он появится здесь.
                  </Empty>
                ) : (
                  <CardBody tight>
                    {state.data?.course_runs
                      .filter((r) => r.course_id === c.id)
                      .map((r) => (
                        <Kv key={r.id} ink label={r.title}>
                          <OpPill status={r.status} />{" "}
                          <Btn
                            size="s"
                            variant="quiet"
                            href={`#/courses/${r.id}`}
                          >
                            Открыть
                          </Btn>
                        </Kv>
                      ))}
                  </CardBody>
                )}
              </Card>
            ))}
            {state.data?.items.length === 0 && (
              <Card>
                <Empty title="Нет доступных курсов">
                  Курсы появятся, когда координатор добавит вас в поток.
                </Empty>
              </Card>
            )}
          </Resource>
          {role === "methodologist" && (
            <Card>
              <CardHead title="Импорт курса из Stepik" />
              <CardBody>
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
                  <Field label="Ссылка на курс">
                    <Inp
                      mono
                      type="url"
                      required
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      placeholder="https://stepik.org/course/…"
                    />
                  </Field>
                  <div className="btn-row">
                    <Btn variant="pri" type="submit" disabled={action.busy}>
                      Импортировать
                    </Btn>
                  </div>
                </form>
              </CardBody>
            </Card>
          )}
          {operation && <OperationPanel api={api} id={operation} />}
        </div>
      </Main>
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
  const back = role === "methodologist" ? "#/dashboard" : "#/courses";
  return (
    <>
      <Topbar
        page={role === "student"}
        crumbs={
          <Crumbs
            back={back}
            items={[{ href: back, label: "Курсы" }]}
            current={state.data?.run?.title ?? "Поток"}
          />
        }
        title={state.data?.run?.title ?? "Поток"}
        status={state.data?.run && <OpPill status={state.data.run.status} />}
        actions={
          role === "reviewer" && (
            <Btn size="s" variant="dark" href={`#/queue/${id}`}>
              Следующая работа
            </Btn>
          )
        }
      />
      <Main page={role === "student"}>
        {action.feedback}
        <Resource value={state}>
          {state.data && (
            <div className="stack">
              <Card>
                <CardHead title="Задания" />
                <CardBody flush>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Задание</th>
                        <th className="n">Сдать до</th>
                        <th>Формат</th>
                        <th className="n">Баллы</th>
                        <th className="r"></th>
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
                          <tr key={h.id} className="is-done">
                            <td>
                              <div className="who">{h.title}</div>
                              <div className="sub">
                                Черновик ·{" "}
                                {h.latest_version_number
                                  ? `версия ${h.latest_version_number}`
                                  : "версия ещё не сохранена"}
                              </div>
                            </td>
                            <td className="n">—</td>
                            <td>—</td>
                            <td className="n">—</td>
                            <td className="r">
                              <Btn
                                size="s"
                                variant="quiet"
                                href={`#/homework/${h.id}?run=${id}`}
                              >
                                Настроить
                              </Btn>
                            </td>
                          </tr>
                        ))}
                      {state.data.homeworks.items.map((h) => (
                        <tr key={h.course_run_homework_id}>
                          <td>
                            <div className="who">{h.title}</div>
                          </td>
                          <td className="n">
                            {dayLong(h.submission_deadline)}
                          </td>
                          <td>{h.artifact_kinds.map(kindLabel).join(", ")}</td>
                          <td className="n">{num(h.max_score)}</td>
                          <td className="r">
                            <Btn
                              size="s"
                              variant={role === "student" ? "pri" : undefined}
                              href={
                                role === "student"
                                  ? `#/submit/${id}/${h.course_run_homework_id}`
                                  : `#/homework/${h.homework_id}?run=${id}`
                              }
                            >
                              {role === "student" ? "Сдать работу" : "Открыть"}
                            </Btn>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {state.data.homeworks.items.length === 0 &&
                    !state.data.allHomeworks?.items.length && (
                      <Empty title="Заданий пока нет">
                        Опубликованных заданий в потоке ещё нет.
                      </Empty>
                    )}
                </CardBody>
              </Card>
              {role === "methodologist" && (
                <>
                  <Card>
                    <CardHead title="Новое задание" />
                    <CardBody>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          void action.run(async () => {
                            const run = state.data!.run;
                            if (!run)
                              throw new Error("Не удалось загрузить поток.");
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
                        <Field label="Название">
                          <Inp
                            required
                            maxLength={512}
                            value={title}
                            onChange={(e) => setTitle(e.target.value)}
                          />
                        </Field>
                        <div className="btn-row">
                          <Btn
                            variant="dark"
                            type="submit"
                            disabled={action.busy || !state.data.run}
                          >
                            Создать задание
                          </Btn>
                        </div>
                      </form>
                    </CardBody>
                  </Card>
                  <WorkspaceRunSettings
                    ws={new WorkspaceClient(api)}
                    runId={id}
                  />
                  <CourseMembers api={api} id={id} />
                </>
              )}
            </div>
          )}
        </Resource>
      </Main>
    </>
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
    <Card>
      <CardHead title="Участники потока">
        <Btn size="s" variant="quiet" href={`#/assignments/${id}`}>
          Закрепления
        </Btn>
      </CardHead>
      <Resource value={s}>
        {s.data?.items.length === 0 ? (
          <Empty title="Участников пока нет">
            Студенты попадают в поток по ссылке задания, ревьюеров добавляет
            координатор.
          </Empty>
        ) : (
          <CardBody tight>
            {s.data?.items.map((m) => (
              <Kv
                key={`${m.user_id}:${m.kind}`}
                ink
                label={
                  s.data?.people.find((p) => p.id === m.user_id)
                    ?.display_name ?? "Участник"
                }
              >
                {m.kind === "student" ? "студент" : "ревьюер"} ·{" "}
                <OpPill status={m.status} />
              </Kv>
            ))}
          </CardBody>
        )}
      </Resource>
    </Card>
  );
}

export function QueuePage({ api, id }: { api: ApiClient; id: string }) {
  const s = useResource(() => api.next(id), id);
  const action = useAction();
  return (
    <>
      <Topbar
        crumbs={
          <Crumbs
            back={`#/courses/${id}`}
            items={[{ href: "#/courses", label: "Курсы" }]}
            current="Следующая работа"
          />
        }
        title="Следующая работа"
        actions={
          <Btn size="s" variant="quiet" onClick={s.refresh}>
            Обновить рекомендацию
          </Btn>
        }
      />
      <Main>
        {action.feedback}
        <Resource value={s}>
          {s.data ? (
            <Card>
              <CardHead
                title="Работа готова к проверке"
                sub="Рекомендация учитывает выбранный поток и вашу доступность"
              />
              <CardBody>
                <Callout>
                  <p>{s.data.reason.join(" · ")}</p>
                </Callout>
              </CardBody>
              <CardFoot>
                <span>Открытие закрепляет работу за вами.</span>
                <Btn
                  variant="pri"
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
                </Btn>
              </CardFoot>
            </Card>
          ) : (
            <Card>
              <Empty title="Подходящих работ нет">
                В этом потоке пока нет работ, которые ждут проверки.
              </Empty>
            </Card>
          )}
        </Resource>
      </Main>
    </>
  );
}
