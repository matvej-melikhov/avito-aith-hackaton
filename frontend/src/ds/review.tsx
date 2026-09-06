// Разбор по требованиям: аккордеон, маркеры вердикта, шкала, находка с
// цитатой, цитата без кода, редактируемый критерий. Разметка из Р5/К6.
import { useState, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { cx } from "./controls";
import { num } from "./format";

/** Ряд значений шкалы из максимума и шага (как в конфигурации задания). */
export function scaleValues(max: number, step: number) {
  if (!Number.isFinite(max) || max < 0 || !Number.isFinite(step) || step <= 0)
    return [] as number[];
  const count = Math.floor(max / step);
  const values =
    count <= 12
      ? Array.from({ length: count + 1 }, (_, i) =>
          Number((i * step).toFixed(6)),
        )
      : [0, step];
  if (values[values.length - 1] !== max) values.push(max);
  return values;
}

export function Acc({
  head,
  headClass,
  open,
  defaultOpen,
  onToggle,
  chevron = true,
  fresh,
  className,
  children,
  ...rest
}: Omit<ComponentPropsWithoutRef<"details">, "open" | "onToggle"> & {
  head: ReactNode;
  /** Сетка заголовка: `acc__h--score` ставит оценку в общую колонку. */
  headClass?: string;
  open?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  chevron?: boolean;
  /** «Своё требование»: на подложке, без черты. */
  fresh?: boolean;
  children?: ReactNode;
}) {
  const [inner, setInner] = useState(defaultOpen ?? open ?? false);
  const shown = open ?? inner;
  return (
    <details
      className={cx("acc", fresh && "acc--new", className)}
      open={shown}
      onToggle={(e) => {
        const next = (e.currentTarget as HTMLDetailsElement).open;
        setInner(next);
        onToggle?.(next);
      }}
      {...rest}
    >
      <summary className={cx("acc__h", headClass)}>
        {head}
        {chevron && (
          <span className="acc__chev" aria-hidden="true">
            {shown ? "▲" : "▼"}
          </span>
        )}
      </summary>
      {children !== undefined && <div className="acc__b">{children}</div>}
    </details>
  );
}

/** Маркер вердикта: квадрат ✓ / ✕, круг ? (решает человек), круг ＋ (своё). */
export function Ck({
  kind,
  className,
}: {
  kind: "y" | "n" | "h" | "q";
  className?: string;
}) {
  const glyph = { y: "✓", n: "✕", h: "?", q: "＋" }[kind];
  const title = {
    y: "Выполнено",
    n: "Не выполнено",
    h: "Решает человек",
    q: "Не проверено",
  }[kind];
  return (
    <span
      className={cx("ck", `ck--${kind}`, className)}
      role="img"
      aria-label={title}
    >
      {glyph}
    </span>
  );
}

/**
 * Шкала оценки. Чипы из значений конфигурации, под ними числовое поле с тем же
 * значением: оно скрыто, когда чипы показаны, и видимо, когда значений
 * слишком много для чипов. Поле носит доступное имя «Баллы: …».
 */
export function Scale({
  max,
  step = 0.5,
  value,
  onChange,
  readOnly,
  disabled,
  label,
  className,
}: {
  max: number;
  step?: number;
  value: number | null | undefined;
  onChange?: (value: number | null) => void;
  readOnly?: boolean;
  disabled?: boolean;
  label: string;
  className?: string;
}) {
  const values = scaleValues(max, step);
  // Чипы только для полной шкалы: свёрнутый ряд «0, шаг … максимум»
  // годится для показа, но не для выбора.
  const chips = values.length > 0 && Math.floor(max / step) <= 12;
  const tone =
    value === 0
      ? "scale--zero"
      : value !== null && value !== undefined && value === max
        ? "scale--max"
        : undefined;
  if (readOnly) {
    // Длинная шкала показывается свёрнуто: ноль, шаг, многоточие, максимум.
    const preview = chips ? values : values.length ? values : [value ?? 0];
    return (
      <span
        className={cx("scale scale--ro", tone, className)}
        aria-label={label}
      >
        {preview.map((v, i) => (
          <span key={i} className={v === value ? "is-on" : undefined}>
            {!chips && i === preview.length - 1 && preview.length > 1
              ? `… ${num(v)}`
              : num(v)}
          </span>
        ))}
      </span>
    );
  }
  return (
    <>
      {chips && (
        <span
          className={cx("scale", tone, className)}
          role="group"
          aria-label={`${label}, шкала`}
        >
          {values.map((v) => (
            <button
              key={v}
              type="button"
              className={v === value ? "is-on" : undefined}
              aria-pressed={v === value}
              disabled={disabled}
              onClick={() => onChange?.(v)}
            >
              {num(v)}
            </button>
          ))}
        </span>
      )}
      <input
        type="number"
        className={chips ? "sr-only" : "inp inp--s inp--mono scale__inp"}
        aria-label={label}
        min={0}
        max={max}
        step="any"
        disabled={disabled}
        value={value ?? ""}
        onChange={(e) =>
          onChange?.(e.target.value === "" ? null : Number(e.target.value))
        }
      />
    </>
  );
}

/** Обёртка для контрола внутри заголовка: клик не сворачивает аккордеон. */
export function AccCtl({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={className}
      onClick={(e) => e.preventDefault()}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") e.stopPropagation();
      }}
    >
      {children}
    </span>
  );
}

export function Finding({
  file,
  lines,
  open,
  why,
  children,
}: {
  file?: ReactNode;
  lines?: ReactNode;
  /** Действие «Открыть»: ссылка на репозиторий или просмотр файла. */
  open?: ReactNode;
  why?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="finding">
      {(file || lines || open) && (
        <div className="finding__src">
          {file && <span className="f">{file}</span>}
          {lines && <span>{lines}</span>}
          {open}
        </div>
      )}
      {children}
      {why && <div className="finding__why">{why}</div>}
    </div>
  );
}

export type CodeLine = {
  n?: number | string;
  text: string;
  hit?: boolean;
  miss?: boolean;
};

export function Code({ lines }: { lines: CodeLine[] }) {
  return (
    <div className="code">
      {lines.map((l, i) => (
        <div
          key={i}
          className={cx("cl", l.hit && "is-hit", l.miss && "is-miss")}
        >
          <span className="n">{l.n ?? ""}</span>
          <span>{l.text || " "}</span>
        </div>
      ))}
    </div>
  );
}

export function Quote({
  tone,
  className,
  children,
}: {
  tone?: "bad" | "human";
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("quote", tone && `quote--${tone}`, className)}>
      {children}
    </div>
  );
}

export function Drag({ label = "Перетащить" }: { label?: string }) {
  return <span className="drag" role="img" aria-label={label} />;
}

/** Раскрытый критерий в редакторе: форма на подложке, подписано каждое поле. */
export function Crit({
  top,
  className,
  children,
}: {
  top?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("crit", className)}>
      {top && <div className="crit__top">{top}</div>}
      {children}
    </div>
  );
}
