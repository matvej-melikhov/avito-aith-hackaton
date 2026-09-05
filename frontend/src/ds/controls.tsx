// Управляющие элементы дизайн-пака. Разметка повторяет docs/design/kit.html
// и screens.html один в один; классы из docs/design/components.css.
import {
  cloneElement,
  isValidElement,
  useId,
  type ComponentPropsWithoutRef,
  type ReactElement,
  type ReactNode,
} from "react";
import { opTone, statusLabel, workStatus } from "./status";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ") || undefined;
}

/* --- Кнопки ----------------------------------------------------------------- */

type BtnVariant = "pri" | "dark" | "quiet" | "danger" | "link";
type BtnBase = {
  variant?: BtnVariant;
  size?: "s" | "l";
  icon?: boolean;
  grow?: boolean;
  className?: string;
  children?: ReactNode;
};
type BtnAsButton = BtnBase &
  ComponentPropsWithoutRef<"button"> & { href?: never };
type BtnAsLink = BtnBase & ComponentPropsWithoutRef<"a"> & { href: string };

function btnClass({ variant, size, icon, grow, className }: BtnBase) {
  return cx(
    "btn",
    variant && `btn--${variant}`,
    size && `btn--${size}`,
    icon && "btn--icon",
    grow && "btn--grow",
    className,
  );
}

export function Btn(props: BtnAsButton | BtnAsLink) {
  if ("href" in props && props.href !== undefined) {
    const { variant, size, icon, grow, className, children, ...rest } = props;
    return (
      <a
        className={btnClass({ variant, size, icon, grow, className })}
        {...rest}
      >
        {children}
      </a>
    );
  }
  const { variant, size, icon, grow, className, children, type, ...rest } =
    props as BtnAsButton;
  return (
    <button
      type={type ?? "button"}
      className={btnClass({ variant, size, icon, grow, className })}
      {...rest}
    >
      {children}
    </button>
  );
}

