import { confirmNavigation, consumeProgrammaticNavigation } from "./navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { WorkspaceNotifications } from "./WorkspaceNotifications";
import { WorkspaceClient } from "./api/workspace";
import { WorkspaceHomework } from "./pages/WorkspaceHomework";
import { WorkspaceSubmissionDetail } from "./pages/WorkspaceSubmissionDetail";
import {
  WorkspaceSubmit,
  StudentSubmissionPage,
} from "./pages/WorkspaceStudent";
import { ActionMessageProvider, useActionMessage } from "./ActionMessage";
import { WorkspaceWorks, WorkspaceStatistics } from "./pages/WorkspaceLists";
import {
  WorkspaceCatalog,
  WorkspaceHomeworkDirectory,
  WorkspacePreferences,
  WorkspaceAssignments,
  WorkspaceHomeworkNew,
} from "./pages/WorkspaceCatalog";
import { ApiClient, ApiError, type Model, type Role } from "./api/client";
import { Empty, ErrorBox, go, roleNames, useAction, useResource } from "./ui";
import { CoursePage, CoursesPage, QueuePage } from "./pages/Courses";
import { ReviewPage } from "./pages/Review";
import { HomeworkPage } from "./pages/Homework";
import { OperationPage } from "./pages/Operations";
import { PeoplePage } from "./pages/People";
import {
  Ava,
  Band,
  Brand,
  Btn,
  Callout,
  Cheer,
  Modal,
  Pill,
  Sel,
  Shell,
  StudentTopbar,
  cx,
  short,
  studentNumber,
  type MenuItem,
} from "./ds";

/* Счётчики пунктов меню: страницы сообщают их из тех же выборок, что рисуют
   в таблицах; оболочка сама ничего не запрашивает.                          */
export type MenuCounts = Partial<
  Record<
    "works" | "pool" | "courses" | "homeworks" | "coordPool" | "registry",
    number
  >
>;
const CountsContext = createContext<{
  counts: MenuCounts;
  setCounts: (next: MenuCounts) => void;
}>({ counts: {}, setCounts: () => {} });
export function useMenuCounts() {
  return useContext(CountsContext);
}

export function App({ api, demo = false }: { api: ApiClient; demo?: boolean }) {
  return (
    <ActionMessageProvider>
      <Application api={api} demo={demo} />
    </ActionMessageProvider>
  );
}

