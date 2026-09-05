import { useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, type W } from "../api/workspace";
import {
  Card,
  Resource,
  Status,
  go,
  safeUrl,
  useAction,
  useResource,
} from "../ui";
import { SelfReviewResult } from "../workspace-ui";
export function ReviewWorkspacePanels({
  ws,
  detail,
  readOnly,
  refresh,
}: {
  ws: WorkspaceClient;
  detail: Model<"ReviewDetail">;
  readOnly: boolean;
  refresh: () => void;
}) {
  const r = useResource(
    () => ws.reviewContext(detail.review_iteration_id),
    `${detail.review_iteration_id}:${detail.revision}`,
  );
  const action = useAction();
  const [reason, setReason] = useState("");
  const [decision, setDecision] =
    useState<W<"OutcomeInput">["decision"]>("needs_changes");
  const [deadline, setDeadline] = useState("");
  return (
    <Resource value={r}>
      {r.data && (
        <div className="two-col">
          <div className="stack">
            <Card title="Самопроверки студента">
              {r.data.self_reviews.length === 0 ? (
                <p>Студент не запускал самопроверку.</p>
              ) : (
                r.data.self_reviews.map((run) => (
                  <div key={run.id}>
                    <SelfReviewResult value={run} />
                    {run.artifact_id && (
                      <ArtifactLink ws={ws} id={run.artifact_id} />
                    )}
                  </div>
                ))
              )}
            </Card>
            {r.data.private_details && (
              <Card title="Рекомендации ревьюерам">
                <p className="preserve">
                  {r.data.private_details.reviewer_guidance}
                </p>
                {r.data.private_details.reference_upload_id && (
                  <ArtifactLink
                    ws={ws}
                    id={r.data.private_details.reference_upload_id}
                  />
                )}
              </Card>
            )}
          </div>
          <div className="stack">
            {action.feedback}
            {r.data.outcome && (
              <Card title="Решение">
                <Status value={r.data.outcome.decision} />
                <p>{r.data.outcome.reason}</p>
              </Card>
            )}
            {!readOnly &&
              !["published", "canceled"].includes(detail.status) && (
                <Card title="Итог проверки">
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void action.run(async () => {
                        await ws.command(
                          "save_review_outcome",
                          detail.review_iteration_id,
                          r.data!.outcome_revision,
                          {
                            decision,
                            revision_deadline: deadline
                              ? new Date(deadline).toISOString()
                              : null,
                            reason,
                          },
                        );
                        refresh();
                      });
                    }}
                  >
                    <label>
                      Решение
                      <select
                        value={decision}
                        onChange={(e) =>
                          setDecision(e.target.value as typeof decision)
                        }
                      >
                        <option value="needs_changes">Нужны правки</option>
                        <option value="passed">Зачтено</option>
                        <option value="failed">Не зачтено</option>
                      </select>
                    </label>
                    {decision === "needs_changes" && (
                      <label>
                        Срок доработки
                        <input
                          type="datetime-local"
                          required
                          value={deadline}
                          onChange={(e) => setDeadline(e.target.value)}
                        />
                      </label>
                    )}
                    <label>
                      Причина
                      <textarea
                        required
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </label>
                    <p className="muted">
                      Решение станет видно студенту после публикации ревью.
                    </p>
                    <button
                      disabled={
                        action.busy || !detail.current_review_revision_id
                      }
                    >
                      Сохранить решение
                    </button>
                  </form>
                </Card>
              )}
            {!readOnly && detail.status === "published" && (
              <Card title="Исправить опубликованное ревью">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action.run(async () => {
                      const corrected = await ws.core.command(
                        "create_review_correction",
                        detail.review_iteration_id,
                        detail.revision,
                        {
                          published_review_revision_id:
                            detail.current_review_revision_id!,
                          reason,
                        },
                      );
                      go(`/reviews/${corrected.review_iteration_id}`);
                    });
                  }}
                >
                  <label>
                    Причина исправления
                    <textarea
                      required
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </label>
                  <button disabled={action.busy}>Создать исправление</button>
                </form>
              </Card>
            )}
            {!readOnly &&
              !["published", "canceled"].includes(detail.status) && (
                <RequirementsMigration
                  ws={ws}
                  detail={detail}
                  homework={r.data.homework_id}
                />
              )}
          </div>
        </div>
      )}
    </Resource>
  );
}
function RequirementsMigration({
  ws,
  detail,
  homework,
}: {
  ws: WorkspaceClient;
  detail: Model<"ReviewDetail">;
  homework: string;
}) {
  const r = useResource(() => ws.core.homework(homework), homework);
  const [version, setVersion] = useState("");
  const action = useAction();
  return (
    <Card title="Новая версия требований">
      {action.feedback}
      <Resource value={r}>
        <label>
          Версия
          <select value={version} onChange={(e) => setVersion(e.target.value)}>
            <option value="">Выбрать</option>
            {r.data?.versions
              .filter(
                (v) => v.id !== detail.immutable_inputs.homework_version_id,
              )
              .map((v) => (
                <option value={v.id} key={v.id}>
                  Версия {v.version_number}
                </option>
              ))}
          </select>
        </label>
        <button
          disabled={!version || action.busy}
          onClick={() =>
            void action.run(async () => {
              const selected = r.data!.versions.find((v) => v.id === version)!;
              const result = await ws.core.command(
                "migrate_review_requirements",
                detail.review_iteration_id,
                detail.revision,
                {
                  homework_version_id: selected.id,
                  criterion_set_id: selected.criterion_set_id,
                },
              );
              go(`/reviews/${result.review_iteration_id}`);
            })
          }
        >
          Открыть ревью по новым требованиям
        </button>
      </Resource>
    </Card>
  );
}
export function ArtifactLink({ ws, id }: { ws: WorkspaceClient; id: string }) {
  const [url, setUrl] = useState("");
  const action = useAction();
  return (
    <>
      {action.feedback}
      {url ? (
        <a
          className="button"
          href={safeUrl(url)}
          target="_blank"
          rel="noreferrer"
        >
          Открыть снимок ↗
        </a>
      ) : (
        <button
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => setUrl((await ws.download(id)).url))
          }
        >
          Получить ссылку на снимок
        </button>
      )}
    </>
  );
}
