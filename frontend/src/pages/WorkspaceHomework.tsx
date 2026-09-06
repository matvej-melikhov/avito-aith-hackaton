import { Btn, Field, Inp, Area, Chk, Steps, Seg } from "../ds";
import { useEffect, useRef, useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, uploadFile, type W } from "../api/workspace";
import { Card, Resource, date, useAction, useResource } from "../ui";
import { ScreenTitle, useDirtyGuard } from "../workspace-ui";
import { MarkdownArea } from "../MarkdownArea";
import { num } from "../ds";
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
  useEffect(() => {
    if (r.data) sessionStorage.setItem("review-ui-selected-run", run);
  }, [r.data, run]);
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
    value.allowed_sources ?? ["upload", "github", "google_docs"],
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
    allowed_sources: data.privateDetails?.allowed_sources ?? [
      "upload",
      "github",
      "google_docs",
    ],
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
      (sum, c) => sum + (Number.isFinite(c.max_points) ? c.max_points : 0),
      0,
    ),
    criteria: initial.criteria?.map((c) => ({
      ...c,
      score_step: c.score_step ?? 0.5,
      evaluate_quality: c.evaluate_quality ?? false,
    })),
  });
  const [title, setTitle] = useState(data.editor.homework_title);
  const titleRef = useRef(title);
  titleRef.current = title;
  const savedTitle = useRef(data.editor.homework_title);
  const editorRevision = useRef(data.editor.revision);
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const [autoStatus, setAutoStatus] = useState("");
  const homeworkRevision = useRef(data.editor.homework_revision);
  const [version, setVersion] = useState(data.latest);
  const [savedContent, setSavedContent] = useState(
    data.latest
      ? versionContent({
          ...initial,
          student_text: data.latest.student_text,
          max_score: data.latest.max_score,
          estimated_review_minutes: data.latest.estimated_review_minutes,
          artifact_kinds: data.latest.artifact_kinds,
          allowed_sources: data.privateDetails?.allowed_sources ?? [
            "upload",
            "github",
            "google_docs",
          ],
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
  const [published, setPublished] = useState<string | undefined>(
    data.publication?.course_run_homework_id,
  );

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
              (sum, c) =>
                sum + (Number.isFinite(c.max_points) ? c.max_points : 0),
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
      const currentTitle = titleRef.current.trim();
      if (!currentTitle) throw new Error("Заполните название задания.");
      if (currentTitle !== savedTitle.current) {
        const renamed = await ws.command(
          "update_workspace_homework",
          data.history.homework_id,
          homeworkRevision.current,
          { title: currentTitle },
        );
        homeworkRevision.current = renamed.revision;
        savedTitle.current = currentTitle;
      }
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
      if (
        JSON.stringify(draftRef.current) === JSON.stringify(next) &&
        titleRef.current.trim() === savedTitle.current
      ) {
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
  }, [draft, title, dirty, reference, materials]);
  async function saveVersion() {
    const value = await saveDraft();
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
    const savedDraft = value;
    const created = await ws.core.command(
      "create_homework_version",
      data.history.homework_id,
      homeworkRevision.current,
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
      allowed_sources: savedDraft.allowed_sources ?? [
        "upload",
        "github",
        "google_docs",
      ],
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
    homeworkRevision.current = history.homework_revision;
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
        <a href="#/courses">Курсы</a> /{" "}
        <a href={`#/courses/${run}`}>Задания потока</a> / Настройка задания
      </p>
      <ScreenTitle
        code={step === 0 ? "К5" : "К6"}
        title={title || "Новое задание"}
      />
      {action.feedback}
      {autoStatus && <small role="status">{autoStatus}</small>}
      <Steps
        steps={["Для студента", "Критерии ревью", "Публикация"].map(
          (label, i) => ({
            n: i + 1,
            label,
            name: `${i + 1}. ${label}`,
            state: i === step ? "on" : i <= furthestStep ? "done" : "next",
            onClick: () => setStep(i),
          }),
        )}
      />
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
          <div className="row-side">
            <Card title="Условие для студента">
              <Field label="Название">
                <Inp
                  required
                  maxLength={512}
                  value={title}
                  onChange={(event) => {
                    setTitle(event.target.value);
                    setDirty(true);
                    setAutoStatus("Ожидает сохранения…");
                  }}
                />
              </Field>
              <Field label="Условие">
                <MarkdownArea
                  rows={12}
                  value={draft.student_text ?? ""}
                  onChange={(value) => change({ student_text: value })}
                />
              </Field>
              <label>
                Материалы для студента
                <Inp
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
                  <Btn
                    type="button"
                    onClick={() =>
                      void action.run(async () => {
                        const file = await ws.download(id);
                        window.open(file.url, "_blank", "noopener,noreferrer");
                      })
                    }
                  >
                    Открыть
                  </Btn>
                  <Btn
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
                  </Btn>
                </div>
              ))}
              <Field label="Способ сдачи" group>
                <Seg<"both" | "link" | "upload">
                  label="Способ сдачи"
                  value={
                    (
                      draft.allowed_sources ?? [
                        "upload",
                        "github",
                        "google_docs",
                      ]
                    ).includes("upload")
                      ? (
                          draft.allowed_sources ?? [
                            "upload",
                            "github",
                            "google_docs",
                          ]
                        ).length === 1
                        ? "upload"
                        : "both"
                      : "link"
                  }
                  options={[
                    { value: "both", label: "Ссылка или файлы" },
                    { value: "link", label: "Только ссылка" },
                    { value: "upload", label: "Только файлы" },
                  ]}
                  onChange={(mode) =>
                    change({
                      allowed_sources:
                        mode === "upload"
                          ? ["upload"]
                          : mode === "link"
                            ? ["github", "google_docs"]
                            : ["upload", "github", "google_docs"],
                    })
                  }
                />
              </Field>
            </Card>
            <Card title="Сроки и правила задания">
              <label>
                Срок сдачи
                <Inp
                  type="datetime-local"
                  value={localDate(draft.submission_deadline)}
                  onChange={(event) =>
                    change({
                      submission_deadline: event.target.value
                        ? new Date(event.target.value).toISOString()
                        : null,
                    })
                  }
                />
                <small>
                  Срок относится к выбранному потоку; перед публикацией его
                  можно уточнить.
                </small>
              </label>
              <Field label="Лимит самопроверок с ИИ на студента">
                <Inp
                  required
                  type="number"
                  min={0}
                  step={1}
                  value={draft.policy?.self_review_limit ?? ""}
                  onChange={(e) =>
                    policy({ self_review_limit: Number(e.target.value) })
                  }
                />
              </Field>
              <p className="muted">
                Один лимит на всё задание в этом потоке. Пересдачи его не
                сбрасывают. Технический сбой не расходует попытку; 0 отключает
                самопроверку.
              </p>
              <Chk
                type="checkbox"
                checked={penaltyEnabled}
                onChange={(e) => {
                  setPenaltyEnabled(e.target.checked);
                  policy({ penalty_per_day: 0 });
                }}
              >
                Штраф за просрочку
              </Chk>
              {penaltyEnabled && (
                <Field label="Баллов за день просрочки">
                  <Inp
                    required
                    type="number"
                    min={0}
                    step="any"
                    value={draft.policy?.penalty_per_day ?? 0}
                    onChange={(e) =>
                      policy({ penalty_per_day: Number(e.target.value) })
                    }
                  />
                </Field>
              )}
              <p className="muted">
                Штраф рассчитывается по правилам публикации. Итоговый балл не
                может быть меньше нуля.{" "}
              </p>
              <Field label="Дней на доработку">
                <Inp
                  type="number"
                  min={1}
                  max={365}
                  value={draft.policy?.revision_days ?? 7}
                  onChange={(e) =>
                    policy({ revision_days: Number(e.target.value) })
                  }
                />
              </Field>
              <Field label="Максимум пересдач">
                <Inp
                  type="number"
                  min={0}
                  value={draft.policy?.max_resubmissions ?? 3}
                  onChange={(e) =>
                    policy({ max_resubmissions: Number(e.target.value) })
                  }
                />
              </Field>
            </Card>
          </div>
          <div className="btn-row">
            <Btn type="submit" variant="dark" disabled={action.busy}>
              Дальше: критерии
            </Btn>
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
                bodyClassName="criteria-editor"
                actions={
                  <Btn
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
                  </Btn>
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
                      <Field label="Название критерия" className="field">
                        <Inp
                          required
                          value={c.title}
                          onChange={(e) => edit({ title: e.target.value })}
                        />
                      </Field>
                      <Field
                        label="Выполнено, если"
                        hint="По этой формулировке работают и модель, и ревьюер. Студент видит её после проверки."
                      >
                        <Area
                          className="criterion-condition"
                          value={c.description}
                          onChange={(event) =>
                            edit({ description: event.target.value })
                          }
                        />
                      </Field>
                      <Field label="Способ проверки" group>
                        <Seg<"formal" | "content" | "judgement">
                          label="Способ проверки"
                          value={c.check_class}
                          options={[
                            { value: "formal", label: "Формальная проверка" },
                            { value: "content", label: "по смыслу, с цитатой" },
                            {
                              value: "judgement",
                              label: "на усмотрение ревьюера",
                            },
                          ]}
                          onChange={(value) =>
                            edit({
                              check_class: value,
                              evaluate_quality:
                                value === "content"
                                  ? c.evaluate_quality
                                  : false,
                            })
                          }
                        />
                      </Field>
                      {c.check_class === "content" && (
                        <div className="field">
                          <Chk
                            type="checkbox"
                            checked={c.evaluate_quality ?? false}
                            onChange={(e) =>
                              edit({ evaluate_quality: e.target.checked })
                            }
                          >
                            Оценивать качество{" "}
                          </Chk>
                          <small>
                            Модель оценивает качество, только если сначала
                            подтвердила выполнение критерия и привела
                            цитату.{" "}
                          </small>
                        </div>
                      )}
                      <div className="actions">
                        <Field label="Баллов за критерий">
                          <Inp
                            type="number"
                            required
                            min={0}
                            step="any"
                            value={
                              Number.isFinite(c.max_points) ? c.max_points : ""
                            }
                            onChange={(e) =>
                              edit({ max_points: Number(e.target.value) })
                            }
                          />
                        </Field>
                        <Field label="Шаг">
                          <Inp
                            type="number"
                            required
                            min={0.000001}
                            step="any"
                            value={
                              Number.isFinite(c.score_step) ? c.score_step : ""
                            }
                            onChange={(e) =>
                              edit({ score_step: Number(e.target.value) })
                            }
                          />
                        </Field>
                        <div>
                          <span className="field__lbl">Что увидит ревьюер</span>
                          <ScorePreview
                            maximum={c.max_points ?? 0}
                            step={c.score_step ?? 0.5}
                          />
                        </div>
                      </div>
                      <div className="actions">
                        <Btn
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
                        </Btn>
                        <Btn
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
                        </Btn>
                        <Btn
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
                        </Btn>
                        <Btn
                          type="button"
                          onClick={() => setEditingCriterion(undefined)}
                        >
                          Свернуть
                        </Btn>
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
                            ? "Формальная проверка"
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
                  <Inp
                    aria-label="Порог зачёта"
                    style={{ width: 80 }}
                    type="number"
                    required
                    min={0}
                    max={
                      Number.isFinite(draft.max_score)
                        ? draft.max_score
                        : undefined
                    }
                    step="any"
                    value={draft.policy?.pass_score ?? 0}
                    onChange={(e) =>
                      policy({ pass_score: Number(e.target.value) })
                    }
                  />
                  <span>баллов из {draft.max_score} возможных</span>
                </label>
              </Card>
              <Card title="Рекомендации ревьюерам">
                <Field label="Внутренние рекомендации">
                  <Area
                    rows={5}
                    value={draft.reviewer_guidance}
                    onChange={(e) =>
                      change({ reviewer_guidance: e.target.value })
                    }
                  />
                </Field>
              </Card>
            </div>
            <div className="stack">
              <Card title="Эталонное решение">
                <label className="drop">
                  Перетащите файл или выберите
                  <Inp
                    type="file"
                    accept=".md,.pdf,.docx"
                    onChange={(e) => {
                      setReference(e.target.files?.[0]);
                      setDirty(true);
                    }}
                  />
                </label>
                {draft.reference_upload_id && (
                  <Btn
                    type="button"
                    onClick={() => {
                      change({ reference_upload_id: null });
                      setReference(undefined);
                    }}
                  >
                    Удалить эталон из новой версии
                  </Btn>
                )}
                <p className="muted">
                  Пример для модели и ревьюеров при калибровке. Студентам не
                  показывается.{" "}
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
            <Btn
              type="submit"
              variant="dark"
              disabled={action.busy || !draft.criteria?.length}
            >
              Дальше: публикация
            </Btn>
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
                    "Укажите лимит самопроверок целым числом от 0.",
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
            <Field label="Сдать до">
              <Inp
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
            </Field>
            <Field label="Проверить до">
              <Inp
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
            </Field>
            <Btn
              type="submit"
              variant="pri"
              disabled={
                action.busy || !version || !draft.policy || versionChanged
              }
            >
              Опубликовать задание
            </Btn>
          </form>
          {published && (
            <div className="notice ok">
              <p>Ссылка на опубликованное задание</p>
              <Field label="Ссылка для Stepik">
                <Inp
                  readOnly
                  value={`${window.location.origin}/#/prepare/${published}`}
                />
              </Field>
              <Btn
                onClick={() =>
                  void navigator.clipboard.writeText(
                    `${window.location.origin}/#/prepare/${published}`,
                  )
                }
              >
                Скопировать ссылку
              </Btn>
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

export function ScorePreview({
  maximum,
  step,
}: {
  maximum: number;
  step: number;
}) {
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
      <span
        className="scale scale--ro score-preview"
        role="group"
        aria-label="Предпросмотр шкалы"
      >
        {values.map((value, i) => (
          <span key={i}>
            {count > 12 && i === values.length - 1
              ? `… ${num(value, 20)}`
              : num(value, 20)}
          </span>
        ))}
      </span>
      <small>
        Число от 0 до {num(maximum, 20)}, шаг {num(step, 20)}
      </small>
    </>
  );
}
