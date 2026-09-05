import { useState } from "react";
import type { ApiClient, Model } from "../api/client";
import {
  Card,
  Empty,
  ErrorBox,
  Id,
  Resource,
  Status,
  date,
  safeUrl,
  useAction,
  useResource,
} from "../ui";
import { WorkspaceClient } from "../api/workspace";
import { ReviewWorkspacePanels } from "./WorkspaceReview";
import { useDirtyGuard } from "../workspace-ui";
import { OperationPanel, DeliveryCards } from "./Operations";
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
  const resource = useResource(async () => {
    if (!ws) return loadReview(api, id);
    const [detail, context] = await Promise.all([
      api.review(id),
      ws.reviewContext(id),
    ]);
    return {
      detail,
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
      <div data-screen={readOnly ? "К8" : "Р5"}>
        {readOnly && !editing && (
          <button onClick={() => setEditing(true)}>
            Перейти к редактированию
          </button>
        )}
      </div>
      <Resource value={resource}>
        {resource.data && (
          <ReviewEditor
            key={`${id}:${resource.data.detail.revision}`}
            api={api}
            {...resource.data}
            session={session}
            readOnly={!editing}
            refresh={resource.refresh}
          />
        )}
      </Resource>
      {ws && resource.data && (
        <ReviewWorkspacePanels
          ws={ws}
          detail={resource.data.detail}
          readOnly={!editing}
          refresh={resource.refresh}
        />
      )}
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
}) {
  const action = useAction();
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
  const [notes, setNotes] = useState(
    detail.review_notes.map((n) => ({
      criterion_id: n.criterion_id ?? null,
      text: n.text,
    })),
  );
  const [dirty, setDirty] = useState(false);
  useDirtyGuard(dirty);
  const [confirm, setConfirm] = useState(false);
  const [operation, setOperation] = useState<string>();
  const editable =
    !readOnly &&
    session.actor_type === "user" &&
    session.roles.some((r) => r === "reviewer" || r === "methodologist") &&
    !["published", "canceled"].includes(detail.status);
  const ai = useResource(
    () => api.review(detail.review_iteration_id),
    detail.review_iteration_id,
    5000,
    (next) =>
      ["pending", "running", "partial"].includes(next.ai_review?.state ?? ""),
  );
  const suggestion = ai.data?.ai_review ?? detail.ai_review;
  const total = decisions.reduce((sum, d) => sum + d.points, 0);
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
    ) &&
    notes.every((n) => n.text.trim());
  function editDecision(
    index: number,
    patch: Partial<Model<"ReviewCriterionDecision">>,
  ) {
    setDirty(true);
    setConfirm(false);
    setDecisions((all) =>
      all.map((d, i) => (i === index ? { ...d, ...patch } : d)),
    );
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Проверка работы</p>
          <h1>
            Ревью <Id value={detail.review_iteration_id} />
          </h1>
        </div>
        <Status value={detail.status} />
      </div>
      {action.feedback}
      <div className="review-grid">
        <div className="stack">
          <Card title="Сдача и условия">
            <p>
              {version?.student_text ??
                "Условия этой версии задания недоступны. Сохранение оценки отключено."}
            </p>
            <dl>
              <dt>Версия задания</dt>
              <dd>
                <Id value={detail.immutable_inputs.homework_version_id} />
              </dd>
              <dt>Срок сдачи</dt>
              <dd>{date(detail.immutable_inputs.effective_deadline)}</dd>
            </dl>
            <a
              className="button"
              href={safeUrl(detail.immutable_inputs.artifact_download_url)}
              target="_blank"
              rel="noreferrer"
            >
              Открыть снимок работы ↗
            </a>
            <p className="muted">
              Ссылка действует до{" "}
              {date(detail.immutable_inputs.artifact_download_expires_at)}. Для
              новой ссылки обновите страницу.
            </p>
          </Card>
          <Card
            title="AI-проверка"
            actions={
              suggestion ? <Status value={suggestion.state} /> : undefined
            }
          >
            {!!ai.error && <ErrorBox error={ai.error} retry={ai.refresh} />}
            {!suggestion && (
              <p className="muted">Проверка ещё не запускалась.</p>
            )}
            {suggestion?.error && (
              <p className="notice warn">
                {suggestion.error.message} {suggestion.error.action}
              </p>
            )}
            {suggestion?.suggestions.map((s) => (
              <article className="suggestion" key={s.id}>
                <strong>
                  {version?.criteria.find((c) => c.id === s.criterion_id)
                    ?.title ?? "Критерий"}{" "}
                  · {s.proposed_points ?? "—"}
                </strong>
                <p>{s.reason}</p>
                {s.evidence.map((e, i) => (
                  <blockquote key={i}>
                    {e.quote}
                    <small>
                      {e.locator} ·{" "}
                      {e.verified ? "Цитата проверена" : "Цитата не проверена"}
                    </small>
                  </blockquote>
                ))}
                <p className="muted">
                  Уверенность: {s.confidence}. {s.reviewer_note}
                </p>
              </article>
            ))}
            {suggestion?.signal && (
              <div className="notice">
                <strong>
                  Сигнал об использовании ИИ: {suggestion.signal.level}
                </strong>
                <p>{suggestion.signal.limitations.join(" ")}</p>
                <p>Сам по себе этот сигнал не меняет оценку.</p>
              </div>
            )}
            {editable && (
              <button
                disabled={
                  action.busy ||
                  ["pending", "running"].includes(suggestion?.state ?? "")
                }
                onClick={() =>
                  void action.run(async () => {
                    const result = await api.command(
                      "start_ai_review",
                      detail.review_iteration_id,
                      detail.revision,
                      {},
                    );
                    setOperation(result.id);
                    ai.refresh();
                  })
                }
              >
                Запустить AI-проверку
              </button>
            )}
          </Card>
          <Card title="История ответственности">
            {detail.responsibility_events.length === 0 ? (
              <Empty>Работу пока никто не взял.</Empty>
            ) : (
              detail.responsibility_events.map((e) => (
                <p key={e.id}>
                  <Id value={e.reviewer_id} /> ·{" "}
                  {
                    {
                      started: "Взял в работу",
                      joined: "Присоединился",
                      released: "Вернул в пул",
                      completed: "Завершил",
                    }[e.action]
                  }
                  <small>{date(e.occurred_at)}</small>
                </p>
              ))
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
                        { action: "started" },
                      );
                      refresh();
                    })
                  }
                >
                  Взять в работу
                </button>
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
              </div>
            )}
          </Card>
        </div>
        <div className="stack">
          <Card
            title="Оценка по критериям"
            actions={
              <strong>
                {total} / {version?.max_score ?? "—"}
              </strong>
            }
          >
            <fieldset disabled={!editable || action.busy}>
              {version?.criteria.map((c, i) => (
                <div className="criterion" key={c.id}>
                  <div className="row">
                    <div>
                      <strong>
                        {i + 1}. {c.title}
                      </strong>
                      <p className="muted">{c.description}</p>
                    </div>
                    <label className="score">
                      Баллы
                      <input
                        aria-label={`Баллы: ${c.title}`}
                        type="number"
                        min="0"
                        max={c.max_points}
                        step="any"
                        value={decisions[i]?.points ?? 0}
                        onChange={(e) =>
                          editDecision(i, {
                            points: e.target.valueAsNumber,
                            decision: "manual",
                          })
                        }
                      />
                      <span>из {c.max_points}</span>
                    </label>
                  </div>
                  <label>
                    Обоснование
                    <textarea
                      value={decisions[i]?.reason ?? ""}
                      onChange={(e) =>
                        editDecision(i, { reason: e.target.value })
                      }
                    />
                  </label>
                </div>
              ))}
              <label>
                Обратная связь студенту
                <textarea
                  rows={6}
                  value={feedback}
                  onChange={(e) => {
                    setFeedback(e.target.value);
                    setDirty(true);
                    setConfirm(false);
                  }}
                />
              </label>
              {notes.map((note, i) => (
                <label key={i}>
                  Замечание {i + 1}
                  <textarea
                    value={note.text}
                    onChange={(e) => {
                      setNotes((all) =>
                        all.map((n, j) =>
                          i === j ? { ...n, text: e.target.value } : n,
                        ),
                      );
                      setDirty(true);
                      setConfirm(false);
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setNotes((all) => all.filter((_, j) => i !== j));
                      setDirty(true);
                      setConfirm(false);
                    }}
                  >
                    Убрать замечание
                  </button>
                </label>
              ))}
              <button
                onClick={() => {
                  setNotes((all) => [...all, { criterion_id: null, text: "" }]);
                  setDirty(true);
                  setConfirm(false);
                }}
              >
                Добавить замечание
              </button>
            </fieldset>
            {editable && (
              <div className="review-footer">
                <p className="muted">
                  {dirty
                    ? "Есть несохранённые изменения"
                    : "Изменения сохранены"}{" "}
                  · Публикацию подтверждает человек.
                </p>
                <div className="actions">
                  <button
                    disabled={action.busy || !canSave}
                    onClick={() =>
                      void action.run(async () => {
                        await api.command(
                          "save_review_revision",
                          detail.review_iteration_id,
                          detail.revision,
                          {
                            feedback,
                            criterion_decisions: decisions,
                            review_notes: notes,
                          },
                        );
                        refresh();
                      }, "Оценка сохранена.")
                    }
                  >
                    Сохранить черновик
                  </button>
                  <button
                    className="primary"
                    disabled={
                      action.busy || dirty || !detail.current_review_revision_id
                    }
                    onClick={() => setConfirm(true)}
                  >
                    Опубликовать ревью
                  </button>
                </div>
              </div>
            )}
            {confirm && (
              <div
                className="notice"
                role="group"
                aria-label="Подтверждение публикации"
              >
                <strong>
                  Опубликовать оценку{" "}
                  {detail.current_review_revision?.total_score}?
                </strong>
                <p>
                  Студент получит сохранённую обратную связь. Внешние доставки
                  выполняются отдельно.
                </p>
                <div className="actions">
                  <button
                    disabled={action.busy}
                    onClick={() => setConfirm(false)}
                  >
                    Отмена
                  </button>
                  <button
                    className="primary"
                    disabled={action.busy}
                    onClick={() =>
                      void action.run(async () => {
                        await api.command(
                          "publish_review",
                          detail.review_iteration_id,
                          detail.revision,
                          {
                            review_revision_id:
                              detail.current_review_revision_id!,
                          },
                        );
                        refresh();
                      })
                    }
                  >
                    Подтвердить публикацию
                  </button>
                </div>
              </div>
            )}
          </Card>
          <DeliveryCards items={detail.deliveries} />
          {operation && <OperationPanel api={api} id={operation} />}
        </div>
      </div>
    </>
  );
}
