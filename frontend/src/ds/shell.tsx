// Оболочка приложения: панель, шапка, витринная плашка, крошки, шаги мастера,
// нижняя панель действий. Разметка из docs/design/screens.html (Р2, С1, К5).
import {
  useEffect,
  useState,
  useLayoutEffect,
  useRef,
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

/* --- Значки разделов --------------------------------------------------------
   Material Symbols Sharp, Google, Apache 2.0. Набор выбран за прямой угол:
   скруглённые контуры паку не подходят. Заливка берёт цвет текста.       */
export type MenuIcon =
  "overview" | "courses" | "runs" | "people" | "works" | "pool" | "cabinet";

const ICON_PATHS: Record<MenuIcon, string> = {
  // dashboard
  overview:
    "M520-600v-240h320v240H520ZM120-440v-400h320v400H120Zm400 320v-400h320v400H520Zm-400 0v-240h320v240H120Zm80-400h160v-240H200v240Zm400 320h160v-240H600v240Zm0-480h160v-80H600v80ZM200-200h160v-80H200v80Zm160-320Zm240-160Zm0 240ZM360-280Z",
  // school
  courses:
    "M480-120 200-272v-240L40-600l440-240 440 240v320h-80v-276l-80 44v240L480-120Zm0-332 274-148-274-148-274 148 274 148Zm0 241 200-108v-151L480-360 280-470v151l200 108Zm0-241Zm0 90Zm0 0Z",
  // layers
  runs: "M480-118 120-398l66-50 294 228 294-228 66 50-360 280Zm0-202L120-600l360-280 360 280-360 280Zm0-280Zm0 178 230-178-230-178-230 178 230 178Z",
  // group
  people:
    "M40-160v-112q0-34 17.5-62.5T104-378q62-31 126-46.5T360-440q66 0 130 15.5T616-378q29 15 46.5 43.5T680-272v112H40Zm720 0v-120q0-44-24.5-84.5T666-434q51 6 96 20.5t84 35.5q36 20 55 44.5t19 53.5v120H760ZM360-480q-66 0-113-47t-47-113q0-66 47-113t113-47q66 0 113 47t47 113q0 66-47 113t-113 47Zm400-160q0 66-47 113t-113 47q-11 0-28-2.5t-28-5.5q27-32 41.5-71t14.5-81q0-42-14.5-81T544-792q14-5 28-6.5t28-1.5q66 0 113 47t47 113ZM120-240h480v-32q0-11-5.5-20T580-306q-54-27-109-40.5T360-360q-56 0-111 13.5T140-306q-9 5-14.5 14t-5.5 20v32Zm240-320q33 0 56.5-23.5T440-640q0-33-23.5-56.5T360-720q-33 0-56.5 23.5T280-640q0 33 23.5 56.5T360-560Zm0 320Zm0-400Z",
  // fact_check
  works:
    "M200-280h200v-80H200v80Zm382-80 198-198-57-57-141 142-57-57-56 57 113 113Zm-382-80h200v-80H200v80Zm0-160h200v-80H200v80ZM80-120v-720h800v720H80Zm80-80h640v-560H160v560Zm0 0v-560 560Z",
  // inbox
  pool: "M120-120v-720h720v720H120Zm80-80h560v-120H640q-30 38-71.5 59T480-240q-47 0-88.5-21T320-320H200v120Zm280-120q38 0 69-22t43-58h168v-360H200v360h168q12 36 43 58t69 22ZM200-200h560-560Z",
  // person
  cabinet:
    "M480-480q-66 0-113-47t-47-113q0-66 47-113t113-47q66 0 113 47t47 113q0 66-47 113t-113 47ZM160-160v-112q0-34 17.5-62.5T224-378q62-31 126-46.5T480-440q66 0 130 15.5T736-378q29 15 46.5 43.5T800-272v112H160Zm80-80h480v-32q0-11-5.5-20T700-306q-54-27-109-40.5T480-360q-56 0-111 13.5T260-306q-9 5-14.5 14t-5.5 20v32Zm240-320q33 0 56.5-23.5T560-640q0-33-23.5-56.5T480-720q-33 0-56.5 23.5T400-640q0 33 23.5 56.5T480-560Zm0-80Zm0 400Z",
};

export function Icon({ name }: { name: MenuIcon }) {
  return (
    <svg
      className="mi"
      viewBox="0 -960 960 960"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

export type MenuItem = {
  href: string;
  label: string;
  icon?: MenuIcon;
  count?: number | string | null;
  on?: boolean;
};

/* Ширина панели тянется за правый край. Ниже порога она защёлкивается в
   фиксированную колонку со значками, выше — возвращается к обычной.      */
const ASIDE_WIDTH_KEY = "aside-width";
export const ASIDE_RAIL = 60;
const ASIDE_MIN = 190;
const ASIDE_MAX = 340;
const ASIDE_DEFAULT = 232;
/** Ниже этой ширины панель становится колонкой значков. */
const ASIDE_SNAP = 150;

function clampAside(px: number) {
  if (px < ASIDE_SNAP) return ASIDE_RAIL;
  return Math.min(ASIDE_MAX, Math.max(ASIDE_MIN, px));
}

/** Ниже 1100px пак кладёт панель строкой: тонкой колонки там не существует. */
const WIDE = "(min-width: 1101px)";
function useWideLayout() {
  const [wide, setWide] = useState(
    () => !window.matchMedia || window.matchMedia(WIDE).matches,
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(WIDE);
    const sync = () => setWide(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return wide;
}

function useAsideWidth() {
  const [width, setWidth] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(ASIDE_WIDTH_KEY));
      return Number.isFinite(saved) && saved > 0
        ? clampAside(saved)
        : ASIDE_DEFAULT;
    } catch {
      return ASIDE_DEFAULT;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(ASIDE_WIDTH_KEY, String(width));
    } catch {
      /* хранилище недоступно: ширина живёт до перезагрузки */
    }
  }, [width]);
  return [width, setWidth] as const;
}

/** Правый край панели: тянется мышью, слушает стрелки с клавиатуры. */
function AsideGrip({
  width,
  onWidth,
}: {
  width: number;
  onWidth: (px: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  return (
    <div
      className={cx("aside__grip", dragging && "is-dragging")}
      role="separator"
      aria-orientation="vertical"
      aria-label="Ширина панели"
      aria-valuenow={width}
      aria-valuemin={ASIDE_RAIL}
      aria-valuemax={ASIDE_MAX}
      tabIndex={0}
      onPointerDown={(e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        setDragging(true);
      }}
      onPointerMove={(e) => {
        if (!dragging) return;
        onWidth(clampAside(e.clientX));
      }}
      onLostPointerCapture={() => setDragging(false)}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        setDragging(false);
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 40 : 16;
        if (e.key === "ArrowLeft") {
          e.preventDefault();
          onWidth(width <= ASIDE_MIN ? ASIDE_RAIL : clampAside(width - step));
        }
        if (e.key === "ArrowRight") {
          e.preventDefault();
          onWidth(clampAside(width === ASIDE_RAIL ? ASIDE_MIN : width + step));
        }
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onWidth(width === ASIDE_RAIL ? ASIDE_DEFAULT : ASIDE_RAIL);
        }
      }}
      onDoubleClick={() =>
        onWidth(width === ASIDE_RAIL ? ASIDE_DEFAULT : ASIDE_RAIL)
      }
      title="Потяните, чтобы изменить ширину"
    />
  );
}

