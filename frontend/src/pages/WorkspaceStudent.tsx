import { Area, Btn, Drop, Field, Inp, Seg, Tab, Tabs, cx } from "../ds";
import { useEffect, useId, useRef, useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, uploadFile, type W } from "../api/workspace";
import {
  Card,
  Resource,
  date,
  go,
  useAction,
  useResource,
  safeUrl,
} from "../ui";
import { Quota, SelfReviewResult, useDirtyGuard } from "../workspace-ui";
import { ArtifactLink } from "./WorkspaceReview";
import {
  PublishedStudentReview,
  newestSelfReviews,
  SubmittedStudentWork,
} from "./WorkspaceSubmissionDetail";

type StudentContext = W<"StudentContext">;
export function WorkspaceSubmit({
  ws,
  id,
  session,
}: {
  ws: WorkspaceClient;
  id: string;
  session: Model<"Session">;
}) {
  const r = useResource(() => ws.studentContext(id), id);
  return (
    <Resource value={r}>
      {r.data && (
        <DraftForm key={id} ws={ws} initial={r.data} session={session} />
      )}
    </Resource>
  );
}
function DraftForm({
  ws,
  initial,
  session,
}: {
  ws: WorkspaceClient;
  initial: StudentContext;
  session: Model<"Session">;
}) {
  const [data, setData] = useState(initial);
  const fileInputId = useId();
  const [fileError, setFileError] = useState<string>();
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState(initial.draft?.artifact_url ?? "");
  const [comment, setComment] = useState(initial.draft?.comment ?? "");
  const [file, setFile] = useState<File>();
  const [selectedSource, setSource] = useState<"url" | "file">(
    initial.draft?.upload_id ? "file" : "url",
  );
  const allowedSources = data.allowed_sources ?? [
    "upload",
    "github",
    "google_docs",
  ];
  const canFile = allowedSources.includes("upload");
  const canGitHub = allowedSources.includes("github");
  const canDocs = allowedSources.includes("google_docs");
  const canLink = canGitHub || canDocs;
  const source =
    selectedSource === "file"
      ? canFile
        ? "file"
        : "url"
      : canLink
        ? "url"
        : "file";
  const sourceLabel =
    canGitHub && canDocs
      ? "Ссылка на репозиторий или Google Docs"
      : canGitHub
        ? "Ссылка на репозиторий GitHub"
        : "Ссылка на Google Docs";
  const [saved, setSaved] = useState(initial.draft);
  const uploaded = useResource(
    () =>
      saved?.upload_id ? ws.download(saved.upload_id) : Promise.resolve(null),
    `uploaded:${saved?.upload_id ?? "none"}`,
  );
  const [dirty, setDirty] = useState(false);
  const [run, setRun] = useState(initial.quota?.active_run_id ?? undefined);
  const [result, setResult] = useState<W<"SelfReviewView">>();
  const [collapsed, setCollapsed] = useState(
    initial.self_reviews.length > 0 ||
      !!initial.submission_id ||
      !!initial.quota?.active_run_id,
  );
  const [stage, setStage] = useState("");
  const [rubricCollapsed, setRubricCollapsed] = useState(false);
  const [reviewTab, setReviewTab] = useState<"ai" | "human">(
    initial.submission_id ? "human" : "ai",
  );
  useEffect(() => {
    if (
      (selectedSource === "file" && !canFile && canLink) ||
      (selectedSource === "url" && !canLink && canFile)
    ) {
      setSource(canLink ? "url" : "file");
      setDirty(true);
    }
  }, [selectedSource, canFile, canLink]);
  const action = useAction();
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const history = useResource(
    async () => (data.submission_id ? ws.submission(data.submission_id) : null),
    data.submission_id ?? "no-submission",
  );
  const current = history.data?.reviews.find(
    (v) => v.id === history.data?.current_publication_id,
  );
  const latestAttempt = history.data?.attempts.at(-1);
  const revision =
    current?.decision === "needs_changes" &&
    current.submission_version_id === latestAttempt?.id;
  const canSubmit =
    !data.submission_id ||
    (!history.loading && !history.error && (!latestAttempt || revision));
  const submittedStatus = latestAttempt
    ? latestAttempt.status === "passed"
      ? "Зачтена"
      : latestAttempt.status === "failed"
        ? "Не зачтена"
        : latestAttempt.sequence > 1
          ? "На повторном ревью"
          : "На ревью"
    : "Черновик";
  const selfReviews = newestSelfReviews([
    ...data.self_reviews.filter((value) => value.id !== result?.id),
    ...(result ? [result] : []),
  ]);
  const activeResult = selfReviews[0];
  useDirtyGuard(dirty);
  function pickFile(next: File | undefined) {
    if (!next || action.busy || !canSubmit || !canFile) return;
    if (!/\.(md|pdf|docx)$/i.test(next.name)) {
      setFileError("Подходят Markdown, PDF или DOCX.");
      return;
    }
    if (next.size > 10_000_000) {
      setFileError("Файл больше 10 МБ. Выберите файл меньшего размера.");
      return;
    }
    setFileError(undefined);
    setFile(next);
    setSource("file");
    setDirty(true);
  }
  function validateSource() {
    if (source === "file" && fileError) throw new Error(fileError);
    if (!canFile && !canLink)
      throw new Error("Для задания не настроены способы сдачи.");
    if (source === "url") {
      let parsed: URL;
      try {
        parsed = new URL(url);
      } catch {
        throw new Error("Укажите полную ссылку на работу.");
      }
      if (parsed.protocol !== "https:") throw new Error("Нужна ссылка HTTPS.");
      if (
        !(canGitHub && parsed.hostname === "github.com") &&
        !(canDocs && parsed.hostname === "docs.google.com")
      )
        throw new Error(
          `Для этого задания доступна: ${sourceLabel.toLowerCase()}.`,
        );
    }
  }
  async function save() {
    if (!canSubmit)
      throw new Error(
        "Новая сдача доступна после возврата работы на доработку.",
      );
    validateSource();
    let uploadId = saved?.upload_id ?? null;
    if (source === "file" && file)
      uploadId = (await uploadFile(ws, file, session.user_id)).id;
    if (source === "file" && !uploadId) throw new Error("Выберите файл.");
    const draft = await ws.command(
      "save_work_draft",
      data.publication_id,
      saved?.revision ?? 0,
      {
        artifact_url: source === "url" ? url : "",
        upload_id: source === "file" ? uploadId : null,
        comment,
      },
    );
    setSaved(draft);
    setFile(undefined);
    setDirty(false);
    if (!data.quota) {
      const refreshed = await ws.studentContext(data.publication_id);
      if (mounted.current) setData(refreshed);
    }
    return draft;
  }
  useEffect(() => {
    if (!dirty || !canSubmit || (source === "file" && fileError)) return;
    if (source === "url") {
      try {
        const parsed = new URL(url);
        if (!["http:", "https:"].includes(parsed.protocol)) return;
      } catch {
        return;
      }
    } else if (!file && !saved?.upload_id) return;
    const timer = setTimeout(() => {
      void action.run(async () => {
        await save();
      });
    }, 1200);
    return () => clearTimeout(timer);
  }, [dirty, url, comment, file, source, canSubmit, fileError]);
  async function prepare() {
    if (!canSubmit)
      throw new Error(
        "Новая сдача доступна после возврата работы на доработку.",
      );
    validateSource();
    const draft = dirty || !saved ? await save() : saved;
    setStage("Проверяем доступ и сохраняем снимок работы…");
    let preparation = await ws.command(
      "prepare_work_draft",
      draft.id,
      draft.revision,
      {},
    );
    while (
      ["queued", "processing", "pending", "running"].includes(
        preparation.status,
      )
    ) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (!mounted.current) return;
      preparation = await ws.core.request<typeof preparation>(
        `/v2/preparations/${preparation.id}`,
      );
    }
    if (!preparation.artifact_id || preparation.status !== "succeeded")
      throw new Error(
        preparation.error_code === "access_denied"
          ? "Не удалось прочитать работу. Откройте доступ по ссылке и повторите попытку."
          : "Не удалось подготовить работу. Проверьте доступ и повторите попытку.",
      );
    return draft;
  }
  async function submit() {
    const draft = await prepare();
    if (!draft) return;
    setStage("Отправляем работу на ревью…");
    const submitted = await ws.command(
      "submit_work_draft",
      draft.id,
      draft.revision,
      {},
    );
    go(`/submissions/${submitted.id}`);
  }
  return (
    <>
      <section
        className="student-band"
        data-screen={revision ? "С3" : activeResult || run ? "С2" : "С1"}
      >
        <div className="brand-circles" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
        <div className="band-content">
          {(data.course_title || data.run_title) && (
            <span className="label">
              {[data.course_title, data.run_title].filter(Boolean).join(" · ")}
            </span>
          )}
          <h1 className="d2">{data.title}</h1>
          <div className="actions">
            <div>
              <span className="caption">
                {revision ? "Прислать исправления до" : "Сдать до"}
              </span>
              <span>
                {date(
                  revision
                    ? (current?.revision_deadline ?? data.submission_deadline)
                    : data.submission_deadline,
                )}
              </span>
            </div>
            {data.max_score !== undefined && (
              <div>
                <span className="caption">
                  {data.policy ? "Порог зачёта" : "Максимум"}
                </span>
                <span>
                  {data.policy
                    ? `${data.policy.pass_score} из ${data.max_score}`
                    : `${data.max_score} баллов`}
                </span>
              </div>
            )}
            {(latestAttempt || revision || activeResult || run) && (
              <div>
                <span className="caption">Статус</span>
                <span
                  style={{ color: revision ? "var(--late)" : "var(--ink)" }}
                >
                  {revision ? "Нужны правки" : submittedStatus}
                </span>
              </div>
            )}
          </div>
        </div>
      </section>
      {action.feedback}
      {history.error && <Resource value={history}>{null}</Resource>}
      <div className="student-grid">
        <div className="stack">
          <section className="card">
            <div className="card-head card__head">
              <h2>Задание</h2>
              <Btn
                variant="link"
                size="s"
                aria-expanded={!collapsed}
                onClick={() => setCollapsed((value) => !value)}
              >
                {collapsed ? "Развернуть" : "Свернуть"}
              </Btn>
            </div>
            {!collapsed && (
              <div className="card-body card__body">
                <p className="preserve">{data.student_text}</p>
                {(data.material_upload_ids ?? []).map((id) => (
                  <ArtifactLink key={id} ws={ws} id={id} />
                ))}
              </div>
            )}
          </section>
          {(run || activeResult || history.data?.attempts.length) && (
            <section className="card student-review-tabs">
              <Tabs
                style={{ padding: "var(--s-2) var(--s-5) 0", marginBottom: 0 }}
              >
                <Tab on={reviewTab === "ai"} onClick={() => setReviewTab("ai")}>
                  ИИ-ревью
                </Tab>
                <Tab
                  on={reviewTab === "human"}
                  onClick={() => setReviewTab("human")}
                >
                  Ревью
                </Tab>
              </Tabs>
              <div className="card-body">
                {reviewTab === "ai" ? (
                  <>
                    {run ? (
                      <details className="acc" open>
                        <summary className="acc__h">Текущая проверка</summary>
                        <StudentSelfReview
                          ws={ws}
                          id={run}
                          onComplete={async (value) => {
                            setResult(value);
                            setData((previous) => ({
                              ...previous,
                              quota: value.quota,
                            }));
                            setRun(undefined);
                            const next = await ws.studentContext(
                              data.publication_id,
                            );
                            if (mounted.current) setData(next);
                          }}
                        />
                      </details>
                    ) : activeResult ? (
                      <details className="acc" key={activeResult.id} open>
                        <summary className="acc__h">
                          Проверка от {date(activeResult.created_at)}
                          <span className="pill">последняя</span>
                        </summary>
                        {(dirty ||
                          saved?.revision !== activeResult.draft_revision) && (
                          <p className="notice">
                            После проверки работа изменена. Результат относится
                            к сохранённому снимку.
                          </p>
                        )}
                        <SelfReviewResult
                          showHeading={false}
                          value={activeResult}
                        />
                      </details>
                    ) : (
                      <p>Самопроверка не запускалась.</p>
                    )}
                    {selfReviews
                      .filter(
                        (value) =>
                          value.id !== run &&
                          (!!run || value.id !== activeResult?.id),
                      )
                      .map((value) => (
                        <details className="acc" key={value.id}>
                          <summary className="acc__h">
                            Проверка от {date(value.created_at)}
                          </summary>
                          <SelfReviewResult showHeading={false} value={value} />
                        </details>
                      ))}
                  </>
                ) : history.data?.attempts.length ? (
                  history.data.attempts.map((attempt, index) => {
                    const published = history.data!.reviews.filter(
                      (value) => value.submission_version_id === attempt.id,
                    );
                    const latest = published.at(-1);
                    return (
                      <details
                        className="acc"
                        key={attempt.id}
                        open={index === history.data!.attempts.length - 1}
                      >
                        <summary className="acc__h">
                          <span>
                            Попытка {attempt.sequence},{" "}
                            {date(attempt.submitted_at)}
                          </span>
                          {index === history.data!.attempts.length - 1 && (
                            <span className="pill">последняя</span>
                          )}
                        </summary>
                        <div style={{ paddingBottom: "var(--s-4)" }}>
                          <SubmittedStudentWork ws={ws} attempt={attempt} />
                          {latest ? (
                            <PublishedStudentReview value={latest} />
                          ) : (
                            <p>Результат ещё не опубликован.</p>
                          )}
                          {published.length > 1 && (
                            <details className="student-history">
                              <summary>
                                Предыдущие публикации этой попытки
                              </summary>
                              {published.slice(0, -1).map((value) => (
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
                  <p>
                    Результат появится после отправки работы и публикации ревью.
                  </p>
                )}
              </div>
            </section>
          )}
          <Card
            title={revision ? "Исправленная работа" : "Ваша работа"}
            headClassName="submission-head"
            bodyClassName="card__body--compact"
            actions={
              <Seg
                value={source}
                label="Способ сдачи"
                disabled={action.busy || !canSubmit}
                options={[
                  ...(canLink
                    ? [{ value: "url" as const, label: "Ссылка" }]
                    : []),
                  ...(canFile
                    ? [{ value: "file" as const, label: "Файлы" }]
                    : []),
                ]}
                onChange={(value) => {
                  setSource(value);
                  setDirty(true);
                }}
              />
            }
          >
            <fieldset disabled={action.busy || !canSubmit}>
              {!canFile && !canLink ? (
                <p>Для задания не настроены способы сдачи.</p>
              ) : source === "url" ? (
                <Field
                  label={sourceLabel}
                  hint="Откройте доступ к работе по ссылке. При отправке сохраняется отдельный снимок."
                >
                  <Inp
                    mono
                    type="url"
                    aria-label={sourceLabel}
                    value={url}
                    placeholder={
                      canGitHub
                        ? "https://github.com/username/project"
                        : "https://docs.google.com/document/d/..."
                    }
                    onChange={(e) => {
                      setUrl(e.target.value);
                      setDirty(true);
                    }}
                  />
                </Field>
              ) : (
                <Field
                  label="Файл работы"
                  group
                  error={fileError}
                  hint="Markdown, PDF или DOCX, до 10 МБ. Ревьюер и модель получают сохранённый файл."
                >
                  <Drop
                    className={cx(
                      "upload",
                      dragging && "upload--over",
                      fileError && "upload--err",
                    )}
                    role="button"
                    tabIndex={action.busy || !canSubmit ? -1 : 0}
                    aria-disabled={action.busy || !canSubmit}
                    title={
                      file
                        ? file.name
                        : saved?.upload_id
                          ? (uploaded.data?.filename ?? "Файл сохранён")
                          : dragging
                            ? "Отпустите файл здесь"
                            : "Перетащите файл или выберите"
                    }
                    hint={
                      file
                        ? `${(file.size / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} КБ · ${action.busy ? "Загружаем…" : "Сохраним в черновике"}`
                        : saved?.upload_id
                          ? "Загружен в черновик. Нажмите, чтобы заменить."
                          : "Нажмите, чтобы выбрать файл"
                    }
                    onKeyDown={(e) => {
                      if (
                        e.target !== e.currentTarget ||
                        action.busy ||
                        !canSubmit
                      )
                        return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.currentTarget
                          .querySelector<HTMLInputElement>('input[type="file"]')
                          ?.click();
                      }
                    }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      if (!action.busy && canSubmit) setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDragging(false);
                      if (action.busy || !canSubmit) return;
                      if (e.dataTransfer.files.length !== 1) {
                        setFileError("Выберите один файл.");
                        return;
                      }
                      pickFile(e.dataTransfer.files[0]);
                    }}
                  >
                    <Inp
                      id={fileInputId}
                      className="sr-only"
                      tabIndex={-1}
                      type="file"
                      accept=".md,.pdf,.docx"
                      aria-label="Файл работы"
                      onChange={(e) => {
                        pickFile(e.target.files?.[0]);
                        e.target.value = "";
                      }}
                    />
                    {!file && uploaded.data && (
                      <a
                        href={safeUrl(uploaded.data.url)}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                      >
                        Открыть сохранённый файл ↗
                      </a>
                    )}
                  </Drop>
                </Field>
              )}
              <Field
                label="Комментарий к сдаче, необязательно"
                hint="Например: какие части делали с помощью ИИ и что дорабатывали руками"
              >
                <Area
                  className="submission-comment"
                  rows={2}
                  aria-label="Комментарий к сдаче, необязательно"
                  value={comment}
                  onChange={(e) => {
                    setComment(e.target.value);
                    setDirty(true);
                  }}
                />
              </Field>
              <small>
                {dirty
                  ? "Сохраняем изменения…"
                  : saved
                    ? "Черновик сохранён"
                    : ""}
              </small>
            </fieldset>
            <div className="student-form-actions">
              {!canSubmit && !history.loading && (
                <p className="caption">
                  Работа уже отправлена. Новую версию можно отправить после
                  возврата на доработку.
                </p>
              )}
              <div className="submission-actions">
                <Btn
                  variant="pri"
                  disabled={action.busy || !canSubmit || (!canFile && !canLink)}
                  onClick={() =>
                    void action.run(async () => {
                      try {
                        await submit();
                      } finally {
                        setStage("");
                      }
                    })
                  }
                >
                  {revision ? "Отправить исправления" : "Отправить на ревью"}
                </Btn>
                <div className="submission-actions__precheck">
                  <Btn
                    disabled={
                      action.busy ||
                      !canSubmit ||
                      (!canFile && !canLink) ||
                      (data.quota
                        ? data.quota.remaining === 0
                        : !data.policy ||
                          data.policy.self_review_limit === 0) ||
                      !!run
                    }
                    onClick={() =>
                      void action.run(async () => {
                        try {
                          const d = await prepare();
                          if (!d) return;
                          setStage("Запускаем самопроверку…");
                          const started = await ws.command(
                            "start_self_review",
                            d.id,
                            d.revision,
                            {},
                          );
                          setReviewTab("ai");
                          setCollapsed(true);
                          setRun(started.id);
                          setResult(started);
                        } finally {
                          setStage("");
                        }
                      })
                    }
                  >
                    ИИ-ревью
                  </Btn>
                  <div className="caption">
                    Результат ИИ-ревью видит ревьюер и учитывает при оценке.
                    Самопроверка необязательна.{" "}
                    {data.quota || !data.policy ? (
                      <Quota
                        compact
                        value={run ? (result?.quota ?? data.quota) : data.quota}
                      />
                    ) : (
                      <span>
                        Доступные попытки уточнятся после сохранения работы.
                      </span>
                    )}
                  </div>
                </div>
              </div>
              {stage && <p role="status">{stage}</p>}
            </div>
          </Card>
        </div>
        <aside className="stack">
          <Card
            title="Что будут проверять"
            bodyClassName="card__body--tight"
            actions={
              <Btn
                variant="link"
                size="s"
                aria-expanded={!rubricCollapsed}
                onClick={() => setRubricCollapsed((value) => !value)}
              >
                {rubricCollapsed ? "Развернуть" : "Свернуть"}
              </Btn>
            }
          >
            {!rubricCollapsed && (
              <>
                {data.criteria.map((c) => (
                  <div className="rubric-row" key={c.id}>
                    <span>{c.title}</span>
                    {"max_points" in c && typeof c.max_points === "number" && (
                      <span className="pill pill--mono">
                        {c.max_points.toLocaleString("ru-RU")} б.
                      </span>
                    )}
                  </div>
                ))}
                {data.max_score !== undefined && (
                  <div className="rubric-row">
                    <span>Всего</span>
                    <span className="points">{data.max_score} баллов</span>
                  </div>
                )}
              </>
            )}
          </Card>
          {data.policy && data.policy.penalty_per_day > 0 && (
            <p className="notice warn">
              За каждый день после срока снимается {data.policy.penalty_per_day}{" "}
              балла. Срок исправлений назначает ревьюер.
            </p>
          )}
        </aside>
      </div>
    </>
  );
}
function StudentSelfReview({
  ws,
  id,
  onComplete,
}: {
  ws: WorkspaceClient;
  id: string;
  onComplete: (value: W<"SelfReviewView">) => Promise<void>;
}) {
  const r = useResource(
    () => ws.selfReview(id),
    id,
    2000,
    (value) => value.disposition === "reserved",
  );
  const notified = useRef(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (r.data && r.data.disposition !== "reserved" && !notified.current) {
      notified.current = true;
      void onComplete(r.data).catch(() => setError(true));
    }
  }, [r.data, onComplete]);
  return (
    <>
      <Resource value={r}>
        {r.data && (
          <>
            <SelfReviewResult showHeading={false} value={r.data} />
          </>
        )}
      </Resource>
      {error && (
        <p role="alert">
          Не удалось обновить лимит. Обновите страницу перед следующим запуском.
        </p>
      )}
    </>
  );
}
