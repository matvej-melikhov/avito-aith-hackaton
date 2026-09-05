import { useEffect, useRef, useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, uploadFile, type W } from "../api/workspace";
import {
  Card,
  Resource,
  Status,
  date,
  go,
  useAction,
  useResource,
} from "../ui";
import { Quota, SelfReviewResult, useDirtyGuard } from "../workspace-ui";
import { ArtifactLink } from "./WorkspaceReview";
import { PublishedStudentReview } from "./WorkspaceSubmissionDetail";

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
  const [url, setUrl] = useState(initial.draft?.artifact_url ?? "");
  const [comment, setComment] = useState(initial.draft?.comment ?? "");
  const [file, setFile] = useState<File>();
  const [source, setSource] = useState<"url" | "file">(
    initial.draft?.upload_id ? "file" : "url",
  );
  const [saved, setSaved] = useState(initial.draft);
  const [dirty, setDirty] = useState(false);
  const [run, setRun] = useState(initial.quota?.active_run_id ?? undefined);
  const [result, setResult] = useState<W<"SelfReviewView">>();
  const [collapsed, setCollapsed] = useState(
    initial.self_reviews.length > 0 ||
      !!initial.submission_id ||
      !!initial.quota?.active_run_id,
  );
  const [stage, setStage] = useState("");
  const [reviewTab, setReviewTab] = useState<"ai" | "human">(
    initial.submission_id ? "human" : "ai",
  );
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
  const revision = current?.decision === "needs_changes";
  const activeResult = result ?? data.self_reviews.at(-1);
  useDirtyGuard(dirty);
  async function save() {
    let uploadId = saved?.upload_id ?? null;
    if (source === "url") {
      let parsed: URL;
      try {
        parsed = new URL(url);
      } catch {
        throw new Error("Укажите полную ссылку на работу.");
      }
      if (!["http:", "https:"].includes(parsed.protocol))
        throw new Error("Нужна ссылка HTTP или HTTPS.");
    }
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
    if (!dirty) return;
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
  }, [dirty, url, comment, file, source]);
  async function prepare() {
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
                {date(current?.revision_deadline ?? data.submission_deadline)}
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
            {revision && <Status value="needs_changes" />}
          </div>
        </div>
      </section>
      {action.feedback}
      {history.error && <Resource value={history}>{null}</Resource>}
      <div className="student-grid">
        <div className="stack">
          <Card
            title="Задание"
            actions={
              <button
                aria-expanded={!collapsed}
                onClick={() => setCollapsed((v) => !v)}
              >
                {collapsed ? "Развернуть" : "Свернуть"}
              </button>
            }
          >
            {!collapsed && (
              <>
                <p className="preserve">{data.student_text}</p>
                {(data.material_upload_ids ?? []).map((id) => (
                  <ArtifactLink key={id} ws={ws} id={id} />
                ))}
              </>
            )}
          </Card>
          {(run || activeResult || history.data?.attempts.length) && (
            <section className="card student-review-tabs">
              <div
                className="tabs"
                style={{ padding: "var(--s-2) var(--s-5) 0", marginBottom: 0 }}
              >
                <button
                  aria-pressed={reviewTab === "ai"}
                  onClick={() => setReviewTab("ai")}
                >
                  ИИ-ревью
                </button>
                <button
                  aria-pressed={reviewTab === "human"}
                  onClick={() => setReviewTab("human")}
                >
                  Ревью
                </button>
              </div>
              <div className="card-body">
                {reviewTab === "ai" ? (
                  <>
                    {data.self_reviews
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
                          <SelfReviewResult value={value} />
                        </details>
                      ))}
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
                        <SelfReviewResult value={activeResult} />
                      </details>
                    ) : (
                      <p>Самопроверка не запускалась.</p>
                    )}
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
            title={revision ? "Исправленная версия" : "Ваша работа"}
            actions={
              <div className="actions">
                <button
                  disabled={action.busy}
                  aria-pressed={source === "url"}
                  onClick={() => {
                    setSource("url");
                    setDirty(true);
                  }}
                >
                  Ссылка
                </button>
                <button
                  disabled={action.busy}
                  aria-pressed={source === "file"}
                  onClick={() => {
                    setSource("file");
                    setDirty(true);
                  }}
                >
                  Файлы
                </button>
              </div>
            }
          >
            <fieldset disabled={action.busy}>
              {source === "url" ? (
                <label>
                  Ссылка на репозиторий или Google Docs
                  <input
                    type="url"
                    aria-label="Ссылка на репозиторий или Google Docs"
                    value={url}
                    placeholder="https://github.com/username/project"
                    onChange={(e) => {
                      setUrl(e.target.value);
                      setDirty(true);
                    }}
                  />
                  <small>
                    Откройте доступ к работе по ссылке. При отправке сохраняется
                    отдельный снимок.
                  </small>
                </label>
              ) : (
                <label>
                  Markdown, PDF или DOCX, до 10 МБ
                  <input
                    type="file"
                    accept=".md,.pdf,.docx"
                    onChange={(e) => {
                      setFile(e.target.files?.[0]);
                      setDirty(true);
                    }}
                  />
                  {saved?.upload_id && !file && (
                    <small>Ранее загруженный файл сохранён.</small>
                  )}
                </label>
              )}
              <label>
                Комментарий к сдаче, необязательно
                <textarea
                  value={comment}
                  placeholder="Что доработали и что стоит учесть при проверке"
                  onChange={(e) => {
                    setComment(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <small>
                {dirty
                  ? "Сохраняем изменения…"
                  : saved
                    ? "Черновик сохранён"
                    : ""}
              </small>
            </fieldset>
            <div className="student-form-actions">
              <div className="actions">
                <button
                  className="primary"
                  disabled={action.busy}
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
                  {revision
                    ? "Отправить исправленную версию"
                    : "Отправить на ревью"}
                </button>
                <button
                  disabled={
                    action.busy ||
                    (data.quota
                      ? data.quota.remaining === 0
                      : !data.policy || data.policy.self_review_limit === 0) ||
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
                  {activeResult
                    ? "Проверить повторно"
                    : "Проверить перед сдачей"}
                </button>
              </div>
              {stage && <p role="status">{stage}</p>}
              {!run &&
                (data.quota || !data.policy ? (
                  <Quota value={data.quota} />
                ) : (
                  <p className="caption">
                    Доступные попытки уточнятся после сохранения работы.
                  </p>
                ))}
              <p className="muted">
                Самопроверка необязательна. Результат и число запусков видит
                ревьюер. Оценку выставляет человек.
              </p>
            </div>
          </Card>
        </div>
        <aside className="stack">
          <Card title="Что будут проверять">
            {data.criteria.map((c) => (
              <div className="rubric-row" key={c.id}>
                <span>{c.title}</span>
                {"max_points" in c && typeof c.max_points === "number" && (
                  <span className="points">{c.max_points} б.</span>
                )}
              </div>
            ))}
            {data.max_score !== undefined && (
              <div className="rubric-row">
                <span>Всего</span>
                <span className="points">{data.max_score} баллов</span>
              </div>
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
            <Quota value={r.data.quota} />
            <SelfReviewResult value={r.data} />
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
