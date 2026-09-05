import { Tab, Tabs } from "../ds";
import { useState } from "react";
import type { Role } from "../api/client";
import { WorkspaceClient, type W } from "../api/workspace";
import { Card, Empty, Resource, Status, date, useResource } from "../ui";
import { ScreenTitle, SelfReviewResult } from "../workspace-ui";
import { ArtifactLink } from "./WorkspaceReview";

export function newestSelfReviews(values: readonly W<"SelfReviewView">[]) {
  return [...values].sort(
    (a, b) =>
      Date.parse(b.created_at) - Date.parse(a.created_at) ||
      b.id.localeCompare(a.id),
  );
}
const verdicts: Record<string, string> = {
  passed: "зачтена",
  failed: "не зачтена",
  needs_changes: "нужны правки",
  published: "результат опубликован",
};
export function SubmittedStudentWork({
  ws,
  attempt,
}: {
  ws: WorkspaceClient;
  attempt: W<"SubmissionAttemptView">;
}) {
  return (
    <div className="submitted-attempt">
      <div className="rubric-row">
        <strong>Отправленная работа</strong>
        {attempt.artifact_id ? (
          <ArtifactLink ws={ws} id={attempt.artifact_id} />
        ) : (
          <span className="muted">Снимок недоступен</span>
        )}
      </div>
      {attempt.comment && (
        <>
          <p className="label">Комментарий к сдаче</p>
          <p className="preserve small">{attempt.comment}</p>
        </>
      )}
    </div>
  );
}
export function PublishedStudentReview({
  value,
  hideFeedback = false,
}: {
  value: W<"StudentReviewView">;
  hideFeedback?: boolean;
}) {
  const maximum = value.criteria.reduce((sum, c) => sum + c.max_points, 0);
  const completed = value.criteria.filter((c) => c.points === c.max_points);
  const otherPoints = completed.reduce((sum, c) => sum + c.points, 0);
  return (
    <article>
      {value.criteria
        .filter((c) => c.points < c.max_points)
        .map((c, index) => (
          <details className="acc" key={index}>
            <summary className="rubric-row">
              <span>{c.title}</span>
              <span className="points" style={{ color: "var(--bad)" }}>
                {c.points.toLocaleString("ru-RU")} из{" "}
                {c.max_points.toLocaleString("ru-RU")}
              </span>
            </summary>
            {c.description && (
              <p className="preserve small">
                <span className="label">Выполнено, если</span>
                <br />
                {c.description}
              </p>
            )}
            {c.reason && <p className="preserve small">{c.reason}</p>}
          </details>
        ))}
      {completed.length > 0 && (
        <details className="acc">
          <summary className="rubric-row">
            <span>Другие</span>
            <span className="points">
              {otherPoints} из {otherPoints}
            </span>
          </summary>
          {completed.map((c, index) => (
            <div key={index}>
              <div className="rubric-row">
                <span>{c.title}</span>
                <span className="points">
                  {c.points.toLocaleString("ru-RU")} из{" "}
                  {c.max_points.toLocaleString("ru-RU")}
                </span>
              </div>
              {c.description && (
                <p className="preserve small">
                  <span className="label">Выполнено, если</span>
                  <br />
                  {c.description}
                </p>
              )}
              {c.reason && <p className="preserve small">{c.reason}</p>}
            </div>
          ))}
        </details>
      )}
      <div className="rubric-row">
        <strong>Итого</strong>
        <strong>
          {value.score} из {maximum}
        </strong>
      </div>
      {value.grade && value.grade.penalty > 0 && (
        <p className="caption">
          По критериям: {value.grade.raw_score}. За просрочку: −
          {value.grade.penalty}.
        </p>
      )}
      {value.decision && (
        <div className="rubric-row">
          <span>Вердикт</span>
          <span
            style={{
              color:
                value.decision === "passed"
                  ? "var(--ok)"
                  : value.decision === "failed"
                    ? "var(--bad)"
                    : "var(--late)",
            }}
          >
            {verdicts[value.decision] ?? value.decision}
          </span>
        </div>
      )}
      {!hideFeedback && (
        <>
          <p
            className="label"
            style={{ marginTop: "var(--s-5)", marginBottom: "var(--s-2)" }}
          >
            Комментарий ревьюера
          </p>
          <p className="preserve small">{value.feedback}</p>
          {value.decision_reason?.trim() &&
            value.decision_reason.trim() !== value.feedback.trim() && (
              <p className="preserve small">{value.decision_reason}</p>
            )}
        </>
      )}
      {value.revision_deadline && (
        <p className="small">Доработать до {date(value.revision_deadline)}</p>
      )}
    </article>
  );
}
export function WorkspaceSubmissionDetail({
  ws,
  id,
  role = "student",
}: {
  ws: WorkspaceClient;
  id: string;
  role?: Role;
}) {
  const r = useResource(() => ws.submission(id), id, 5000);
  const [tab, setTab] = useState<"human" | "ai">("human");
  const context = useResource(
    () =>
      r.data && role === "student"
        ? ws.studentContext(r.data.publication_id)
        : Promise.resolve(null),
    `${role}:${r.data?.publication_id ?? "no-context"}`,
  );
  const selfReviews = newestSelfReviews(context.data?.self_reviews ?? []);
  const current = r.data?.reviews.find(
    (v) => v.id === r.data?.current_publication_id,
  );
  const latestAttempt = r.data?.attempts.at(-1);
  const currentStatus =
    current?.submission_version_id === latestAttempt?.id
      ? (current?.decision ?? "published")
      : "pending_review";
  const events = [
    ...(r.data?.attempts.map((a) => ({
      id: a.id,
      time: a.submitted_at,
      label: a.sequence > 1 ? "Исправленная версия сдана" : "Сдана",
    })) ?? []),
    ...(r.data?.reviews.map((review) => ({
      id: review.id,
      time: review.published_at,
      label: review.decision
        ? (verdicts[review.decision] ?? "Результат опубликован")
        : "Результат опубликован",
    })) ?? []),
    ...(context.data?.self_reviews.map((review) => ({
      id: review.id,
      time: review.created_at,
      label: "ИИ-ревью",
    })) ?? []),
  ].sort((a, b) => b.time.localeCompare(a.time));
  return (
    <Resource value={r}>
      {r.data && (
        <>
          <div className="crumbs">
            <a className="crumbs__back" href="#/works" aria-label="Назад">
              ←
            </a>
            <a href="#/works">
              {role === "student" ? "Мои домашки" : "Домашки"}
            </a>
            <span>/</span>
            <span className="cur">{r.data.title}</span>
          </div>
          <ScreenTitle code="С5" title={r.data.title}>
            <div className="actions">
              <Status value={currentStatus} attempt={latestAttempt?.sequence} />
            </div>
          </ScreenTitle>
          <div className="student-grid">
            <section className="card">
              <Tabs
                style={{ padding: "var(--s-2) var(--s-5) 0", marginBottom: 0 }}
              >
                {role === "student" && (
                  <Tab on={tab === "ai"} onClick={() => setTab("ai")}>
                    ИИ-ревью
                  </Tab>
                )}
                <Tab on={tab === "human"} onClick={() => setTab("human")}>
                  Ревью
                </Tab>
              </Tabs>
              <div className="card-body">
                {tab === "ai" ? (
                  <Resource value={context}>
                    {selfReviews.length ? (
                      selfReviews.map((value, index) => (
                        <details
                          className="acc"
                          key={value.id}
                          open={index === 0}
                        >
                          <summary className="acc__h">
                            Проверка от {date(value.created_at)}
                          </summary>
                          <SelfReviewResult showHeading={false} value={value} />
                        </details>
                      ))
                    ) : (
                      <Empty>Самопроверка не запускалась.</Empty>
                    )}
                  </Resource>
                ) : r.data.attempts.length ? (
                  r.data.attempts.map((attempt, index) => {
                    const reviews = r.data!.reviews.filter(
                      (value) => value.submission_version_id === attempt.id,
                    );
                    const latest = reviews.at(-1);
                    return (
                      <details
                        className="acc"
                        key={attempt.id}
                        open={index === r.data!.attempts.length - 1}
                      >
                        <summary className="acc__h">
                          <span>
                            Попытка {attempt.sequence},{" "}
                            {date(attempt.submitted_at)}
                          </span>
                          {index === r.data!.attempts.length - 1 && (
                            <span className="pill">последняя</span>
                          )}
                        </summary>
                        <div style={{ paddingBottom: "var(--s-4)" }}>
                          <SubmittedStudentWork ws={ws} attempt={attempt} />
                          {latest ? (
                            <PublishedStudentReview value={latest} />
                          ) : (
                            <Empty>
                              Работа отправлена на ревью. Результат появится
                              после публикации.
                            </Empty>
                          )}
                          {reviews.length > 1 && (
                            <details className="student-history">
                              <summary>
                                Предыдущие публикации этой попытки
                              </summary>
                              {reviews.slice(0, -1).map((value) => (
                                <div key={value.id}>
                                  <p className="caption">
                                    {date(value.published_at)}
                                  </p>
                                  <PublishedStudentReview value={value} />
                                </div>
                              ))}
                            </details>
                          )}
                        </div>
                      </details>
                    );
                  })
                ) : (
                  <Empty>Сдач пока нет.</Empty>
                )}
              </div>
            </section>
            <aside>
              <Card title="История" bodyClassName="card__body--tight">
                {events.length ? (
                  events.map((event) => (
                    <div className="rubric-row" key={event.id}>
                      <span>{event.label}</span>
                      <span className="caption">{date(event.time)}</span>
                    </div>
                  ))
                ) : (
                  <Empty>Событий пока нет.</Empty>
                )}
              </Card>
            </aside>
          </div>
        </>
      )}
    </Resource>
  );
}
