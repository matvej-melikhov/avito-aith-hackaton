import { useEffect } from "react";
import type { ApiClient, Model, Role } from "../api/client";
import { WorkspaceClient } from "../api/workspace";
import { useMenuCounts } from "../App";
import {
  Btn,
  BtnRow,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Empty,
  Kv,
  Main,
  Pill,
  Sel,
  St,
  Tab,
  Tabs,
  Tile,
  Tiles,
  Topbar,
  dayNum,
  isClosed,
  plural,
  short,
  workStatus,
} from "../ds";
import { Resource, roleNames, useResource } from "../ui";
import { DeliveriesBody } from "./Operations";
import { PeopleBody } from "./People";

type AccountProps = {
  session: Model<"Session">;
  role: Role;
  demo: boolean;
  busy: boolean;
  onRole: (role: Role) => void;
  onLogout: () => void;
};

/** Карточка учётной записи: кто вы, роли, выход. Общая для всех кабинетов. */
export function AccountCard({
  session,
  role,
  demo,
  busy,
  onRole,
  onLogout,
}: AccountProps) {
  return (
    <Card>
      <CardHead
        title="Учётная запись"
        sub={demo ? "Демо: данные живут в памяти вкладки" : "Сессия на сервере"}
      />
      <CardBody tight>
        <Kv label="Идентификатор">
          <span className="mono">{session.user_id}</span>
        </Kv>
        <Kv label="Роли">
          <span className="btn-row">
            {session.roles.map((r) => (
              <Pill key={r} tone={r === role ? "ok" : undefined}>
                {roleNames[r]}
              </Pill>
            ))}
          </span>
        </Kv>
        {session.roles.length > 1 && (
          <Kv label="Работать как">
            <Sel
              small
              aria-label="Роль"
              value={role}
              onChange={(e) => onRole(e.target.value as Role)}
            >
              {session.roles.map((r) => (
                <option key={r} value={r}>
                  {roleNames[r]}
                </option>
              ))}
            </Sel>
          </Kv>
        )}
      </CardBody>
      <CardFoot>
        <span>Выход закрывает сессию на всех вкладках этого браузера.</span>
        <Btn size="s" variant="quiet" disabled={busy} onClick={onLogout}>
          Выйти
        </Btn>
      </CardFoot>
    </Card>
  );
}

