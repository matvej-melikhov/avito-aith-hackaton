import { useEffect, useRef, useState, type DragEvent } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, uploadFile, type W } from "../api/workspace";
import { ErrorBox, Resource, go, useAction, useResource } from "../ui";
import { Quota, SelfReviewResult, useDirtyGuard } from "../workspace-ui";
import { ArtifactLink } from "./WorkspaceReview";
import { PublishedStudentReview } from "./WorkspaceSubmissionDetail";
import {
  Acc,
  Band,
  BandMeta,
  BandVal,
  Btn,
  Callout,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Field,
  Inp,
  Kv,
  Main,
  Pill,
  Seg,
  Skel,
  Tab,
  Tabs,
  cheer,
  dayLong,
  dayNum,
  num,
  outOf,
  plural,
  points,
  workStatus,
  workTone,
  cx,
} from "../ds";

type StudentContext = W<"StudentContext">;
const FILE_TYPES = [".md", ".pdf", ".docx"];
const FILE_LIMIT = 10_000_000;

function bytes(n: number) {
  return n < 1_000_000
    ? `${Math.max(1, Math.round(n / 1000))} КБ`
    : `${(n / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
}
/** Проверка файла до загрузки: формат и размер, понятным языком. */
function checkFile(file: File) {
  const name = file.name.toLowerCase();
  if (!FILE_TYPES.some((ext) => name.endsWith(ext)))
    return "Подходят Markdown, PDF или DOCX. Другие форматы платформа не читает.";
  if (file.size > FILE_LIMIT)
    return "Файл больше 10 МБ. Сожмите или разбейте его.";
  return null;
}

export function WorkspaceSubmit({
  ws,
  id,
  session,
  attemptId,
}: {
  ws: WorkspaceClient;
  id: string;
  session: Model<"Session">;
  attemptId?: string;
}) {
  const r = useResource(() => ws.studentContext(id), id);
  if (r.loading || r.error)
    return (
      <>
        <Band>
          <span className="label">Домашка</span>
          <h1 className="d2">
            {r.error ? "Не удалось открыть" : "Загружаем…"}
          </h1>
        </Band>
        <Main page>
          {r.error ? (
            <ErrorBox error={r.error} retry={r.refresh} />
          ) : (
            <Skel lines={5} label="Загружаем домашку…" />
          )}
        </Main>
      </>
    );
  return (
    <DraftForm
      key={id}
      ws={ws}
      initial={r.data!}
      session={session}
      attemptId={attemptId}
    />
  );
}

/** Старые ссылки на отправленную работу открывают ту же страницу задания. */
export function StudentSubmissionPage({
  ws,
  id,
  session,
  attemptId,
}: {
  ws: WorkspaceClient;
  id: string;
  session: Model<"Session">;
  attemptId?: string;
}) {
  const data = useResource(() => ws.submission(id), id);
  return (
    <Resource value={data}>
      {data.data && (
        <WorkspaceSubmit
          ws={ws}
          id={data.data.publication_id}
          session={session}
          attemptId={attemptId}
        />
      )}
    </Resource>
  );
}

function DraftForm({
  ws,
  initial,
  session,
  attemptId,
}: {
  ws: WorkspaceClient;
  initial: StudentContext;
  session: Model<"Session">;
  attemptId?: string;
}) {
  const [data, setData] = useState(initial);
  const allowedSources = data.allowed_sources ?? [
    "upload",
    "github",
    "google_docs",
  ];
  const linkKinds = allowedSources.filter((kind) => kind !== "upload");
  const linkAllowed = linkKinds.length > 0;
  const fileAllowed = allowedSources.includes("upload");
  const [url, setUrl] = useState(initial.draft?.artifact_url ?? "");
  const [comment, setComment] = useState(initial.draft?.comment ?? "");
  const [file, setFile] = useState<File>();
  const [source, setSource] = useState<"url" | "file">(
    initial.draft?.upload_id && fileAllowed
      ? "file"
      : linkAllowed
        ? "url"
        : "file",
  );
  const [saved, setSaved] = useState(initial.draft);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
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
  const [openAttempts, setOpenAttempts] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setOpenAttempts({});
    if (attemptId) setReviewTab("human");
  }, [attemptId]);
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
    (v) =>
      v.id === history.data?.current_publication_id &&
      v.submission_version_id === history.data?.attempts.at(-1)?.id,
  );
  /* Имя и ссылка уже загруженного файла: черновик хранит только его id. */
  const uploaded = useResource(
    async () => (saved?.upload_id ? ws.download(saved.upload_id) : null),
    `upload:${saved?.upload_id ?? "none"}`,
  );
  useEffect(() => {
    if (!linkAllowed && fileAllowed && source === "url") setSource("file");
    if (!fileAllowed && linkAllowed && source === "file") setSource("url");
  }, [linkAllowed, fileAllowed, source]);
  function pickFile(next: File | undefined) {
    if (!fileAllowed || !maySubmit) return;
    if (!next) return;
    const problem = checkFile(next);
    setFileError(problem);
    if (problem) return;
    setFile(next);
    setSource("file");
    setDirty(true);
  }
  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    pickFile(e.dataTransfer.files?.[0]);
  }
  const revision = current?.decision === "needs_changes";
  const activeResult = result ?? data.self_reviews.at(-1);
  const attempts = history.data?.attempts ?? [];
  const lastAttempt = attempts.at(-1);
  // Статус домашки живёт в списке домашек студента, у контекста сдачи его нет.
  const listed = useResource(
    async () =>
      data.submission_id
        ? ((await ws.studentWorks({ limit: 100 })).items.find(
            (item) => item.submission_id === data.submission_id,
          ) ?? null)
        : null,
    `listed:${data.submission_id ?? "none"}`,
  );
  useDirtyGuard(dirty);
  const maySubmit =
    !data.submission_id || revision || listed.data?.status === "needs_changes";

  async function save() {
    if (!maySubmit)
      throw new Error(
        "Новая попытка доступна после возврата работы на доработку.",
      );
    if (
      (source === "url" && !linkAllowed) ||
      (source === "file" && !fileAllowed)
    )
      throw new Error("Этот тип ответа не разрешён для задания.");
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
    if (source === "file" && !uploadId)
      throw new Error("Приложите файл работы: Markdown, PDF или DOCX.");
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
    if (
      !dirty ||
      !maySubmit ||
      (source === "url" ? !linkAllowed : !fileAllowed)
    )
      return;
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
  }, [dirty, url, comment, file, source, linkAllowed, fileAllowed, maySubmit]);
  async function prepare() {
    const changedSource = !!saved && !!saved.upload_id !== (source === "file");
    const draft = dirty || !saved || changedSource ? await save() : saved;
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
    cheer(revision ? "Исправления отправлены" : "Работа отправлена на ревью");
    go(`/submissions/${submitted.id}`);
  }

  const screen = revision ? "С3" : activeResult || run ? "С2" : "С1";
  const bandStatus = revision
    ? { label: "Нужны правки", late: true }
    : lastAttempt
      ? {
          label:
            workStatus(
              current?.decision ?? listed.data?.status,
              lastAttempt.sequence,
            )?.label ?? "Сдана",
          late: false,
        }
      : activeResult || run
        ? { label: "Черновик", late: false }
        : null;
  // Работа уже у ревьюера: догонять её самопроверкой нечем, пока не вернут
  // на доработку.
  const awaitingReview = ["sent", "review", "rereview"].includes(
    workTone(
      current?.decision ?? listed.data?.status,
      lastAttempt?.sequence ?? 1,
    ) ?? "",
  );
  const canSelfReview =
    !action.busy &&
    maySubmit &&
    (source === "url" ? linkAllowed : fileAllowed) &&
    !run &&
    !awaitingReview &&
    (data.quota
      ? data.quota.remaining > 0
      : !!data.policy && data.policy.self_review_limit > 0);
  const hasChecks = !!(run || activeResult || attempts.length);
  const penalty = data.policy?.penalty_per_day ?? 0;

  return (
    <>
      <Band data-screen={screen}>
        {(data.course_title || data.run_title) && (
          <span className="label">
            {[data.course_title, data.run_title].filter(Boolean).join(", ")}
          </span>
        )}
        <h1 className="d2">{data.title}</h1>
        <BandMeta>
          <BandVal label={revision ? "Прислать исправления до" : "Сдать до"}>
            {dayLong(current?.revision_deadline ?? data.submission_deadline)}
          </BandVal>
          {data.max_score !== undefined && (
            <BandVal label={data.policy ? "Порог зачёта" : "Максимум"}>
              {data.policy
                ? outOf(data.policy.pass_score, data.max_score)
                : `${num(data.max_score)} ${plural(data.max_score, "балл", "балла", "баллов")}`}
            </BandVal>
          )}
          {bandStatus && (
            <BandVal label="Статус" late={bandStatus.late}>
              {bandStatus.label}
            </BandVal>
          )}
        </BandMeta>
      </Band>
      <Main page data-screen={screen}>
        {action.feedback}
        {!!history.error && (
          <ErrorBox error={history.error} retry={history.refresh} />
        )}
        {attemptId &&
          history.data &&
          !attempts.some((attempt) => attempt.id === attemptId) && (
            <Callout tone="info">
              <p>
                Указанная попытка не найдена. Выберите попытку в истории ниже.
              </p>
            </Callout>
          )}
        <div className="row-side">
          <div className="stack">
            <Card>
              <CardHead title="Задание">
                <Btn
                  size="s"
                  variant="link"
                  aria-expanded={!collapsed}
                  onClick={() => setCollapsed((v) => !v)}
                >
                  {collapsed ? "Развернуть" : "Свернуть"}
                </Btn>
              </CardHead>
              {!collapsed && (
                <CardBody prose>
                  <p className="preserve">{data.student_text}</p>
                  {!!data.material_upload_ids?.length && (
                    <p className="btn-row">
                      {data.material_upload_ids.map((id) => (
                        <ArtifactLink key={id} ws={ws} id={id} />
                      ))}
                    </p>
                  )}
                </CardBody>
              )}
            </Card>

            {hasChecks && (
              <Card>
                <Tabs className="tabs--card" label="Проверки">
                  <Tab
                    on={reviewTab === "ai"}
                    onClick={() => setReviewTab("ai")}
                  >
                    ИИ-ревью
                  </Tab>
                  <Tab
                    on={reviewTab === "human"}
                    onClick={() => setReviewTab("human")}
                  >
                    Ревью
                  </Tab>
                </Tabs>
                <CardBody>
                  {reviewTab === "ai" ? (
                    <>
                      {data.self_reviews
                        .filter(
                          (value) =>
                            value.id !== run &&
                            (!!run || value.id !== activeResult?.id),
                        )
                        .map((value) => (
                          <Acc
                            key={value.id}
                            className="acc--pill"
                            head={
                              <span className="acc__t">
                                Проверка от {dayNum(value.created_at)}
                              </span>
                            }
                          >
                            <SelfReviewResult value={value} />
                          </Acc>
                        ))}
                      {run ? (
                        <Acc
                          className="acc--pill"
                          defaultOpen
                          head={
                            <>
                              <span className="acc__t">Текущая проверка</span>
                              <Pill dot>идёт</Pill>
                            </>
                          }
                        >
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
                        </Acc>
                      ) : activeResult ? (
                        <Acc
                          key={activeResult.id}
                          className="acc--pill"
                          defaultOpen
                          head={
                            <>
                              <span className="acc__t">
                                Проверка от {dayNum(activeResult.created_at)}
                              </span>
                              <Pill>последняя</Pill>
                            </>
                          }
                        >
                          {(dirty ||
                            saved?.revision !==
                              activeResult.draft_revision) && (
                            <p className="caption self-review__stale">
                              После проверки работа изменена. Результат
                              относится к сохранённому снимку.
                            </p>
                          )}
                          <SelfReviewResult value={activeResult} />
                        </Acc>
                      ) : (
                        <p className="small dim">ИИ-ревью не запускалось.</p>
                      )}
                    </>
                  ) : attempts.length ? (
                    attempts.map((attempt, index) => {
                      const published = history.data!.reviews.filter(
                        (value) => value.submission_version_id === attempt.id,
                      );
                      const latest = published.at(-1);
                      const last = index === attempts.length - 1;
                      return (
                        <Acc
                          key={attempt.id}
                          className="acc--pill"
                          open={
                            openAttempts[attempt.id] ??
                            (attemptId ? attempt.id === attemptId : last)
                          }
                          onToggle={(open) =>
                            setOpenAttempts((previous) =>
                              previous[attempt.id] === open
                                ? previous
                                : { ...previous, [attempt.id]: open },
                            )
                          }
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
                          {attempt.artifact_id && (
                            <Kv label="Отправленная работа">
                              <ArtifactLink ws={ws} id={attempt.artifact_id} />
                            </Kv>
                          )}
                          {attempt.comment && (
                            <>
                              <div className="label attempt__label">
                                Комментарий к сдаче
                              </div>
                              <p className="small dim preserve">
                                {attempt.comment}
                              </p>
                            </>
                          )}
                          {latest ? (
                            <PublishedStudentReview value={latest} />
                          ) : (
                            <p className="small dim">
                              Результат ещё не опубликован.
                            </p>
                          )}
                          {published.length > 1 && (
                            <Acc
                              className="acc--nested"
                              head={
                                <span className="acc__t small">
                                  Предыдущие публикации этой попытки
                                </span>
                              }
                            >
                              {published.slice(0, -1).map((value) => (
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
                    <p className="small dim">
                      Результат появится после отправки работы и публикации
                      ревью.
                    </p>
                  )}
                </CardBody>
              </Card>
            )}

            <Card hard>
              <CardHead
                title={revision ? "Исправленная версия" : "Ваша работа"}
              >
                <Seg
                  label="Как сдаём"
                  value={source}
                  disabled={action.busy || !maySubmit}
                  onChange={(value) => {
                    setSource(value);
                    setDirty(true);
                  }}
                  options={[
                    ...(linkAllowed
                      ? [
                          {
                            value: "url" as const,
                            label: "Ссылка",
                          },
                        ]
                      : []),
                    ...(fileAllowed
                      ? [{ value: "file" as const, label: "Файл" }]
                      : []),
                  ]}
                />
              </CardHead>
              <CardBody compact>
                {!maySubmit && (
                  <Callout tone="info">
                    <p>
                      {awaitingReview
                        ? "Работа уже отправлена на проверку. Результат появится здесь."
                        : "Новая попытка доступна после возврата работы на доработку."}
                    </p>
                  </Callout>
                )}
                {maySubmit && attempts.length > 0 && (
                  <p className="caption">
                    Новый ответ будет сохранён отдельной попыткой. Предыдущие
                    попытки не изменятся.
                  </p>
                )}
                <fieldset
                  className="acc-list"
                  disabled={action.busy || !maySubmit}
                >
                  {source === "url" ? (
                    <Field
                      label="Ссылка на репозиторий или Google Docs"
                      hint={
                        revision && lastAttempt
                          ? `Версия попытки ${lastAttempt.sequence} сохранится в истории, ревьюер увидит обе.`
                          : "Откройте доступ к работе по ссылке. При отправке сохраняется отдельный снимок."
                      }
                    >
                      <Inp
                        mono
                        type="url"
                        value={url}
                        placeholder={
                          linkKinds.length === 1 &&
                          linkKinds[0] === "google_docs"
                            ? "https://docs.google.com/document/d/…"
                            : "https://github.com/username/project"
                        }
                        onChange={(e) => {
                          setUrl(e.target.value);
                          setDirty(true);
                        }}
                      />
                    </Field>
                  ) : (
                    <div className="field">
                      <span className="field__lbl" id="upload-lbl">
                        Файл работы
                      </span>
                      <label
                        className={cx(
                          "drop upload",
                          (file || saved?.upload_id) && "upload--filled",
                          dragging && "upload--over",
                          fileError && "upload--err",
                        )}
                        onDragOver={(e) => {
                          e.preventDefault();
                          setDragging(true);
                        }}
                        onDragLeave={() => setDragging(false)}
                        onDrop={onDrop}
                      >
                        <input
                          ref={fileInput}
                          className="sr-only"
                          type="file"
                          accept={FILE_TYPES.join(",")}
                          aria-labelledby="upload-lbl"
                          onChange={(e) => {
                            pickFile(e.target.files?.[0]);
                            e.target.value = "";
                          }}
                        />
                        {file ? (
                          <>
                            <span className="upload__icon" aria-hidden="true">
                              <svg viewBox="0 0 16 16" width="14" height="14">
                                <path
                                  d="M8 13V3.5M4.5 7 8 3.5 11.5 7"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="1.8"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            </span>
                            <span className="upload__body">
                              <b>{file.name}</b>
                              <span>
                                {bytes(file.size)} ·{" "}
                                {action.busy ? "загружаем…" : "загрузится сам"}
                              </span>
                            </span>
                            <span className="btn btn--s btn--quiet">
                              Заменить
                            </span>
                          </>
                        ) : saved?.upload_id ? (
                          <>
                            <span
                              className="upload__icon upload__icon--ok"
                              aria-hidden="true"
                            >
                              <svg viewBox="0 0 16 16" width="14" height="14">
                                <path
                                  d="M3.2 8.6l3 3 6.6-6.8"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                />
                              </svg>
                            </span>
                            <span className="upload__body">
                              <b>
                                {uploaded.data?.filename ?? "Файл загружен"}
                              </b>
                              <span>
                                Загружен и сохранён в черновике
                                {uploaded.data && (
                                  <>
                                    {" · "}
                                    <a
                                      href={uploaded.data.url}
                                      target="_blank"
                                      rel="noreferrer"
                                      onClick={(e) => e.stopPropagation()}
                                    >
                                      открыть ↗
                                    </a>
                                  </>
                                )}
                              </span>
                            </span>
                            <span className="btn btn--s btn--quiet">
                              Заменить
                            </span>
                          </>
                        ) : (
                          <>
                            <b>
                              {dragging
                                ? "Отпустите файл здесь"
                                : "Перетащите файл или выберите"}
                            </b>
                            <span>Markdown, PDF или DOCX, до 10 МБ</span>
                          </>
                        )}
                      </label>
                      {fileError ? (
                        <span className="field__err" role="alert">
                          {fileError}
                        </span>
                      ) : (
                        <span className="field__hint">
                          {revision && lastAttempt
                            ? `Версия попытки ${lastAttempt.sequence} сохранится в истории, ревьюер увидит обе.`
                            : "Ревьюер и модель читают именно этот файл. Ссылки внутри файла не открываются."}
                        </span>
                      )}
                    </div>
                  )}
                  <Field label="Комментарий к сдаче, необязательно">
                    <Inp
                      value={comment}
                      placeholder="Например: какие части делали с помощью ИИ и что дорабатывали руками"
                      onChange={(e) => {
                        setComment(e.target.value);
                        setDirty(true);
                      }}
                    />
                  </Field>
                  {(dirty || saved) && (
                    <span className="caption" role="status">
                      {dirty ? "Сохраняем изменения…" : "Черновик сохранён"}
                    </span>
                  )}
                </fieldset>
              </CardBody>
              <CardFoot>
                <div className="submission-actions">
                  <Btn
                    variant="pri"
                    disabled={
                      action.busy ||
                      !maySubmit ||
                      !(source === "url" ? linkAllowed : fileAllowed)
                    }
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
                  </Btn>
                  <div className="submission-actions__precheck">
                    <Btn
                      aria-label={
                        activeResult
                          ? "Проверить повторно"
                          : "Проверить перед сдачей"
                      }
                      disabled={!canSelfReview}
                      title={
                        awaitingReview
                          ? "Работа уже на ревью, ИИ-ревью запускается до отправки"
                          : undefined
                      }
                      onClick={() =>
                        void action.run(async () => {
                          try {
                            const d = await prepare();
                            if (!d) return;
                            setStage("Запускаем ИИ-ревью…");
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
                    <span
                      className="caption"
                      role={stage ? "status" : undefined}
                    >
                      {stage ? (
                        stage
                      ) : run ? (
                        "Проверяем работу, это займёт около минуты."
                      ) : awaitingReview ? (
                        "Работа отправлена на ревью. ИИ-ревью запускается до отправки."
                      ) : (
                        <>
                          {
                            "Результат ИИ-ревью видит ревьюер и учитывает при оценке. "
                          }
                          {data.quota || !data.policy ? (
                            <Quota value={data.quota} />
                          ) : (
                            "Доступные попытки уточнятся после сохранения работы."
                          )}
                        </>
                      )}
                    </span>
                  </div>
                </div>
              </CardFoot>
            </Card>
          </div>

          {/* Критерии студенту не показываем: по ним можно подогнать работу под
              проверку. Студент видит только текст задания, порог и максимум. */}
          <div className="stack">
            {penalty > 0 && (
              <Callout tone="warn">
                <p>
                  За каждый день после срока снимается {num(penalty)}{" "}
                  {plural(penalty, "балл", "балла", "баллов")}. Работу можно
                  пересдать после замечаний ревьюера, срок пересдачи назначает
                  ревьюер.
                </p>
              </Callout>
            )}
          </div>
        </div>
      </Main>
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
            <SelfReviewResult value={r.data} />
            <div className="caption self-review__foot">
              <Quota value={r.data.quota} />
            </div>
          </>
        )}
      </Resource>
      {error && (
        <Callout tone="warn" role="alert">
          <p>
            Не удалось обновить лимит. Обновите страницу перед следующим
            запуском.
          </p>
        </Callout>
      )}
    </>
  );
}