function Application({ api, demo }: { api: ApiClient; demo: boolean }) {
  const ws = useMemo(() => new WorkspaceClient(api), [api]);
  const s = useResource(() => api.session(), "session");
  const message = useActionMessage();
  useEffect(() => message.clear(), [s.data?.user_id, message.clear]);
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    api.onUnauthorized = () => setExpired(true);
    return () => {
      api.onUnauthorized = undefined;
    };
  }, [api]);
  const [role, setRoleState] = useState<Role | undefined>(() => {
    try {
      return (sessionStorage.getItem("role") as Role) || undefined;
    } catch {
      return undefined;
    }
  });
  const setRole = useCallback((next: Role | undefined) => {
    setRoleState(next);
    try {
      if (next) sessionStorage.setItem("role", next);
      else sessionStorage.removeItem("role");
    } catch {
      /* хранилище недоступно: роль живёт только в памяти вкладки */
    }
  }, []);
  const [route, setRoute] = useState(window.location.hash.slice(1) || "/home");
  const [counts, setCountsState] = useState<MenuCounts>({});
  const setCounts = useCallback(
    (next: MenuCounts) => setCountsState((prev) => ({ ...prev, ...next })),
    [],
  );
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
      <div className="login login--wait" data-screen="Р1">
        <p role="status" className="caption">
          Проверяем сессию…
        </p>
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
  const logout = () =>
    void action.run(async () => {
      await api.request("/v1/session", { method: "DELETE" });
      api.clearPending();
      setRole(undefined);
      go("/home");
      s.refresh();
    });
  const accountProps = {
    session,
    role: activeRole,
    demo,
    busy: action.busy,
    onRole: (next: Role) => {
      if (!confirmNavigation()) return;
      setRole(next);
      go("/home");
    },
    onLogout: logout,
  };
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
        reviewerPool={section === "pool"}
      />
    );
  else if (section === "dashboard" && activeRole === "methodologist")
    page = <WorkspaceCatalog ws={ws} mode="overview" />;
  else if (section === "runs" && activeRole === "methodologist")
    page = <WorkspaceCatalog ws={ws} mode="runs" />;
  else if (section === "homeworks" && activeRole === "methodologist")
    page = <WorkspaceHomeworkDirectory ws={ws} />;
  else if (section === "homework-new" && activeRole === "methodologist")
    page = <WorkspaceHomeworkNew ws={ws} />;
  else if (section === "statistics" && session.roles.includes("reviewer"))
    page = <WorkspaceStatistics ws={ws} />;
  else if (section === "assignments" && id && activeRole === "methodologist")
    page = <WorkspaceAssignments ws={ws} runId={id} />;
  else if (section === "prepare" && id && activeRole === "student")
    page = (
      <WorkspaceSubmit
        ws={ws}
        id={id}
        session={session}
        attemptId={new URLSearchParams(query).get("attempt") ?? undefined}
      />
    );
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
        coordinator={activeRole === "methodologist"}
        ws={ws}
      />
    );
  else if (section === "submit" && activeRole === "student" && id && subId)
    page = (
      <WorkspaceSubmit
        ws={ws}
        id={subId}
        session={session}
        attemptId={new URLSearchParams(query).get("attempt") ?? undefined}
      />
    );
  else if (section === "submissions" && id)
    page =
      activeRole === "student" ? (
        <StudentSubmissionPage
          ws={ws}
          id={id}
          session={session}
          attemptId={new URLSearchParams(query).get("attempt") ?? undefined}
        />
      ) : (
        <WorkspaceSubmissionDetail ws={ws} id={id} />
      );
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
  else if (
    (section === "cabinet" ||
      section === "people" ||
      section === "deliveries") &&
    activeRole === "methodologist"
  )
    page = <PeoplePage api={api} ws={ws} />;
  else if (section === "preferences" && session.roles.includes("reviewer"))
    page = <WorkspacePreferences ws={ws} session={session} />;
  else if (section === "operations" && id)
    page = <OperationPage api={api} id={id} />;
  else
    page = (
      <div className="main">
        <Empty>
          Страница недоступна. <a href="#/courses">Вернуться к курсам</a>
        </Empty>
      </div>
    );

  const account = <AccountMenu {...accountProps} />;
  const skip = (
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
  );
  const main = (
    <main id="main" tabIndex={-1} key={`${activeRole}:${route}`}>
      {!!action.error && (
        <div className="main main--feedback">{action.feedback}</div>
      )}
      {page}
    </main>
  );
  const provider = (children: ReactNode) => (
    <CountsContext.Provider value={{ counts, setCounts }}>
      {activeRole === "reviewer" && (
        <ReviewerCounts ws={ws} userId={session.user_id} route={route} />
      )}
      {children}
    </CountsContext.Provider>
  );

  if (activeRole === "student")
    return provider(
      <div className="student">
        {skip}
        <StudentTopbar
          nav={[
            {
              href: "#/works",
              label: "Мои домашки",
              on: ["works", "submissions", "prepare", "submit"].includes(
                section,
              ),
            },
          ]}
          right={account}
        />
        {main}
        <Cheer />
      </div>,
    );

  const menu: MenuItem[] =
    activeRole === "reviewer"
      ? [
          {
            href: "#/works",
            label: "Мои работы",
            icon: "works" as const,
            count: counts.works,
            on: section === "works",
          },
          {
            href: "#/pool",
            label: "Пул",
            icon: "pool" as const,
            count: counts.pool,
            on: section === "pool",
          },
          {
            href: "#/preferences",
            label: "Кабинет",
            icon: "cabinet" as const,
            on: section === "preferences" || section === "statistics",
          },
        ]
      : [
          {
            href: "#/dashboard",
            label: "Обзор",
            icon: "overview" as const,
            on: ["dashboard", "coord-pool", "registry"].includes(section),
          },
          {
            href: "#/courses",
            label: "Курсы",
            icon: "courses" as const,
            count: counts.courses,
            on: ["courses", "homeworks", "homework"].includes(section),
          },
          {
            href: "#/runs",
            label: "Потоки",
            icon: "runs" as const,
            on: section === "runs",
          },
          {
            href: "#/people",
            label: "Ревьюеры",
            icon: "people" as const,
            on: ["people", "cabinet", "deliveries"].includes(section),
          },
        ];
  return provider(
    <>
      {skip}
      <Shell menu={menu} foot={account}>
        {main}
        <WorkspaceNotifications ws={ws} />
        <Cheer />
      </Shell>
    </>,
  );
}

/* Подвал панели и аватар студента: человек и роль как в макете, по клику
   раскрывается смена роли и выход. В макете этих действий нет.             */
