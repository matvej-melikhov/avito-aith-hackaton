import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient, Model } from "../api/client";
import { ErrorBox, go, safeUrl, useAction, useResource } from "../ui";
import { WorkspaceClient, type W } from "../api/workspace";
import { Modal, useDirtyGuard } from "../workspace-ui";
import { exitReviewMode, isReviewMode, nextFromPool } from "../reviewMode";
import {
  Acc,
  Area,
  Btn,
  BtnRow,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Chk,
  Ck,
  Code,
  Crumbs,
  Dock,
  Field,
  Finding,
  Inp,
  Kv,
  Main,
  Meter,
  OpPill,
  Pill,
  Quote,
  Scale,
  Skel,
  St,
  Sum,
  Tab,
  Tabs,
  Topbar,
  cx,
  dayLong,
  num,
  outOf,
  plural,
  short,
} from "../ds";

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
  if (resource.loading || resource.error)
    return (
      <>
        <Topbar
          crumbs={
            <Crumbs
              back={readOnly ? "#/registry" : "#/works"}
              items={[
                readOnly
                  ? { href: "#/registry", label: "Домашки" }
                  : { href: "#/works", label: "Мои работы" },
              ]}
              current="Работа"
            />
          }
          title="Проверка работы"
        />
        <Main>
          {resource.error ? (
            <ErrorBox error={resource.error} retry={resource.refresh} />
          ) : (
            <Skel lines={6} label="Загружаем работу…" />
          )}
        </Main>
      </>
    );
  return (
    <>
      {resource.data && (
        <ReviewEditor
          key={`${id}:${resource.data.detail.revision}`}
          api={api}
          {...resource.data}
          session={session}
          readOnly={readOnly}
          ws={ws}
          refresh={resource.refresh}
        />
      )}
    </>
  );
}

const ACTIVE_ACTIONS = ["joined", "started"];
const AUTOSAVE_DELAY = 1200;
const ASSIST_START_DELAY = 400;

