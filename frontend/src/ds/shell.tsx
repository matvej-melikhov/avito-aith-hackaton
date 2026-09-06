// Оболочка приложения: панель, шапка, витринная плашка, крошки, шаги мастера,
// нижняя панель действий. Разметка из docs/design/screens.html (Р2, С1, К5).
import {
  useEffect,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import { cx } from "./controls";
import { Logo, LogoMark } from "./logo";

export const BRAND = "Avito Reviewer";

export function Brand({
  href,
  flat,
  mark,
  className,
}: {
  href?: string;
  flat?: boolean;
  /** Узкая панель: от логотипа остаётся только знак. */
  mark?: boolean;
  className?: string;
}) {
  const cls = cx(
    "brand",
    flat && "brand--flat",
    mark && "brand--mark",
    className,
  );
  const body = mark ? <LogoMark /> : <Logo />;
  return href ? (
    <a className={cls} href={href} aria-label={BRAND}>
      {body}
    </a>
  ) : (
    <span className={cls}>{body}</span>
  );
}

/* --- Значки разделов --------------------------------------------------------
   Один контур в currentColor на сетке 16, как у лупы поиска.               */
export type MenuIcon =
  "overview" | "courses" | "runs" | "people" | "works" | "pool" | "cabinet";

const ICON_PATHS: Record<MenuIcon, ReactNode> = {
  overview: (
    <path d="M2.6 2.6H7V7H2.6ZM9 2.6h4.4V7H9ZM2.6 9H7v4.4H2.6ZM9 9h4.4v4.4H9Z" />
  ),
  courses: <path d="M8 4.4 2.6 2.8v10.4L8 14.6l5.4-1.4V2.8L8 4.4Zm0 0v10.2" />,
  runs: <path d="M2.6 3.8h10.8v9.6H2.6ZM2.6 6.8h10.8M5.6 2.2v3M10.4 2.2v3" />,
  people: (
    <path d="M3.6 2.8h4.8v4.2H3.6ZM1.8 13.8v-2.4h8.4v2.4M10.2 4.6h3.4v3.4h-3.4M11.4 13.8v-2h2.8" />
  ),
  works: (
    <path d="M3.2 2.6h7.2l2.4 2.4v8.4H3.2ZM10.4 2.6V5h2.4M5.4 8.6l1.8 1.8 3.4-3.4" />
  ),
  pool: (
    <path d="M4.6 2.6h6.8l2.6 6.6v4.2H2V9.2ZM2 9.2h3.4l1 1.8h3.2l1-1.8h3.4" />
  ),
  cabinet: <path d="M5.6 2.6h4.8V7H5.6ZM2.6 13.8v-2.6h10.8v2.6" />,
};

export function Icon({ name }: { name: MenuIcon }) {
  return (
    <svg
      className="mi"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

export function Ava({
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"span">) {
  return (
    <span className={cx("ava", className)} {...rest}>
      {children}
    </span>
  );
}

export type MenuItem = {
  href: string;
  label: string;
  icon?: MenuIcon;
  count?: number | string | null;
  on?: boolean;
};

const RAIL_KEY = "aside-rail";

/** Панель свёрнута до значков. Выбор человека переживает перезагрузку. */
function useRail() {
  const [rail, setRail] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(RAIL_KEY, rail ? "1" : "0");
    } catch {
      /* хранилище недоступно: состояние живёт до перезагрузки */
    }
  }, [rail]);
  return [rail, setRail] as const;
}

/** Панель: бренд, один список разделов, подвал с человеком. */
export function Aside({
  brandHref = "#/home",
  menu,
  foot,
  rail = false,
  onRail,
}: {
  brandHref?: string;
  menu: MenuItem[];
  foot?: ReactNode;
  rail?: boolean;
  onRail?: () => void;
}) {
  return (
    <aside className={cx("aside", rail && "aside--rail")}>
      <div className="aside__top">
        <Brand href={brandHref} mark={rail} />
      </div>
      <nav className="menu" aria-label="Разделы">
        {menu.map((m) => (
          <a
            key={m.href}
            className={cx(m.on && "is-on")}
            href={m.href}
            aria-current={m.on ? "page" : undefined}
            title={rail ? m.label : undefined}
          >
            <span className="menu__lead">
              {m.icon && <Icon name={m.icon} />}
              <span className="menu__label">{m.label}</span>
            </span>
            {m.count !== undefined && m.count !== null && (
              <span className="c">{m.count}</span>
            )}
          </a>
        ))}
      </nav>
      {foot && <div className="aside__foot">{foot}</div>}
      {onRail && (
        <button
          type="button"
          className="aside__rail"
          aria-expanded={!rail}
          aria-label={rail ? "Развернуть панель" : "Свернуть панель"}
          title={rail ? "Развернуть панель" : "Свернуть панель"}
          onClick={onRail}
        >
          <svg
            viewBox="0 0 8 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="square"
            strokeLinejoin="miter"
            aria-hidden="true"
          >
            <path
              d={rail ? "M2.5 8.5 6 12l-3.5 3.5" : "M5.5 8.5 2 12l3.5 3.5"}
            />
          </svg>
        </button>
      )}
    </aside>
  );
}

export function Shell({
  menu,
  foot,
  brandHref,
  children,
}: {
  menu: MenuItem[];
  foot?: ReactNode;
  brandHref?: string;
  children: ReactNode;
}) {
  const [rail, setRail] = useRail();
  return (
    <div className={cx("app", rail && "app--rail")}>
      <Aside
        menu={menu}
        foot={foot}
        brandHref={brandHref}
        rail={rail}
        onRail={() => setRail((v) => !v)}
      />
      <div className="app__page">{children}</div>
    </div>
  );
}

/** Шапка экрана: слева заголовок (и крошки, статус, поиск), справа действия. */
export function Topbar({
  title,
  crumbs,
  status,
  lead,
  actions,
  center,
  page,
  className,
  children,
}: {
  title?: ReactNode;
  crumbs?: ReactNode;
  status?: ReactNode;
  lead?: ReactNode;
  actions?: ReactNode;
  center?: boolean;
  /** Внутренняя ширина как у контента студента (`.page`). */
  page?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  const heading = title && <h1>{title}</h1>;
  const inner = (
    <>
      <div className={cx(lead ? "topbar__lead" : undefined)}>
        {crumbs}
        {status ? (
          <div
            className={cx("topbar__title", !crumbs && "topbar__title--flat")}
          >
            {heading}
            {status}
          </div>
        ) : (
          heading
        )}
        {lead}
        {children}
      </div>
      {actions && <div className="btn-row">{actions}</div>}
    </>
  );
  const cls = cx(
    "topbar",
    (center || !crumbs) && "topbar--center",
    page && "topbar--page",
    className,
  );
  return page ? (
    <div className={cls}>
      <div className="page topbar__in">{inner}</div>
    </div>
  ) : (
    <div className={cls}>{inner}</div>
  );
}

/** Шапка студента: бренд слева, разделы и аватар справа. */
export function StudentTopbar({
  nav = [],
  right,
  brandHref = "#/home",
}: {
  nav?: MenuItem[];
  right?: ReactNode;
  brandHref?: string;
}) {
  return (
    <div className="topbar topbar--center topbar--student">
      <div className="topbar__brand">
        <Brand href={brandHref} flat />
      </div>
      <div className="btn-row">
        {nav.length > 0 && (
          <nav className="topnav" aria-label="Разделы">
            {nav.map((m) => (
              <a
                key={m.href}
                className={cx("topnav__a", m.on && "is-on")}
                href={m.href}
                aria-current={m.on ? "page" : undefined}
              >
                {m.label}
              </a>
            ))}
          </nav>
        )}
        {right}
      </div>
    </div>
  );
}

export function Main({
  flush,
  page,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & { flush?: boolean; page?: boolean }) {
  return (
    <div className={cx("main", flush && "main--flush", className)} {...rest}>
      {page ? <div className="page">{children}</div> : children}
    </div>
  );
}

/**
 * Нижняя панель действий: липнет к низу окна на длинных экранах, чтобы
 * главное решение всегда было под рукой. Слева итог или подсказка, справа кнопки.
 */
export function Dock({
  children,
  actions,
  className,
}: {
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("dock", className)}>
      <div className="dock__lead">{children}</div>
      {actions && <div className="btn-row dock__actions">{actions}</div>}
    </div>
  );
}

/** Витринная плашка: пастельная заливка, круги, капитель, крупный заголовок. */
export function Band({
  tone,
  art = "submit",
  aside,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & {
  tone?: "blue" | "violet" | "ink";
  art?: "submit" | "login" | "none";
  /** Правая часть плашки: поля «подпись — значение». */
  aside?: ReactNode;
}) {
  return (
    <div
      className={cx(
        "band",
        tone && `band--${tone}`,
        art !== "none" && `band--${art}`,
        className,
      )}
      {...rest}
    >
      {art !== "none" && (
        <>
          <div className="circ circ--g" aria-hidden="true" />
          <div className="circ circ--b" aria-hidden="true" />
          <div className="circ circ--r" aria-hidden="true" />
        </>
      )}
      <div className={cx("band__in", aside ? "band__in--split" : undefined)}>
        <div className="band__main">{children}</div>
        {aside && <div className="band__aside">{aside}</div>}
      </div>
    </div>
  );
}

export function BandMeta({ children }: { children: ReactNode }) {
  return <div className="band__meta">{children}</div>;
}

export function BandVal({
  label,
  late,
  children,
}: {
  label: ReactNode;
  late?: boolean;
  children: ReactNode;
}) {
  return (
    <div>
      <span className="caption band__cap">{label}</span>
      <div className={cx("band__val", late && "band__val--late")}>
        {children}
      </div>
    </div>
  );
}

export type Crumb = { href?: string; label: ReactNode };

/** Крошки: стрелка возврата первой, путь, текущая страница последней. */
export function Crumbs({
  back,
  items = [],
  current,
  className,
}: {
  back?: string;
  items?: Crumb[];
  current?: ReactNode;
  className?: string;
}) {
  return (
    <nav className={cx("crumbs", className)} aria-label="Путь">
      {back && (
        <a
          className="crumbs__back"
          href={back}
          title="Назад"
          aria-label="Назад"
        >
          ←
        </a>
      )}
      {items.map((c, i) => (
        <span key={i} className="crumbs__seg">
          {c.href ? <a href={c.href}>{c.label}</a> : <span>{c.label}</span>}
          <span aria-hidden="true">/</span>
        </span>
      ))}
      {current && <span className="cur">{current}</span>}
    </nav>
  );
}

export type StepItem = {
  n: number;
  label: string;
  name?: string;
  state: "done" | "on" | "next";
  onClick?: () => void;
};

/** Шаги мастера: пройденный нажимается, текущий помечен, следующий выключен. */
export function Steps({
  steps,
  className,
}: {
  steps: StepItem[];
  className?: string;
}) {
  return (
    <div className={cx("steps", "steps--wizard", className)} role="list">
      {steps.map((s, i) => (
        <span key={s.n} className="steps__item" role="listitem">
          {i > 0 && <span className="bar" aria-hidden="true" />}
          <button
            type="button"
            className={cx(
              "step",
              s.state === "on" && "is-on",
              s.state === "done" && "is-done",
              s.state === "next" && "step--next",
            )}
            aria-label={s.name ?? `${s.n}. ${s.label}`}
            aria-current={s.state === "on" ? "step" : undefined}
            disabled={s.state === "next"}
            onClick={s.state === "done" ? s.onClick : undefined}
          >
            <span className="n" aria-hidden="true">
              {s.state === "done" ? "✓" : s.n}
            </span>
            {s.label}
          </button>
        </span>
      ))}
    </div>
  );
}