/** Кабинет студента: сводка по домашкам, курсы, учётная запись. */
export function StudentCabinet({
  ws,
  account,
}: {
  ws: WorkspaceClient;
  account: AccountProps;
}) {
  const r = useResource(() => ws.studentWorks({ limit: 100 }), "cabinet-works");
  const { setCounts } = useMenuCounts();
  useEffect(() => {
    if (r.data) setCounts({ works: r.data.total });
  }, [r.data, setCounts]);
  const items = r.data?.items ?? [];
  const closed = items.filter((i) => isClosed(i.status));
  const passed = items.filter((i) => i.status === "passed");
  const fix = items.filter((i) => i.status === "needs_changes");
  const active = items.filter(
    (i) => !isClosed(i.status) && i.status !== "needs_changes",
  );
  const courses = [...new Set(items.map((i) => i.course_title))].map(
    (title) => {
      const own = items.filter((i) => i.course_title === title);
      return {
        title,
        runs: [...new Set(own.map((i) => i.course_run_title))].join(", "),
        total: own.length,
        passed: own.filter((i) => i.status === "passed").length,
        next: own
          .filter((i) => !isClosed(i.status))
          .sort((a, b) =>
            a.submission_deadline.localeCompare(b.submission_deadline),
          )[0],
      };
    },
  );
  const nextDeadline = items
    .filter((i) => !isClosed(i.status))
    .sort((a, b) =>
      a.submission_deadline.localeCompare(b.submission_deadline),
    )[0];
  return (
    <>
      <Topbar page title="Кабинет" />
      <Main page data-screen="С6">
        <div className="stack">
          <Resource value={r}>
            <Tiles>
              <Tile
                n={items.length}
                l={plural(items.length, "домашка", "домашки", "домашек")}
                d={`по ${courses.length} ${plural(courses.length, "курсу", "курсам", "курсам")}`}
              />
              <Tile
                n={active.length}
                l="в работе или на проверке"
                d={
                  nextDeadline
                    ? `ближайший срок ${dayNum(nextDeadline.submission_deadline)}`
                    : "сроков впереди нет"
                }
              />
              <Tile
                n={fix.length}
                alert={fix.length > 0}
                l={plural(
                  fix.length,
                  "ждёт ваших правок",
                  "ждут ваших правок",
                  "ждут ваших правок",
                )}
                d={
                  fix.length ? "ревьюер вернул с замечаниями" : "возвратов нет"
                }
              />
              <Tile
                n={passed.length}
                l={plural(passed.length, "зачтена", "зачтены", "зачтено")}
                d={`из ${closed.length} ${plural(closed.length, "закрытой", "закрытых", "закрытых")}`}
              />
            </Tiles>
          </Resource>
          <div className="row-side">
            <Card>
              <CardHead
                title="Курсы"
                sub="Куда вы записаны и что там происходит"
              />
              {r.data && courses.length === 0 ? (
                <Empty title="Курсов пока нет">
                  Вы попадёте в курс, открыв домашку по ссылке из Stepik.
                </Empty>
              ) : (
                <CardBody flush>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Курс</th>
                        <th className="n">Домашек</th>
                        <th className="n">Зачтено</th>
                        <th>Ближайшая</th>
                        <th className="r"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {courses.map((c) => (
                        <tr key={c.title}>
                          <td>
                            <div className="who">{c.title}</div>
                            <div className="sub">{c.runs}</div>
                          </td>
                          <td className="n">{c.total}</td>
                          <td className="n">{c.passed}</td>
                          <td>
                            {c.next ? (
                              <>
                                <div className="who">{c.next.title}</div>
                                <div className="sub">
                                  до {dayNum(c.next.submission_deadline)} ·{" "}
                                  {workStatus(c.next.status, c.next.attempt)
                                    ?.label ?? "—"}
                                </div>
                              </>
                            ) : (
                              <span className="dim">всё закрыто</span>
                            )}
                          </td>
                          <td className="r">
                            {c.next && (
                              <Btn
                                size="s"
                                variant="quiet"
                                href={
                                  c.next.submission_id &&
                                  !["draft", "needs_changes"].includes(
                                    c.next.status,
                                  )
                                    ? `#/submissions/${c.next.submission_id}`
                                    : `#/prepare/${c.next.publication_id}`
                                }
                              >
                                Открыть
                              </Btn>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CardBody>
              )}
            </Card>
            <div className="stack">
              <AccountCard {...account} />
              {fix.length > 0 && (
                <Card hard>
                  <CardHead title="Нужны правки" />
                  <CardBody tight>
                    {fix.map((i) => (
                      <Kv key={i.publication_id} ink label={i.title}>
                        <Btn size="s" href={`#/prepare/${i.publication_id}`}>
                          Исправить
                        </Btn>
                      </Kv>
                    ))}
                  </CardBody>
                </Card>
              )}
            </div>
          </div>
        </div>
      </Main>
    </>
  );
}

/** Кабинет организатора: профиль и организация, участники, доставки. */
export function CoordinatorCabinet({
  api,
  ws,
  tab,
  account,
}: {
  api: ApiClient;
  ws: WorkspaceClient;
  tab: "profile" | "people" | "deliveries";
  account: AccountProps;
}) {
  const catalog = useResource(() => ws.catalog(), "cabinet-catalog");
  const people = useResource(() => ws.directory(), "cabinet-directory");
  const courses = catalog.data?.courses ?? [];
  const runs = catalog.data?.course_runs ?? [];
  const members = people.data?.items ?? [];
  const reviewers = members.filter((p) => p.roles.includes("reviewer"));
  const students = members.filter((p) => p.roles.includes("student"));
  return (
    <>
      <Topbar title="Кабинет" />
      <Main data-screen="К11">
        <Tabs className="tabs--page" label="Кабинет">
          <Tab href="#/cabinet" on={tab === "profile"}>
            Профиль
          </Tab>
          <Tab href="#/people" on={tab === "people"}>
            Участники
          </Tab>
          <Tab href="#/deliveries" on={tab === "deliveries"}>
            Доставки
          </Tab>
        </Tabs>
        {tab === "profile" && (
          <div className="stack">
            <Tiles>
              <Tile
                n={courses.length}
                l={plural(courses.length, "курс", "курса", "курсов")}
                d={`${runs.filter((x) => x.status === "active").length} ${plural(runs.filter((x) => x.status === "active").length, "активный поток", "активных потока", "активных потоков")}`}
              />
              <Tile
                n={reviewers.length}
                l={plural(reviewers.length, "ревьюер", "ревьюера", "ревьюеров")}
                d="с доступом к пулу"
              />
              <Tile
                n={students.length}
                l={plural(students.length, "студент", "студента", "студентов")}
                d="узнаны по ссылке из курса"
              />
              <Tile
                n={members.length}
                l={plural(
                  members.length,
                  "участник",
                  "участника",
                  "участников",
                )}
                d="в организации"
              />
            </Tiles>
            <div className="row-side">
              <Card>
                <CardHead title="Потоки" sub="Что открыто прямо сейчас" />
                {catalog.data && runs.length === 0 ? (
                  <Empty title="Потоков нет">
                    Создайте поток на обзоре, чтобы открыть задания студентам.
                  </Empty>
                ) : (
                  <CardBody flush>
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>Курс</th>
                          <th>Поток</th>
                          <th>Статус</th>
                          <th className="r"></th>
                        </tr>
                      </thead>
                      <tbody>
                        {runs.map((run) => (
                          <tr
                            key={run.id}
                            className={
                              run.status === "archived" ? "is-done" : undefined
                            }
                          >
                            <td>
                              <div className="who">
                                {courses.find((c) => c.id === run.course_id)
                                  ?.title ?? "Курс"}
                              </div>
                            </td>
                            <td>{run.title}</td>
                            <td>
                              <St status={run.status} />
                            </td>
                            <td className="r">
                              <BtnRow end>
                                <Btn
                                  size="s"
                                  variant="quiet"
                                  href={`#/registry?run=${run.id}`}
                                >
                                  Домашки
                                </Btn>
                                <Btn
                                  size="s"
                                  variant="quiet"
                                  href={`#/assignments/${run.id}`}
                                >
                                  Участники
                                </Btn>
                              </BtnRow>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </CardBody>
                )}
              </Card>
              <AccountCard {...account} />
            </div>
          </div>
        )}
        {tab === "people" && <PeopleBody api={api} />}
        {tab === "deliveries" && <DeliveriesBody api={api} />}
      </Main>
    </>
  );
}

export function initials(role: Role, userId: string) {
  return role === "reviewer"
    ? "РВ"
    : role === "methodologist"
      ? "КО"
      : short(userId);
}
