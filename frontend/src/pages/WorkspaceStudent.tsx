import { useState } from "react";
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
import {
  Quota,
  SelfReviewMonitor,
  SelfReviewResult,
  ScreenTitle,
  useDirtyGuard,
} from "../workspace-ui";
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
        <DraftForm
          key={`${id}:${r.data.draft?.revision ?? 0}`}
          ws={ws}
          data={r.data}
          session={session}
          refresh={r.refresh}
        />
      )}
    </Resource>
  );
}
function DraftForm({
  ws,
  data,
  session,
  refresh,
}: {
  ws: WorkspaceClient;
  data: W<"StudentContext">;
  session: Model<"Session">;
  refresh: () => void;
}) {
  const [url, setUrl] = useState(data.draft?.artifact_url ?? "");
  const [comment, setComment] = useState(data.draft?.comment ?? "");
  const [file, setFile] = useState<File>();
  const [source, setSource] = useState<"url" | "file">(
    data.draft?.upload_id ? "file" : "url",
  );
  const [saved, setSaved] = useState(data.draft);
  const [dirty, setDirty] = useState(false);
  const [run, setRun] = useState(data.quota?.active_run_id ?? undefined);
  const [fileReady, setFileReady] = useState(false);
  const [cap, setCap] = useState<Model<"ArtifactCapability">>();
  const action = useAction();
  useDirtyGuard(dirty);
  async function save() {
    let uploadId = data.draft?.upload_id ?? null;
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
    setDirty(false);
    return draft;
  }
  return (
    <>
      <ScreenTitle code="С1" title={data.title}>
        <Status value="draft" />
      </ScreenTitle>
      {action.feedback}
      <div className="two-col">
        <div className="stack">
          <Card title="Задание">
            <p className="preserve">{data.student_text}</p>
            <p>Сдать до {date(data.submission_deadline)}</p>
            {data.criteria.map((c) => (
              <p key={c.id}>{c.title}</p>
            ))}
          </Card>
          {data.self_reviews.length > 0 && (
            <Card title="История самопроверок">
              {data.self_reviews
                .filter((v) => v.id !== run)
                .map((v) => (
                  <SelfReviewResult key={v.id} value={v} />
                ))}
            </Card>
          )}
        </div>
        <div>
          <Card title="Ваша работа">
            <div className="tabs">
              <button
                type="button"
                aria-pressed={source === "url"}
                onClick={() => {
                  setSource("url");
                  setDirty(true);
                  setCap(undefined);
                  setFileReady(false);
                }}
              >
                Ссылка
              </button>
              <button
                type="button"
                aria-pressed={source === "file"}
                onClick={() => {
                  setSource("file");
                  setDirty(true);
                  setCap(undefined);
                  setFileReady(false);
                }}
              >
                Файлы
              </button>
            </div>
            <fieldset disabled={action.busy}>
              {source === "url" ? (
                <label>
                  GitHub или Google Docs
                  <input
                    type="url"
                    value={url}
                    onChange={(e) => {
                      setUrl(e.target.value);
                      setDirty(true);
                      setCap(undefined);
                      setFileReady(false);
                    }}
                  />
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
                      setCap(undefined);
                      setFileReady(false);
                    }}
                  />
                  {saved?.upload_id && !file && (
                    <span>Ранее загруженный файл сохранён.</span>
                  )}
                </label>
              )}
              <label>
                Комментарий к работе
                <textarea
                  value={comment}
                  onChange={(e) => {
                    setComment(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <button
                onClick={() =>
                  void action.run(async () => {
                    await save();
                  }, "Черновик сохранён.")
                }
              >
                Сохранить черновик
              </button>
            </fieldset>
            <p className="muted">
              Ревьюеру доступны результаты самопроверок и снимки работы.
              Самопроверка не отправляет работу на ревью.
            </p>
            {!run && <Quota value={data.quota} />}
            <div className="actions">
              <button
                disabled={
                  action.busy ||
                  !data.quota ||
                  data.quota.remaining === 0 ||
                  !!run
                }
                onClick={() =>
                  void action.run(async () => {
                    const d = dirty || !saved ? await save() : saved;
                    const started = await ws.command(
                      "start_self_review",
                      d.id,
                      d.revision,
                      {},
                    );
                    setRun(started.id);
                  })
                }
              >
                Проверить с AI
              </button>
              <button
                disabled={action.busy}
                onClick={() =>
                  void action.run(async () => {
                    const d = dirty || !saved ? await save() : saved;
                    if (d.upload_id) {
                      throw new Error(
                        "Отправка файла подключается через новый маршрут сдачи; самопроверка файла уже доступна.",
                      );
                    }
                    const current = await ws.core.homeworks(data.course_run_id);
                    const h = current.items.find(
                      (h) => h.course_run_homework_id === data.publication_id,
                    );
                    if (!h) throw new Error("Задание недоступно.");
                    setCap(
                      await ws.core.command(
                        "preflight_submission",
                        data.publication_id,
                        h.revision,
                        { artifact_url: d.artifact_url },
                      ),
                    );
                  })
                }
              >
                Подготовить сдачу
              </button>
            </div>
            {fileReady && saved && (
              <div className="notice">
                <p>Файл подготовлен. Отправить работу на ревью?</p>
                <button
                  className="primary"
                  disabled={action.busy || dirty}
                  onClick={() =>
                    void action.run(async () => {
                      const result = await ws.command(
                        "submit_uploaded_draft",
                        saved.id,
                        saved.revision,
                        {},
                      );
                      go(`/submissions/${result.id}`);
                    })
                  }
                >
                  Сдать работу
                </button>
              </div>
            )}
            {cap && (
              <div className="notice">
                <p>
                  Доступ к работе: <Status value={cap.read_capability} />
                </p>
                {cap.error && <p>{cap.error.message}</p>}
                <button
                  className="primary"
                  disabled={
                    action.busy ||
                    cap.read_capability !== "available" ||
                    !cap.artifact_reference_id
                  }
                  onClick={() =>
                    void action.run(async () => {
                      const result = await ws.core.command(
                        "submit_work",
                        cap.submission_id,
                        cap.submission_revision,
                        { artifact_reference_id: cap.artifact_reference_id! },
                      );
                      go(`/submissions/${result.submission_id}`);
                    })
                  }
                >
                  Сдать работу
                </button>
              </div>
            )}
          </Card>
          {run && (
            <Card title="Самопроверка">
              <SelfReviewMonitor
                ws={ws}
                id={run}
                onResult={() => {
                  setRun(undefined);
                  refresh();
                }}
              />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