export function BtnRow({
  end,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & { end?: boolean }) {
  return (
    <div className={cx("btn-row", end && "btn-row--end", className)} {...rest}>
      {children}
    </div>
  );
}

/* --- Пилюли и статусы ------------------------------------------------------- */

export function Pill({
  tone,
  mono,
  dot,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"span"> & {
  tone?: "solid" | "ok" | "bad" | "human" | "info" | "late";
  mono?: boolean;
  dot?: boolean;
}) {
  return (
    <span
      className={cx(
        "pill",
        tone && `pill--${tone}`,
        mono && "pill--mono",
        className,
      )}
      {...rest}
    >
      {dot && <i aria-hidden="true" />}
      {children}
    </span>
  );
}

/** Статус домашки: ровно семь значений. Остальное — служебная пилюля. */
export function St({
  status,
  attempt = 1,
  decision,
  className,
}: {
  status: string | null | undefined;
  attempt?: number;
  decision?: string | null;
  className?: string;
}) {
  const s = workStatus(status, attempt, decision);
  if (!s) return <OpPill status={status} className={className} />;
  return (
    <span className={cx("st", `st--${s.tone}`, className)}>{s.label}</span>
  );
}

/** Служебный статус операции, доставки, самопроверки, выгрузки. */
export function OpPill({
  status,
  className,
}: {
  status: string | null | undefined;
  className?: string;
}) {
  const tone = opTone(status);
  return (
    <Pill
      tone={
        tone === "ok" || tone === "bad" || tone === "late" ? tone : undefined
      }
      dot={tone === "busy"}
      className={className}
    >
      {statusLabel(status)}
    </Pill>
  );
}

/* --- Сегмент и табы ---------------------------------------------------------- */

export function Seg<T extends string>({
  value,
  options,
  onChange,
  label,
  className,
  disabled,
}: {
  value: T | null | undefined;
  options: { value: T; label: ReactNode; disabled?: boolean }[];
  onChange?: (value: T) => void;
  label?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <div className={cx("seg", className)} role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? "is-on" : undefined}
          aria-pressed={o.value === value}
          disabled={disabled || o.disabled}
          onClick={() => onChange?.(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs({
  label,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & { label?: string }) {
  return (
    <div className={cx("tabs", className)} aria-label={label} {...rest}>
      {children}
    </div>
  );
}

type TabBase = {
  on?: boolean;
  count?: ReactNode;
  className?: string;
  children?: ReactNode;
};
export function Tab(
  props:
    | (TabBase & ComponentPropsWithoutRef<"button"> & { href?: never })
    | (TabBase & ComponentPropsWithoutRef<"a"> & { href: string }),
) {
  const { on, count, className, children } = props;
  const cls = cx("tab", on && "is-on", className);
  const body = (
    <>
      {children}
      {count !== undefined && count !== null && (
        <span className="c">{count}</span>
      )}
    </>
  );
  if ("href" in props && props.href !== undefined) {
    const {
      on: _on,
      count: _c,
      className: _cls,
      children: _ch,
      ...rest
    } = props;
    return (
      <a className={cls} aria-current={on ? "page" : undefined} {...rest}>
        {body}
      </a>
    );
  }
  const {
    on: _on,
    count: _c,
    className: _cls,
    children: _ch,
    ...rest
  } = props as TabBase & ComponentPropsWithoutRef<"button">;
  return (
    <button type="button" className={cls} aria-pressed={!!on} {...rest}>
      {body}
    </button>
  );
}

/* --- Чекбокс, радио, тумблер ---------------------------------------------------- */

export function Chk({
  radio,
  row,
  hint,
  trail,
  className,
  children,
  ...input
}: ComponentPropsWithoutRef<"input"> & {
  radio?: boolean;
  /** Строка списка: флажок по центру, подпись растянута, хвост справа. */
  row?: boolean;
  hint?: ReactNode;
  trail?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label
      className={cx("chk", radio && "chk--radio", row && "chk--row", className)}
    >
      <input type={radio ? "radio" : "checkbox"} {...input} />
      <span className="chk__box" aria-hidden="true" />
      <span className="chk__lbl">
        {children}
        {hint && <span className="caption chk__hint">{hint}</span>}
      </span>
      {trail}
    </label>
  );
}

export function Tgl({
  className,
  ...input
}: ComponentPropsWithoutRef<"input">) {
  return (
    <span className={cx("tgl", className)}>
      <input type="checkbox" role="switch" {...input} />
      <span className="tgl__t" aria-hidden="true" />
    </span>
  );
}

/* --- Поле с подписью ---------------------------------------------------------- */

/**
 * Подпись вынесена из <label> вокруг поля: иначе в доступное имя поля попадает
 * и подсказка. Для группы (сегмент, чекбоксы) подпись связывается через
 * aria-labelledby.
 */
export function Field({
  label,
  hint,
  opt,
  error,
  id,
  group,
  className,
  children,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  opt?: ReactNode;
  error?: ReactNode;
  id?: string;
  group?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const auto = useId();
  const fid = id ?? auto;
  const labelId = `${fid}-l`;
  let control = children;
  if (isValidElement(children)) {
    const el = children as ReactElement<{
      id?: string;
      "aria-labelledby"?: string;
    }>;
    control = group
      ? label
        ? cloneElement(el, {
            "aria-labelledby": el.props["aria-labelledby"] ?? labelId,
          })
        : el
      : cloneElement(el, { id: el.props.id ?? fid });
  }
  return (
    <div className={cx("field", className)}>
      {label &&
        (group ? (
          <span className="field__lbl" id={labelId}>
            {label}
            {opt && <span className="field__opt">{opt}</span>}
          </span>
        ) : (
          <label className="field__lbl" htmlFor={fid}>
            {label}
            {opt && <span className="field__opt">{opt}</span>}
          </label>
        ))}
      {control}
      {error && (
        <span className="field__err" role="alert">
          {error}
        </span>
      )}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}

type InpMods = {
  small?: boolean;
  mono?: boolean;
  err?: boolean;
  className?: string;
};
function inpClass({ small, mono, err, className }: InpMods, extra?: string) {
  return cx(
    "inp",
    extra,
    small && "inp--s",
    mono && "inp--mono",
    err && "inp--err",
    className,
  );
}

export function Inp({
  small,
  mono,
  err,
  className,
  ...rest
}: InpMods & ComponentPropsWithoutRef<"input">) {
  return (
    <input className={inpClass({ small, mono, err, className })} {...rest} />
  );
}

export function Area({
  small,
  mono,
  err,
  className,
  ...rest
}: InpMods & ComponentPropsWithoutRef<"textarea">) {
  return (
    <textarea
      className={inpClass({ small, mono, err, className }, "inp--area")}
      {...rest}
    />
  );
}

export function Sel({
  small,
  mono,
  err,
  className,
  children,
  ...rest
}: InpMods & ComponentPropsWithoutRef<"select">) {
  return (
    <select className={inpClass({ small, mono, err, className })} {...rest}>
      {children}
    </select>
  );
}
