import { confirmNavigation, consumeProgrammaticNavigation } from "./navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { WorkspaceClient } from "./api/workspace";
import { WorkspaceHomework } from "./pages/WorkspaceHomework";
import { WorkspaceSubmissionDetail } from "./pages/WorkspaceSubmissionDetail";
import { WorkspaceSubmit } from "./pages/WorkspaceStudent";
import { WorkspaceWorks, WorkspaceStatistics } from "./pages/WorkspaceLists";
import {
  WorkspaceCatalog,
  WorkspacePreferences,
  WorkspaceAssignments,
} from "./pages/WorkspaceCatalog";
import { ApiClient, ApiError, type Model, type Role } from "./api/client";
import {
  Card,
  Empty,
  ErrorBox,
  Resource,
  go,
  roleNames,
  useAction,
  useResource,
} from "./ui";
import { CoursePage, CoursesPage, QueuePage } from "./pages/Courses";
import { ReviewPage } from "./pages/Review";
import { SubmitPage, SubmissionPage } from "./pages/Submission";
import { HomeworkPage } from "./pages/Homework";
import { PeoplePage, PreferencesPage } from "./pages/People";
import { DeliveriesPage, OperationPanel } from "./pages/Operations";
export function App({ api, demo = false }: { api: ApiClient; demo?: boolean }) {
  const ws = useMemo(() => new WorkspaceClient(api), [api]);
  const s = useResource(() => api.session(), "session");
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    api.onUnauthorized = () => setExpired(true);
    return () => {
      api.onUnauthorized = undefined;
    };
  }, [api]);
  const [role, setRole] = useState<Role>();
  const [route, setRoute] = useState(window.location.hash.slice(1) || "/home");
  const previousRoute = useRef(route);
  useEffect(() => {
    const update = () => {
      const next = window.location.hash.slice(1) || "/home";
      if (
        next !== previousRoute.current &&
        !consumeProgrammaticNavigation() &&
        !confirmNavigation()
      ) {
        history.replaceState(
          null,
          "",
          window.location.pathname +
            window.location.search +
            "#" +
            previousRoute.current,
        );
        return;
      }
      previousRoute.current = next;
      setRoute(next);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const action = useAction();
  const session = s.data;
  const activeRole =
    role && session?.roles.includes(role)
      ? role
      : (session?.roles.find((r) => r === "reviewer") ?? session?.roles[0]);
  const refreshSession = async () => {
    s.refresh();
  };
  if (s.loading)
    return (
      <div className="login" data-screen="Р1">
        <p role="status">Проверяем сессию…</p>
      </div>
    );
  if (!session || expired)
    return (
      <Login
        api={api}
        error={s.error}
        refresh={() => {
          setExpired(false);
          s.refresh();
        }}
        demo={demo}
      />
    );
  if (!activeRole)
    return <Empty>У вашей учётной записи нет активной роли.</Empty>;
  const [path, query = ""] = route.split("?");
  const [, routeSection, id, subId] = path.split("/");
  const section =
    routeSection === "home"
      ? activeRole === "student"
        ? "works"
        : activeRole === "reviewer"
          ? "pool"
          : "dashboard"
      : routeSection;
  const canReview = session.roles.some(
    (r) => r === "reviewer" || r === "methodologist",
  );
  let page;
  if (
    section === "works" ||
    section === "pool" ||
    section === "registry" ||
    section === "coord-pool"
  )
    page = (
      <WorkspaceWorks
        ws={ws}
        role={activeRole}
        coordinatorPool={section === "coord-pool"}
      />
    );
  else if (section === "dashboard" && activeRole === "methodologist")
    page = <WorkspaceCatalog ws={ws} />;
  else if (section === "statistics" && session.roles.includes("reviewer"))
    page = <WorkspaceStatistics ws={ws} />;
  else if (section === "assignments" && id && activeRole === "methodologist")
    page = <WorkspaceAssignments ws={ws} runId={id} />;
  else if (section === "prepare" && id && activeRole === "student")
    page = <WorkspaceSubmit ws={ws} id={id} session={session} />;
  else if (section === "courses")
    page = id ? (
      <CoursePage api={api} id={id} role={activeRole} />
    ) : (
      <CoursesPage api={api} role={activeRole} />
    );
  else if (section === "queue" && session.roles.includes("reviewer") && id)
    page = <QueuePage api={api} id={id} />;
  else if (section === "reviews" && canReview && id)
    page = (
      <ReviewPage
        api={api}
        id={id}
        session={session}
        readOnly={activeRole === "methodologist"}
        ws={ws}
      />
    );
  else if (section === "submit" && activeRole === "student" && id && subId)
    page = <WorkspaceSubmit ws={ws} id={subId} session={session} />;
  else if (section === "submissions" && id)
    page = <WorkspaceSubmissionDetail ws={ws} id={id} />;
  else if (section === "homework" && id && activeRole === "methodologist")
    page = (
      <WorkspaceHomework
        ws={ws}
        id={id}
        run={new URLSearchParams(query).get("run") ?? ""}
        session={session}
      />
    );
  else if (section === "homework" && id)
    page = (
      <HomeworkPage
        api={api}
        id={id}
        run={new URLSearchParams(query).get("run") ?? ""}
        role={activeRole}
      />
    );
  else if (section === "people" && session.roles.includes("methodologist"))
    page = <PeoplePage api={api} />;
  else if (section === "preferences" && session.roles.includes("reviewer"))
    page = <WorkspacePreferences ws={ws} session={session} />;
  else if (section === "deliveries" && session.roles.includes("methodologist"))
    page = <DeliveriesPage api={api} />;
  else if (section === "operations" && id)
    page = <OperationPanel api={api} id={id} />;
  else if (section === "open") page = <OpenResource role={activeRole} />;
  else
    page = (
      <Empty>
        Страница недоступна. <a href="#/courses">Вернуться к курсам</a>
      </Empty>
    );
  const nav =
    activeRole === "student"
      ? [
          ["works", "Мои домашки"],
          ["courses", "Мои курсы"],
        ]
      : activeRole === "reviewer"
        ? [
            ["pool", "Мои работы и общий пул"],
            ["courses", "Курсы"],
            ["preferences", "Настройки"],
            ["statistics", "Статистика"],
          ]
        : [
            ["dashboard", "Обзор"],
            ["coord-pool", "Пул проверок"],
            ["registry", "Реестр домашних работ"],
            ["courses", "Курсы и задания"],
            ["people", "Участники"],
            ["deliveries", "Доставки"],
          ];
  return (
    <div className="app-shell">
      <a
        className="skip"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        Перейти к содержимому
      </a>
      <aside className="sidebar">
        <a className="brand" href="#/home">
          <span className="mark">Р</span>Платформа ревью
        </a>
        <div className="nav-label">Рабочее пространство</div>
        <nav>
          {nav.map(([link, label]) => (
            <a
              key={link}
              href={`#/${link}`}
              aria-current={section === link ? "page" : undefined}
            >
              {label}
              <span>›</span>
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="avatar">{roleNames[activeRole][0]}</div>
          <div>
            <strong>{roleNames[activeRole]}</strong>
            <small>{session.user_id.slice(0, 8)}</small>
          </div>
        </div>
      </aside>
      <div className="main-area">
        <header>
          <div>
            <span className="muted">Учебная платформа / </span>
            {roleNames[activeRole]}
          </div>
          <div className="actions">
            {demo && <span className="demo-tag">Демо · локальные данные</span>}
            {session.roles.length > 1 && (
              <select
                aria-label="Роль"
                value={activeRole}
                onChange={(e) => {
                  if (!confirmNavigation()) return;
                  setRole(e.target.value as Role);
                  go("/home");
                }}
              >
                {session.roles.map((r) => (
                  <option key={r} value={r}>
                    {roleNames[r]}
                  </option>
                ))}
              </select>
            )}
            <button
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  await api.request("/v1/session", { method: "DELETE" });
                  api.clearPending();
                  setRole(undefined);
                  go("/home");
                  s.refresh();
                })
              }
            >
              Выйти
            </button>
          </div>
        </header>
        <main id="main" tabIndex={-1} key={`${activeRole}:${route}`}>
          {action.feedback}
          {page}
        </main>
      </div>
    </div>
  );
}
function OpenResource({ role }: { role: Role }) {
  const [id, setId] = useState("");
  return (
    <>
      <h1>{role === "student" ? "Открыть мою работу" : "Открыть проверку"}</h1>
      <Card title="Ссылка на работу">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            go(`/${role === "student" ? "submissions" : "reviews"}/${id}`);
          }}
        >
          <label>
            ID из ссылки на работу
            <input
              required
              pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
              value={id}
              onChange={(e) => setId(e.target.value.trim())}
            />
          </label>
          <button className="primary">Открыть</button>
        </form>
        <p className="muted">
          Общий список работ пока недоступен. Для новой проверки выберите поток
          в разделе курсов.
        </p>
      </Card>
    </>
  );
}
function Login({
  api,
  error,
  refresh,
  demo,
}: {
  api: ApiClient;
  error: unknown;
  refresh: () => void;
  demo: boolean;
}) {
  const params = new URLSearchParams(window.location.search);
  const [token, setToken] = useState(params.get("token") ?? "");
  const [stateId, setStateId] = useState(params.get("state_id") ?? "");
  const action = useAction();
  const callback = params.has("code") && params.has("state");
  async function complete() {
    if (callback) {
      await api.request(
        `/v1/auth/stepik/callback?${new URLSearchParams({ code: params.get("code")!, state: params.get("state")! })}`,
        { method: "POST" },
      );
    } else {
      await api.request("/v1/auth/reviewer/magic-link", {
        method: "POST",
        body: JSON.stringify({ token, state_id: stateId }),
      });
    }
    history.replaceState(
      null,
      "",
      window.location.pathname + window.location.hash,
    );
    setToken("");
    setStateId("");
    refresh();
  }
  return (
    <div className="login" data-screen="Р1">
      <a className="brand" href="#/home">
        <span className="mark">Р</span>Платформа ревью
      </a>
      <Card title="Войти в рабочее пространство">
        {demo && (
          <p className="notice">
            Демо-режим. Данные существуют только в памяти этой вкладки.
          </p>
        )}
        {!!error && (!(error instanceof ApiError) || error.status !== 401) && (
          <ErrorBox error={error} retry={refresh} />
        )}
        <p>
          Студенты и координаторы входят через Stepik. Ревьюеры — по
          приглашению.
        </p>
        {demo ? (
          <button className="primary" onClick={() => window.location.reload()}>
            Начать демо заново
          </button>
        ) : callback ? (
          <button
            className="primary"
            disabled={action.busy}
            onClick={() => void action.run(complete)}
          >
            Завершить вход через Stepik
          </button>
        ) : (
          <a className="button primary" href="/api/v1/auth/stepik/start">
            Войти через Stepik
          </a>
        )}
        {action.feedback}
        <details open={!!token}>
          <summary>У меня есть приглашение ревьюера</summary>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(complete);
            }}
          >
            <label>
              Токен приглашения
              <input
                type="password"
                required
                minLength={32}
                maxLength={4096}
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            <label>
              Код состояния из приглашения
              <input
                required
                pattern="[0-9a-fA-F-]{36}"
                value={stateId}
                onChange={(e) => setStateId(e.target.value)}
              />
            </label>
            <button disabled={action.busy}>Войти по приглашению</button>
          </form>
        </details>
      </Card>
    </div>
  );
}
