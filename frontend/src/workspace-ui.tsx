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
export { Modal } from "./ds";
import { Btn, Callout, Kv, OpPill, Tab, Tabs, plural } from "./ds";
/** Остаток попыток ИИ-ревью: подпись в подвале формы, не отдельный блок. */
export function Quota({ value }: { value: W<"QuotaView"> | null }) {
  return value ? (
    <span className="quota">
      <span>
        Осталось {value.remaining} из {value.limit}.
      </span>{" "}
      <span>
        Получено результатов: {value.used}
        {value.reserved > 0 ? ` · Выполняется: ${value.reserved}` : ""}
      </span>
    </span>
  ) : (
    <span className="quota">Координатор ещё не настроил лимит ИИ-ревью.</span>
  );
}

/** Вывод ИИ-ревью: вердикт строкой, места для внимания, замечание. */
export function SelfReviewResult({ value }: { value: W<"SelfReviewView"> }) {
  const raw = value.result?.findings ?? [];
  /* Итог самопроверки приходит в первой строке результата с префиксом:
     сервис не раздаёт студенту цитаты и адреса, только грубые статусы и итог. */
  const SUMMARY = "Итог самопроверки: ";
  const summary = raw[0]?.feedback.startsWith(SUMMARY)
    ? raw[0].feedback.split("\n")[0].slice(SUMMARY.length)
    : null;
  const findings = raw.map((f, index) =>
    index === 0 && summary
      ? { ...f, feedback: f.feedback.split("\n").slice(1).join("\n") }
      : f,
  );
  const attention = findings.filter((f) => f.status === "needs_attention");
  const unchecked = findings.filter((f) => f.status === "not_checked");
  const checked = findings.some((f) => f.status !== "not_checked");
  // Одинаковые подсказки (например, «не удалось подтвердить») показываем один раз.
  const places = attention.filter(
    (f, index) => attention.findIndex((g) => g.feedback === f.feedback) === index,
  );
  return (
    <div className="self-review">
      {findings.length > 0 && (
        <>
          <p className="small self-review__verdict">
            <b>
              {attention.length
                ? "Есть замечания перед отправкой."
                : checked
                  ? "По проверенным местам замечаний нет."
                  : "Работу не удалось проверить автоматически."}
            </b>{" "}
            {attention.length
              ? `${attention.length} ${plural(attention.length, "место стоит поправить", "места стоит поправить", "мест стоит поправить")} до отправки.`
              : checked
                ? "Можно отправлять работу на ревью."
                : "Проверьте доступ к работе и повторите."}
          </p>
          {summary && <p className="small">{summary}</p>}
          {places.length > 0 && (
            <ul className="small list--plain">
              {places.map((f, index) => (
                <li key={`${f.criterion_id}:${index}`}>{f.feedback}</li>
              ))}
            </ul>
          )}
          <Callout tone="human" mark="?">
            <p>
              ИИ-ревью не гарантирует, что работу примут. Верно и обратное:
              замечания не означают, что работу отклонят. Итоговое решение
              принимает ревьюер.
            </p>
          </Callout>
          {attention.length > 0 && (
            <div className="caption self-review__foot">
              Доработайте работу и проверьте ссылку в форме ниже.
            </div>
          )}
        </>
      )}
      {value.error_code && (
        <Callout tone="warn">
          <p>
            Проверка не завершилась.{" "}
            {value.disposition === "released"
              ? "Попытка возвращена. Можно повторить проверку."
              : "Ожидаем подтверждения результата."}
          </p>
        </Callout>
      )}
      {!findings.length && !value.error_code && (
        <p className="small dim">
          {value.disposition === "reserved"
            ? "Модель разбирает работу. Со страницы можно уйти, результат сохранится."
            : "Результат пока пуст."}
        </p>
      )}
      {value.disposition !== "consumed" && (
        <div className="caption self-review__foot">
          {value.disposition === "reserved"
            ? "Попытка зарезервирована до завершения проверки."
            : "Попытка не потрачена."}
        </div>
      )}
    </div>
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
        <div className="export">
          <Kv label="Файл">
            <OpPill status={r.data.status} />
          </Kv>
          <Kv label="Строк">{r.data.rows}</Kv>
          {r.data.error && (
            <Callout tone="bad" role="alert">
              <p>Не удалось подготовить файл: {r.data.error}</p>
            </Callout>
          )}
          {r.data.download && (
            <div className="btn-row btn-row--after">
              <Btn
                variant="pri"
                size="s"
                href={safeUrl(r.data.download.url) ?? "#"}
                download
              >
                Скачать файл
              </Btn>
            </div>
          )}
        </div>
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

/** Две вкладки одного кабинета ревьюера: настройки и статистика. */
export function CabinetTabs({ on }: { on: "preferences" | "statistics" }) {
  return (
    <Tabs className="tabs--page" label="Кабинет">
      <Tab href="#/preferences" on={on === "preferences"}>
        Настройки
      </Tab>
      <Tab href="#/statistics" on={on === "statistics"}>
        Статистика
      </Tab>
    </Tabs>
  );
}
