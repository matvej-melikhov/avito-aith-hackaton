import { Btn, Dock, plural } from "../ds";
import { ArtifactLink } from "./WorkspaceReview";
import { nextPoolWork, openQueueWork } from "./reviewMode";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient, Model } from "../api/client";
import {
  Card,
  ErrorBox,
  Resource,
  Status,
  date,
  safeUrl,
  go,
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
  detail: initialDetail,
  version,
  session,
  refresh,
  readOnly = false,
  ws,
  context: initialContext,
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
  const [detail, setDetail] = useState(initialDetail);
  const [context, setContext] = useState(initialContext);
  const [savedNotice, setSavedNotice] = useState("");
  const directory = useResource(
    () =>
      ws && session.roles.includes("methodologist")
        ? ws.directory()
        : Promise.resolve(null),
    "review-directory",
  );
  const criterionNodes = useRef(new Map<string, HTMLDetailsElement>());
  const action = useAction();
  const [correcting, setCorrecting] = useState(false);
  const [poolEmpty, setPoolEmpty] = useState(false);
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
  const touchedCriteria = useRef(new Set<string>());
  const appliedCriteria = useRef(new Set<string>());
  const feedbackTouched = useRef(false);
  const [dirty, setDirty] = useState(false);
  useDirtyGuard(dirty);
  useEffect(() => {
    if (dirty) setSavedNotice("");
  }, [dirty]);
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
  const [addingRequirement, setAddingRequirement] = useState(false);
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
  useEffect(() => {
    const run = assist.data;
    if (
      !editable ||
      detail.current_review_revision_id ||
      !version ||
      run?.status !== "succeeded" ||
      !run.result
    )
      return;
    const valid = run.result.suggestions.filter((s) => {
      const criterion = version.criteria.find((c) => c.id === s.criterion_id);
      return (
        criterion &&
        !touchedCriteria.current.has(s.criterion_id) &&
        !appliedCriteria.current.has(s.criterion_id) &&
        s.proposed_points != null &&
        Number.isFinite(s.proposed_points) &&
        s.proposed_points >= 0 &&
        s.proposed_points <= criterion.max_points &&
        s.reason.trim()
      );
    });
    valid.forEach((s) => appliedCriteria.current.add(s.criterion_id));
    if (valid.length)
      setDecisions((current) =>
        current.map((d) => {
          const suggested = valid.find(
            (s) => s.criterion_id === d.criterion_id,
          );
          return suggested
            ? {
                ...d,
                points: suggested.proposed_points!,
                reason: suggested.reason,
                decision: "accepted",
              }
            : d;
        }),
      );
    const draft =
      run.result.feedback_draft ??
      run.result.suggestions
        .map((s) => s.student_feedback)
        .filter(Boolean)
        .join("\n\n");
    const fillFeedback = !feedbackTouched.current && !!draft;
    if (fillFeedback) {
      feedbackTouched.current = true;
      setFeedback(draft);
    }
    if (valid.length || fillFeedback) {
      setSourceRun(run.id);
      setDirty(true);
    }
  }, [assist.data, editable, detail.current_review_revision_id, version]);
  const total = decisions.reduce(
    (sum, d) => sum + (Number.isFinite(d.points) ? d.points : 0),
    0,
  );
  const displayPenalty = grade.data
    ? Math.min(
        total,
        Math.round(grade.data.penalty_days * grade.data.penalty_rate * 100) /
          100,
      )
    : null;
  const displayFinal =
    displayPenalty === null ? null : Math.max(0, total - displayPenalty);
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
    const criterion = version?.criteria[index];
    if (criterion) touchedCriteria.current.add(criterion.id);
    setDirty(true);
    setDecisions((all) =>
      all.map((d, i) => (i === index ? { ...d, ...patch } : d)),
    );
  }
  async function reloadLocal() {
    const [fresh, nextContext] = await Promise.all([
      ws
        ? ws.reviewDetail(detail.review_iteration_id)
        : api.review(detail.review_iteration_id),
      ws
        ? ws.reviewContext(detail.review_iteration_id)
        : Promise.resolve(context),
    ]);
    setDetail(fresh);
    setContext(nextContext);
    return fresh;
  }
  async function save() {
    const draft = {
      feedback,
      criterion_decisions: decisions,
      review_notes: notes,
    };
    let revisionId: string;
    if (ws)
      revisionId = (
        await ws.command(
          "save_workspace_review",
          detail.review_iteration_id,
          detail.revision,
          { draft, ai_run_id: sourceRun, signal_decisions: signalDecisions },
        )
      ).id;
    else
      revisionId = (
        await api.command(
          "save_review_revision",
          detail.review_iteration_id,
          detail.revision,
          draft,
        )
      ).review_revision_id;
    const [fresh, nextContext] = await Promise.all([
      ws
        ? ws.reviewDetail(detail.review_iteration_id)
        : api.review(detail.review_iteration_id),
      ws
        ? ws.reviewContext(detail.review_iteration_id)
        : Promise.resolve(context),
    ]);
    if (fresh.current_review_revision_id !== revisionId)
      throw new Error(
        "После сохранения коллега изменил ревью. Ваш текст сохранён в редакторе; обновите проверку перед следующей записью.",
      );
    setDetail(fresh);
    setContext(nextContext);
    setDirty(false);
    setSavedNotice("Черновик сохранён.");
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
          apply_penalty: true,
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
    await reloadLocal();
    setSavedNotice("Ревью опубликовано студенту.");
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
          <div className="crumbs">
            <a
              className="crumbs__back"
              aria-label="Назад"
              href={readOnly ? "#/registry" : "#/works"}
            >
              ←
            </a>
            <a href={readOnly ? "#/registry" : "#/works"}>
              {readOnly ? "Домашки" : "Мои работы"}
            </a>
            {context?.student_name && (
              <>
                <span>/</span>
                <span className="cur">{context.student_name}</span>
              </>
            )}
          </div>
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
        <div className="actions page-heading-actions">
          {onEdit && !["published", "canceled"].includes(detail.status) && (
            <div className="actions">
              <Btn onClick={onEdit}>Редактировать проверку</Btn>
            </div>
          )}
          {canCorrect && (
            <div className="actions">
              <Btn onClick={() => setCorrecting(true)}>
                Исправить опубликованное ревью
              </Btn>
            </div>
          )}
          {editable && (
            <div className="actions">
              <Btn
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
              </Btn>
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
                  className={`tab ${selected ? "is-on" : ""}`}
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
      <div className="review-grid row-rev review-layout">
        <div className="stack">
          <Card title="Работа">
            <div className="row review-artifact-row">
              <span
                className="mono"
                title={context?.artifact_label ?? "Снимок работы"}
              >
                {(context?.artifact_label ?? "Снимок работы").replace(
                  /^https?:\/\//,
                  "",
                )}
              </span>
              {ws ? (
                <ArtifactLink
                  ws={ws}
                  id={detail.immutable_inputs.artifact_version_id}
                  label="Открыть ↗"
                />
              ) : (
                <a
                  className="button"
                  href={safeUrl(detail.immutable_inputs.artifact_download_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть ↗
                </a>
              )}
            </div>
            <dl className="review-facts">
              <dt>Попытка</dt>
              <dd>{context?.attempt ?? "—"}</dd>
              <dt>
                {(context?.attempt ?? 0) > 1 ? "Исправления сданы" : "Сдана"}
              </dt>
              <dd>
                {context?.submitted_at ? date(context.submitted_at) : "—"}
              </dd>
              {(context?.attempt ?? 0) <= 1 && (
                <>
                  <dt>Срок сдачи был</dt>
                  <dd>{date(detail.immutable_inputs.effective_deadline)}</dd>
                </>
              )}
              <dt>ИИ-ревью до сдачи</dt>
              <dd>
                {context?.self_reviews.length ?? 0}{" "}
                {plural(
                  context?.self_reviews.length ?? 0,
                  "запуск",
                  "запуска",
                  "запусков",
                )}
              </dd>
            </dl>
            {session.roles.includes("methodologist") && directory.loading && (
              <p role="status" className="caption">
                Загружаем участников…
              </p>
            )}
            {directory.data ? (
              <div className="review-participants">
                <strong>Участники проверки</strong>
                <ul>
                  {[...activePeople.entries()]
                    .filter(([, state]) =>
                      ["joined", "started"].includes(state),
                    )
                    .map(([id]) => {
                      const person = directory.data!.items.find(
                        (p) => p.id === id,
                      );
                      return (
                        <li key={id}>
                          {person?.display_name ?? "Имя участника недоступно"} ·
                          ревьюер
                        </li>
                      );
                    })}
                </ul>
              </div>
            ) : participants - (joined ? 1 : 0) > 0 ? (
              <p className="muted">
                Других участников: {participants - (joined ? 1 : 0)}
              </p>
            ) : null}
            {editable && !joined && (
              <Btn
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
              </Btn>
            )}
          </Card>
          <Card title="Условие задания">
            <div className="preserve">
              {context?.student_text ??
                version?.student_text ??
                "Условие недоступно."}
            </div>
          </Card>
          {readOnly && (
            <Card
              title="История решений"
              subtitle="Что предложила модель и что изменил человек"
              bodyClassName="card__body--tight"
            >
              {context?.decision_history?.length ? (
                context.decision_history.map((event, index) => (
                  <div
                    className="kv decision-event"
                    key={`${event.timestamp}:${index}`}
                  >
                    <span>
                      {event.text}
                      {event.actor && <small>{event.actor}</small>}
                    </span>
                    <time className="caption" dateTime={event.timestamp}>
                      {date(event.timestamp)}
                    </time>
                  </div>
                ))
              ) : (
                <p className="muted">История решений пока пуста.</p>
              )}
            </Card>
          )}
          {!readOnly && (
            <>
              <Card
                title="Признаки генерации ИИ"
                subtitle="Оценка модели, на балл не влияет"
              >
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
                        <Btn
                          aria-pressed={
                            signalDecisions[signal.id] === "confirm"
                          }
                          onClick={() => {
                            setSourceRun(assist.data!.id);
                            setSignalDecisions({ [signal.id]: "confirm" });
                            setDirty(true);
                          }}
                        >
                          Подтвердить сигнал
                        </Btn>
                        <Btn
                          aria-pressed={signalDecisions[signal.id] === "reject"}
                          onClick={() => {
                            setSourceRun(assist.data!.id);
                            setSignalDecisions({ [signal.id]: "reject" });
                            setDirty(true);
                          }}
                        >
                          Отклонить сигнал
                        </Btn>
                      </div>
                    )}
                  </>
                ) : (
                  <p>Нет данных о признаках генерации.</p>
                )}
              </Card>
              <Card
                title="Ответ студенту"
                subtitle="Студент получит его вместе с решением"
                headClassName="card__head--response"
                actions={
                  editable && (
                    <Btn
                      className="btn btn--s btn--quiet"
                      disabled={
                        action.busy ||
                        (!assist.data?.result?.feedback_draft &&
                          !suggestions.some((s) => s.student_feedback))
                      }
                      onClick={() => {
                        feedbackTouched.current = true;
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
                      Составить заново{" "}
                    </Btn>
                  )
                }
              >
                {editable ? (
                  <textarea
                    aria-label="Обратная связь студенту"
                    disabled={!editable || action.busy}
                    rows={6}
                    value={feedback}
                    onChange={(e) => {
                      feedbackTouched.current = true;
                      setFeedback(e.target.value);
                      setDirty(true);
                    }}
                  />
                ) : (
                  <p className="preserve">{feedback || "Отзыв не добавлен."}</p>
                )}
              </Card>
            </>
          )}
        </div>
        <div className="stack">
          <Card
            title={
              editable
                ? suggestions.length
                  ? "Разбор по требованиям"
                  : "Оценка по критериям"
                : "Разбор по требованиям"
            }
            headClassName={editable ? "card__head--review" : undefined}
            bodyClassName="review-criteria"
            actions={
              <div className="review-ai-actions">
                {assist.data?.status === "failed" && (
                  <p role="alert">
                    Не удалось завершить проверку ИИ. Запустите её ещё раз.{" "}
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
                  <Btn
                    disabled={
                      action.busy ||
                      ["queued", "running"].includes(assist.data?.status ?? "")
                    }
                    onClick={() =>
                      void action.run(async () => {
                        if (
                          assist.data &&
                          ["failed", "unknown_outcome"].includes(
                            assist.data.status,
                          )
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
                    {assist.data ? "Повторить ИИ-ревью" : "Получить ИИ-ревью"}
                  </Btn>
                )}
              </div>
            }
          >
            <fieldset disabled={action.busy}>
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
                const SuggestionContainer = editable ? "div" : "details";
                return (
                  <details
                    className="acc"
                    key={c.id}
                    ref={(node) => {
                      if (node) criterionNodes.current.set(c.id, node);
                      else criterionNodes.current.delete(c.id);
                    }}
                    open={
                      i === 0 ||
                      suggestion?.status === "needs_human" ||
                      (!!suggestion &&
                        suggestion.proposed_points !== c.max_points)
                    }
                  >
                    <summary className="acc__h acc__h--score">
                      <span className={`ck ck--${marker}`} aria-hidden="true">
                        {marker === "y" ? "✓" : marker === "n" ? "✕" : "?"}
                      </span>
                      <span className="acc__t">{c.title}</span>
                      {editable ? (
                        <label className="scale score">
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
                      ) : (
                        <span className="mono">
                          {decisions[i]?.points.toLocaleString("ru-RU") ?? "—"}{" "}
                          из {c.max_points.toLocaleString("ru-RU")}
                        </span>
                      )}
                      <span className="acc__chev" aria-hidden="true">
                        ⌄
                      </span>
                    </summary>
                    <div className="acc__b">
                      <p className="criterion-description">{c.description}</p>
                      {!editable && (
                        <div>
                          <p className="label">Обоснование ревьюера</p>
                          <p className="preserve">
                            {decisions[i]?.reason ||
                              "Обоснование ещё не сохранено."}
                          </p>
                        </div>
                      )}
                      {suggestion && (
                        <SuggestionContainer
                          className={
                            editable ? undefined : "ai-suggestion-history"
                          }
                        >
                          {!editable && (
                            <summary>
                              Предложение ИИ до решения ревьюера
                            </summary>
                          )}
                          <article className="suggestion">
                            {(suggestion.sources ?? []).map((source, index) => (
                              <div className="finding" key={`source:${index}`}>
                                <div className="finding__src">
                                  <span className="f">
                                    {source.path ??
                                      source.locator ??
                                      "Снимок работы"}
                                  </span>
                                  {source.line_start && (
                                    <span>
                                      строки {source.line_start}
                                      {source.line_end
                                        ? `–${source.line_end}`
                                        : ""}
                                    </span>
                                  )}
                                </div>
                                <div className="code">
                                  {source.quote
                                    .split("\n")
                                    .map((line, number) => (
                                      <div className="cl" key={number}>
                                        {source.line_start && (
                                          <span className="n">
                                            {source.line_start + number}
                                          </span>
                                        )}
                                        <span>{line || " "}</span>
                                      </div>
                                    ))}
                                </div>
                              </div>
                            ))}
                            {(!suggestion.sources?.length
                              ? (suggestion.evidence ?? [])
                              : []
                            ).map((quote, index) => (
                              <div className="finding" key={index}>
                                <div className="code">
                                  <pre className="quote-text">{quote}</pre>
                                </div>
                              </div>
                            ))}
                            <div
                              className={
                                hasEvidence ? "finding__why" : "acc__empty"
                              }
                            >
                              {suggestion.reason}
                            </div>
                            {suggestion.reviewer_note && (
                              <p className="muted">
                                {suggestion.reviewer_note}
                              </p>
                            )}
                          </article>
                        </SuggestionContainer>
                      )}
                      {editable &&
                      (!suggestion ||
                        touchedCriteria.current.has(c.id) ||
                        decisions[i]?.decision === "manual") ? (
                        <label>
                          Обоснование
                          <textarea
                            value={decisions[i]?.reason ?? ""}
                            onChange={(e) =>
                              edit(i, { reason: e.target.value })
                            }
                          />
                        </label>
                      ) : editable && !suggestion ? (
                        <p className="preserve">{decisions[i]?.reason}</p>
                      ) : null}
                    </div>
                  </details>
                );
              })}
              {editable && ws && (
                <div className="extra-requirement-entry">
                  {editable && ws && !addingRequirement && (
                    <Btn
                      className="btn btn--s btn--quiet"
                      type="button"
                      onClick={() => setAddingRequirement(true)}
                    >
                      Добавить своё требование
                    </Btn>
                  )}
                  {editable && ws && addingRequirement && (
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
                            onChange={(e) =>
                              setExtraMax(e.target.valueAsNumber)
                            }
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
                        <Btn
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
                        </Btn>
                      </div>
                      {dirty && <small>Сначала сохраните черновик.</small>}
                    </div>
                  )}
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
      {!readOnly && (
        <Dock
          className="review-dock"
          actions={
            <>
              {editable && (
                <>
                  <Btn
                    disabled={action.busy || !canSave}
                    onClick={() => void action.run(save)}
                  >
                    Сохранить черновик
                  </Btn>
                  <Btn
                    className="btn btn--dark"
                    disabled={
                      action.busy || dirty || !detail.current_review_revision_id
                    }
                    onClick={() => {
                      setOutcome("needs_changes");
                      setReason("");
                    }}
                  >
                    Вернуть на доработку
                  </Btn>
                  <Btn
                    className="primary btn btn--pri"
                    disabled={
                      action.busy || dirty || !detail.current_review_revision_id
                    }
                    onClick={() => {
                      setOutcome("passed");
                      setReason("");
                    }}
                  >
                    Зачесть
                  </Btn>
                </>
              )}
              {ws && detail.status === "published" && (
                <Btn
                  disabled={action.busy}
                  onClick={() =>
                    void action.run(async () => {
                      const next = await nextPoolWork(
                        ws,
                        detail.review_iteration_id,
                        context?.submission_id,
                      );
                      if (!next) {
                        setPoolEmpty(true);
                        return;
                      }
                      const id = await openQueueWork(ws, next, session.user_id);
                      go(`/reviews/${id}`);
                    })
                  }
                >
                  Следующая работа
                </Btn>
              )}
            </>
          }
        >
          <section className="review-dock-summary">
            <h2>Результат</h2>
            <dl className="review-facts">
              <dt>По требованиям задания</dt>
              <dd>{total.toLocaleString("ru-RU")}</dd>
              <dt>Просрочка</dt>
              <dd>
                {displayPenalty
                  ? `−${displayPenalty.toLocaleString("ru-RU")}`
                  : "—"}
              </dd>
              <dt>Итог</dt>
              <dd>
                {displayFinal === null
                  ? "—"
                  : displayFinal.toLocaleString("ru-RU")}{" "}
                из {version?.max_score.toLocaleString("ru-RU") ?? "—"}
              </dd>
            </dl>
            {grade.data?.pass_score != null && (
              <small>
                Порог зачёта: {grade.data.pass_score.toLocaleString("ru-RU")} из{" "}
                {version?.max_score.toLocaleString("ru-RU")}.
              </small>
            )}
            {editable && !canSave && (
              <div className="review-save-requirements">
                <strong>Чтобы сохранить черновик:</strong>
                {!version && <p>Нужны критерии задания.</p>}
                <ul>
                  {version?.criteria.flatMap((criterion, index) => {
                    const decision = decisions[index];
                    const issues = [];
                    if (!decision?.reason.trim())
                      issues.push("добавьте обоснование");
                    if (
                      !Number.isFinite(decision?.points) ||
                      decision.points < 0 ||
                      decision.points > criterion.max_points
                    )
                      issues.push(
                        `укажите балл от 0 до ${criterion.max_points}`,
                      );
                    return issues.length
                      ? [
                          <li key={criterion.id}>
                            <Btn
                              variant="link"
                              onClick={() => {
                                const node = criterionNodes.current.get(
                                  criterion.id,
                                );
                                if (node) {
                                  node.open = true;
                                  node.scrollIntoView({ block: "center" });
                                  node
                                    .querySelector<HTMLElement>(
                                      "textarea,input",
                                    )
                                    ?.focus();
                                }
                              }}
                            >
                              {criterion.title}: {issues.join("; ")}
                            </Btn>
                          </li>,
                        ]
                      : [];
                  })}
                </ul>
              </div>
            )}
            {savedNotice && <small role="status">{savedNotice}</small>}
            {dirty && <small>Сохраните черновик перед публикацией.</small>}
            {detail.status === "published" &&
              context?.outcome?.decision === "needs_changes" &&
              context.outcome.revision_deadline && (
                <p>Исправления до {date(context.outcome.revision_deadline)}</p>
              )}
            {poolEmpty && <small>В пуле больше нет доступных работ.</small>}
          </section>
        </Dock>
      )}
      {correcting && (
        <Modal title="Исправить опубликованное ревью" close={closeCorrection}>
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
            <Btn
              type="submit"
              className="primary"
              disabled={action.busy || !correctionReason.trim()}
            >
              Открыть новую версию
            </Btn>
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
                ? grade.data.final_score.toLocaleString("ru-RU")
                : ws
                  ? "—"
                  : detail.current_review_revision?.total_score}
              .
            </p>
            <Btn
              type="submit"
              className="primary"
              disabled={
                action.busy ||
                (!!ws && (grade.loading || !!grade.error || !grade.data))
              }
            >
              Подтвердить публикацию
            </Btn>
          </form>
        </Modal>
      )}
    </>
  );
}
