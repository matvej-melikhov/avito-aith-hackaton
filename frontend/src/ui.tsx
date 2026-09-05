import { markProgrammaticNavigation } from "./navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "./api/client";
import {
  Btn,
  Callout,
  Card as DsCard,
  CardBody,
  CardHead,
  Empty as DsEmpty,
  Skel,
  St,
  statuses,
} from "./ds";
export { statuses, Skel };
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
        {error && <ErrorBox error={error} />}
        {success && (
          <Callout tone="info" role="status" className="feedback">
            <p>{success}</p>
          </Callout>
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
    <Callout tone="bad" role="alert" className="feedback">
      <p>
        {errorMessage(error)}
        {retry && (
          <>
            {" "}
            <Btn variant="link" onClick={retry}>
              Обновить данные
            </Btn>
          </>
        )}
      </p>
    </Callout>
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
        <Skel label="Загрузка…" />
      ) : value.error ? (
        <ErrorBox error={value.error} retry={value.refresh} />
      ) : (
        children
      )}
    </>
  );
}
export function Status({
  value,
  attempt,
}: {
  value: string;
  attempt?: number;
}) {
  return <St status={value} attempt={attempt} />;
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
  sub,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <DsCard>
      <CardHead title={title} sub={sub}>
        {actions}
      </CardHead>
      <CardBody>{children}</CardBody>
    </DsCard>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <DsEmpty>{children}</DsEmpty>;
}
export function Id({ value }: { value: string }) {
  return (
    <span className="mono" title={value}>
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
