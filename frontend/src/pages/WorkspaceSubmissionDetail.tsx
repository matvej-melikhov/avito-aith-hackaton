import { useState } from "react";
import { WorkspaceClient } from "../api/workspace";
import { Card, Empty, Resource, Status, date, useResource } from "../ui";
import { ScreenTitle } from "../workspace-ui";
import { OperationPanel } from "./Operations";
import { ArtifactLink } from "./WorkspaceReview";
export function WorkspaceSubmissionDetail({
  ws,
  id,
}: {
  ws: WorkspaceClient;
  id: string;
}) {
  const r = useResource(() => ws.submission(id), id, 5000);
  const [selected, setSelected] = useState("");
  return (
    <Resource value={r}>
      {r.data && (
        <>
          <ScreenTitle
            code={
              r.data.reviews.find(
                (v) => v.id === r.data!.current_publication_id,
              )?.decision === "needs_changes"
                ? "С3"
                : "С5"
            }
            title={r.data.title}
          />
          <div className="tabs">
            {r.data.attempts.map((a) => (
              <button
                key={a.id}
                aria-pressed={
                  (selected || r.data!.attempts.at(-1)?.id) === a.id
                }
                onClick={() => setSelected(a.id)}
              >
                Попытка {a.sequence}
              </button>
            ))}
          </div>
          {r.data.attempts
            .filter((a) => a.id === (selected || r.data!.attempts.at(-1)?.id))
            .map((a) => {
              const reviews = r.data!.reviews.filter(
                (v) => v.submission_version_id === a.id,
              );
              return (
                <div className="two-col" key={a.id}>
                  <Card title={`Сдача · ${date(a.submitted_at)}`}>
                    <Status value={a.status} />
                    {a.artifact_id && (
                      <ArtifactLink ws={ws} id={a.artifact_id} />
                    )}{" "}
                    {a.capture_operation_id && (
                      <OperationPanel
                        api={ws.core}
                        id={a.capture_operation_id}
                      />
                    )}
                  </Card>
                  <Card title="Опубликованный результат">
                    {reviews.length === 0 ? (
                      <Empty>Результат ещё не опубликован.</Empty>
                    ) : (
                      reviews.map((v) => (
                        <article className="list-item" key={v.id}>
                          <div className="row">
                            <strong>Оценка: {v.score}</strong>
                            {v.decision && <Status value={v.decision} />}
                          </div>
                          <p className="preserve">{v.feedback}</p>
                          {v.criteria.map((c, i) => (
                            <div className="criterion" key={i}>
                              <strong>
                                {c.title} · {c.points} / {c.max_points}
                              </strong>
                              <p>{c.reason}</p>
                            </div>
                          ))}
                          {v.revision_deadline && (
                            <p>Доработать до {date(v.revision_deadline)}</p>
                          )}
                          <small>
                            {date(v.published_at)}
                            {v.id === r.data!.current_publication_id
                              ? " · Текущий результат"
                              : " · Предыдущий результат"}
                          </small>
                        </article>
                      ))
                    )}
                  </Card>
                </div>
              );
            })}
          <a
            className="button primary"
            href={`#/prepare/${r.data.publication_id}`}
          >
            Подготовить новую версию
          </a>
        </>
      )}
    </Resource>
  );
}
