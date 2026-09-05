// Семь статусов домашки — один набор на всю платформу (design-pack.md, §8
// «Статусы»). Всё остальное в API — статусы операций, самопроверок, выгрузок,
// доставок — показывается пилюлей, а не статусом домашки.

export const statuses: Record<string, string> = {
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
  in_review: "На ревью",
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
  pending_review: "Сдана",
  consumed: "Использовано",
  revoked: "Отозвано",
  expired: "Истекло",
  available: "Доступно",
  requires_action: "Нужно действие",
  unavailable: "Недоступно",
  not_supported: "Не поддерживается",
  met: "Выполнено",
  needs_attention: "Нужно внимание",
  not_checked: "Не проверено",
  suggested: "Предложено моделью",
  needs_human: "Решает человек",
};

export type WorkTone =
  "draft" | "sent" | "review" | "fix" | "rereview" | "pass" | "fail";

export const workLabels: Record<WorkTone, string> = {
  draft: "Черновик",
  sent: "Сдана",
  review: "На ревью",
  fix: "Нужны правки",
  rereview: "На повторном ревью",
  pass: "Зачтена",
  fail: "Не зачтена",
};

/**
 * Статус API → один из семи статусов домашки. Бэкенд отдаёт draft,
 * pending_review, queued, in_review, ready_to_publish, published, canceled,
 * passed, failed, needs_changes; «На повторном ревью» он не отдаёт никогда,
 * это выводится из номера попытки.
 */
export function workTone(
  status: string | null | undefined,
  attempt = 1,
  decision?: string | null,
): WorkTone | null {
  const again = attempt > 1;
  switch (status) {
    case "draft":
      return "draft";
    case "pending_review":
    case "queued":
    case "submitted":
      return again ? "rereview" : "sent";
    case "in_review":
    case "ready_to_publish":
    case "pending":
      return again ? "rereview" : "review";
    case "resubmitted":
      return "rereview";
    case "needs_changes":
      return "fix";
    case "passed":
      return "pass";
    case "failed":
      return "fail";
    case "published":
      return decision ? workTone(decision, attempt) : null;
    default:
      return null;
  }
}

export function workStatus(
  status: string | null | undefined,
  attempt = 1,
  decision?: string | null,
) {
  const tone = workTone(status, attempt, decision);
  return tone ? { tone, label: workLabels[tone] } : null;
}

/** Закрытая работа: строка таблицы гаснет до серого (.is-done). */
export function isClosed(status: string | null | undefined) {
  return status === "passed" || status === "failed" || status === "canceled";
}

export type OpTone = "ok" | "bad" | "late" | "busy" | "";

/** Тон пилюли для служебных статусов операций, доставок, самопроверок. */
export function opTone(status: string | null | undefined): OpTone {
  switch (status) {
    case "succeeded":
    case "ready":
    case "available":
    case "active":
    case "published":
    case "met":
    case "passed":
      return "ok";
    case "failed":
    case "access_error":
    case "retryable_failed":
    case "unavailable":
    case "not_supported":
      return "bad";
    case "unknown_outcome":
    case "action_required":
    case "requires_action":
    case "reconciling":
    case "stale":
    case "needs_attention":
    case "needs_changes":
      return "late";
    case "queued":
    case "pending":
    case "processing":
    case "running":
    case "capturing":
    case "validating":
    case "partial":
      return "busy";
    default:
      return "";
  }
}

export function statusLabel(status: string | null | undefined) {
  return status ? (statuses[status] ?? status) : DASH_LABEL;
}
const DASH_LABEL = "—";
