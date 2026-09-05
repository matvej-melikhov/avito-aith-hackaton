import { useState } from "react";
import { WorkspaceClient, type W } from "../api/workspace";
import {
  Acc,
  Btn,
  BtnRow,
  Card,
  CardBody,
  CardHead,
  Crumbs,
  Empty,
  Kv,
  Main,
  Pill,
  Skel,
  St,
  Tab,
  Tabs,
  cx,
  dayDot,
  dayLong,
  dayNum,
  num,
  outOf,
  plural,
  workStatus,
} from "../ds";
import { ErrorBox, Resource, useResource } from "../ui";
import { SelfReviewResult } from "../workspace-ui";

const verdicts: Record<string, { label: string; tone: string }> = {
  passed: { label: "зачтена", tone: "ok" },
  failed: { label: "не зачтена", tone: "bad" },
  needs_changes: { label: "нужны правки", tone: "late" },
};

/** Блок попытки: невыполненные требования, «Другие», итог, вердикт, комментарий. */
export function PublishedStudentReview({
  value,
  hideFeedback = false,
}: {
  value: W<"StudentReviewView">;
  hideFeedback?: boolean;
}) {
  const maximum = value.criteria.reduce((sum, c) => sum + c.max_points, 0);
  const unmet = value.criteria.filter((c) => c.points < c.max_points);
  const met = value.criteria.filter((c) => c.points >= c.max_points);
  const otherPoints = met.reduce((sum, c) => sum + c.points, 0);
  const otherMax = met.reduce((sum, c) => sum + c.max_points, 0);
  const verdict = value.decision ? verdicts[value.decision] : undefined;
  return (
    <div className="attempt">
      {unmet.map((c, index) => (
        <div key={index} className="attempt__row">
          <Kv label={c.title} ink className={cx(c.reason && "kv--noline")}>
            <span className="neg">{outOf(c.points, c.max_points)}</span>
          </Kv>
          {c.reason && <div className="kv-note preserve">{c.reason}</div>}
        </div>
      ))}
      {met.length > 0 && (
        <Kv label="Другие" ink className="kv--noline">
          <span className="ink">{outOf(otherPoints, otherMax)}</span>
        </Kv>
      )}
      <Kv label="Итого" className="kv--result" ink>
        {outOf(value.score, maximum)}
      </Kv>
      {value.grade && value.grade.penalty > 0 && (
        <div className="kv-note">
          По требованиям {num(value.grade.raw_score)}, за просрочку −
          {num(value.grade.penalty)}.
        </div>
      )}
      {verdict && (
        <Kv label="Вердикт" className="kv--verdict" ink>
          <span className={verdict.tone}>{verdict.label}</span>
        </Kv>
      )}
      {!hideFeedback && (
        <>
          <div className="label attempt__label">Комментарий ревьюера</div>
          <div className="small dim preserve attempt__text">
            {value.feedback || "Ревьюер не оставил комментария."}
          </div>
          {value.decision_reason?.trim() &&
            value.decision_reason.trim() !== value.feedback.trim() && (
              <div className="small dim preserve attempt__text">
                {value.decision_reason}
              </div>
            )}
        </>
      )}
      {value.revision_deadline && (
        <div className="caption attempt__deadline">
          Доработать до {dayLong(value.revision_deadline)}
        </div>
      )}
    </div>
  );
}