function AccountMenu({
  session,
  role,
  demo,
  busy,
  onRole,
  onLogout,
}: {
  session: Model<"Session">;
  role: Role;
  demo: boolean;
  busy: boolean;
  onRole: (role: Role) => void;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeMenu = () => setOpen(false);
    window.addEventListener("hashchange", closeMenu);
    const away = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("hashchange", closeMenu);
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const initials =
    role === "reviewer"
      ? "РВ"
      : role === "methodologist"
        ? "КО"
        : short(session.user_id);
  const name =
    role === "reviewer"
      ? `rev-${short(session.user_id)}`
      : role === "methodologist"
        ? "Координатор"
        : `Студент ${studentNumber(session.user_id)}`;
  const caption =
    role === "reviewer"
      ? "ревьюер"
      : role === "methodologist"
        ? "методист"
        : "студент";
  const student = role === "student";
  return (
    <div className={cx("acct", student && "acct--student")} ref={ref}>
      <button
        type="button"
        className="acct__s"
        aria-label={student ? "Ваш профиль" : `${name}, ${caption}`}
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((v) => !v)}
      >
        <Ava seed={session.user_id}>{initials}</Ava>
        {!student && (
          <span>
            <span className="small aside__name">{name}</span>
            <span className="caption">{caption}</span>
          </span>
        )}
      </button>
      {open && (
        <div className="pop pop--acct">
          <div className="pop__g">Учётная запись</div>
          <div className="pop__row pop__row--static">
            <div className="t">
              <span>{name}</span>
              <span className="s">{roleNames[role]}</span>
            </div>
            {demo && <Pill>Демо</Pill>}
          </div>
          {session.roles.length > 1 && (
            <div className="pop__row pop__row--static">
              <label className="field field--inline">
                <span className="field__lbl">Роль</span>
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
              </label>
            </div>
          )}
          <div className="pop__foot">
            {demo && <span>Данные живут в памяти вкладки</span>}
            <Btn size="s" variant="quiet" disabled={busy} onClick={onLogout}>
              Выйти
            </Btn>
          </div>
        </div>
      )}
    </div>
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
  const [stepikInfo, setStepikInfo] = useState(false);
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
    <div className="login" data-screen="Р1">
      <Band art="login">
        <Brand />
        <h1 className="d2">Готовый разбор по каждой работе</h1>
      </Band>
      <div className="login__side">
        <div className="login__form">
          <h2>Войти в рабочее пространство</h2>
          {!!error &&
            (!(error instanceof ApiError) || error.status !== 401) && (
              <ErrorBox error={error} retry={refresh} />
            )}
          {action.feedback}
          {!!local.error && (
            <Callout tone="bad" role="alert">
              <p>Не удалось проверить доступные способы входа.</p>
              <Btn size="s" onClick={local.refresh}>
                Повторить загрузку способов входа
              </Btn>
            </Callout>
          )}
          {action.busy ? (
            <p role="status" className="caption">
              Завершаем вход…
            </p>
          ) : callback || invitation ? (
            <Btn
              variant="pri"
              size="l"
              onClick={() => void action.run(complete)}
            >
              Повторить вход по ссылке
            </Btn>
          ) : demo ? (
            <>
              <Btn
                variant="pri"
                size="l"
                onClick={() => window.location.reload()}
              >
                Начать демо заново
              </Btn>
              <span className="caption">
                Демо-режим. Данные существуют в памяти этой вкладки.
              </span>
            </>
          ) : (
            <>
              {local.data?.enabled ? (
                <Btn variant="pri" size="l" onClick={() => setStepikInfo(true)}>
                  Войти через Stepik
                </Btn>
              ) : (
                <Btn
                  href="/api/v1/auth/stepik/start"
                  variant="pri"
                  size="l"
                  aria-disabled={local.loading || !!local.error}
                  onClick={(event) => {
                    if (local.loading || local.error) event.preventDefault();
                  }}
                >
                  Войти через Stepik
                </Btn>
              )}
              <span className="caption">
                {local.data?.enabled
                  ? "Для демонстрации выберите участника локального стенда."
                  : "Для студентов и координаторов. После входа откроется ваше рабочее пространство."}
              </span>
            </>
          )}
          {local.data?.enabled && (
            <div className="login__local">
              <span className="label">Локальный стенд</span>

              {stepikInfo && (
                <Modal
                  title="Вход через Stepik"
                  close={() => setStepikInfo(false)}
                >
                  <p>
                    Вход через Stepik пока не подключён на этом стенде. Для
                    демонстрации выберите участника ниже.
                  </p>
                  <Btn variant="pri" onClick={() => setStepikInfo(false)}>
                    Понятно
                  </Btn>
                </Modal>
              )}
              <div className="stack--login">
                {local.data.items.map((identity) => (
                  <Btn
                    key={identity.key}
                    className="login__identity"
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
                  </Btn>
                ))}
              </div>
            </div>
          )}
          {!demo && !callback && !invitation && (
            <Callout tone="info">
              <p>
                <b>Вы ревьюер?</b> Откройте ссылку в письме с приглашением, она
                выполнит вход автоматически. Если приглашения нет, обратитесь к
                координатору курса.
              </p>
            </Callout>
          )}
        </div>
      </div>
    </div>
  );
}

function ReviewerCounts({
  ws,
  userId,
  route,
}: {
  ws: WorkspaceClient;
  userId: string;
  route: string;
}) {
  const { setCounts } = useMenuCounts();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = () => setRevision((v) => v + 1);
    window.addEventListener("workspace:changed", changed);
    return () => window.removeEventListener("workspace:changed", changed);
  }, []);
  const data = useResource(
    async () => {
      const [mine, pool] = await Promise.all([
        ws.works({ view: "active", limit: 1 }),
        ws.works({ view: "pool", limit: 1 }),
      ]);
      return { works: mine.total, pool: pool.total };
    },
    `${userId}:${route}:${revision}`,
    30000,
  );
  useEffect(() => {
    if (data.data) setCounts(data.data);
  }, [data.data, setCounts]);
  return null;
}
