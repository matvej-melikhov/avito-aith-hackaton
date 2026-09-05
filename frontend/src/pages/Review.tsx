import { useCallback, useEffect, useState } from "react";
import type { ApiClient, Model } from "../api/client";
import {
  Card,
  ErrorBox,
  Resource,
  Status,
  date,
  safeUrl,
  useAction,
  useResource,
} from "../ui";
import { WorkspaceClient, type W } from "../api/workspace";
import { HeaderProfile, Modal, useDirtyGuard } from "../workspace-ui";
export async function loadReview(api: ApiClient, id: string) {
  const detail = await api.review(id);
  const published = await api.homeworks(detail.immutable_inputs.course_run_id);
  const histories = await Promise.all(
    [...new Set(published.items.map((h) => h.homework_id))].map((h) =>
      api.homework(h),
    ),
  );
  const version = histories
    .flatMap((h) => h.versions)
    .find((v) => v.id === detail.immutable_inputs.homework_version_id);
  return { detail, version };
}
export function ReviewPage({
  api,
  id,
  session,
  readOnly = false,
  ws,
}: {
  api: ApiClient;
  id: string;
  session: Model<"Session">;
  readOnly?: boolean;
  ws?: WorkspaceClient;
}) {
  const [editing, setEditing] = useState(!readOnly);
  useEffect(() => setEditing(!readOnly), [id, readOnly]);
  const resource = useResource(async () => {
    if (!ws) return loadReview(api, id);
    const [detail, context] = await Promise.all([
      ws.reviewDetail(id),
      ws.reviewContext(id),
    ]);
    return {
      detail,
      context,
      version: {
        id: context.homework_version_id,
        student_text: context.student_text,
        max_score: context.max_score,
        criteria: context.criteria,
      },
    };
  }, id);
  return (
    <>
      <Resource value={resource}>
        {resource.data && (
          <ReviewEditor
            key={`${id}:${resource.data.detail.revision}`}
            api={api}
            {...resource.data}
            session={session}
            readOnly={!editing}
            onEdit={readOnly && !editing ? () => setEditing(true) : undefined}
            ws={ws}
            refresh={resource.refresh}
          />
        )}
      </Resource>
    </>
  );
}
export function ReviewEditor({
  api,
  detail,
  version,
  session,
  refresh,
  readOnly = false,
  ws,
  context,
  onEdit,
}: {
  api: ApiClient;
  detail: Model<"ReviewDetail">;
  version?: Pick<
    Model<"HomeworkVersionSummary">,
    "id" | "student_text" | "max_score" | "criteria"
  >;
  session: Model<"Session">;
  refresh: () => void;
  readOnly?: boolean;
  ws?: WorkspaceClient;
  context?: W<"ReviewContext">;
  onEdit?: () => void;
}) {
  const action = useAction();
  const [correcting, setCorrecting] = useState(false);
  const [correctionReason, setCorrectionReason] = useState("");
  const closeCorrection = useCallback(() => setCorrecting(false), []);
  const history = useResource(
    () =>
      ws && context?.submission_id
        ? ws.submission(context.submission_id)
        : Promise.resolve(null),
    context?.submission_id ?? "no-submission",
  );
  const [feedback, setFeedback] = useState(
    detail.current_review_revision?.feedback ?? "",
  );
  const [decisions, setDecisions] = useState<
    Model<"ReviewCriterionDecision">[]
  >(
    version?.criteria.map(
      (c) =>
        detail.criterion_decisions.find((d) => d.criterion_id === c.id) ?? {
          criterion_id: c.id,
          points: 0,
          decision: "manual",
          reason: "",
        },
    ) ?? [],
  );
  const [notes] = useState(
    detail.review_notes.map((n) => ({
      criterion_id: n.criterion_id ?? null,
      text: n.text,
    })),
  );
  const [dirty, setDirty] = useState(false);
  useDirtyGuard(dirty);
  const [sourceRun, setSourceRun] = useState<string | null>(
    context?.ai_run_id ?? null,
  );
  const [signalDecisions, setSignalDecisions] = useState<
    Record<string, "confirm" | "reject">
  >(context?.signal_decisions ?? {});
  const [outcome, setOutcome] = useState<"passed" | "needs_changes" | null>(
    null,
  );
  const close = useCallback(() => setOutcome(null), []);
  const [deadline, setDeadline] = useState("");
  const [reason, setReason] = useState("");
  const [penalty, setPenalty] = useState(true);
  const [extraTitle, setExtraTitle] = useState("");
  const [extraMax, setExtraMax] = useState(1);
  const [unscored, setUnscored] = useState(false);
  const editable =
    !readOnly &&
    session.actor_type === "user" &&
    session.roles.some((r) => r === "reviewer" || r === "methodologist") &&
    !["published", "canceled"].includes(detail.status);
  const assist = useResource(
    () =>
      ws ? ws.reviewAssist(detail.review_iteration_id) : Promise.resolve(null),
    detail.review_iteration_id,
    3000,
    (next) =>
      !!next && ["queued", "running", "unknown_outcome"].includes(next.status),
  );
  const grade = useResource(
    () =>
      ws
        ? ws.gradePreview(detail.review_iteration_id)
        : Promise.resolve(undefined),
    `${detail.review_iteration_id}:${detail.revision}`,
  );
  const suggestions = assist.data?.result?.suggestions ?? [];
  const signal = assist.data?.result?.authorship_signal;
  const total = decisions.reduce(
    (sum, d) => sum + (Number.isFinite(d.points) ? d.points : 0),
    0,
  );
  const canSave =
    !!version &&
    decisions.length === version.criteria.length &&
    decisions.every(
      (d) =>
        d.reason.trim() &&
        Number.isFinite(d.points) &&
        d.points >= 0 &&
        d.points <=
          (version.criteria.find((c) => c.id === d.criterion_id)?.max_points ??
            -1),
    );
  function edit(
    index: number,
    patch: Partial<Model<"ReviewCriterionDecision">>,
  ) {
    setDirty(true);
    setDecisions((all) =>
      all.map((d, i) => (i === index ? { ...d, ...patch } : d)),
    );
  }
  async function save() {
    const draft = {
      feedback,
      criterion_decisions: decisions,
      review_notes: notes,
    };
    if (ws)
      await ws.command(
        "save_workspace_review",
        detail.review_iteration_id,
        detail.revision,
        { draft, ai_run_id: sourceRun, signal_decisions: signalDecisions },
      );
    else
      await api.command(
        "save_review_revision",
        detail.review_iteration_id,
        detail.revision,
        draft,
      );
    setDirty(false);
    refresh();
  }
  async function publish(revisionDeadline = deadline) {
    if (!outcome || !detail.current_review_revision_id) return;
    if (ws) {
      const latest = await ws.reviewContext(detail.review_iteration_id);
      await ws.command(
        "save_review_outcome",
        detail.review_iteration_id,
        context?.outcome_revision ?? latest.outcome_revision,
        {
          decision: outcome,
          reason: reason.trim() || feedback.trim() || "Работа зачтена",
          revision_deadline:
            outcome === "needs_changes"
              ? new Date(revisionDeadline).toISOString()
              : null,
        },
      );
      const saved = await ws.reviewDetail(detail.review_iteration_id);
      if (
        saved.current_review_revision_id !== detail.current_review_revision_id
      )
        throw new Error(
          "Коллега изменил черновик. Обновите проверку и проверьте сохранённую оценку перед публикацией.",
        );
      await ws.command(
        "publish_workspace_review",
        detail.review_iteration_id,
        saved.revision,
        {
          review_revision_id: detail.current_review_revision_id,
          apply_penalty: penalty,
        },
      );
    } else
      await api.command(
        "publish_review",
        detail.review_iteration_id,
        detail.revision,
        { review_revision_id: detail.current_review_revision_id },
      );
    close();
    refresh();
  }
  const activePeople = new Map<string, string>();
  detail.responsibility_events.forEach((e) =>
    activePeople.set(e.reviewer_id, e.action),
  );
  const participants = [...activePeople.values()].filter(
    (v) => v === "joined" || v === "started",
  ).length;
  const joined = ["started", "joined"].includes(
    activePeople.get(session.user_id) ?? "",
  );
  const canCorrect =
    session.actor_type === "user" &&
    session.roles.some(
      (role) => role === "reviewer" || role === "methodologist",
    ) &&
    detail.status === "published";
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            <a href={readOnly ? "#/registry" : "#/works"}>
              ← {readOnly ? "Домашки" : "Мои работы"}
            </a>
            {context?.student_name && <> / {context.student_name}</>}
          </p>
          <div className="row">
            <h1>{context?.title || "Проверка работы"}</h1>
            <Status
              attempt={context?.attempt}
              value={
                detail.status === "published"
                  ? (context?.outcome?.decision ?? detail.status)
                  : detail.status
              }
            />
          </div>
        </div>
        <div className="actions">
          {onEdit && !["published", "canceled"].includes(detail.status) && (
            <div className="actions">
              <button onClick={onEdit}>Редактировать проверку</button>
            </div>
          )}
          {canCorrect && (
            <div className="actions">
              <button onClick={() => setCorrecting(true)}>
                Создать исправление
              </button>
            </div>
          )}
          {editable && (
            <div className="actions">
              <button
                disabled={action.busy || dirty}
                onClick={() =>
                  void action.run(async () => {
                    await api.command(
                      "record_review_responsibility",
                      detail.review_iteration_id,
                      detail.revision,
                      { action: "released" },
                    );
                    refresh();
                  })
                }
              >
                Вернуть в пул
              </button>
              <button
                disabled={action.busy || !canSave}
                onClick={() => void action.run(save, "Черновик сохранён.")}
              >
                Сохранить черновик
              </button>
            </div>
          )}
          <HeaderProfile />
        </div>
      </div>
      {!outcome && !correcting && action.feedback}
      {(history.data?.attempts.length ?? 0) > 1 && (
        <nav className="tabs" aria-label="Попытки сдачи">
          {[...history.data!.attempts]
            .sort((a, b) => b.sequence - a.sequence)
            .map((attempt) => {
              const published = history
                .data!.reviews.filter(
                  (r) => r.submission_version_id === attempt.id,
                )
                .sort((a, b) =>
                  b.published_at.localeCompare(a.published_at),
                )[0];
              const selected =
                attempt.id === detail.immutable_inputs.submission_version_id;
              const latest =
                attempt.sequence ===
                Math.max(...history.data!.attempts.map((a) => a.sequence));
              const reviewId = selected
                ? detail.review_iteration_id
                : (published?.iteration_id ??
                  (latest ? context?.latest_review_iteration_id : undefined));
              return reviewId ? (
                <a
                  key={attempt.id}
                  aria-current={selected ? "page" : undefined}
                  href={`#/reviews/${reviewId}`}
                >
                  Попытка {attempt.sequence}
                </a>
              ) : (
                <span key={attempt.id}>Попытка {attempt.sequence}</span>
              );
            })}
        </nav>
      )}
      <div className="review-grid">
        <div className="stack">
          <Card title="Работа">
            <div className="row">
              <span className="mono">
                {context?.artifact_label ?? "Снимок работы"}
              </span>
              <a
                className="button"
                href={safeUrl(detail.immutable_inputs.artifact_download_url)}
                target="_blank"
                rel="noreferrer"
              >
                Открыть ↗
              </a>
            </div>
            <dl className="review-facts">
              <dt>Попытка</dt>
              <dd>{context?.attempt ?? "—"}</dd>
              <dt>Сдана</dt>
              <dd>
                {context?.submitted_at ? date(context.submitted_at) : "—"}
              </dd>
              <dt>Срок сдачи был</dt>
              <dd>{date(detail.immutable_inputs.effective_deadline)}</dd>
              <dt>ИИ-ревью до сдачи</dt>
              <dd>
                {context?.self_reviews.length ?? 0}{" "}
                {(context?.self_reviews.length ?? 0) === 1
                  ? "запуск"
                  : (context?.self_reviews.length ?? 0) < 5
                    ? "запуска"
                    : "запусков"}
              </dd>
            </dl>
            {participants - (joined ? 1 : 0) > 0 && (
              <p className="muted">
                Других участников: {participants - (joined ? 1 : 0)}
              </p>
            )}
            {editable && !joined && (
              <button
                disabled={action.busy || dirty}
                onClick={() =>
                  void action.run(async () => {
                    await api.command(
                      "record_review_responsibility",
                      detail.review_iteration_id,
                      detail.revision,
                      { action: "joined" },
                    );
                    refresh();
                  })
                }
              >
                Присоединиться
              </button>
            )}
          </Card>
          <Card title="Признаки генерации ИИ">
            <p className="muted">Оценка модели, на балл не влияет</p>
            {signal ? (
              <>
                <p className="stat-number">
                  {signal.probability == null
                    ? "Недостаточно данных"
                    : `${Math.round(signal.probability * 100)}%`}
                </p>
                <p>{signal.explanation}</p>
                {(signal.evidence ?? []).map((e, i) => (
                  <blockquote key={i}>
                    {e.quote}
                    <small>{e.locator ?? e.path}</small>
                  </blockquote>
                ))}
                {editable && (
                  <div className="actions">
                    <button
                      aria-pressed={signalDecisions[signal.id] === "confirm"}
                      onClick={() => {
                        setSourceRun(assist.data!.id);
                        setSignalDecisions({ [signal.id]: "confirm" });
                        setDirty(true);
                      }}
                    >
                      Подтвердить сигнал
                    </button>
                    <button
                      aria-pressed={signalDecisions[signal.id] === "reject"}
                      onClick={() => {
                        setSourceRun(assist.data!.id);
                        setSignalDecisions({ [signal.id]: "reject" });
                        setDirty(true);
                      }}
                    >
                      Отклонить сигнал
                    </button>
                  </div>
                )}
              </>
            ) : (
              <p>Нет данных о признаках генерации.</p>
            )}
            {assist.data?.status === "failed" && (
              <p role="alert">
                Не удалось завершить AI-проверку. Повторите запуск.
              </p>
            )}
            {!!assist.error && (
              <ErrorBox error={assist.error} retry={assist.refresh} />
            )}{" "}
            {assist.data &&
              ["queued", "running", "unknown_outcome", "failed"].includes(
                assist.data.status,
              ) && <Status value={assist.data.status} />}{" "}
            {editable && ws && (
              <button
                disabled={
                  action.busy ||
                  ["queued", "running"].includes(assist.data?.status ?? "")
                }
                onClick={() =>
                  void action.run(async () => {
                    if (
                      assist.data &&
                      ["failed", "unknown_outcome"].includes(assist.data.status)
                    )
                      await ws.command(
                        "retry_review_assist",
                        assist.data.id,
                        assist.data.revision,
                        {},
                      );
                    else
                      await ws.command(
                        "start_review_assist",
                        detail.review_iteration_id,
                        detail.revision,
                        {},
                      );
                    assist.refresh();
                  })
                }
              >
                {assist.data ? "Повторить проверку" : "Запустить проверку"}
              </button>
            )}
          </Card>
          <Card
            title="Ответ студенту"
            actions={
              editable && (
                <button
                  disabled={
                    action.busy ||
                    (!assist.data?.result?.feedback_draft &&
                      !suggestions.some((s) => s.student_feedback))
                  }
                  onClick={() => {
                    setFeedback(
                      assist.data?.result?.feedback_draft ??
                        suggestions
                          .map((s) => s.student_feedback)
                          .filter(Boolean)
                          .join("\n\n"),
                    );
                    setDirty(true);
                  }}
                >
                  Собрать заново
                </button>
              )
            }
          >
            <p className="muted">Уходит вместе с вердиктом</p>
            <textarea
              aria-label="Обратная связь студенту"
              disabled={!editable || action.busy}
              rows={6}
              value={feedback}
              onChange={(e) => {
                setFeedback(e.target.value);
                setDirty(true);
              }}
            />
          </Card>
          <Card title="Результат">
            <dl className="review-facts">
              <dt>По требованиям задания</dt>
              <dd>{total.toLocaleString("ru-RU")}</dd>
              <dt>Просрочка</dt>
              <dd>
                {grade.data
                  ? `−${grade.data.penalty.toLocaleString("ru-RU")}`
                  : "—"}
              </dd>
              <dt>Итог</dt>
              <dd>
                {(dirty
                  ? total
                  : (grade.data?.final_score ?? total)
                ).toLocaleString("ru-RU")}{" "}
                из {version?.max_score.toLocaleString("ru-RU") ?? "—"}
              </dd>
            </dl>
            {grade.data?.pass_score != null && (
              <p>
                Порог зачёта: {grade.data.pass_score.toLocaleString("ru-RU")} из{" "}
                {version?.max_score.toLocaleString("ru-RU")}.
              </p>
            )}
            {dirty && (
              <p className="muted">Сохраните черновик перед публикацией.</p>
            )}
            {editable && (
              <div className="actions">
                <button
                  disabled={
                    action.busy || dirty || !detail.current_review_revision_id
                  }
                  onClick={() => {
                    setOutcome("needs_changes");
                    setReason("");
                  }}
                >
                  Вернуть на доработку
                </button>
                <button
                  className="primary"
                  disabled={
                    action.busy || dirty || !detail.current_review_revision_id
                  }
                  onClick={() => {
                    setOutcome("passed");
                    setReason("");
                  }}
                >
                  Зачесть
                </button>
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          <Card
            title="Предварительное ревью от модели"
            actions={
              editable && (
                <button
                  disabled={action.busy || !suggestions.length}
                  onClick={() => {
                    setSourceRun(assist.data?.id ?? null);
                    suggestions.forEach((s) => {
                      const i = decisions.findIndex(
                        (d) => d.criterion_id === s.criterion_id,
                      );
                      if (i >= 0 && s.proposed_points !== null)
                        edit(i, {
                          points: s.proposed_points,
                          reason: s.reason,
                          decision: "accepted",
                        });
                    });
                  }}
                >
                  Принять все
                </button>
              )
            }
          >
            <fieldset disabled={!editable || action.busy}>
              {version?.criteria.map((c, i) => {
                const suggestion = suggestions.find(
                  (s) => s.criterion_id === c.id,
                );
                const needsHuman =
                  suggestion?.status === "needs_human" ||
                  context?.private_details?.criterion_classes?.[c.key] ===
                    "judgement";
                const hasEvidence = !!(
                  suggestion?.sources?.length || suggestion?.evidence?.length
                );
                const met =
                  suggestion?.requirement_met ??
                  (suggestion?.proposed_points ?? 0) > 0;
                const marker = needsHuman
                  ? "h"
                  : !hasEvidence
                    ? "q"
                    : met
                      ? "y"
                      : "n";
                return (
                  <details
                    className="criterion"
                    key={c.id}
                    open={
                      suggestion?.status === "needs_human" ||
                      (!!suggestion &&
                        suggestion.proposed_points !== c.max_points)
                    }
                  >
                    <summary className="row">
                      <span className={`ck ck--${marker}`} aria-hidden="true">
                        {marker === "y" ? "✓" : marker === "n" ? "✕" : "?"}
                      </span>
                      <strong>{c.title}</strong>
                      <label className="score">
                        <span className="sr-only">Баллы</span>
                        <input
                          aria-label={`Баллы: ${c.title}`}
                          type="number"
                          min={0}
                          max={c.max_points}
                          step="any"
                          value={
                            Number.isFinite(decisions[i]?.points)
                              ? decisions[i].points
                              : ""
                          }
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) =>
                            edit(i, {
                              points: e.target.valueAsNumber,
                              decision: "manual",
                            })
                          }
                        />
                        <span>из {c.max_points.toLocaleString("ru-RU")}</span>
                      </label>
                    </summary>
                    {suggestion && (
                      <article className="suggestion">
                        <p>{suggestion.reason}</p>
                        {(suggestion.sources ?? []).map((source, index) => (
                          <blockquote key={`source:${index}`}>
                            <small>
                              {source.path ?? source.locator ?? "Снимок работы"}
                              {source.line_start
                                ? ` · строки ${source.line_start}${source.line_end ? `–${source.line_end}` : ""}`
                                : ""}
                            </small>
                            <pre className="preserve">{source.quote}</pre>
                          </blockquote>
                        ))}
                        {(!suggestion.sources?.length
                          ? (suggestion.evidence ?? [])
                          : []
                        ).map((quote, index) => (
                          <blockquote key={index}>
                            <pre className="preserve">{quote}</pre>
                          </blockquote>
                        ))}
                        {suggestion.reviewer_note && (
                          <p className="muted">{suggestion.reviewer_note}</p>
                        )}
                        {editable && suggestion.proposed_points !== null && (
                          <button
                            onClick={() => {
                              setSourceRun(assist.data?.id ?? null);
                              edit(i, {
                                points: suggestion.proposed_points!,
                                reason: suggestion.reason,
                                decision: "accepted",
                              });
                            }}
                          >
                            Принять предложение
                          </button>
                        )}
                      </article>
                    )}
                    <label>
                      Обоснование
                      <textarea
                        value={decisions[i]?.reason ?? ""}
                        onChange={(e) => edit(i, { reason: e.target.value })}
                      />
                    </label>
                  </details>
                );
              })}
              {editable && ws && (
                <div className="criterion">
                  <strong>Своё требование</strong>
                  <div className="row">
                    <input
                      aria-label="Своё требование"
                      placeholder="Текст требования"
                      value={extraTitle}
                      onChange={(e) => setExtraTitle(e.target.value)}
                    />
                    <label className="score">
                      Максимум
                      <input
                        type="number"
                        min={0}
                        step="any"
                        disabled={unscored}
                        value={extraMax}
                        onChange={(e) => setExtraMax(e.target.valueAsNumber)}
                      />
                    </label>
                  </div>
                  <div className="row">
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={unscored}
                        onChange={(e) => setUnscored(e.target.checked)}
                      />
                      Не влияет на балл
                    </label>
                    <button
                      disabled={
                        dirty ||
                        !extraTitle.trim() ||
                        !Number.isFinite(extraMax) ||
                        extraMax < 0
                      }
                      onClick={() =>
                        void action.run(async () => {
                          const result = await ws.command(
                            "add_review_requirement",
                            detail.review_iteration_id,
                            detail.revision,
                            {
                              title: extraTitle,
                              description: "",
                              max_points: unscored ? 0 : extraMax,
                            },
                          );
                          window.location.hash = `/reviews/${result.id}`;
                        })
                      }
                    >
                      Добавить ещё требование
                    </button>
                  </div>
                  {dirty && <small>Сначала сохраните черновик.</small>}
                </div>
              )}
            </fieldset>
            <p className="row">
              <strong>Сумма по требованиям</strong>
              <span>
                {total.toLocaleString("ru-RU")} из{" "}
                {version?.max_score.toLocaleString("ru-RU") ?? "—"}
              </span>
            </p>
          </Card>
        </div>
      </div>
      {correcting && (
        <Modal title="Создать исправление" close={closeCorrection}>
          {action.feedback}
          <p>
            Откроется новая версия проверки. Студент продолжит видеть прежний
            результат до публикации исправления.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const result = await api.command(
                  "create_review_correction",
                  detail.review_iteration_id,
                  detail.revision,
                  {
                    published_review_revision_id:
                      detail.current_review_revision_id!,
                    reason: correctionReason,
                  },
                );
                closeCorrection();
                window.location.hash = `/reviews/${result.review_iteration_id}`;
              });
            }}
          >
            <label>
              Причина исправления
              <textarea
                required
                value={correctionReason}
                onChange={(e) => setCorrectionReason(e.target.value)}
              />
            </label>
            <button
              className="primary"
              disabled={action.busy || !correctionReason.trim()}
            >
              Открыть новую версию
            </button>
          </form>
        </Modal>
      )}
      {outcome && (
        <Modal
          title={
            outcome === "needs_changes"
              ? "Вернуть на доработку"
              : "Зачесть работу"
          }
          close={close}
        >
          {action.feedback}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const revisionDeadline = String(
                new FormData(e.currentTarget).get("revision_deadline") ?? "",
              );
              void action.run(() => publish(revisionDeadline));
            }}
          >
            {outcome === "needs_changes" && (
              <>
                <label>
                  Срок доработки
                  <input
                    required
                    type="datetime-local"
                    name="revision_deadline"
                    value={deadline}
                    onChange={(e) => setDeadline(e.target.value)}
                  />
                </label>
                <label>
                  Что нужно исправить
                  <textarea
                    required
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </label>
              </>
            )}
            <p>
              Студент получит сохранённый отзыв и оценку{" "}
              {grade.data
                ? (penalty
                    ? grade.data.final_score
                    : grade.data.raw_score
                  ).toLocaleString("ru-RU")
                : detail.current_review_revision?.total_score}
              .
            </p>
            {grade.data && grade.data.penalty > 0 && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={penalty}
                  onChange={(e) => setPenalty(e.target.checked)}
                />
                Применить просрочку
              </label>
            )}
            <button
              className="primary"
              disabled={
                action.busy || (!!ws && (grade.loading || !!grade.error))
              }
            >
              Подтвердить публикацию
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