function step(c: object) {
  const value =
    "score_step" in c ? (c as { score_step?: unknown }).score_step : undefined;
  return typeof value === "number" && value > 0 ? value : 0.5;
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
}) {
  const action = useAction();
  /* Живая копия ревью: после автосохранения обновляется на месте, без
     перемонтирования редактора и потери фокуса. */
  const [live, setLive] = useState(detail);
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
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const touched = useRef(new Set<string>());
  useDirtyGuard(dirty);
  const [sourceRun, setSourceRun] = useState<string | null>(
    context?.ai_run_id ?? null,
  );
  const [signalDecisions] = useState<Record<string, "confirm" | "reject">>(
    context?.signal_decisions ?? {},
  );
  const [outcome, setOutcome] = useState<"passed" | "needs_changes" | null>(
    null,
  );
  const close = useCallback(() => setOutcome(null), []);
  const [deadline, setDeadline] = useState("");
  const [reason, setReason] = useState("");
  const [penalty, setPenalty] = useState(true);
  const [showFeedback, setShowFeedback] = useState(false);
  const [mode, setMode] = useState(isReviewMode);
  const feedbackTouched = useRef(false);
  const editable =
    !readOnly &&
    session.actor_type === "user" &&
    session.roles.some((r) => r === "reviewer" || r === "methodologist") &&
    !["published", "canceled"].includes(live.status);
  const assist = useResource(
    () =>
      ws ? ws.reviewAssist(live.review_iteration_id) : Promise.resolve(null),
    live.review_iteration_id,
    3000,
    (next) =>
      !!next && ["queued", "running", "unknown_outcome"].includes(next.status),
  );
  const grade = useResource(
    () =>
      ws
        ? ws.gradePreview(live.review_iteration_id)
        : Promise.resolve(undefined),
    `${live.review_iteration_id}:${live.revision}`,
  );
  const suggestions = assist.data?.result?.suggestions ?? [];
  const signal = assist.data?.result?.authorship_signal;
  const assistRunning =
    !!assist.data && ["queued", "running"].includes(assist.data.status);
  const assistFailed =
    !!assist.data &&
    ["failed", "unknown_outcome", "stale"].includes(assist.data.status);

  /* Проверка запускается сама, как только работа открыта: ревьюеру не нужно
     нажимать кнопку, чтобы получить разбор. */
  const started = useRef(false);
  useEffect(() => {
    if (!ws || !editable || assist.loading || assist.error) return;
    if (assist.data !== null || started.current) return;
    started.current = true;
    const timer = window.setTimeout(() => {
      void ws
        .command(
          "start_review_assist",
          live.review_iteration_id,
          live.revision,
          {},
        )
        .then(() => assist.refresh())
        .catch(() => {
          started.current = false;
        });
    }, ASSIST_START_DELAY);
    return () => window.clearTimeout(timer);
  }, [ws, editable, assist.data, assist.loading, assist.error]);

  /* Оценки модели проставляются сразу: ревьюер меняет там, где не согласен.
     Требования, которые он уже трогал, не перезаписываются. */
  const applied = useRef<string | null>(null);
  useEffect(() => {
    if (!editable || !assist.data || applied.current === assist.data.id) return;
    if (!suggestions.length) return;
    applied.current = assist.data.id;
    setSourceRun(assist.data.id);
    let changed = false;
    const next = decisions.map((d) => {
      const s = suggestions.find((x) => x.criterion_id === d.criterion_id);
      if (
        !s ||
        s.proposed_points === null ||
        touched.current.has(d.criterion_id) ||
        d.reason.trim() ||
        d.points !== 0
      )
        return d;
      changed = true;
      return {
        ...d,
        points: s.proposed_points,
        reason: s.reason,
        decision: "accepted" as const,
      };
    });
    const draftText =
      assist.data.result?.feedback_draft ??
      suggestions
        .map((x) => x.student_feedback)
        .filter(Boolean)
        .join("\n\n");
    if (!feedbackTouched.current && !feedback.trim() && draftText) {
      setFeedback(draftText);
      changed = true;
    }
    if (changed) {
      setDecisions(next);
      setDirty(true);
    }
  }, [editable, assist.data, suggestions]);

  const total = decisions.reduce(
    (sum, d) => sum + (Number.isFinite(d.points) ? d.points : 0),
    0,
  );
  const maxScore = version?.max_score ?? 0;
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
      all.map((d, i) => {
        if (i !== index) return d;
        touched.current.add(d.criterion_id);
        return { ...d, ...patch };
      }),
    );
  }
  async function save() {
    const draft = {
      feedback,
      criterion_decisions: decisions,
      review_notes: notes,
    };
    if (ws) {
      await ws.command(
        "save_workspace_review",
        live.review_iteration_id,
        live.revision,
        { draft, ai_run_id: sourceRun, signal_decisions: signalDecisions },
      );
      const fresh = await ws.reviewDetail(live.review_iteration_id);
      setLive(fresh);
    } else {
      await api.command(
        "save_review_revision",
        live.review_iteration_id,
        live.revision,
        draft,
      );
      refresh();
    }
    setDirty(false);
    setSavedAt(new Date());
  }
  /* Автосохранение: через секунду после последней правки, когда все баллы и
     обоснования на месте. */
  useEffect(() => {
    if (!editable || !dirty || !canSave || action.busy) return;
    const timer = window.setTimeout(() => {
      void action.run(save);
    }, AUTOSAVE_DELAY);
    return () => window.clearTimeout(timer);
  }, [
    editable,
    dirty,
    canSave,
    action.busy,
    decisions,
    feedback,
    signalDecisions,
  ]);

  async function publish(revisionDeadline = deadline) {
    if (!outcome || !live.current_review_revision_id) return;
    if (ws) {
      const latest = await ws.reviewContext(live.review_iteration_id);
      await ws.command(
        "save_review_outcome",
        live.review_iteration_id,
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
      const saved = await ws.reviewDetail(live.review_iteration_id);
      if (saved.current_review_revision_id !== live.current_review_revision_id)
        throw new Error(
          "Коллега изменил черновик. Обновите проверку и проверьте сохранённую оценку перед публикацией.",
        );
      await ws.command(
        "publish_workspace_review",
        live.review_iteration_id,
        saved.revision,
        {
          review_revision_id: live.current_review_revision_id,
          apply_penalty: penalty,
        },
      );
    } else
      await api.command(
        "publish_review",
        live.review_iteration_id,
        live.revision,
        { review_revision_id: live.current_review_revision_id },
      );
    close();
    if (ws && mode) {
      const nextId = await nextFromPool(ws, [context?.submission_id ?? ""]);
      if (nextId) {
        go(`/reviews/${nextId}`);
        return;
      }
      exitReviewMode();
      setMode(false);
      go("/works");
      return;
    }
    refresh();
  }
  async function skipToNext() {
    if (!ws) return;
    await api.command(
      "record_review_responsibility",
      live.review_iteration_id,
      live.revision,
      { action: "released" },
    );
    const nextId = await nextFromPool(ws, [context?.submission_id ?? ""]);
    if (nextId) go(`/reviews/${nextId}`);
    else {
      exitReviewMode();
      setMode(false);
      go("/works");
    }
  }

  const activePeople = new Map<string, string>();
  live.responsibility_events.forEach((e) =>
    activePeople.set(e.reviewer_id, e.action),
  );
  const participants = [...activePeople.values()].filter((v) =>
    ACTIVE_ACTIONS.includes(v),
  ).length;
  const reviewerId =
    [...activePeople.entries()].find(([, v]) =>
      ACTIVE_ACTIONS.includes(v),
    )?.[0] ??
    live.current_review_revision?.author_user_id ??
    null;
  const attempts = history.data?.attempts ?? [];
  const repeat = (context?.attempt ?? 1) > 1;
  const statusValue =
    live.status === "published"
      ? (context?.outcome?.decision ?? live.status)
      : live.status;
  const studentName = context?.student_name ?? "";
  const studentLabel = /^студент/i.test(studentName)
    ? studentName
    : `Студент ${studentName}`;
  const artifactHref = safeUrl(live.immutable_inputs.artifact_download_url);
  const deadlineAt = live.immutable_inputs.effective_deadline;
  const late =
    !!context?.submitted_at &&
    !!deadlineAt &&
    Date.parse(context.submitted_at) > Date.parse(deadlineAt);
  /* Итог считается здесь же из суммы по требованиям и штрафа: серверное превью
     отстаёт от правок на экране. */
  const finalScore = Math.max(0, total - (grade.data?.penalty ?? 0));
  const passScore = grade.data?.pass_score ?? null;
  const below = passScore != null && finalScore < passScore;
  const selfRuns = context?.self_reviews.length ?? 0;
  const signalPct =
    signal?.probability == null ? null : Math.round(signal.probability * 100);
  /* Сервис проверки не даёт процент: уровень (низкий / средний / высокий)
     приходит первой строкой объяснения. */
  const signalLevel =
    signal?.explanation?.match(/Уровень:\s*([^.\n]+)/)?.[1]?.trim() ?? null;
  const signalDecision = signal ? signalDecisions[signal.id] : undefined;
  const backHref = readOnly ? "#/registry" : "#/works";
  const screen = readOnly ? "К9" : repeat ? "Р6" : "Р5";
  const saveNote = !editable
    ? null
    : action.busy
      ? "Сохраняем…"
      : dirty
        ? canSave
          ? "Сохраним через секунду"
          : "Заполните баллы и обоснование по каждому требованию"
        : savedAt
          ? `Черновик сохранён ${savedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
          : null;

  return (
    <>
      <Topbar
        crumbs={
          <Crumbs
            back={backHref}
            items={
              readOnly
                ? [
                    { href: "#/registry", label: "Домашки" },
                    { label: context?.title ?? "Задание" },
                  ]
                : [{ href: "#/works", label: "Мои работы" }]
            }
            current={studentLabel}
          />
        }
        title={
          readOnly
            ? `Ревью работы ${studentName || ""}`.trim()
            : context?.title || "Проверка работы"
        }
        status={
          <>
            <St status={statusValue} attempt={context?.attempt} />
            {readOnly && <Pill>только просмотр</Pill>}
            {editable && mode && (
              <Pill tone="info" className="mode-pill">
                Режим проверки
                <button
                  type="button"
                  className="mode-pill__x"
                  aria-label="Выйти из режима проверки"
                  onClick={() => {
                    exitReviewMode();
                    setMode(false);
                  }}
                >
                  ✕
                </button>
              </Pill>
            )}
          </>
        }
        actions={
          editable
            ? !repeat && (
                <Btn
                  size="s"
                  variant="quiet"
                  disabled={action.busy || dirty}
                  onClick={() =>
                    void action.run(async () => {
                      await api.command(
                        "record_review_responsibility",
                        live.review_iteration_id,
                        live.revision,
                        { action: "released" },
                      );
                      refresh();
                    })
                  }
                >
                  Вернуть в пул
                </Btn>
              )
            : artifactHref && (
                <Btn
                  size="s"
                  variant="quiet"
                  href={artifactHref}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть работу ↗
                </Btn>
              )
        }
      />
      <Main data-screen={screen}>
        {!outcome && action.feedback}
        {attempts.length > 1 && (
          <Tabs className="tabs--attempts" label="Попытки сдачи">
            {[...attempts]
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
                  attempt.id === live.immutable_inputs.submission_version_id;
                const latest =
                  attempt.sequence ===
                  Math.max(...attempts.map((a) => a.sequence));
                const reviewId = selected
                  ? live.review_iteration_id
                  : (published?.iteration_id ??
                    (latest ? context?.latest_review_iteration_id : undefined));
                return reviewId ? (
                  <Tab
                    key={attempt.id}
                    on={selected}
                    href={`#/reviews/${reviewId}`}
                  >
                    Попытка {attempt.sequence}
                  </Tab>
                ) : (
                  <Tab key={attempt.id} disabled>
                    Попытка {attempt.sequence}
                  </Tab>
                );
              })}
          </Tabs>
        )}
        <div className="row-rev">
          <div className="stack">
            <Card>
              <CardHead title="Работа" />
              <CardBody tight>
                <Kv
                  label={
                    <span className="mono">
                      {context?.artifact_label ?? "Снимок работы"}
                    </span>
                  }
                  ink
                >
                  {artifactHref && (
                    <Btn
                      size="s"
                      variant="quiet"
                      href={artifactHref}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Открыть ↗
                    </Btn>
                  )}
                </Kv>
                <Kv label="Попытка">{context?.attempt ?? "—"}</Kv>
                {readOnly && reviewerId && (
                  <Kv label="Ревьюер">
                    <span className="mono">rev-{short(reviewerId)}</span>
                  </Kv>
                )}
                <Kv label={repeat ? "Правки пришли" : "Сдана"}>
                  {dayLong(context?.submitted_at)}
                </Kv>
                {!repeat && (
                  <Kv label="Срок сдачи был">
                    <span className={cx(late && "late")}>
                      {dayLong(deadlineAt)}
                    </span>
                  </Kv>
                )}
                <Kv label="ИИ-ревью до сдачи">
                  {selfRuns} {plural(selfRuns, "запуск", "запуска", "запусков")}
                </Kv>
                {readOnly && (
                  <>
                    <Kv label="Признаки ИИ">
                      {signalPct == null ? "нет данных" : `${signalPct}%`}
                    </Kv>
                    <Kv label="Сигнал ревьюера">
                      {signalDecision === "confirm"
                        ? "подтверждён"
                        : signalDecision === "reject"
                          ? "отклонён"
                          : "не поднимался"}
                    </Kv>
                  </>
                )}
              </CardBody>
              {editable && (
                <CardFoot>
                  <span>
                    {participants
                      ? `${participants} ${plural(participants, "ревьюер", "ревьюера", "ревьюеров")} в работе`
                      : "Работу пока никто не проверяет"}
                  </span>
                  <Btn
                    size="s"
                    variant="quiet"
                    disabled={action.busy || dirty}
                    onClick={() =>
                      void action.run(async () => {
                        await api.command(
                          "record_review_responsibility",
                          live.review_iteration_id,
                          live.revision,
                          { action: "joined" },
                        );
                        refresh();
                      })
                    }
                  >
                    Присоединиться
                  </Btn>
                </CardFoot>
              )}
            </Card>

            {readOnly ? (
              <Card>
                <CardHead
                  title="История решений"
                  sub="Что предложила модель и что изменил человек"
                />
                <CardBody tight>
                  {assist.data && (
                    <Kv label="Модель предложила разбор" ink>
                      {suggestions.length}{" "}
                      {plural(
                        suggestions.length,
                        "требование",
                        "требования",
                        "требований",
                      )}
                    </Kv>
                  )}
                  {live.criterion_decisions
                    .filter((d) => d.decision === "manual")
                    .map((d) => {
                      const c = version?.criteria.find(
                        (c) => c.id === d.criterion_id,
                      );
                      const s = suggestions.find(
                        (s) => s.criterion_id === d.criterion_id,
                      );
                      return (
                        <Kv
                          key={d.criterion_id}
                          label={`Ревьюер изменил «${c?.title ?? "требование"}»`}
                          ink
                        >
                          {s?.proposed_points != null
                            ? `с ${num(s.proposed_points)} на ${num(d.points)}`
                            : num(d.points)}
                        </Kv>
                      );
                    })}
                  {live.current_review_revision && (
                    <Kv
                      label={`Ревьюер подтвердил итог ${num(finalScore)} и отправил студенту`}
                      ink
                    >
                      {dayLong(live.current_review_revision.created_at)}
                    </Kv>
                  )}
                  {!assist.data &&
                    !live.current_review_revision &&
                    live.criterion_decisions.length === 0 && (
                      <p className="small dim">Решений по работе ещё нет.</p>
                    )}
                </CardBody>
              </Card>
            ) : (
              <>
                <Card>
                  <CardHead
                    title="Признаки генерации ИИ"
                    sub="Оценка модели, на балл не влияет"
                  />
                  <CardBody>
                    {signal ? (
                      <>
                        <div className="meter-row">
                          <span className="d3 d3--human">
                            {signalPct == null
                              ? (signalLevel ?? "—")
                              : `${signalPct}%`}
                          </span>
                          <div>
                            <Meter value={signalPct ?? 0} human />
                          </div>
                        </div>
                        {signal.explanation && (
                          <p className="caption" style={{ whiteSpace: "pre-wrap" }}>
                            {signal.explanation}
                          </p>
                        )}
                        <ul className="list--small">
                          {(signal.evidence ?? []).length
                            ? (signal.evidence ?? []).map((e, i) => (
                                <li key={i}>
                                  {e.quote}
                                  {(e.locator || e.path) && (
                                    <span className="caption">
                                      {" "}
                                      {e.locator ?? e.path}
                                    </span>
                                  )}
                                </li>
                              ))
                            : null}
                        </ul>
                      </>
                    ) : assistFailed ? (
                      <div className="hint-row">
                        <OpPill status={assist.data?.status} />
                        <span className="small dim">
                          Проверка не завершилась. Это не отказ: результат можно
                          запросить заново.
                        </span>
                        {editable && ws && (
                          <Btn
                            size="s"
                            variant="quiet"
                            disabled={action.busy}
                            onClick={() =>
                              void action.run(async () => {
                                await ws.command(
                                  "retry_review_assist",
                                  assist.data!.id,
                                  assist.data!.revision,
                                  {},
                                );
                                assist.refresh();
                              })
                            }
                          >
                            Повторить проверку
                          </Btn>
                        )}
                      </div>
                    ) : assist.data && !assistRunning ? (
                      <p className="small dim">
                        Модель разобрала работу, но признаков генерации не
                        нашла.
                      </p>
                    ) : (
                      <>
                        <Skel lines={2} label="Модель разбирает работу…" />
                        <p className="caption">
                          Модель разбирает работу, это займёт около минуты. Со
                          страницы можно уйти, разбор сохранится.
                        </p>
                      </>
                    )}
                    {!!assist.error && (
                      <ErrorBox error={assist.error} retry={assist.refresh} />
                    )}
                  </CardBody>
                </Card>

                <Card>
                  <CardHead
                    title="Ответ студенту"
                    sub="Уходит вместе с вердиктом, черновик собрала модель"
                  />
                  <CardBody>
                    <Area
                      className="inp--tall"
                      aria-label="Обратная связь студенту"
                      disabled={!editable || action.busy}
                      value={feedback}
                      placeholder="Привет! Что в порядке, что поправить: короткими пунктами, как в PR."
                      onChange={(e) => {
                        feedbackTouched.current = true;
                        setFeedback(e.target.value);
                        setDirty(true);
                      }}
                    />
                  </CardBody>
                </Card>
              </>
            )}
          </div>

          <div className="stack">
            <Card>
              <CardHead
                title={
                  readOnly
                    ? "Разбор по требованиям"
                    : "Предварительное ревью от модели"
                }
                sub={
                  readOnly
                    ? undefined
                    : "Оценки уже проставлены моделью, меняйте там, где не согласны"
                }
              />
              <CardBody>
                <fieldset
                  className="acc-list"
                  disabled={!editable || action.busy}
                >
                  {version?.criteria.map((c, i) => {
                    const suggestion = suggestions.find(
                      (s) => s.criterion_id === c.id,
                    );
                    const decision = decisions[i];
                    const points = Number.isFinite(decision?.points)
                      ? decision.points
                      : null;
                    const needsHuman =
                      suggestion?.status === "needs_human" ||
                      context?.private_details?.criterion_classes?.[c.key] ===
                        "judgement";
                    const sources = suggestion?.sources ?? [];
                    // Факты запуска из песочницы («запуск: …») показываем и рядом с цитатами из кода.
                    const quotes = !sources.length
                      ? (suggestion?.evidence ?? [])
                      : (suggestion?.evidence ?? []).filter((e) =>
                          e.startsWith("запуск:"),
                        );
                    const hasEvidence = sources.length > 0 || quotes.length > 0;
                    const met =
                      suggestion?.requirement_met ??
                      (suggestion
                        ? (suggestion.proposed_points ?? 0) >= c.max_points
                        : (points ?? 0) >= c.max_points);
                    const marker = needsHuman
                      ? "h"
                      : !suggestion && !readOnly
                        ? "q"
                        : met
                          ? "y"
                          : "n";
                    const changed =
                      !!suggestion &&
                      suggestion.proposed_points !== null &&
                      points !== null &&
                      points !== suggestion.proposed_points;
                    const open =
                      !readOnly ||
                      suggestion?.status === "needs_human" ||
                      (!!suggestion &&
                        suggestion.proposed_points !== c.max_points);
                    const findings = [
                      ...sources.map((source, index) => ({
                        key: `source:${index}`,
                        file:
                          source.path ?? source.locator ?? "Работа студента",
                        lines: source.line_start
                          ? `строки ${source.line_start}${source.line_end && source.line_end !== source.line_start ? `–${source.line_end}` : ""}`
                          : undefined,
                        text: source.quote,
                        start: source.line_start ?? undefined,
                      })),
                      ...quotes.map((quote, index) => ({
                        key: `quote:${index}`,
                        file: "Работа студента",
                        lines: undefined,
                        text: quote,
                        start: undefined,
                      })),
                    ];
                    return (
                      <Acc
                        key={c.id}
                        defaultOpen={open}
                        head={
                          <>
                            <Ck kind={marker} />
                            <span className="acc__t">{c.title}</span>
                            {needsHuman && !changed && (
                              <Pill tone="human">оценочное</Pill>
                            )}
                            {changed && (
                              <Pill tone="human">
                                {readOnly ? "изменено ревьюером" : "изменено"}
                              </Pill>
                            )}
                            <span className="mono acc__score">
                              {points === null
                                ? "—"
                                : outOf(points, c.max_points)}
                            </span>
                          </>
                        }
                      >
                        {suggestion ? (
                          <>
                            {needsHuman && !hasEvidence && (
                              <Quote tone="human">{suggestion.reason}</Quote>
                            )}
                            {findings.map((f, index) => (
                              <Finding
                                key={f.key}
                                file={f.file}
                                lines={f.lines}
                                href="#"
                                why={
                                  index === findings.length - 1
                                    ? suggestion.reason
                                    : undefined
                                }
                              >
                                <Code
                                  lines={f.text.split("\n").map((text, k) => ({
                                    n: f.start ? f.start + k : undefined,
                                    text,
                                    miss: !met,
                                    hit: met,
                                  }))}
                                />
                              </Finding>
                            ))}
                            {!hasEvidence && !needsHuman && (
                              <div className="acc__empty">
                                {met
                                  ? "Нарушений не найдено"
                                  : suggestion.reason}
                              </div>
                            )}
                            {suggestion.reviewer_note && (
                              <p className="caption acc__note">
                                {suggestion.reviewer_note}
                              </p>
                            )}
                          </>
                        ) : (
                          !readOnly && (
                            <div className="acc__empty">
                              {assistRunning || (!assist.data && !assist.error)
                                ? "Модель разбирает это требование…"
                                : "Модель это требование не проверяла. Оцените вручную."}
                            </div>
                          )
                        )}
                        {!readOnly && (
                          <div className="crit-score">
                            <span className="crit-score__lbl">Оценка</span>
                            <span className="acc__ctl">
                              <Scale
                                max={c.max_points}
                                step={step(c)}
                                value={points}
                                label={`Баллы: ${c.title}`}
                                disabled={!editable || action.busy}
                                onChange={(v) =>
                                  edit(i, {
                                    points: v ?? Number.NaN,
                                    decision: "manual",
                                  })
                                }
                              />
                            </span>
                            <span className="crit-score__of">
                              из {num(c.max_points)}
                            </span>
                            {changed && suggestion?.proposed_points != null && (
                              <span className="caption crit-score__was">
                                модель предлагала{" "}
                                {num(suggestion.proposed_points)}
                              </span>
                            )}
                          </div>
                        )}
                        <Field
                          label="Обоснование"
                          className="acc__field"
                          hint={
                            readOnly
                              ? undefined
                              : "Уходит студенту вместе с баллом. Заполнено из разбора модели, правьте свободно."
                          }
                        >
                          <Area
                            rows={2}
                            disabled={readOnly}
                            value={decision?.reason ?? ""}
                            onChange={(e) =>
                              edit(i, { reason: e.target.value })
                            }
                          />
                        </Field>
                      </Acc>
                    );
                  })}
                </fieldset>
                <div className="review-sum">
                  <Kv label="Сумма по требованиям" total sum>
                    {outOf(total, maxScore)}
                  </Kv>
                  {grade.data && grade.data.penalty > 0 && (
                    <Kv label="Просрочка" className="kv--noline">
                      <span className="neg">−{num(grade.data.penalty)}</span>
                    </Kv>
                  )}
                  <Kv label="Итог" className="kv--result">
                    <span className={cx(below && "bad")}>
                      {num(finalScore)}
                      <span className="unit"> из {num(maxScore)}</span>
                    </span>
                  </Kv>
                  {passScore != null && (
                    <Kv label="Порог зачёта" className="kv--verdict">
                      <span className={cx(below ? "bad" : "ok")}>
                        {outOf(passScore, maxScore)}
                        <span className="unit">
                          {" "}
                          · {below ? "пока не пройден" : "пройден"}
                        </span>
                      </span>
                    </Kv>
                  )}
                  {!readOnly && passScore != null && (
                    <div
                      className={cx(
                        "caption review-sum__note",
                        below && "bad-text",
                      )}
                    >
                      {below
                        ? "Итог ниже порога: верните на доработку или измените оценку там, где не согласны с моделью."
                        : "Порог пройден. Проверьте обоснования и отправьте решение студенту."}
                    </div>
                  )}
                </div>
              </CardBody>
              {readOnly && live.current_review_revision && (
                <CardFoot>
                  <span>
                    Ответ студенту отправлен{" "}
                    {dayLong(live.current_review_revision.created_at)}
                  </span>
                  <Btn
                    variant="link"
                    onClick={() => setShowFeedback((v) => !v)}
                    aria-expanded={showFeedback}
                  >
                    {showFeedback
                      ? "Скрыть текст ответа"
                      : "Показать текст ответа"}
                  </Btn>
                </CardFoot>
              )}
              {readOnly && showFeedback && (
                <CardBody className="preserve small dim">
                  {live.current_review_revision?.feedback || "Ответ пуст."}
                </CardBody>
              )}
            </Card>
          </div>
        </div>
        {editable && (
          <Dock
            actions={
              <>
                {mode && ws && (
                  <Btn
                    size="s"
                    variant="quiet"
                    disabled={action.busy}
                    title="Вернуть эту работу в пул и открыть следующую"
                    onClick={() => void action.run(skipToNext)}
                  >
                    Пропустить
                  </Btn>
                )}
                <Btn
                  size="s"
                  disabled={action.busy || !canSave || !dirty}
                  onClick={() => void action.run(save, "Черновик сохранён.")}
                >
                  Сохранить черновик
                </Btn>
                <Btn
                  variant="dark"
                  disabled={
                    action.busy || dirty || !live.current_review_revision_id
                  }
                  onClick={() => {
                    setOutcome("needs_changes");
                    setReason("");
                  }}
                >
                  Вернуть на доработку
                </Btn>
                <Btn
                  variant="pri"
                  disabled={
                    action.busy || dirty || !live.current_review_revision_id
                  }
                  onClick={() => {
                    setOutcome("passed");
                    setReason("");
                  }}
                >
                  Зачесть
                </Btn>
              </>
            }
          >
            <Sum value={num(finalScore)} of={`из ${num(maxScore)}`} />
            {passScore != null && (
              <span className={cx(below && "bad-text")}>
                {below
                  ? `ниже порога ${num(passScore)}`
                  : `порог ${num(passScore)} пройден`}
              </span>
            )}
            {saveNote && (
              <span className="caption" role="status">
                {saveNote}
              </span>
            )}
          </Dock>
        )}
      </Main>
      {outcome && (
        <Modal
          title={
            outcome === "needs_changes"
              ? "Вернуть на доработку"
              : "Зачесть работу"
          }
          close={close}
          flush
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const revisionDeadline = String(
                new FormData(e.currentTarget).get("revision_deadline") ?? "",
              );
              void action.run(() => publish(revisionDeadline));
            }}
          >
            <div className="card__body">
              {action.feedback}
              {outcome === "needs_changes" && (
                <>
                  <Field label="Срок доработки">
                    <Inp
                      required
                      type="datetime-local"
                      name="revision_deadline"
                      value={deadline}
                      onChange={(e) => setDeadline(e.target.value)}
                    />
                  </Field>
                  <Field label="Что нужно исправить">
                    <Area
                      required
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </Field>
                </>
              )}
              <p className="small dim">
                Студент получит сохранённый отзыв и оценку{" "}
                {grade.data
                  ? num(penalty ? grade.data.final_score : grade.data.raw_score)
                  : num(live.current_review_revision?.total_score)}
                .
              </p>
              {grade.data && grade.data.penalty > 0 && (
                <div className="field--after">
                  <Chk
                    checked={penalty}
                    onChange={(e) => setPenalty(e.target.checked)}
                  >
                    Применить просрочку −{num(grade.data.penalty)}
                  </Chk>
                </div>
              )}
            </div>
            <div className="card__foot card__foot--end">
              <BtnRow>
                <Btn variant="quiet" onClick={close}>
                  Отмена
                </Btn>
                <Btn
                  variant="pri"
                  type="submit"
                  disabled={
                    action.busy || (!!ws && (grade.loading || !!grade.error))
                  }
                >
                  Подтвердить публикацию
                </Btn>
              </BtnRow>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
