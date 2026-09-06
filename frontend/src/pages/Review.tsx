import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient, Model } from "../api/client";
import { ErrorBox, go, safeUrl, useAction, useResource } from "../ui";
import { WorkspaceClient, type W } from "../api/workspace";
import { Modal, useDirtyGuard } from "../workspace-ui";
import { ArtifactLink } from "./WorkspaceReview";
import { FileActions, FileIcon, FilePreview, fileKind } from "../FileView";
import { nextFromMine, nextFromPool, RELEASED_NOTICE } from "../reviewQueue";
import { ActionMessage, useActionMessage } from "../ActionMessage";
import {
  Acc,
  AccCtl,
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
  Finding,
  Kv,
  Main,
  Meter,
  OpPill,
  Pill,
  Quote,
  Scale,
  Skel,
  St,
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

/* Срок доработки, когда ревьюер возвращает работу одной кнопкой. Совпадает с
   значением revision_days по умолчанию в контракте бэкенда (7 дней). */
const REVISION_DAYS = 7;
function defaultRevisionDeadline() {
  const at = new Date();
  at.setDate(at.getDate() + REVISION_DAYS);
  return at.toISOString();
}

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
  coordinator = false,
  ws,
}: {
  api: ApiClient;
  id: string;
  session: Model<"Session">;
  readOnly?: boolean;
  coordinator?: boolean;
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
          coordinator={coordinator}
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
  coordinator = false,
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
  coordinator?: boolean;
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
  const [preview, setPreview] = useState(false);
  const message = useActionMessage();
  const feedbackTouched = useRef(false);
  const [conditionOpen, setConditionOpen] = useState(true);
  const [correcting, setCorrecting] = useState(false);
  const [correctionReason, setCorrectionReason] = useState("");
  const [confirmPool, setConfirmPool] = useState(false);
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
  /* Черновик ответа от модели: и подставляется сам при первом разборе, и
     возвращается кнопкой «Сгенерировать», если ревьюер стёр текст. */
  const modelDraft =
    assist.data?.result?.feedback_draft ??
    suggestions
      .map((x) => x.student_feedback)
      .filter(Boolean)
      .join("\n\n");
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
    if (!feedbackTouched.current && !feedback.trim() && modelDraft) {
      setFeedback(modelDraft);
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

  async function publish(
    decision: "passed" | "needs_changes",
    revisionDeadline = deadline,
  ) {
    if (!live.current_review_revision_id) return;
    if (ws) {
      const latest = await ws.reviewContext(live.review_iteration_id);
      await ws.command(
        "save_review_outcome",
        live.review_iteration_id,
        context?.outcome_revision ?? latest.outcome_revision,
        {
          decision,
          reason: reason.trim() || feedback.trim() || "Работа зачтена",
          revision_deadline:
            decision === "needs_changes"
              ? new Date(
                  revisionDeadline || defaultRevisionDeadline(),
                ).toISOString()
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
    message.show(
      decision === "passed"
        ? "Работа зачтена. Оценка и отзыв опубликованы для студента."
        : "Работа возвращена на доработку. Оценка и отзыв опубликованы для студента.",
    );
    refresh();
  }
  async function openNext() {
    if (!ws) return;
    message.clear();
    const nextId = await nextFromMine(ws, [context?.submission_id ?? ""]);
    if (nextId) go(`/reviews/${nextId}`);
    else setConfirmPool(true);
  }
  async function release() {
    message.clear();
    await api.command(
      "record_review_responsibility",
      live.review_iteration_id,
      live.revision,
      { action: "released" },
    );
    message.show(RELEASED_NOTICE);
    go("/works");
  }

  const activePeople = new Map<string, string>();
  live.responsibility_events.forEach((e) =>
    activePeople.set(e.reviewer_id, e.action),
  );
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
  const artifactLabel = context?.artifact_label ?? "Снимок работы";
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
  const signalDecision = signal ? signalDecisions[signal.id] : undefined;
  const backHref = coordinator ? "#/registry" : "#/works";
  const screen = coordinator ? "К9" : repeat ? "Р6" : "Р5";
  const saveNote = !editable
    ? null
    : action.busy
      ? "Сохраняем…"
      : dirty
        ? canSave
          ? "Сохраним через секунду"
          : "Проставьте баллы по каждому требованию"
        : savedAt
          ? `Черновик сохранён ${savedAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
          : null;

  return (
    <>
      <Topbar
        className="topbar--review"
        crumbs={
          <Crumbs
            back={backHref}
            items={
              coordinator
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
          </>
        }
        actions={
          editable ? (
            <>
              {!coordinator && (
                <Btn
                  size="s"
                  variant="quiet"
                  disabled={action.busy || dirty}
                  title={dirty ? "Дождитесь сохранения черновика" : undefined}
                  onClick={() => void action.run(release)}
                >
                  Снять с себя проверку
                </Btn>
              )}
            </>
          ) : (
            <>
              {artifactHref && (
                <Btn
                  size="s"
                  variant="quiet"
                  href={artifactHref}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть работу ↗
                </Btn>
              )}
              {!readOnly && live.status === "published" && (
                <Btn
                  size="s"
                  disabled={action.busy}
                  onClick={() => {
                    setCorrectionReason("");
                    setCorrecting(true);
                  }}
                >
                  Изменить оценку и отзыв
                </Btn>
              )}
              {!readOnly &&
                !coordinator &&
                ws &&
                live.status === "published" && (
                  <Btn
                    size="s"
                    variant="pri"
                    disabled={action.busy}
                    onClick={() => void action.run(openNext)}
                  >
                    Следующая работа
                  </Btn>
                )}
            </>
          )
        }
      />
      <Main data-screen={screen}>
        <ActionMessage />
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
        <div className="row-rev review-layout">
          <div className="stack">
            <Card>
              <CardHead title="Работа" />
              <CardBody tight>
                <Kv
                  className="kv--file"
                  label={
                    <>
                      <FileIcon kind={fileKind(artifactLabel)} />
                      <span className="mono">{artifactLabel}</span>
                    </>
                  }
                  ink
                >
                  <FileActions name={artifactLabel} url={artifactHref} />
                </Kv>
                <Kv label="Попытка">{context?.attempt ?? "—"}</Kv>
                {readOnly && reviewerId && (
                  <Kv label="Ревьюер">
                    <span className="mono">rev-{short(reviewerId)}</span>
                  </Kv>
                )}
                <Kv label={repeat ? "Исправления сданы" : "Сдана"}>
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
                            {signalPct == null ? "—" : `${signalPct}%`}
                          </span>
                          <div>
                            <Meter value={signalPct ?? 0} human />
                          </div>
                        </div>
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
                            : signal.explanation && (
                                <li>{signal.explanation}</li>
                              )}
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
                    ) : !editable && !assist.data && !assist.loading ? (
                      <p className="caption">
                        Для этой версии проверки нет разбора модели.
                      </p>
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
                    sub="Студент получит его вместе с оценкой за работу"
                    className="card__head--response"
                  >
                    <Btn
                      size="s"
                      variant="quiet"
                      disabled={!editable || action.busy || !modelDraft}
                      title={
                        modelDraft
                          ? "Заменить текст черновиком модели"
                          : "Модель ещё не собрала черновик"
                      }
                      onClick={() => {
                        feedbackTouched.current = true;
                        setFeedback(modelDraft);
                        setDirty(true);
                      }}
                    >
                      Сгенерировать
                    </Btn>
                  </CardHead>
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
            <Card>
              <CardHead title="Результат" />
              <CardBody tight>
                <Kv label="По требованиям задания">{num(total)}</Kv>
                {!!grade.data && grade.data.penalty > 0 && (
                  <Kv label="Просрочка">
                    <span className="neg">−{num(grade.data.penalty)}</span>
                  </Kv>
                )}
                <Kv label="Итог" total>
                  <span className={cx(below && "bad")}>
                    {num(finalScore)}
                    <span className="unit"> из {num(maxScore)}</span>
                  </span>
                </Kv>
              </CardBody>
              {editable && (
                <CardFoot block>
                  {passScore != null && (
                    <div
                      className={cx("decision-note", below && "bad-text")}
                      role={below ? "status" : undefined}
                    >
                      {below
                        ? `Ниже порога зачёта, нужно ${outOf(passScore, maxScore)}. Выберите, что делать дальше.`
                        : `Порог ${num(passScore)} пройден.`}
                    </div>
                  )}
                  {!feedback.trim() && (
                    <div className="caption decision-note">
                      Напишите ответ студенту: он уходит вместе с решением о
                      доработке.
                    </div>
                  )}
                  {saveNote && (
                    <div className="caption decision-note" role="status">
                      {saveNote}
                    </div>
                  )}
                  <BtnRow className="decision-actions">
                    <Btn
                      variant="dark"
                      disabled={
                        action.busy ||
                        dirty ||
                        !live.current_review_revision_id ||
                        !feedback.trim()
                      }
                      onClick={() =>
                        void action.run(() => publish("needs_changes"))
                      }
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
                  </BtnRow>
                </CardFoot>
              )}
            </Card>
          </div>

          <div className="stack">
            <Card>
              <CardHead title="Условие задания">
                <Btn
                  size="s"
                  variant="link"
                  aria-expanded={conditionOpen}
                  aria-controls="review-condition"
                  onClick={() => setConditionOpen((v) => !v)}
                >
                  {conditionOpen ? "Свернуть" : "Развернуть"}
                </Btn>
              </CardHead>
              {conditionOpen && (
                <CardBody prose id="review-condition">
                  <p className="preserve">
                    {version?.student_text || "Условие задания не задано."}
                  </p>
                  {ws &&
                    !!context?.private_details?.material_upload_ids?.length && (
                      <div className="btn-row">
                        {context.private_details.material_upload_ids.map(
                          (id) => (
                            <ArtifactLink key={id} ws={ws} id={id} />
                          ),
                        )}
                      </div>
                    )}
                </CardBody>
              )}
            </Card>
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
                className="card__head--review"
              />
              <CardBody className="review-criteria">
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
                    const quotes = !sources.length
                      ? (suggestion?.evidence ?? [])
                      : [];
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
                    /* Раскрыто то, что требует внимания: оценочные требования,
                       непроставленные баллы и всё, где модель нашла нарушение.
                       Выполненные свёрнуты, как в макете Р5. */
                    const open =
                      needsHuman ||
                      !suggestion ||
                      suggestion.proposed_points !== c.max_points;
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
                        headClass="acc__h--score"
                        head={
                          <>
                            <Ck kind={marker} />
                            <span className="acc__t">
                              {c.title}
                              {needsHuman && !changed && (
                                <Pill tone="human">оценочное</Pill>
                              )}
                              {changed && (
                                <Pill tone="human">
                                  {readOnly ? "изменено ревьюером" : "изменено"}
                                </Pill>
                              )}
                            </span>
                            {readOnly ? (
                              <span className="mono">
                                {points === null
                                  ? "—"
                                  : outOf(points, c.max_points)}
                              </span>
                            ) : (
                              <AccCtl className="acc__ctl">
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
                              </AccCtl>
                            )}
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
                                open={
                                  artifactHref &&
                                  (fileKind(artifactLabel) === "link" ? (
                                    <a
                                      className="o"
                                      href={artifactHref}
                                      target="_blank"
                                      rel="noreferrer"
                                    >
                                      Открыть ↗
                                    </a>
                                  ) : (
                                    <button
                                      type="button"
                                      className="o"
                                      onClick={() => setPreview(true)}
                                    >
                                      Открыть
                                    </button>
                                  ))
                                }
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
                            {suggestion.reviewer_note &&
                              !/^Проверьте работу самостоятельно[.!]?$/i.test(
                                suggestion.reviewer_note.trim(),
                              ) && (
                                <p className="caption acc__note">
                                  {suggestion.reviewer_note}
                                </p>
                              )}
                          </>
                        ) : (
                          !readOnly && (
                            <div className="acc__empty">
                              {assistRunning ||
                              (editable && !assist.data && !assist.error)
                                ? "Модель разбирает это требование…"
                                : "Модель это требование не проверяла."}
                            </div>
                          )
                        )}
                        {editable &&
                          (changed || decision?.decision === "manual") && (
                            <label className="field">
                              <span>Обоснование ревьюера</span>
                              <Area
                                aria-label={`Обоснование: ${c.title}`}
                                rows={2}
                                maxLength={10000}
                                value={decision?.reason ?? ""}
                                onChange={(e) =>
                                  edit(i, {
                                    reason: e.target.value,
                                    decision: "manual",
                                  })
                                }
                              />
                            </label>
                          )}
                        {!editable &&
                          decision?.decision === "manual" &&
                          decision.reason && (
                            <p className="preserve">
                              Обоснование ревьюера: {decision.reason}
                            </p>
                          )}
                        {!readOnly &&
                          changed &&
                          suggestion?.proposed_points != null && (
                            <p className="caption acc__note">
                              Модель предлагала{" "}
                              {num(suggestion.proposed_points)}
                            </p>
                          )}
                      </Acc>
                    );
                  })}
                </fieldset>
                <div className="review-sum">
                  <Kv label="Сумма по требованиям" total sum>
                    {outOf(total, maxScore)}
                  </Kv>
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
      </Main>
      {outcome && (
        <Modal title="Зачесть работу" close={close} flush>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(() => publish("passed"));
            }}
          >
            <div className="card__body">
              {action.feedback}
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
      {preview && artifactHref && (
        <FilePreview
          name={artifactLabel}
          url={artifactHref}
          close={() => setPreview(false)}
        />
      )}
      {correcting && (
        <Modal
          title="Изменить оценку и отзыв"
          close={() => setCorrecting(false)}
        >
          {action.feedback}
          <p>До публикации исправления студент видит прежнюю оценку и отзыв.</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const result = await api.command(
                  "create_review_correction",
                  live.review_iteration_id,
                  live.revision,
                  {
                    published_review_revision_id:
                      live.current_review_revision_id!,
                    reason: correctionReason.trim(),
                  },
                );
                setCorrecting(false);
                go(`/reviews/${result.review_iteration_id}`);
              });
            }}
          >
            <label>
              Причина исправления
              <Area
                required
                maxLength={10000}
                value={correctionReason}
                onChange={(e) => setCorrectionReason(e.target.value)}
              />
            </label>
            <Btn
              type="submit"
              disabled={action.busy || !correctionReason.trim()}
            >
              Открыть новую версию
            </Btn>
          </form>
        </Modal>
      )}
      {confirmPool && (
        <Modal
          title="Все ваши работы проверены"
          close={() => setConfirmPool(false)}
        >
          {action.feedback}
          <p>
            У вас больше нет работ, готовых к проверке. Взять следующую работу
            из общего пула?
          </p>
          <BtnRow>
            <Btn
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  const id = await nextFromPool(ws!, [
                    context?.submission_id ?? "",
                  ]);
                  setConfirmPool(false);
                  if (id) go(`/reviews/${id}`);
                  else message.show("В пуле пока нет подходящих работ.");
                })
              }
            >
              Взять из пула
            </Btn>
            <Btn
              variant="quiet"
              disabled={action.busy}
              onClick={() => setConfirmPool(false)}
            >
              Остаться
            </Btn>
          </BtnRow>
        </Modal>
      )}
    </>
  );
}
