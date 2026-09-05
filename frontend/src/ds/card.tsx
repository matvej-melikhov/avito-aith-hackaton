// Карточка, строки «ключ — значение», плитки, замечания, пустое состояние,
// скелетон, метр. Разметка из docs/design/screens.html.
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cx } from "./controls";

export function Card({
  hard,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"section"> & { hard?: boolean }) {
  return (
    <section className={cx("card", hard && "card--hard", className)} {...rest}>
      {children}
    </section>
  );
}

/** Шапка карточки: заголовок (h4), подзаголовок и действия справа. */
export function CardHead({
  title,
  sub,
  level = 4,
  className,
  children,
}: {
  title?: ReactNode;
  sub?: ReactNode;
  level?: 2 | 3 | 4;
  className?: string;
  children?: ReactNode;
}) {
  const H = `h${level}` as "h2" | "h3" | "h4";
  return (
    <div className={cx("card__head", className)}>
      {sub ? (
        <div>
          {title && <H>{title}</H>}
          <div className="card__sub">{sub}</div>
        </div>
      ) : (
        title && <H>{title}</H>
      )}
      {children}
    </div>
  );
}

export function CardBody({
  tight,
  compact,
  flush,
  prose,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & {
  tight?: boolean;
  compact?: boolean;
  flush?: boolean;
  prose?: boolean;
}) {
  return (
    <div
      className={cx(
        "card__body",
        tight && "card__body--tight",
        compact && "card__body--compact",
        flush && "card__body--flush",
        prose && "card__body--prose",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardFoot({
  block,
  end,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & { block?: boolean; end?: boolean }) {
  return (
    <div
      className={cx(
        "card__foot",
        block && "card__foot--block",
        end && "card__foot--end",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/** Строка «подпись — значение». total: последняя строка с чертой сверху. */
export function Kv({
  label,
  total,
  ink,
  sum,
  className,
  children,
}: {
  label: ReactNode;
  total?: boolean;
  ink?: boolean;
  sum?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={cx(
        "kv",
        total && "kv--total",
        ink && "kv--ink",
        sum && "kv--sum",
        className,
      )}
    >
      <span>{label}</span>
      <span>{children}</span>
    </div>
  );
}

export function Sum({ value, of }: { value: ReactNode; of: ReactNode }) {
  return (
    <div className="sum">
      <b>{value}</b>
      <span>{of}</span>
    </div>
  );
}

export function Tiles({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cx("tiles", className)}>{children}</div>;
}

export function Tile({
  n,
  l,
  d,
  alert,
  href,
}: {
  n: ReactNode;
  l: ReactNode;
  d?: ReactNode;
  alert?: boolean;
  /** Плитка ведёт на список, из которого посчитана. */
  href?: string;
}) {
  const body = (
    <>
      <div className="n">{n}</div>
      <div className="l">{l}</div>
      {d && <div className="d">{d}</div>}
    </>
  );
  const cls = cx("tile", alert && "tile--alert");
  return href ? (
    <a className={cls} href={href}>
      {body}
    </a>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export function Callout({
  tone,
  mark,
  role,
  className,
  children,
}: {
  tone?: "warn" | "bad" | "human" | "info";
  mark?: ReactNode;
  role?: string;
  className?: string;
  children: ReactNode;
}) {
  const sign = mark ?? (tone === "info" || tone === undefined ? "i" : "!");
  return (
    <div
      className={cx("callout", tone && `callout--${tone}`, className)}
      role={role}
    >
      <span className="callout__mark" aria-hidden="true">
        {sign}
      </span>
      {typeof children === "string" ? <p>{children}</p> : children}
    </div>
  );
}

/** Пустое состояние: круги, заголовок, одно предложение, одно действие. */
export function Empty({
  title,
  action,
  className,
  children,
}: {
  title?: ReactNode;
  action?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={cx("empty", className)}>
      <div className="empty__art" aria-hidden="true">
        <span className="circ circ--g" />
        <span className="circ circ--b" />
        <span className="circ circ--r" />
      </div>
      {title && <h3>{title}</h3>}
      {children &&
        (typeof children === "string" ? <p>{children}</p> : children)}
      {action && <div className="empty__act">{action}</div>}
    </div>
  );
}

/** Скелетон загрузки: серые прямоугольники, подпись рядом. */
export function Skel({
  lines = 3,
  label = "Загружаем…",
  className,
}: {
  lines?: number;
  label?: string;
  className?: string;
}) {
  const widths = [70, 100, 45, 85, 60];
  return (
    <div
      className={cx("skel-group", className)}
      role="status"
      aria-label={label}
    >
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="skel skel--pulse"
          style={{ width: `${widths[i % widths.length]}%` }}
        />
      ))}
    </div>
  );
}

export function Meter({
  value,
  low,
  human,
  thin,
  className,
}: {
  value: number;
  low?: boolean;
  human?: boolean;
  thin?: boolean;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      className={cx(
        "meter",
        thin && "meter--thin",
        human && "meter--human",
        className,
      )}
    >
      <i className={cx(low && "is-low")} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Drop({
  title,
  hint,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"label"> & { title: ReactNode; hint?: ReactNode }) {
  return (
    <label className={cx("drop", className)} {...rest}>
      <b>{title}</b>
      {hint && <span>{hint}</span>}
      {children}
    </label>
  );
}
