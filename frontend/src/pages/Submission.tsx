import { useState } from "react";
import type { ApiClient, Model } from "../api/client";
import {
  Card,
  Empty,
  Id,
  Resource,
  Status,
  date,
  go,
  useAction,
  useResource,
} from "../ui";
import { OperationPanel } from "./Operations";
export function SubmitPage({
  api,
  run,
  id,
}: {
  api: ApiClient;
  run: string;
  id: string;
}) {
  const state = useResource(async () => {
    const list = await api.homeworks(run);
    const homework = list.items.find((h) => h.course_run_homework_id === id);
    if (!homework) throw new Error("Задание не найдено в этом потоке.");
    const history = await api.homework(homework.homework_id);
    return {
      homework,
      version: history.versions.find(
        (v) => v.id === homework.current_version_id,
      ),
    };
  }, `${run}:${id}`);
  return (
    <Resource value={state}>
      {state.data && <SubmissionForm key={id} api={api} {...state.data} />}
    </Resource>
  );
}
export function SubmissionForm({
  api,
  homework,
  version,
}: {
  api: ApiClient;
  homework: Model<"HomeworkSummary">;
  version?: Model<"HomeworkVersionSummary">;
}) {
  const [url, setUrl] = useState("");
  const [capability, setCapability] = useState<Model<"ArtifactCapability">>();
  const action = useAction();
  return (
    <>
      <p className="eyebrow">Сдача работы</p>
      <h1>{homework.title}</h1>
      <div className="two-col">
        <Card title="Условия">
          <p className="preserve">{version?.student_text}</p>
          <dl>
            <dt>Сдать до</dt>
            <dd>{date(homework.submission_deadline)}</dd>
            <dt>Максимальная оценка</dt>
            <dd>{homework.max_score}</dd>
          </dl>
          {version?.criteria.map((c) => (
            <p key={c.id}>
              <strong>
                {c.title} · {c.max_points}
              </strong>
              <small>{c.description}</small>
            </p>
          ))}
        </Card>
        <Card title="Прикрепить работу">
          {action.feedback}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                const c = await api.command(
                  "preflight_submission",
                  homework.course_run_homework_id,
                  homework.revision,
                  { artifact_url: url },
                );
                setCapability(c);
              });
            }}
          >
            <label>
              Ссылка на{" "}
              {homework.artifact_kinds
                .map((k) => (k === "github" ? "GitHub" : "Google Docs"))
                .join(" или ")}
              <input
                required
                type="url"
                maxLength={2048}
                value={url}
                disabled={action.busy}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setCapability(undefined);
                }}
                placeholder="https://…"
              />
            </label>
            <p className="muted">
              Сначала проверим доступ. Работа отправится только после вашего
              нажатия «Сдать работу».
            </p>
            <button disabled={action.busy}>Проверить доступ</button>
          </form>
          {capability && (
            <div className="stack">
              <p>
                Чтение: <Status value={capability.read_capability} />
              </p>
              <p>
                Комментарии в источнике:{" "}
                <Status value={capability.feedback_capability} />
              </p>
              {capability.error && (
                <div className="notice warn">
                  {capability.error.message} {capability.error.action}
                </div>
              )}
              <button
                className="primary"
                disabled={
                  action.busy ||
                  capability.read_capability !== "available" ||
                  !capability.artifact_reference_id
                }
                onClick={() =>
                  void action.run(async () => {
                    const result = await api.command(
                      "submit_work",
                      capability.submission_id,
                      capability.submission_revision,
                      {
                        artifact_reference_id:
                          capability.artifact_reference_id!,
                      },
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
      </div>
    </>
  );
}
export function SubmissionPage({ api, id }: { api: ApiClient; id: string }) {
  const s = useResource(() => api.submission(id), id, 5000);
  return (
    <>
      <h1>Моя работа</h1>
      <p>
        <Id value={id} />
      </p>
      <Resource value={s}>
        {s.data && (
          <div className="two-col">
            <div className="stack">
              <Card title="Версии сдачи">
                {s.data.versions.map((v) => (
                  <article key={v.id} className="list-item">
                    <div className="row">
                      <strong>Попытка {v.sequence}</strong>
                      <Status value={v.status} />
                    </div>
                    <p>{date(v.submitted_at)}</p>
                    {v.capture_operation_id && (
                      <OperationPanel api={api} id={v.capture_operation_id} />
                    )}
                  </article>
                ))}
                {s.data.versions.length === 0 && (
                  <Empty>Работа ещё не отправлена.</Empty>
                )}
              </Card>
            </div>
            <Card title="Результаты проверки">
              {s.data.publications.length === 0 && (
                <Empty>Опубликованного ревью пока нет.</Empty>
              )}
              {s.data.publications.map((p) => {
                const revision = s.data!.review_revisions.find(
                  (r) => r.id === p.review_revision_id,
                );
                return (
                  <article key={p.id} className="list-item">
                    <strong>Оценка: {p.total_score}</strong>
                    <p className="preserve">{revision?.feedback}</p>
                    <small>{date(p.published_at)}</small>
                  </article>
                );
              })}
            </Card>
          </div>
        )}
      </Resource>
    </>
  );
}
