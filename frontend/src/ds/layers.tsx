// Слои поверх страницы: модалка, тост, поиск с выпадающим списком.
// Модалка живёт в <dialog>, сброшенном до прозрачной рамки на весь экран:
// внутри неё .scrim и .overlay из components.css работают как в макете.
import {
  useEffect,
  useRef,
  type ComponentPropsWithoutRef,
  type ComponentPropsWithRef,
  type ReactNode,
} from "react";
import { Btn, cx } from "./controls";

export function Modal({
  title,
  close,
  wide,
  foot,
  flush,
  className,
  children,
}: {
  title: ReactNode;
  close: () => void;
  wide?: boolean;
  foot?: ReactNode;
  /** Тело и подвал рисует вызывающий (например, форма с кнопкой отправки). */
  flush?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    if (!dialog.open) dialog.showModal();
    const cancel = (e: Event) => {
      e.preventDefault();
      close();
    };
    dialog.addEventListener("cancel", cancel);
    return () => {
      dialog.removeEventListener("cancel", cancel);
      if (dialog.open) dialog.close();
    };
  }, [close]);
  return (
    <dialog
      ref={ref}
      className={cx("ds-layer", className)}
      aria-label={typeof title === "string" ? title : undefined}
    >
      <div className="scrim" onClick={close} aria-hidden="true" />
      <div className={cx("overlay", wide && "overlay--wide")}>
        <div className={cx("modal", wide && "overlay--wide")}>
          <div className="card__head">
            <h4>{title}</h4>
            <Btn
              variant="quiet"
              size="s"
              icon
              aria-label="Закрыть"
              onClick={close}
            >
              ✕
            </Btn>
          </div>
          {flush ? children : <div className="card__body">{children}</div>}
          {foot && <div className="card__foot card__foot--end">{foot}</div>}
        </div>
      </div>
    </dialog>
  );
}

export function Toasts({ children }: { children: ReactNode }) {
  return (
    <div className="toasts" role="status" aria-live="polite">
      {children}
    </div>
  );
}

export function Toast({
  tone,
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div"> & { tone?: "ok" | "bad" }) {
  return (
    <div className={cx("toast", tone && `toast--${tone}`, className)} {...rest}>
      {children}
    </div>
  );
}

/** Поле поиска с лупой. Лупа нарисована разметкой: в гарнитурах её нет. */
export function Srch({
  wide,
  className,
  children,
  ...rest
}: ComponentPropsWithRef<"span"> & { wide?: boolean }) {
  return (
    <span className={cx("srch", wide && "srch--w", className)} {...rest}>
      <svg
        className="srch__i"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        aria-hidden="true"
      >
        <circle cx="6.8" cy="6.8" r="4.6" />
        <path d="M10.2 10.2 L14 14" strokeLinecap="round" />
      </svg>
      {children}
    </span>
  );
}

export function Pop({
  className,
  children,
  ...rest
}: ComponentPropsWithoutRef<"div">) {
  return (
    <div className={cx("pop", className)} {...rest}>
      {children}
    </div>
  );
}