/** Панель: бренд, один список разделов, подвал с человеком. */
export function Aside({
  brandHref = "#/home",
  menu,
  foot,
  className,
}: {
  brandHref?: string;
  menu: MenuItem[];
  foot?: ReactNode;
  className?: string;
}) {
  const [width, setWidth] = useAsideWidth();
  const wide = useWideLayout();
  const rail = wide && width <= ASIDE_RAIL;
  const ref = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const shell = ref.current?.closest<HTMLElement>(".app-shell, .app");
    if (!shell) return;
    shell.style.setProperty("--sidebar-w", `${width}px`);
    shell.classList.toggle("app--rail", rail);
    return () => {
      shell.style.removeProperty("--sidebar-w");
      shell.classList.remove("app--rail");
    };
  }, [width, rail]);
  return (
    <aside ref={ref} className={cx("aside", className, rail && "aside--rail")}>
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
            aria-label={rail ? m.label : undefined}
          >
            <span className="menu__lead">
              {m.icon && <Icon name={m.icon} />}
              <span className="menu__label">{m.label}</span>
            </span>{" "}
            {m.count !== undefined && m.count !== null && (
              <span className="c">{m.count}</span>
            )}
          </a>
        ))}
      </nav>
      {foot && <div className="aside__foot">{foot}</div>}
      {wide && <AsideGrip width={width} onWidth={setWidth} />}
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
  return (
    <div className="app">
      <Aside menu={menu} foot={foot} brandHref={brandHref} />
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
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    const shell = node?.closest<HTMLElement>(".app-shell");
    if (!node || !shell) return;
    const update = () => {
      const box = node.getBoundingClientRect();
      const clearance =
        box.bottom > 0 && box.top < window.innerHeight
          ? window.innerHeight - Math.max(0, box.top)
          : 0;
      shell.style.setProperty("--dock-clearance", `${clearance}px`);
    };
    update();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(node);
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      shell.style.removeProperty("--dock-clearance");
    };
  }, []);
  return (
    <div ref={ref} className={cx("dock", className)}>
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
