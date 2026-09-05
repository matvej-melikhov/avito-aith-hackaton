import { HeaderProfileContext } from "./workspace-ui";
import { Aside, Brand, Btn } from "./ds";
import { confirmNavigation, consumeProgrammaticNavigation } from "./navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { WorkspaceNotifications } from "./WorkspaceNotifications";
import { WorkspaceClient, type W } from "./api/workspace";
import { WorkspaceHomework } from "./pages/WorkspaceHomework";
import { WorkspaceSubmissionDetail } from "./pages/WorkspaceSubmissionDetail";
import { WorkspaceSubmit } from "./pages/WorkspaceStudent";
import { WorkspaceWorks, WorkspaceStatistics } from "./pages/WorkspaceLists";
import {
  WorkspaceCatalog,
  WorkspaceHomeworkDirectory,
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
  const profile = useResource(
    () =>
      s.data
        ? api.request<W<"ProfileView">>("/v2/profile")
        : Promise.resolve(null),
    `own-profile:${s.data?.user_id ?? "none"}`,
  );
  useEffect(() => {
    if (!s.data) return;
    const owner = `${s.data.organization_id}:${s.data.user_id}`;
    if (sessionStorage.getItem("review-ui-owner") !== owner)
      sessionStorage.removeItem("review-ui-selected-run");
    sessionStorage.setItem("review-ui-owner", owner);
  }, [s.data?.organization_id, s.data?.user_id]);
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
          ? "works"
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
        reviewerMode={section === "pool" ? "pool" : "active"}
      />
    );
  else if (section === "dashboard" && activeRole === "methodologist")
    page = <WorkspaceCatalog ws={ws} mode="overview" />;
  else if (section === "homeworks" && activeRole === "methodologist")
    page = <WorkspaceHomeworkDirectory ws={ws} />;
  else if (section === "statistics" && session.roles.includes("reviewer"))
    page = <WorkspaceStatistics ws={ws} />;
  else if (section === "assignments" && id && activeRole === "methodologist")
    page = <WorkspaceAssignments ws={ws} runId={id} />;
  else if (section === "prepare" && id && activeRole === "student")
    page = <WorkspaceSubmit ws={ws} id={id} session={session} />;
  else if (section === "courses")
    page = id ? (
      <CoursePage api={api} id={id} role={activeRole} />
    ) : activeRole === "methodologist" ? (
      <WorkspaceCatalog ws={ws} mode="courses" />
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
    page = <WorkspaceSubmissionDetail ws={ws} id={id} role={activeRole} />;
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
            ["works", "Мои работы"],
            ["pool", "Пул"],
            ["preferences", "Кабинет"],
          ]
        : [
            ["dashboard", "Обзор"],
            ["courses", "Курсы"],
            ["homeworks", "Задания"],
            ["coord-pool", "Пул проверок"],
            ["registry", "Домашки"],
          ];
  const accountControls = (
    <>
      {" "}
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
    </>
  );
  const profileMenu = (
    <details className="profile-menu">
      <summary
        className="avatar"
        aria-label="Ваш профиль"
        title={roleNames[activeRole]}
      >
        {activeRole === "student"
          ? session.user_id.slice(0, 4)
          : activeRole === "reviewer"
            ? "РВ"
            : "КО"}
      </summary>
      <div className="account-controls">
        {profile.data?.display_name && (
          <strong>{profile.data.display_name}</strong>
        )}
        <span className="small">{roleNames[activeRole]}</span>
        {accountControls}
      </div>
    </details>
  );
  return (
    <div
      className={`app-shell ${activeRole === "student" ? "student-shell" : ""}`}
    >
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
      {activeRole !== "student" && (
        <SidebarNavigation
          ws={ws}
          role={activeRole}
          items={nav}
          section={section}
        />
      )}
      <div className="main-area">
        {activeRole === "student" && (
          <header>
            <div>
              {activeRole === "student" ? (
                <Brand href="#/home" flat />
              ) : (
                <>
                  <span className="muted">Авито Ревью / </span>
                  {roleNames[activeRole]}
                </>
              )}
            </div>
            <div className="actions">
              {activeRole === "student" && (
                <>
                  <Btn size="s" href="#/courses">
                    Мои курсы
                  </Btn>
                  {["prepare", "submit"].includes(section) && (
                    <Btn size="s" href="#/works">
                      Мои домашки
                    </Btn>
                  )}
                  {profileMenu}
                </>
              )}
            </div>
          </header>
        )}
        <HeaderProfileContext.Provider
          value={activeRole === "student" ? null : profileMenu}
        >
          <main id="main" tabIndex={-1} key={`${activeRole}:${route}`}>
            {action.feedback}
            {page}
          </main>
        </HeaderProfileContext.Provider>
        {activeRole !== "student" && <WorkspaceNotifications ws={ws} />}
      </div>
    </div>
  );
}

