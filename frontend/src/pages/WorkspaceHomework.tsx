import { useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, uploadFile, type W } from "../api/workspace";
import { Card, Resource, date, useAction, useResource } from "../ui";
import { ScreenTitle, useDirtyGuard } from "../workspace-ui";
export function WorkspaceHomework({
  ws,
  id,
  run,
  session,
}: {
  ws: WorkspaceClient;
  id: string;
  run: string;
  session: Model<"Session">;
}) {
  const r = useResource(async () => {
    const [history, editor] = await Promise.all([
      ws.core.homework(id),
      ws.editorDraft(id, run),
    ]);
    const latest = [...history.versions].sort(
      (a, b) => b.version_number - a.version_number,
    )[0];
    const publication = history.course_run_publications.find(
      (p) => p.is_current && p.course_run_id === run,
    );
    const [privateDetails, policy] = await Promise.all([
      latest ? ws.privateHomework(latest.id) : Promise.resolve(null),
      publication
        ? ws.policy(publication.course_run_homework_id)
        : Promise.resolve(null),
    ]);
    return { history, editor, latest, privateDetails, policy, publication };
  }, `${id}:${run}`);
  return (
    <Resource value={r}>
      {r.data && (
        <HomeworkWizard
          key={`${id}:${run}`}
          ws={ws}
          data={r.data}
          run={run}
          session={session}
        />
      )}
    </Resource>
  );
}
type WizardData = {
  history: Model<"HomeworkHistory">;
  editor: W<"EditorDraftView">;
  latest?: Model<"HomeworkVersionSummary">;
  privateDetails: W<"PrivateHomeworkView"> | null;
  policy: W<"PublicationPolicyView"> | null;
  publication?: Model<"CourseRunHomeworkPublicationSummary">;
};
function HomeworkWizard({
  ws,
  data,
  run,
  session,
}: {
  ws: WorkspaceClient;
  data: WizardData;
  run: string;
  session: Model<"Session">;
}) {
  const initial: W<"EditorDraftInput"> = data.editor.value ?? {
    course_run_id: run,
    student_text: data.latest?.student_text ?? "",
    max_score: data.latest?.max_score ?? 0,
    estimated_review_minutes: data.latest?.estimated_review_minutes ?? 30,
    artifact_kinds: data.latest?.artifact_kinds ?? ["github", "google_docs"],
    criteria:
      data.latest?.criteria.map((c) => ({
        key: c.key,
        title: c.title,
        description: c.description,
        max_points: c.max_points,
        check_class:
          data.privateDetails?.criterion_classes?.[c.key] ?? "content",
      })) ?? [],
    reviewer_guidance: data.privateDetails?.reviewer_guidance ?? "",
    reference_upload_id: data.privateDetails?.reference_upload_id ?? null,
    policy: data.policy
      ? {
          self_review_limit: data.policy.self_review_limit,
          pass_score: data.policy.pass_score ?? 0,
          penalty_per_day: data.policy.penalty_per_day ?? 0,
          revision_days: data.policy.revision_days ?? 7,
          max_resubmissions: data.policy.max_resubmissions ?? 3,
        }
      : null,
    submission_deadline: data.publication?.submission_deadline ?? null,
    review_deadline: data.publication?.review_deadline ?? null,
  };
  const [draft, setDraft] = useState(initial);
  const [editorRevision, setEditorRevision] = useState(data.editor.revision);
  const [homeworkRevision, setHomeworkRevision] = useState(
    data.history.homework_revision,
  );
  const [version, setVersion] = useState(data.latest);
  const [step, setStep] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [reference, setReference] = useState<File>();
  const [published, setPublished] = useState<string>();
  const [policyRevision, setPolicyRevision] = useState(
    data.policy?.revision ?? 0,
  );
  const action = useAction();
  useDirtyGuard(dirty);
  function change(patch: Partial<W<"EditorDraftInput">>) {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  }
  function policy(patch: Partial<W<"PublicationPolicyInput">>) {
    change({
      policy: {
        self_review_limit: draft.policy?.self_review_limit ?? 0,
        pass_score: draft.policy?.pass_score ?? 0,
        revision_days: draft.policy?.revision_days ?? 7,
        penalty_per_day: draft.policy?.penalty_per_day ?? 0,
        max_resubmissions: draft.policy?.max_resubmissions ?? 3,
        ...patch,
      },
    });
  }
  async function saveDraft(value = draft) {
    let next = value;
    if (reference) {
      const file = await uploadFile(ws, reference, session.user_id, true);
      next = { ...value, reference_upload_id: file.id };
      setReference(undefined);
      setDraft(next);
    }
    const saved = await ws.command(
      "save_editor_draft",
      data.history.homework_id,
      editorRevision,
      next,
    );
    setEditorRevision(saved.revision);
    setDirty(false);
    return next;
  }
  async function saveVersion() {
    const value = await saveDraft();
    if (!value.criteria?.length || value.criteria.some((c) => !c.title?.trim()))
      throw new Error("Добавьте критерии и заполните их названия.");
    const created = await ws.core.command(
      "create_homework_version",
      data.history.homework_id,
      homeworkRevision,
      {
        student_text: value.student_text ?? "",
        max_score: value.max_score ?? 0,
        artifact_kinds: value.artifact_kinds ?? [],
        estimated_review_minutes: value.estimated_review_minutes ?? 30,
        criteria: value.criteria.map((c) => ({
          key: c.key,
          title: c.title ?? "",
          description: c.description ?? "",
          max_points: c.max_points ?? 0,
        })),
      },
    );
    await ws.command("save_private_homework", created.id, 0, {
      reviewer_guidance: value.reviewer_guidance ?? "",
      reference_upload_id: value.reference_upload_id ?? null,
      criterion_classes: Object.fromEntries(
        value.criteria.map((c) => [c.key, c.check_class ?? "content"]),
      ),
    });
    const history = await ws.core.homework(data.history.homework_id);
    setHomeworkRevision(history.homework_revision);
    setVersion(history.versions.find((v) => v.id === created.id));
    setStep(2);
  }
  function localDate(value: string | null | undefined) {
    if (!value) return "";
    const d = new Date(value);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  }
  return (
    <>
      <ScreenTitle code={step === 0 ? "К4" : "К5"} title="Настройка задания">
        <button
          disabled={action.busy}
          onClick={() =>
            void action.run(async () => {
              await saveDraft();
            }, "Черновик сохранён.")
          }
        >
          Сохранить черновик
        </button>
      </ScreenTitle>
      {action.feedback}
      <div className="steps">
        {["Для студента", "Критерии ревью", "Публикация"].map((label, i) => (
          <button
            key={label}
            aria-current={step === i ? "step" : undefined}
            onClick={() => setStep(i)}
          >
            {i + 1}. {label}
          </button>
        ))}
      </div>
      {step === 0 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(async () => {
              await saveDraft();
              setStep(1);
            });
          }}
        >
          <div className="two-col">
            <Card title="Условие для студента">
              <label>
                Условие
                <textarea
                  rows={12}
                  value={draft.student_text ?? ""}
                  onChange={(e) => change({ student_text: e.target.value })}
                />
              </label>
              <label>
                Максимальный балл
                <input
                  type="number"
                  required
                  min={0}
                  step="any"
                  value={draft.max_score ?? 0}
                  onChange={(e) =>
                    change({ max_score: e.target.valueAsNumber })
                  }
                />
              </label>
              <div className="actions">
                {(["github", "google_docs"] as const).map((kind) => (
                  <label className="check" key={kind}>
                    <input
                      type="checkbox"
                      checked={draft.artifact_kinds?.includes(kind) ?? false}
                      onChange={(e) =>
                        change({
                          artifact_kinds: e.target.checked
                            ? [...(draft.artifact_kinds ?? []), kind]
                            : (draft.artifact_kinds ?? []).filter(
                                (v) => v !== kind,
                              ),
                        })
                      }
                    />
                    {kind === "github" ? "GitHub" : "Google Docs"}
                  </label>
                ))}
              </div>
            </Card>
            <Card title="Правила задания">
              <label>
                Лимит AI-самопроверок на студента
                <input
                  required
                  type="number"
                  min={0}
                  step={1}
                  value={draft.policy?.self_review_limit ?? ""}
                  onChange={(e) =>
                    policy({ self_review_limit: e.target.valueAsNumber })
                  }
                />
              </label>
              <p className="muted">
                Один лимит на всё задание в этом потоке. Пересдачи его не
                сбрасывают. Технический сбой не расходует попытку; 0 отключает
                самопроверку.
              </p>
              <label>
                Порог зачёта
                <input
                  required
                  type="number"
                  min={0}
                  max={draft.max_score ?? 0}
                  step="any"
                  value={draft.policy?.pass_score ?? 0}
                  onChange={(e) =>
                    policy({ pass_score: e.target.valueAsNumber })
                  }
                />
              </label>
              <label>
                Баллов за день просрочки
                <input
                  required
                  type="number"
                  min={0}
                  step="any"
                  value={draft.policy?.penalty_per_day ?? 0}
                  onChange={(e) =>
                    policy({ penalty_per_day: e.target.valueAsNumber })
                  }
                />
              </label>
              <label>
                Дней на доработку
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={draft.policy?.revision_days ?? 7}
                  onChange={(e) =>
                    policy({ revision_days: e.target.valueAsNumber })
                  }
                />
              </label>
              <label>
                Максимум пересдач
                <input
                  type="number"
                  min={0}
                  value={draft.policy?.max_resubmissions ?? 3}
                  onChange={(e) =>
                    policy({ max_resubmissions: e.target.valueAsNumber })
                  }
                />
              </label>
            </Card>
          </div>
          <button className="primary" disabled={action.busy}>
            Дальше: критерии
          </button>
        </form>
      )}
      {step === 1 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(saveVersion);
          }}
        >
          <div className="two-col">
            <Card title="Критерии и баллы">
              {draft.criteria?.map((c, i) => (
                <div key={c.key} className="criterion">
                  <label>
                    Критерий {i + 1}
                    <input
                      required
                      value={c.title ?? ""}
                      onChange={(e) =>
                        change({
                          criteria: draft.criteria!.map((x, j) =>
                            i === j ? { ...x, title: e.target.value } : x,
                          ),
                        })
                      }
                    />
                  </label>
                  <label>
                    Что считается выполненным
                    <textarea
                      value={c.description ?? ""}
                      onChange={(e) =>
                        change({
                          criteria: draft.criteria!.map((x, j) =>
                            i === j ? { ...x, description: e.target.value } : x,
                          ),
                        })
                      }
                    />
                  </label>
                  <div className="two-col">
                    <label>
                      Максимум баллов
                      <input
                        type="number"
                        min={0}
                        step="any"
                        required
                        value={c.max_points ?? 0}
                        onChange={(e) =>
                          change({
                            criteria: draft.criteria!.map((x, j) =>
                              i === j
                                ? { ...x, max_points: e.target.valueAsNumber }
                                : x,
                            ),
                          })
                        }
                      />
                    </label>
                    <label>
                      Класс проверки
                      <select
                        value={c.check_class ?? "content"}
                        onChange={(e) =>
                          change({
                            criteria: draft.criteria!.map((x, j) =>
                              i === j
                                ? {
                                    ...x,
                                    check_class: e.target.value as
                                      "formal" | "content" | "judgement",
                                  }
                                : x,
                            ),
                          })
                        }
                      >
                        <option value="formal">Формальная</option>
                        <option value="content">Содержательная</option>
                        <option value="judgement">Оценочная</option>
                      </select>
                    </label>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      change({
                        criteria: draft.criteria!.filter((_, j) => i !== j),
                      })
                    }
                  >
                    Убрать критерий
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() =>
                  change({
                    criteria: [
                      ...(draft.criteria ?? []),
                      {
                        key: crypto.randomUUID(),
                        title: "",
                        description: "",
                        max_points: 1,
                        check_class: "content",
                      },
                    ],
                  })
                }
              >
                Добавить критерий
              </button>
              <label>
                Ожидаемое время проверки, минут
                <input
                  type="number"
                  min={1}
                  max={10080}
                  value={draft.estimated_review_minutes ?? 30}
                  onChange={(e) =>
                    change({ estimated_review_minutes: e.target.valueAsNumber })
                  }
                />
              </label>
            </Card>
            <div>
              <Card title="Рекомендации ревьюерам">
                <label>
                  Внутренние рекомендации
                  <textarea
                    rows={8}
                    value={draft.reviewer_guidance ?? ""}
                    onChange={(e) =>
                      change({ reviewer_guidance: e.target.value })
                    }
                  />
                </label>
              </Card>
              <Card title="Эталонное решение">
                <label>
                  Закрытый файл Markdown/PDF/DOCX
                  <input
                    type="file"
                    accept=".md,.pdf,.docx"
                    onChange={(e) => {
                      setReference(e.target.files?.[0]);
                      setDirty(true);
                    }}
                  />
                </label>
                {draft.reference_upload_id && (
                  <p className="muted">Эталон уже сохранён.</p>
                )}
                <p className="muted">
                  Не передаётся студенту и в его самопроверку.
                </p>
              </Card>
              <Card title="Версии">
                {data.history.versions.map((v) => (
                  <p key={v.id}>Версия {v.version_number}</p>
                ))}
              </Card>
            </div>
          </div>
          <button
            className="primary"
            disabled={action.busy || !draft.criteria?.length}
          >
            Сохранить версию и продолжить
          </button>
        </form>
      )}
      {step === 2 && (
        <Card title="Опубликовать в потоке">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void action.run(async () => {
                if (
                  !version ||
                  !draft.policy ||
                  !draft.submission_deadline ||
                  !draft.review_deadline
                )
                  throw new Error("Сначала сохраните версию, лимит и сроки.");
                await saveDraft();
                const result = await ws.command(
                  "publish_workspace_homework",
                  version.id,
                  version.revision,
                  {
                    course_run_id: run,
                    submission_deadline: draft.submission_deadline,
                    review_deadline: draft.review_deadline,
                    policy: draft.policy,
                    expected_policy_revision: policyRevision,
                  },
                );
                setPublished(result.publication_id);
                setPolicyRevision(result.policy_revision);
                const updated = await ws.core.homework(
                  data.history.homework_id,
                );
                setVersion(updated.versions.find((v) => v.id === version.id));
              });
            }}
          >
            <p>Сохранённая версия: {version?.version_number ?? "ещё нет"}</p>
            <p>
              Лимит самопроверок:{" "}
              {draft.policy?.self_review_limit ?? "не задан"}
            </p>
            <label>
              Сдать до
              <input
                required
                type="datetime-local"
                value={localDate(draft.submission_deadline)}
                onChange={(e) =>
                  change({
                    submission_deadline: e.target.value
                      ? new Date(e.target.value).toISOString()
                      : null,
                  })
                }
              />
            </label>
            <label>
              Проверить до
              <input
                required
                type="datetime-local"
                min={localDate(draft.submission_deadline)}
                value={localDate(draft.review_deadline)}
                onChange={(e) =>
                  change({
                    review_deadline: e.target.value
                      ? new Date(e.target.value).toISOString()
                      : null,
                  })
                }
              />
            </label>
            <button
              className="primary"
              disabled={action.busy || !version || !draft.policy}
            >
              Опубликовать задание
            </button>
          </form>
          {published && (
            <div className="notice ok">
              <p>Задание и лимит опубликованы.</p>
              <label>
                Ссылка для Stepik
                <input
                  readOnly
                  value={`${window.location.origin}/#/prepare/${published}`}
                />
              </label>
              <button
                onClick={() =>
                  void navigator.clipboard.writeText(
                    `${window.location.origin}/#/prepare/${published}`,
                  )
                }
              >
                Скопировать ссылку
              </button>
            </div>
          )}
          {data.history.course_run_publications.map((p) => (
            <p key={p.id}>
              Публикация {date(p.published_at)} · срок{" "}
              {date(p.submission_deadline)}
            </p>
          ))}
        </Card>
      )}
    </>
  );
}
