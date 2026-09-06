// Оболочка приложения: панель, шапка, витринная плашка, крошки, шаги мастера,
// нижняя панель действий. Разметка из docs/design/screens.html (Р2, С1, К5).
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cx } from "./controls";

export const BRAND = "Авито Ревью";

export function Brand({
  href,
  flat,
  className,
}: {
  href?: string;
  flat?: boolean;
  className?: string;
}) {
  const cls = cx("brand", flat && "brand--flat", className);
  const body = (
    <>
      <span className="dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {BRAND}
    </>
  );
  return href ? (
    <a className={cls} href={href}>
      {body}
    </a>
  ) : (
    <span className={cls}>{body}</span>
  );
}

/* Аватар. С seed рисует зверька: один и тот же человек всегда получает
   одну и ту же мордочку, имени и фото для этого не нужно. */
export function Ava({
  seed,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"span"> & { seed?: string }) {
  if (seed) {
    const h = hashSeed(seed);
    return (
      <span
        className={cx("ava", "ava--face", className)}
        style={{ background: FACE_BG[h % FACE_BG.length] }}
        {...rest}
      >
        <CuteFace kind={(h >>> 8) % 6} />
      </span>
    );
  }
  return (
    <span className={cx("ava", className)} {...rest}>
      {children}
    </span>
  );
}

const FACE_BG = [
  "#FFE1EA",
  "#DFF0FF",
  "#E4F7DF",
  "#FFF0D2",
  "#EEE4FF",
  "#FFE6D5",
  "#DDF5F2",
  "#F3E9DA",
];

function hashSeed(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

const INK = "#2B2B2B";

/** Шесть зверьков: кот, медведь, зайка, лиса, лягушка, панда. */
export function CuteFace({ kind }: { kind: number }) {
  const eyes = (
    <>
      <circle cx="13" cy="16.5" r="1.4" fill={INK} />
      <circle cx="19" cy="16.5" r="1.4" fill={INK} />
      <circle cx="13.5" cy="16" r="0.45" fill="#fff" />
      <circle cx="19.5" cy="16" r="0.45" fill="#fff" />
    </>
  );
  const blush = (
    <>
      <circle cx="10.5" cy="20" r="1.5" fill="#FFB3C1" opacity="0.75" />
      <circle cx="21.5" cy="20" r="1.5" fill="#FFB3C1" opacity="0.75" />
    </>
  );
  const smile = (
    <path
      d="M14.3 20.6q1.7 1.7 3.4 0"
      fill="none"
      stroke={INK}
      strokeWidth="1.1"
      strokeLinecap="round"
    />
  );
  let body: ReactNode;
  switch (kind) {
    case 0: // кот
      body = (
        <>
          <path d="M7.5 14 8.5 5.5 15 10.5Z" fill="#F2A65A" />
          <path d="M24.5 14 23.5 5.5 17 10.5Z" fill="#F2A65A" />
          <circle cx="16" cy="17.5" r="9" fill="#F2A65A" />
          {eyes}
          <path d="M15 19.3h2l-1 1.1z" fill={INK} />
          {smile}
          {blush}
        </>
      );
      break;
    case 1: // медведь
      body = (
        <>
          <circle cx="8.5" cy="9.5" r="3.6" fill="#C98E5E" />
          <circle cx="23.5" cy="9.5" r="3.6" fill="#C98E5E" />
          <circle cx="16" cy="17.5" r="9" fill="#C98E5E" />
          <ellipse cx="16" cy="20.3" rx="4" ry="3" fill="#F1D3B3" />
          {eyes}
          <circle cx="16" cy="19.5" r="1.3" fill={INK} />
          {smile}
          {blush}
        </>
      );
      break;
    case 2: // зайка
      body = (
        <>
          <ellipse cx="12" cy="7" rx="2.7" ry="6.5" fill="#F4F1EC" />
          <ellipse cx="20" cy="7" rx="2.7" ry="6.5" fill="#F4F1EC" />
          <ellipse cx="12" cy="7.5" rx="1.3" ry="4.5" fill="#FFC6D3" />
          <ellipse cx="20" cy="7.5" rx="1.3" ry="4.5" fill="#FFC6D3" />
          <circle cx="16" cy="18" r="9" fill="#F4F1EC" />
          {eyes}
          <circle cx="16" cy="19.6" r="1.1" fill="#F08AA0" />
          {smile}
          {blush}
        </>
      );
      break;
    case 3: // лиса
      body = (
        <>
          <path d="M7 15 7.5 4.5 15 10Z" fill="#F08A4B" />
          <path d="M25 15 24.5 4.5 17 10Z" fill="#F08A4B" />
          <circle cx="16" cy="17.5" r="9" fill="#F08A4B" />
          <ellipse cx="16" cy="21" rx="5.2" ry="3.4" fill="#FFF6EE" />
          {eyes}
          <circle cx="16" cy="20.6" r="1.3" fill={INK} />
          {blush}
        </>
      );
      break;
    case 4: // лягушка
      body = (
        <>
          <circle cx="16" cy="18" r="9" fill="#8CCB6B" />
          <circle cx="11.5" cy="10" r="3.8" fill="#8CCB6B" />
          <circle cx="20.5" cy="10" r="3.8" fill="#8CCB6B" />
          <circle cx="11.5" cy="10" r="2.3" fill="#fff" />
          <circle cx="20.5" cy="10" r="2.3" fill="#fff" />
          <circle cx="12" cy="10.3" r="1.2" fill={INK} />
          <circle cx="21" cy="10.3" r="1.2" fill={INK} />
          <path
            d="M11 19.5q5 4.5 10 0"
            fill="none"
            stroke={INK}
            strokeWidth="1.2"
            strokeLinecap="round"
          />
          {blush}
        </>
      );
      break;
    default: // панда
      body = (
        <>
          <circle cx="8.5" cy="9.5" r="3.6" fill={INK} />
          <circle cx="23.5" cy="9.5" r="3.6" fill={INK} />
          <circle cx="16" cy="17.5" r="9" fill="#FAFAFA" />
          <ellipse cx="13" cy="16.5" rx="2.5" ry="2.9" fill={INK} />
          <ellipse cx="19" cy="16.5" rx="2.5" ry="2.9" fill={INK} />
          <circle cx="13.4" cy="16.4" r="0.9" fill="#fff" />
          <circle cx="19.4" cy="16.4" r="0.9" fill="#fff" />
          <circle cx="16" cy="20.2" r="1.2" fill={INK} />
          {smile}
          {blush}
        </>
      );
  }
  return (
    <svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true">
      {body}
    </svg>
  );
}

export type MenuItem = {
  href: string;
  label: string;
  count?: number | string | null;
  on?: boolean;
};

/** Панель: бренд, один список разделов, подвал с человеком. */
export function Aside({
  brandHref = "#/home",
  menu,
  foot,
}: {
  brandHref?: string;
  menu: MenuItem[];
  foot?: ReactNode;
}) {
  return (
    <aside className="aside">
      <Brand href={brandHref} />
      <nav className="menu" aria-label="Разделы">
        {menu.map((m) => (
          <a
            key={m.href}
            className={cx(m.on && "is-on")}
            href={m.href}
            aria-current={m.on ? "page" : undefined}
          >
            {m.label}
            {m.count !== undefined && m.count !== null && (
              <span className="c">{m.count}</span>
            )}
          </a>
        ))}
      </nav>
      {foot && <div className="aside__foot">{foot}</div>}
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
