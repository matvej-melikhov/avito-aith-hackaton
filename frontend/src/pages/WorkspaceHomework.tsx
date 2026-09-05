import { useEffect, useRef, useState } from "react";
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
function versionContent(value: W<"EditorDraftInput">) {
  return JSON.stringify([
    value.student_text,
    value.max_score,
    value.estimated_review_minutes,
    value.artifact_kinds,
    value.criteria?.map((c) => [
      c.key,
      c.title,
      c.description,
      c.max_points,
      c.check_class ?? "content",
      c.score_step ?? 0.5,
      c.evaluate_quality ?? false,
    ]),
    value.reviewer_guidance,
    value.reference_upload_id ?? null,
    value.material_upload_ids ?? [],
  ]);
}
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
        score_step:
          data.privateDetails?.criterion_settings?.[c.key]?.score_step ?? 0.5,
        evaluate_quality:
          data.privateDetails?.criterion_settings?.[c.key]?.evaluate_quality ??
          false,
        check_class:
          data.privateDetails?.criterion_classes?.[c.key] ?? "content",
      })) ?? [],
    reviewer_guidance: data.privateDetails?.reviewer_guidance ?? "",
    reference_upload_id: data.privateDetails?.reference_upload_id ?? null,
    material_upload_ids: data.privateDetails?.material_upload_ids ?? [],
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
  const [draft, setDraft] = useState<W<"EditorDraftInput">>({
    ...initial,
    max_score: (initial.criteria ?? []).reduce(
      (sum, c) => sum + (c.max_points ?? 0),
      0,
    ),
    criteria: initial.criteria?.map((c) => ({
      ...c,
      score_step: c.score_step ?? 0.5,
      evaluate_quality: c.evaluate_quality ?? false,
    })),
  });
  const editorRevision = useRef(data.editor.revision);
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const [autoStatus, setAutoStatus] = useState("");
  const [homeworkRevision, setHomeworkRevision] = useState(
    data.history.homework_revision,
  );
  const [version, setVersion] = useState(data.latest);
  const [savedContent, setSavedContent] = useState(
    data.latest
      ? versionContent({
          ...initial,
          student_text: data.latest.student_text,
          max_score: data.latest.max_score,
          estimated_review_minutes: data.latest.estimated_review_minutes,
          artifact_kinds: data.latest.artifact_kinds,
          criteria: data.latest.criteria.map((c) => ({
            key: c.key,
            title: c.title,
            description: c.description,
            max_points: c.max_points,
            score_step:
              data.privateDetails?.criterion_settings?.[c.key]?.score_step ??
              0.5,
            evaluate_quality:
              data.privateDetails?.criterion_settings?.[c.key]
                ?.evaluate_quality ?? false,
            check_class:
              data.privateDetails?.criterion_classes?.[c.key] ?? "content",
          })),
          reviewer_guidance: data.privateDetails?.reviewer_guidance ?? "",
          reference_upload_id: data.privateDetails?.reference_upload_id ?? null,
          material_upload_ids: data.privateDetails?.material_upload_ids ?? [],
        })
      : "",
  );
  const [penaltyEnabled, setPenaltyEnabled] = useState(
    (initial.policy?.penalty_per_day ?? 0) > 0,
  );
  const [step, setStep] = useState(0);
  const [furthestStep, setFurthestStep] = useState(data.latest ? 2 : 0);
  const [editingCriterion, setEditingCriterion] = useState<string | undefined>(
    initial.criteria?.[0]?.key,
  );
  const [dirty, setDirty] = useState(false);
  const [reference, setReference] = useState<File>();
  const [materials, setMaterials] = useState<File[]>([]);
  const referenceRef = useRef(reference);
  const materialsRef = useRef(materials);
  referenceRef.current = reference;
  materialsRef.current = materials;
  const versionChanged =
    !!reference ||
    materials.length > 0 ||
    versionContent(draft) !== savedContent;
  const [published, setPublished] = useState<string>();

  const [policyRevision, setPolicyRevision] = useState(
    data.policy?.revision ?? 0,
  );
  const action = useAction();
  useDirtyGuard(dirty);
  function change(patch: Partial<W<"EditorDraftInput">>) {
    setAutoStatus("Ожидает сохранения…");
    setDraft((d) => ({
      ...d,
      ...patch,
      ...(patch.criteria
        ? {
            max_score: patch.criteria.reduce(
              (sum, c) => sum + (c.max_points ?? 0),
              0,
            ),
          }
        : {}),
    }));
    setDirty(true);
  }
  function moveCriterion(source: string, target: string) {
    const list = [...(draft.criteria ?? [])];
    const from = list.findIndex((c) => c.key === source);
    const to = list.findIndex((c) => c.key === target);
    if (from < 0 || to < 0 || from === to) return;
    const [criterion] = list.splice(from, 1);
    list.splice(to, 0, criterion);
    change({ criteria: list });
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
  function saveDraft() {
    const pending = saveQueue.current.then(async () => {
      let next = draftRef.current;
      const original = next;
      setAutoStatus("Сохраняется…");
      if (referenceRef.current) {
        const file = await uploadFile(
          ws,
          referenceRef.current,
          session.user_id,
          true,
        );
        next = { ...next, reference_upload_id: file.id };
        referenceRef.current = undefined;
        setReference(undefined);
      }
      if (materialsRef.current.length) {
        const uploaded = await Promise.all(
          materialsRef.current.map((file) =>
            uploadFile(ws, file, session.user_id),
          ),
        );
        next = {
          ...next,
          material_upload_ids: [
            ...(next.material_upload_ids ?? []),
            ...uploaded.map((file) => file.id),
          ],
        };
        materialsRef.current = [];
        setMaterials([]);
      }
      if (next !== original) {
        const uploaded = next;
        setDraft((current) =>
          current === original
            ? uploaded
            : {
                ...current,
                reference_upload_id: uploaded.reference_upload_id,
                material_upload_ids: uploaded.material_upload_ids,
              },
        );
        if (draftRef.current === original) draftRef.current = next;
      }
      const saved = await ws.command(
        "save_editor_draft",
        data.history.homework_id,
        editorRevision.current,
        next,
      );
      editorRevision.current = saved.revision;
      if (JSON.stringify(draftRef.current) === JSON.stringify(next)) {
        setDirty(false);
        setAutoStatus("Изменения сохранены");
      } else setAutoStatus("Ожидает сохранения…");
      return next;
    });
    saveQueue.current = pending.then(
      () => undefined,
      () => undefined,
    );
    return pending;
  }
  const saveLatest = useRef(saveDraft);
  saveLatest.current = saveDraft;
  useEffect(() => {
    if (
      !dirty ||
      !Number.isFinite(draft.max_score) ||
      draft.criteria?.some(
        (c) => !Number.isFinite(c.score_step) || c.score_step <= 0,
      )
    )
      return;
    const timer = window.setTimeout(() => {
      void saveLatest
        .current()
        .catch(() =>
          setAutoStatus("Не удалось сохранить. Изменения остаются в форме."),
        );
    }, 500);
    return () => window.clearTimeout(timer);
  }, [draft, dirty, reference, materials]);
  async function saveVersion() {
    const value = draft;
    if (!value.student_text.trim())
      throw new Error("Заполните условие задания.");
    if (!value.artifact_kinds?.length)
      throw new Error("Выберите хотя бы один формат сдачи.");
    if (!Number.isFinite(value.max_score) || value.max_score < 0)
      throw new Error("Укажите корректный максимальный балл.");
    if (
      value.criteria?.some(
        (c) => !Number.isFinite(c.max_points) || (c.max_points ?? 0) < 0,
      )
    )
      throw new Error(
        "Максимумы критериев должны быть неотрицательными числами.",
      );
    if (
      Math.abs(
        (value.criteria ?? []).reduce(
          (sum, c) => sum + (c.max_points ?? 0),
          0,
        ) - value.max_score,
      ) > 0.000001
    )
      throw new Error(
        "Сумма баллов критериев должна совпадать с максимумом задания.",
      );
    if (
      value.criteria?.some(
        (c) => !Number.isFinite(c.score_step) || (c.score_step ?? 0) <= 0,
      )
    )
      throw new Error("Укажите положительный шаг каждого критерия.");
    if (!value.criteria?.length || value.criteria.some((c) => !c.title?.trim()))
      throw new Error("Добавьте критерии и заполните их названия.");
    const savedDraft = await saveDraft();
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
      reference_upload_id: savedDraft.reference_upload_id ?? null,
      material_upload_ids: savedDraft.material_upload_ids ?? [],
      criterion_settings: Object.fromEntries(
        value.criteria.map((c) => [
          c.key,
          {
            score_step: c.score_step ?? 0.5,
            evaluate_quality: c.evaluate_quality ?? false,
          },
        ]),
      ),
      criterion_classes: Object.fromEntries(
        value.criteria.map((c) => [c.key, c.check_class ?? "content"]),
      ),
    });
    const history = await ws.core.homework(data.history.homework_id);
    setHomeworkRevision(history.homework_revision);
    setVersion(history.versions.find((v) => v.id === created.id));
    setSavedContent(versionContent(savedDraft));
    setFurthestStep(2);
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
      <p className="crumbs">
        <a
          className="crumbs__back"
          href="#/homeworks"
          aria-label="Назад к заданиям"
        >
          ←
        </a>
        <a href="#/catalog">Курсы</a> /{" "}
        <a href={`#/courses/${run}`}>Задания потока</a> / Настройка задания
      </p>
      <ScreenTitle code={step === 0 ? "К4" : "К5"} title="Настройка задания" />
      {action.feedback}
      {autoStatus && <small role="status">{autoStatus}</small>}
      <div className="steps">
        {["Для студента", "Критерии ревью", "Публикация"].map((label, i) => (
          <button
            key={label}
            aria-current={step === i ? "step" : undefined}
            disabled={i > furthestStep}
            className={`step ${step === i ? "is-on" : i < furthestStep ? "is-done" : ""}`}
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
              setFurthestStep((current) => Math.max(current, 1));
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
                Материалы для студента
                <input
                  type="file"
                  multiple
                  accept=".md,.pdf,.docx"
                  onChange={(e) => {
                    setMaterials(Array.from(e.target.files ?? []));
                    setDirty(true);
                  }}
                />
              </label>
              <p className="muted">
                Markdown, PDF или DOCX, до 10 МБ на файл. Эти материалы будут
                доступны студенту.
              </p>
              {materials.map((file) => (
                <p key={file.name}>{file.name} · ожидает сохранения</p>
              ))}
              {(draft.material_upload_ids ?? []).map((id, index) => (
                <div className="actions" key={id}>
                  <span>Материал {index + 1}</span>
                  <button
                    type="button"
                    onClick={() =>
                      void action.run(async () => {
                        const file = await ws.download(id);
                        window.open(file.url, "_blank", "noopener,noreferrer");
                      })
                    }
                  >
                    Открыть
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      change({
                        material_upload_ids: draft.material_upload_ids!.filter(
                          (value) => value !== id,
                        ),
                      })
                    }
                  >
                    Убрать
                  </button>
                </div>
              ))}
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
              <label className="check">
                <input
                  type="checkbox"
                  checked={penaltyEnabled}
                  onChange={(e) => {
                    setPenaltyEnabled(e.target.checked);
                    policy({ penalty_per_day: 0 });
                  }}
                />
                Штраф за просрочку
              </label>
              {penaltyEnabled && (
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
              )}
              <p className="muted">
                Штраф применяется по правилам публикации. Итог не может быть
                меньше нуля.
              </p>
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
          <div className="btn-row">
            <button className="primary" disabled={action.busy}>
              Дальше: критерии
            </button>
          </div>
        </form>
      )}
      {step === 1 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void action.run(saveVersion);
          }}
        >
          <div className="row-side">
            <div className="stack">
              <Card
                title="Критерии"
                actions={
                  <button
                    type="button"
                    onClick={() => {
                      const key = crypto.randomUUID();
                      change({
                        criteria: [
                          ...(draft.criteria ?? []),
                          {
                            key,
                            title: "",
                            description: "",
                            max_points: 1,
                            score_step: 1,
                            evaluate_quality: false,
                            check_class: "content",
                          },
                        ],
                      });
                      setEditingCriterion(key);
                    }}
                  >
                    Добавить критерий
                  </button>
                }
              >
                {draft.criteria?.map((c, i) => {
                  const edit = (patch: Partial<W<"EditorCriterion">>) =>
                    change({
                      criteria: draft.criteria!.map((value) =>
                        value.key === c.key ? { ...value, ...patch } : value,
                      ),
                    });
                  return editingCriterion === c.key ? (
                    <div
                      className="crit"
                      key={c.key}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => {
                        event.preventDefault();
                        moveCriterion(
                          event.dataTransfer.getData("text/plain"),
                          c.key,
                        );
                      }}
                    >
                      <label className="field">
                        Название критерия
                        <input
                          required
                          value={c.title}
                          onChange={(e) => edit({ title: e.target.value })}
                        />
                      </label>
                      <label className="field">
                        Выполнено, если
                        <textarea
                          value={c.description}
                          onChange={(e) =>
                            edit({ description: e.target.value })
                          }
                        />
                        <small>
                          По этой формулировке работают и модель, и ревьюер.
                          Студент видит её после проверки.
                        </small>
                      </label>
                      <div className="field">
                        <span className="field__lbl">Как проверяем</span>
                        <div className="seg">
                          {(
                            [
                              ["formal", "формальная"],
                              ["content", "по смыслу, с цитатой"],
                              ["judgement", "на усмотрение ревьюера"],
                            ] as const
                          ).map(([value, label]) => (
                            <button
                              type="button"
                              key={value}
                              className={c.check_class === value ? "is-on" : ""}
                              aria-pressed={c.check_class === value}
                              onClick={() =>
                                edit({
                                  check_class: value,
                                  evaluate_quality:
                                    value === "content"
                                      ? c.evaluate_quality
                                      : false,
                                })
                              }
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {c.check_class === "content" && (
                        <div className="field">
                          <label className="check">
                            <input
                              type="checkbox"
                              checked={c.evaluate_quality ?? false}
                              onChange={(e) =>
                                edit({ evaluate_quality: e.target.checked })
                              }
                            />
                            Ещё и оценить качество
                          </label>
                          <small>
                            Второй шаг после того, как модель нашла и
                            процитировала. Считается только если первый шаг дал
                            «да».
                          </small>
                        </div>
                      )}
                      <div className="actions">
                        <label>
                          Баллов за критерий
                          <input
                            type="number"
                            required
                            min={0}
                            step="any"
                            value={c.max_points}
                            onChange={(e) =>
                              edit({ max_points: e.target.valueAsNumber })
                            }
                          />
                        </label>
                        <label>
                          Шаг
                          <input
                            type="number"
                            required
                            min={0.000001}
                            step="any"
                            value={c.score_step ?? 0.5}
                            onChange={(e) =>
                              edit({ score_step: e.target.valueAsNumber })
                            }
                          />
                        </label>
                        <div>
                          <span className="field__lbl">Что увидит ревьюер</span>
                          <ScorePreview
                            maximum={c.max_points ?? 0}
                            step={c.score_step ?? 0.5}
                          />
                        </div>
                      </div>
                      <div className="actions">
                        <button
                          type="button"
                          disabled={i === 0}
                          aria-label={`Поднять критерий ${i + 1}`}
                          onClick={() => {
                            const list = [...draft.criteria!];
                            [list[i - 1], list[i]] = [list[i], list[i - 1]];
                            change({ criteria: list });
                          }}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          disabled={i === draft.criteria!.length - 1}
                          aria-label={`Опустить критерий ${i + 1}`}
                          onClick={() => {
                            const list = [...draft.criteria!];
                            [list[i + 1], list[i]] = [list[i], list[i + 1]];
                            change({ criteria: list });
                          }}
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            change({
                              criteria: draft.criteria!.filter(
                                (value) => value.key !== c.key,
                              ),
                            })
                          }
                        >
                          Убрать критерий
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingCriterion(undefined)}
                        >
                          Свернуть
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div
                      className="acc"
                      key={c.key}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => {
                        event.preventDefault();
                        moveCriterion(
                          event.dataTransfer.getData("text/plain"),
                          c.key,
                        );
                      }}
                    >
                      <button
                        type="button"
                        className="acc__h"
                        onClick={() => setEditingCriterion(c.key)}
                      >
                        <span
                          className="drag"
                          draggable
                          onDragStart={(event) =>
                            event.dataTransfer.setData("text/plain", c.key)
                          }
                          title="Перетащить критерий"
                        />
                        <span className="acc__t">
                          {c.title || "Новый критерий"}
                        </span>
                        <span className="caption">
                          {c.check_class === "formal"
                            ? "формальная"
                            : c.check_class === "content"
                              ? `по смыслу, с цитатой${c.evaluate_quality ? ", плюс качество" : ""}`
                              : "на усмотрение ревьюера"}
                        </span>
                        <span className="mono">{c.max_points}</span>
                        <span className="acc__chev">▼</span>
                      </button>
                    </div>
                  );
                })}
                <label className="actions">
                  Порог зачёта
                  <input
                    aria-label="Порог зачёта"
                    style={{ width: 80 }}
                    type="number"
                    required
                    min={0}
                    max={draft.max_score}
                    step="any"
                    value={draft.policy?.pass_score ?? 0}
                    onChange={(e) =>
                      policy({ pass_score: e.target.valueAsNumber })
                    }
                  />
                  <span>баллов из {draft.max_score} возможных</span>
                </label>
              </Card>
              <Card title="Рекомендации ревьюерам">
                <label>
                  Внутренние рекомендации
                  <textarea
                    rows={5}
                    value={draft.reviewer_guidance}
                    onChange={(e) =>
                      change({ reviewer_guidance: e.target.value })
                    }
                  />
                </label>
              </Card>
            </div>
            <div className="stack">
              <Card title="Эталонное решение">
                <label className="drop">
                  Перетащите файл или выберите
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
                  <button
                    type="button"
                    onClick={() => {
                      change({ reference_upload_id: null });
                      setReference(undefined);
                    }}
                  >
                    Удалить эталон из новой версии
                  </button>
                )}
                <p className="muted">
                  Используется как пример для модели и ревьюеров при калибровке.
                  Студентам не показывается никогда.
                </p>
              </Card>
              <Card title="Версии">
                {data.history.versions.map((v) => (
                  <p key={v.id}>Версия {v.version_number}</p>
                ))}
              </Card>
            </div>
          </div>
          <div className="btn-row">
            <button
              className="primary"
              disabled={action.busy || !draft.criteria?.length}
            >
              Дальше: публикация
            </button>
          </div>
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
                if (versionChanged)
                  throw new Error(
                    "Условия или критерии изменились. Сохраните новую версию на шаге 2.",
                  );
                if (
                  !Number.isInteger(draft.policy.self_review_limit) ||
                  draft.policy.self_review_limit < 0
                )
                  throw new Error(
                    "Укажите конечный целый лимит самопроверок от 0.",
                  );
                if (
                  new Date(draft.review_deadline) <
                  new Date(draft.submission_deadline)
                )
                  throw new Error(
                    "Срок проверки должен быть не раньше срока сдачи.",
                  );
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
            {versionChanged && (
              <p className="notice">
                Условия, критерии или закрытые материалы изменились. Вернитесь
                на шаг 2 и сохраните новую версию перед публикацией.
              </p>
            )}
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
              disabled={
                action.busy || !version || !draft.policy || versionChanged
              }
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

function ScorePreview({ maximum, step }: { maximum: number; step: number }) {
  if (
    !Number.isFinite(maximum) ||
    maximum < 0 ||
    !Number.isFinite(step) ||
    step <= 0
  )
    return <small>Укажите баллы и положительный шаг.</small>;
  const count = Math.floor(maximum / step);
  const values =
    count <= 12
      ? Array.from({ length: count + 1 }, (_, i) =>
          Number((i * step).toFixed(6)),
        )
      : [0, step];
  if (values[values.length - 1] !== maximum) values.push(maximum);
  return (
    <>
      <span className="scale scale--ro">
        {values.map((value, i) => (
          <span key={i}>
            {count > 12 && i === values.length - 1 ? `… ${value}` : value}
          </span>
        ))}
      </span>
      <small>
        Число от 0 до {maximum}, шаг {step}
      </small>
    </>
  );
}