function SidebarNavigation({
  ws,
  role,
  items,
  section,
}: {
  ws: WorkspaceClient;
  role: Role;
  items: string[][];
  section: string;
}) {
  const counts = useResource<Record<string, number>>(
    async (): Promise<Record<string, number>> => {
      if (role === "reviewer") {
        const [active, pool] = await Promise.all([
          ws.works({ view: "active", limit: 1 }),
          ws.works({ view: "pool", limit: 1 }),
        ]);
        return { works: active.total, pool: pool.total };
      }
      const [catalog, pool, all] = await Promise.all([
        ws.catalog(),
        ws.works({ view: "pool", limit: 1 }),
        ws.works({ view: "all", limit: 1 }),
      ]);
      const homeworks = await Promise.all(
        catalog.courses.map((c) => ws.courseHomeworks(c.id)),
      );
      return {
        courses: catalog.courses.length,
        homeworks: homeworks.reduce((sum, h) => sum + h.items.length, 0),
        "coord-pool": pool.total,
        registry: all.total,
      };
    },
    role,
    15000,
  );
  return (
    <Aside
      className="sidebar"
      menu={items.map(([link, label]) => ({
        href: `#/${link}`,
        label,
        on:
          section === link ||
          (link === "preferences" && section === "statistics"),
        count: counts.data?.[link],
      }))}
    />
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
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const action = useAction();
  const attempted = useRef(false);
  const local = useResource(
    () =>
      api.request<{
        enabled: boolean;
        items: { key: string; label: string; roles: string[] }[];
      }>("/v1/auth/local/identities"),
    "local-identities",
  );
  const callback = params.has("code") && params.has("state");
  const invitation = params.has("token") && params.has("state_id");
  async function complete() {
    if (callback) {
      await api.request(
        `/v1/auth/stepik/callback?${new URLSearchParams({ code: params.get("code")!, state: params.get("state")! })}`,
        { method: "POST" },
      );
    } else if (invitation) {
      await api.request("/v1/auth/reviewer/magic-link", {
        method: "POST",
        body: JSON.stringify({
          token: params.get("token"),
          state_id: params.get("state_id"),
        }),
      });
    } else return;
    history.replaceState(
      null,
      "",
      window.location.pathname + window.location.hash,
    );
    refresh();
  }
  useEffect(() => {
    if ((callback || invitation) && !attempted.current) {
      attempted.current = true;
      void action.run(complete);
    }
  }, [callback, invitation]);
  return (
    <div className="login-layout" data-screen="Р1">
      <div className="login-intro band">
        <div className="brand-circles" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <div className="band-content">
          <a className="brand" href="#/home">
            <span className="brand-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            Авито Ревью
          </a>
          <h1 className="d2"> Проверка учебных работ </h1>
          <p>
            Модель разбирает работу по требованиям задания и готовит черновик.
            Ревьюер проверяет выводы и принимает решение.
          </p>
        </div>
      </div>
      <div className="login-form">
        <div>
          <h2> Вход </h2>
          {demo && (
            <p className="notice">
              Демо-режим. Данные существуют в памяти этой вкладки.
            </p>
          )}
          {!!error &&
            (!(error instanceof ApiError) || error.status !== 401) && (
              <ErrorBox error={error} retry={refresh} />
            )}
          {action.feedback}
          {local.data?.enabled && (
            <div className="login-invitation">
              <h3>Локальный стенд</h3>
              <p className="muted">
                Выберите участника для входа на локальный стенд.{" "}
              </p>
              <div className="stack">
                {local.data.items.map((identity) => (
                  <button
                    key={identity.key}
                    disabled={action.busy}
                    onClick={() =>
                      void action.run(async () => {
                        await api.request("/v1/auth/local/login", {
                          method: "POST",
                          body: JSON.stringify({ identity: identity.key }),
                        });
                        refresh();
                      })
                    }
                  >
                    {identity.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {action.busy ? (
            <p role="status">Завершаем вход…</p>
          ) : callback || invitation ? (
            <button
              className="primary"
              onClick={() => void action.run(complete)}
            >
              Повторить вход по ссылке
            </button>
          ) : demo ? (
            <button
              className="primary"
              onClick={() => window.location.reload()}
            >
              Начать демо заново
            </button>
          ) : (
            <>
              <a className="button primary" href="/api/v1/auth/stepik/start">
                Войти через Stepik
              </a>
              <p className="muted">Для студентов и координаторов. </p>
              <div className="login-invitation">
                <h3>Вы ревьюер?</h3>
                <p>
                  Войдите по ссылке из приглашения. Если приглашения нет,
                  обратитесь к координатору курса.{" "}
                </p>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