export function WorkspaceSubmissionDetail({
  ws,
  id,
}: {
  ws: WorkspaceClient;
  id: string;
}) {
  const r = useResource(() => ws.submission(id), id, 5000);
  const [tab, setTab] = useState<"human" | "ai">("human");
  const context = useResource(
    () =>
      r.data ? ws.studentContext(r.data.publication_id) : Promise.resolve(null),
    r.data?.publication_id ?? "no-context",
  );
  const current = r.data?.reviews.find(
    (v) => v.id === r.data?.current_publication_id,
  );
  const lastAttempt = r.data?.attempts.at(-1);
  // Статус домашки живёт в списке домашек студента, у самой сдачи его нет.
  const works = useResource(
    () => ws.studentWorks({ limit: 100 }),
    "student-works-for-status",
  );
  const listed = works.data?.items.find((item) => item.submission_id === id);
  const status = current?.decision ?? listed?.status ?? null;
  const selfReviews = context.data?.self_reviews ?? [];
  const events = [
    ...(r.data?.attempts.map((a) => ({
      id: a.id,
      time: a.submitted_at,
      label: a.sequence > 1 ? "Исправленная версия сдана" : "Сдана",
      day: false,
    })) ?? []),
    ...(r.data?.reviews.map((review) => ({
      id: review.id,
      time: review.published_at,
      label: review.decision
        ? (workStatus(review.decision)?.label ?? "Результат опубликован")
        : "Результат опубликован",
      day: false,
    })) ?? []),
    ...(selfReviews.length
      ? [
          {
            id: "self-reviews",
            time: selfReviews.at(-1)!.created_at,
            label: `ИИ-ревью, ${selfReviews.length} ${plural(selfReviews.length, "запуск", "запуска", "запусков")}`,
            day: true,
          },
        ]
      : []),
  ].sort((a, b) => b.time.localeCompare(a.time));

  if (r.loading || r.error)
    return (
      <Main page data-screen="С5">
        {r.error ? (
          <ErrorBox error={r.error} retry={r.refresh} />
        ) : (
          <Skel lines={5} label="Загружаем домашку…" />
        )}
      </Main>
    );
  const data = r.data!;
  return (
    <Main page data-screen="С5">
      <div className="page-head page-head--top">
        <div>
          <Crumbs
            back="#/works"
            items={[{ href: "#/works", label: "Мои домашки" }]}
            current={data.title}
          />
          <h1 className="page-head__title">{data.title}</h1>
        </div>
        <BtnRow className="page-head__actions">
          {status && (
            <St status={status} attempt={lastAttempt?.sequence ?? 1} />
          )}
          <Btn size="s" href={`#/prepare/${data.publication_id}`}>
            Открыть страницу сдачи
          </Btn>
        </BtnRow>
      </div>
      <div className="row-side">
        <div className="stack">
          <Card>
            <Tabs className="tabs--card" label="Проверки">
              <Tab on={tab === "ai"} onClick={() => setTab("ai")}>
                ИИ-ревью
              </Tab>
              <Tab on={tab === "human"} onClick={() => setTab("human")}>
                Ревью
              </Tab>
            </Tabs>
            <CardBody>
              {tab === "ai" ? (
                <Resource value={context}>
                  {selfReviews.length ? (
                    selfReviews.map((value, index) => {
                      const last = index === selfReviews.length - 1;
                      return (
                        <Acc
                          key={value.id}
                          className="acc--pill"
                          defaultOpen={last}
                          head={
                            <>
                              <span className="acc__t">
                                Проверка от {dayNum(value.created_at)}
                              </span>
                              {last && <Pill>последняя</Pill>}
                            </>
                          }
                        >
                          <SelfReviewResult value={value} />
                        </Acc>
                      );
                    })
                  ) : (
                    <Empty title="ИИ-ревью не запускалось">
                      Проверить работу до сдачи можно на странице сдачи.
                    </Empty>
                  )}
                </Resource>
              ) : data.attempts.length ? (
                data.attempts.map((attempt, index) => {
                  const reviews = data.reviews.filter(
                    (value) => value.submission_version_id === attempt.id,
                  );
                  const latest = reviews.at(-1);
                  const last = index === data.attempts.length - 1;
                  return (
                    <Acc
                      key={attempt.id}
                      className="acc--pill"
                      defaultOpen={last}
                      head={
                        <>
                          <span className="acc__t">
                            Попытка {attempt.sequence},{" "}
                            {dayNum(attempt.submitted_at)}
                          </span>
                          {last && <Pill>последняя</Pill>}
                        </>
                      }
                    >
                      {latest ? (
                        <PublishedStudentReview value={latest} />
                      ) : (
                        <p className="small dim">
                          Работа отправлена на ревью. Результат появится после
                          публикации.
                        </p>
                      )}
                      {reviews.length > 1 && (
                        <Acc
                          className="acc--nested"
                          head={
                            <span className="acc__t small">
                              Предыдущие публикации этой попытки
                            </span>
                          }
                        >
                          {reviews.slice(0, -1).map((value) => (
                            <div key={value.id} className="attempt__old">
                              <div className="caption">
                                {dayNum(value.published_at)}
                              </div>
                              <PublishedStudentReview value={value} />
                            </div>
                          ))}
                        </Acc>
                      )}
                    </Acc>
                  );
                })
              ) : (
                <Empty title="Сдач пока нет">
                  Отправьте работу на странице сдачи, и здесь появится её
                  история.
                </Empty>
              )}
            </CardBody>
          </Card>
        </div>
        <div className="stack">
          <Card>
            <CardHead title="История" />
            <CardBody tight>
              {events.length ? (
                events.map((event) => (
                  <Kv key={event.id} label={event.label} ink>
                    {event.day ? dayDot(event.time) : dayNum(event.time)}
                  </Kv>
                ))
              ) : (
                <p className="small dim">Событий пока нет.</p>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </Main>
  );
}
