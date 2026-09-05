import { useState } from "react";
import type { ApiClient, Model, Role } from "../api/client";
import type { CommandPayloads } from "../api/commands";
import { Card, Empty, Resource, date, useAction, useResource } from "../ui";
type Draft = CommandPayloads["create_homework_version"];
export function HomeworkPage({
  api,
  id,
  run,
  role,
}: {
  api: ApiClient;
  id: string;
  run: string;
  role: Role;
}) {
  const s = useResource(() => api.homework(id), id);
  return (
    <Resource value={s}>
      {s.data && (
        <HomeworkEditor
          key={`${id}:${s.data.homework_revision}:${s.data.course_run_publications.length}`}
          api={api}
          history={s.data}
          run={run}
          role={role}
          refresh={s.refresh}
        />
      )}
    </Resource>
  );
}
function HomeworkEditor({
  api,
  history,
  run,
  role,
  refresh,
}: {
  api: ApiClient;
  history: Model<"HomeworkHistory">;
  run: string;
  role: Role;
  refresh: () => void;
}) {
  const latest = [...history.versions].sort(
    (a, b) => b.version_number - a.version_number,
  )[0];
  const [draft, setDraft] = useState<Draft>(
    latest
      ? {
          student_text: latest.student_text,
          max_score: latest.max_score,
          estimated_review_minutes: latest.estimated_review_minutes,
          artifact_kinds: latest.artifact_kinds,
          criteria: latest.criteria.map(
            ({ key, title, description, max_points }) => ({
              key,
              title,
              description,
              max_points,
            }),
          ),
        }
      : {
          student_text: "",
          max_score: 1,
          estimated_review_minutes: 30,
          artifact_kinds: ["github"],
          criteria: [
            { key: "criterion_1", title: "", description: "", max_points: 1 },
          ],
        },
  );
  const [submitDeadline, setSubmitDeadline] = useState("");
  const [reviewDeadline, setReviewDeadline] = useState("");
  const action = useAction();
  const [dirty, setDirty] = useState(false);
  function update(patch: Partial<Draft>) {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  }
  return (
    <>
      <p className="eyebrow">Задание</p>
      <h1>{latest ? `Версия ${latest.version_number}` : "Новое задание"}</h1>
      {action.feedback}
      <div className="two-col">
        <Card
          title={
            role === "methodologist" ? "Условия и критерии" : "Условия задания"
          }
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                await api.command(
                  "create_homework_version",
                  history.homework_id,
                  history.homework_revision,
                  draft,
                );
                refresh();
              });
            }}
          >
            <fieldset disabled={role !== "methodologist" || action.busy}>
              <label>
                Условие для студента
                <textarea
                  rows={6}
                  maxLength={100000}
                  value={draft.student_text}
                  onChange={(e) => update({ student_text: e.target.value })}
                />
              </label>
              <div className="two-col">
                <label>
                  Максимальный балл
                  <input
                    type="number"
                    min={0}
                    step="any"
                    required
                    value={draft.max_score}
                    onChange={(e) =>
                      update({ max_score: e.target.valueAsNumber })
                    }
                  />
                </label>
                <label>
                  Время на ревью, минут
                  <input
                    type="number"
                    min={1}
                    max={10080}
                    required
                    value={draft.estimated_review_minutes}
                    onChange={(e) =>
                      update({
                        estimated_review_minutes: e.target.valueAsNumber,
                      })
                    }
                  />
                </label>
              </div>
              <div className="actions">
                {(["github", "google_docs"] as const).map((kind) => (
                  <label className="check" key={kind}>
                    <input
                      type="checkbox"
                      checked={draft.artifact_kinds.includes(kind)}
                      onChange={(e) =>
                        update({
                          artifact_kinds: e.target.checked
                            ? [...draft.artifact_kinds, kind]
                            : draft.artifact_kinds.filter((k) => k !== kind),
                        })
                      }
                    />
                    {kind === "github" ? "GitHub" : "Google Docs"}
                  </label>
                ))}
              </div>
              {draft.criteria.map((c, i) => (
                <div className="criterion" key={i}>
                  <label>
                    Критерий {i + 1}
                    <input
                      required
                      maxLength={512}
                      value={c.title}
                      onChange={(e) =>
                        update({
                          criteria: draft.criteria.map((v, j) =>
                            i === j ? { ...v, title: e.target.value } : v,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Что считается выполненным
                    <textarea
                      value={c.description}
                      onChange={(e) =>
                        update({
                          criteria: draft.criteria.map((v, j) =>
                            i === j ? { ...v, description: e.target.value } : v,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Максимум баллов
                    <input
                      type="number"
                      min={0}
                      step="any"
                      required
                      value={c.max_points}
                      onChange={(e) =>
                        update({
                          criteria: draft.criteria.map((v, j) =>
                            i === j
                              ? { ...v, max_points: e.target.valueAsNumber }
                              : v,
                          ),
                        })
                      }
                    />
                  </label>
                </div>
              ))}
              <button
                type="button"
                onClick={() =>
                  update({
                    criteria: [
                      ...draft.criteria,
                      {
                        key: crypto.randomUUID(),
                        title: "",
                        description: "",
                        max_points: 1,
                      },
                    ],
                  })
                }
              >
                Добавить критерий
              </button>
              {role === "methodologist" && (
                <button
                  className="primary"
                  disabled={action.busy || draft.artifact_kinds.length === 0}
                >
                  Сохранить новую версию
                </button>
              )}
            </fieldset>
          </form>
        </Card>
        <div className="stack">
          {role === "methodologist" && (
            <Card title="Опубликовать в потоке">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void action.run(async () => {
                    if (!latest) throw new Error("Сначала сохраните версию.");
                    await api.command(
                      "publish_homework_version",
                      latest.id,
                      latest.revision,
                      {
                        course_run_id: run,
                        submission_deadline: new Date(
                          submitDeadline,
                        ).toISOString(),
                        review_deadline: new Date(reviewDeadline).toISOString(),
                      },
                    );
                    refresh();
                  });
                }}
              >
                <label>
                  Срок сдачи
                  <input
                    required
                    type="datetime-local"
                    value={submitDeadline}
                    onChange={(e) => setSubmitDeadline(e.target.value)}
                  />
                </label>
                <label>
                  Срок ревью
                  <input
                    required
                    type="datetime-local"
                    min={submitDeadline}
                    value={reviewDeadline}
                    onChange={(e) => setReviewDeadline(e.target.value)}
                  />
                </label>
                <p className="muted">
                  Время указано в часовом поясе браузера. Будет опубликована
                  сохранённая версия {latest?.version_number ?? "—"}.
                </p>
                <button
                  className="primary"
                  disabled={action.busy || !latest || !run || dirty}
                >
                  Опубликовать задание
                </button>
                {dirty && <p className="muted">Сначала сохраните изменения.</p>}
              </form>
            </Card>
          )}
          <Card title="История публикаций">
            {history.course_run_publications.map((p) => (
              <p key={p.id}>
                {p.is_current ? "Текущая публикация" : "Прошлая публикация"} ·{" "}
                {date(p.published_at)}
                <small>Срок сдачи: {date(p.submission_deadline)}</small>
              </p>
            ))}
            {history.course_run_publications.length === 0 && (
              <Empty>Задание ещё не опубликовано.</Empty>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
