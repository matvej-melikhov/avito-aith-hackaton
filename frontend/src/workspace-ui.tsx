import { registerDirtyEditor } from "./navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Card,
  Resource,
  Status,
  Empty,
  date,
  safeUrl,
  useResource,
} from "./ui";
import type { W, WorkspaceClient } from "./api/workspace";
export function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    const cancel = (e: Event) => {
      e.preventDefault();
      close();
    };
    dialog.addEventListener("cancel", cancel);
    return () => {
      dialog.removeEventListener("cancel", cancel);
      dialog.close();
    };
  }, [close]);
  return (
    <dialog ref={ref} className="workspace-modal" aria-label={title}>
      <div className="card-head">
        <h2>{title}</h2>
        <button type="button" aria-label="Закрыть" onClick={close}>
          ×
        </button>
      </div>
      <div className="card-body">{children}</div>
    </dialog>
  );
}
export function ScreenTitle({
  code,
  title,
  children,
}: {
  code: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading" data-screen={code}>
      <div>
        <h1>{title}</h1>
      </div>
      {children}
    </div>
  );
}
export function Quota({ value }: { value: W<"QuotaView"> | null }) {
  return value ? (
    <div className="quota" role="status">
      <strong>
        Осталось {value.remaining} из {value.limit}
      </strong>
      <span>
        Получено результатов: {value.used}
        {value.reserved > 0 ? ` · Выполняется: ${value.reserved}` : ""}
      </span>
    </div>
  ) : (
    <p className="notice warn">
      Координатор ещё не настроил лимит самопроверок.
    </p>
  );
}
export function SelfReviewResult({ value }: { value: W<"SelfReviewView"> }) {
  return (
    <article className="self-review-result" data-screen="С2">
      <div className="row">
        <strong>Самопроверка · {date(value.created_at)}</strong>
        <Status value={value.status} />
      </div>
      {value.result?.findings.map((f) => (
        <div className="finding" key={f.criterion_id}>
          <span
            className={`status ${f.status === "met" ? "ok" : f.status === "needs_attention" ? "warn" : ""}`}
          >
            {f.status === "met"
              ? "Проверено"
              : f.status === "needs_attention"
                ? "Требует внимания"
                : "Не проверено"}
          </span>
          <p>{f.feedback}</p>
          {f.evidence && <blockquote>{f.evidence}</blockquote>}
        </div>
      ))}
      {value.error_code && (
        <p className="notice warn">
          Проверка не завершилась: {value.error_code}.{" "}
          {value.disposition === "released"
            ? "Попытка возвращена."
            : "Ожидаем подтверждения результата."}
        </p>
      )}
      <p className="muted">
        {value.disposition === "consumed"
          ? "За результат списана одна попытка."
          : value.disposition === "reserved"
            ? "Попытка зарезервирована до завершения."
            : "Попытка не потрачена."}
      </p>
    </article>
  );
}
export function SelfReviewMonitor({
  ws,
  id,
  onResult,
}: {
  ws: WorkspaceClient;
  id: string;
  onResult?: () => void;
}) {
  const resource = useResource(
    () => ws.selfReview(id),
    id,
    2000,
    (r) => r.disposition === "reserved",
  );
  const notified = useRef(false);
  useEffect(() => {
    if (
      resource.data &&
      resource.data.disposition !== "reserved" &&
      !notified.current
    ) {
      notified.current = true;
      onResult?.();
    }
  }, [resource.data, onResult]);
  return (
    <Resource value={resource}>
      {resource.data && (
        <>
          <Quota value={resource.data.quota} />
          <SelfReviewResult value={resource.data} />
        </>
      )}
    </Resource>
  );
}
export function ExportMonitor({ ws, id }: { ws: WorkspaceClient; id: string }) {
  const r = useResource(
    () => ws.export(id),
    id,
    2000,
    (v) => ["queued", "processing"].includes(v.status),
  );
  return (
    <Resource value={r}>
      {r.data && (
        <>
          <Status value={r.data.status} />
          <p>Строк: {r.data.rows}</p>
          {r.data.error && (
            <p role="alert">Не удалось подготовить файл: {r.data.error}</p>
          )}
          {r.data.download && (
            <a
              className="button primary"
              href={safeUrl(r.data.download.url)}
              download
            >
              Скачать файл
            </a>
          )}
        </>
      )}
    </Resource>
  );
}
export function useDirtyGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const unregister = registerDirtyEditor();
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => {
      unregister();
      window.removeEventListener("beforeunload", handler);
    };
  }, [dirty]);
}
export function EmptyTable({ children }: { children: ReactNode }) {
  return <Empty>{children}</Empty>;
}
