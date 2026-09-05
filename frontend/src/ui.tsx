import { markProgrammaticNavigation } from "./navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "./api/client";
export function useResource<T>(
  load: () => Promise<T>,
  key: string,
  interval = 0,
  shouldPoll: (data: T) => boolean = () => true,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  const poll = useRef(shouldPoll);
  poll.current = shouldPoll;
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setData(undefined);
    setLoading(true);
    setError(undefined);
    const run = async () => {
      try {
        const next = await loader.current();
        if (alive) {
          setData(next);
          setError(undefined);
          if (interval && poll.current(next)) timer = setTimeout(run, interval);
        }
      } catch (e) {
        if (alive) setError(e);
      } finally {
        if (alive) {
          setLoading(false);
        }
      }
    };
    void run();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [key, version, interval]);
  return { data, error, loading, refresh: () => setVersion((v) => v + 1) };
}
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [success, setSuccess] = useState("");
  const lock = useRef(false);
  async function run(work: () => Promise<void>, message = "") {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(undefined);
    setSuccess("");
    try {
      await work();
      setSuccess(message);
    } catch (e) {
      setError(e);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return {
    busy,
    run,
    error,
    success,
    feedback: (
      <>
        {error && <ErrorBox error={error} />}{" "}
        {success && (
          <p role="status" className="notice ok">
            {success}
          </p>
        )}
      </>
    ),
  };
}
export function ErrorBox({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div role="alert" className="notice bad">
      {errorMessage(error)}{" "}
      {retry && <button onClick={retry}>Обновить данные</button>}
    </div>
  );
}
export function Resource({
  value,
  children,
}: {
  value: { loading: boolean; error: unknown; refresh: () => void };
  children: ReactNode;
}) {
  return (
    <>
      {value.loading ? (
        <p role="status" className="notice">
          Загрузка…
        </p>
      ) : value.error ? (
        <ErrorBox error={value.error} retry={value.refresh} />
      ) : (
        children
      )}
    </>
  );
}
const statuses: Record<string, string> = {
  passed: "Зачтена",
  failed: "Не зачтена",
  needs_changes: "Нужны правки",
  active: "Активен",
  archived: "В архиве",
  draft: "Черновик",
  queued: "В очереди",
  submitted: "Сдана",
  resubmitted: "На повторном ревью",
  capturing: "Подготовка снимка",
  in_review: "На проверке",
  ready_to_publish: "Готово к публикации",
  published: "Опубликовано",
  canceled: "Отменено",
  pending: "Ожидает",
  processing: "Выполняется",
  running: "Выполняется",
  partial: "Частичный результат",
  succeeded: "Готово",
  retryable_failed: "Нужен повтор",
  unknown_outcome: "Результат неизвестен",
  reconciling: "Сверка результата",
  action_required: "Нужно действие",
  stale: "Устарело",
  superseded: "Заменено",
  validating: "Проверка доступа",
  ready: "Готово",
  access_error: "Нет доступа",
  pending_review: "Ожидает ревью",
  consumed: "Использовано",
  revoked: "Отозвано",
  expired: "Истекло",
  available: "Доступно",
  requires_action: "Нужно действие",
  unavailable: "Недоступно",
  not_supported: "Не поддерживается",
};
export function Status({ value }: { value: string }) {
  return (
    <span
      className={`status ${["passed", "succeeded", "published", "active", "ready", "available"].includes(value) ? "ok" : /failed|error/.test(value) ? "bad" : ["in_review", "pending_review", "resubmitted"].includes(value) ? "human" : value === "needs_changes" || /action|unknown/.test(value) ? "warn" : value === "submitted" ? "info" : ""}`}
    >
      {statuses[value] ?? value}
    </span>
  );
}
export const roleNames = {
  student: "Студент",
  reviewer: "Ревьюер",
  methodologist: "Координатор",
};
export function Card({
  title,
  children,
  actions,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {actions}
      </div>
      <div className="card-body">{children}</div>
    </section>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
export function Id({ value }: { value: string }) {
  return (
    <span className="identifier" title={value}>
      {value}
    </span>
  );
}
export function date(value: string) {
  return new Date(value).toLocaleString("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
export function safeUrl(value: string) {
  try {
    const url = new URL(value, window.location.origin);
    return ["https:", "http:"].includes(url.protocol) ||
      (url.protocol === "blob:" && url.origin === window.location.origin)
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function go(path: string) {
  markProgrammaticNavigation();
  window.location.hash = path;
}
