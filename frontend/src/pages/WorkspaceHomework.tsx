import { useEffect, useRef, useState } from "react";
import type { Model } from "../api/client";
import { WorkspaceClient, uploadFile, type W } from "../api/workspace";
import { ErrorBox, useAction, useResource } from "../ui";
import { useDirtyGuard } from "../workspace-ui";
import {
  Area,
  Btn,
  BtnRow,
  Callout,
  Card,
  CardBody,
  CardFoot,
  CardHead,
  Chk,
  Crit,
  Crumbs,
  Dock,
  Drag,
  Field,
  Inp,
  Kv,
  Main,
  Scale,
  Seg,
  Skel,
  Steps,
  Sum,
  Topbar,
  cx,
  dayLong,
  num,
  plural,
} from "../ds";

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
    const [history, editor, catalog] = await Promise.all([
      ws.core.homework(id),
      ws.editorDraft(id, run),
      ws.catalog(),
    ]);
    const latest = [...history.versions].sort(
      (a, b) => b.version_number - a.version_number,
    )[0];
    const publication = history.course_run_publications.find(
      (p) => p.is_current && p.course_run_id === run,
    );
    const courseRun = catalog.course_runs.find((x) => x.id === run);
    const course = catalog.courses.find((c) => c.id === courseRun?.course_id);
    const [privateDetails, policy, directory] = await Promise.all([
      latest ? ws.privateHomework(latest.id) : Promise.resolve(null),
      publication
        ? ws.policy(publication.course_run_homework_id)
        : Promise.resolve(null),
      course ? ws.courseHomeworks(course.id) : Promise.resolve(null),
    ]);
    const title = directory?.items.find((h) => h.id === id)?.title;
    return {
      history,
      editor,
      latest,
      privateDetails,
      policy,
      publication,
      courseRun,
      course,
      title,
    };
  }, `${id}:${run}`);
  if (r.loading || r.error)
    return (
      <>
        <Topbar
          crumbs={
            <Crumbs
              back="#/homeworks"
              items={[{ href: "#/homeworks", label: "Задания" }]}
              current="Задание"
            />
          }
          title="Задание"
        />
        <Main>
          {r.error ? (
            <ErrorBox error={r.error} retry={r.refresh} />
          ) : (
            <Skel lines={6} label="Загружаем задание…" />
          )}
        </Main>
      </>
    );
  return (
    <HomeworkWizard
      key={`${id}:${run}`}
      ws={ws}
      data={r.data!}
      run={run}
      session={session}
    />
  );
}

type WizardData = {
  history: Model<"HomeworkHistory">;
  editor: W<"EditorDraftView">;
  latest?: Model<"HomeworkVersionSummary">;
  privateDetails: W<"PrivateHomeworkView"> | null;
  policy: W<"PublicationPolicyView"> | null;
  publication?: Model<"CourseRunHomeworkPublicationSummary">;
  courseRun?: W<"CourseRunView">;
  course?: W<"CourseView">;
  title?: string;
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

const CLASS_LABELS = {
  formal: "формальная",
  content: "по смыслу, с цитатой",
  judgement: "на усмотрение ревьюера",
} as const;

function localDate(value: string | null | undefined) {
  if (!value) return "";
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

/** Сегмент из заранее известных значений плюс текущее, если оно не из набора. */
function choices(values: number[], current: number | undefined) {
  const list = [...values];
  if (
    current !== undefined &&
    Number.isFinite(current) &&
    !list.includes(current)
  )
    list.push(current);
  return list.sort((a, b) => a - b);
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

  const title =
    data.title ?? data.latest?.student_text.slice(0, 60) ?? "Задание";
  const criteria = draft.criteria ?? [];
  const stepItems = ["Для студента", "Критерии ревью", "Публикация"].map(
    (label, i) => ({
      n: i + 1,
      label,
      name: `${i + 1}. ${label}`,
      state:
        step === i
          ? ("on" as const)
          : i <= furthestStep
            ? ("done" as const)
            : ("next" as const),
      onClick: () => setStep(i),
    }),
  );
  const publicationLink = published
    ? `${window.location.origin}/#/prepare/${published}`
    : "";

  return (
    <>
      <Topbar
        crumbs={
          <Crumbs
            back="#/homeworks"
            items={[
              { href: "#/courses", label: "Курсы" },
              ...(data.course
                ? [{ href: "#/homeworks", label: data.course.title }]
                : []),
            ]}
            current={title}
          />
        }
        title={title}
        status={
          <span className="st st--draft">
            {version ? `Версия ${version.version_number}` : "Черновик"}
            {versionChanged && version ? ", есть правки" : ""}
          </span>
        }
      >
        {autoStatus && step === 2 && (
          <div className="topbar__note" role="status">
            {autoStatus}
          </div>
        )}
      </Topbar>
      <Main data-screen={step === 0 ? "К5" : step === 1 ? "К6" : "К5"}>
        {action.feedback}
        <Steps steps={stepItems} />

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
              <Card>
                <CardHead title="Условие задания">
                  <span className="caption">
                    Видно студенту на странице сдачи
                  </span>
                </CardHead>
                <CardBody>
                  <Field label="Условие">
                    <Area
                      className="inp--taller"
                      value={draft.student_text ?? ""}
                      onChange={(e) => change({ student_text: e.target.value })}
                    />
                  </Field>
                  <Field
                    group
                    label="Что сдаём"
                    hint="Какие ссылки принимаем. Файл Markdown, PDF или DOCX студент может приложить всегда."
                  >
                    <div className="btn-row">
                      {(["github", "google_docs"] as const).map((kind) => (
                        <Chk
                          key={kind}
                          checked={
                            draft.artifact_kinds?.includes(kind) ?? false
                          }
                          onChange={(e) =>
                            change({
                              artifact_kinds: e.target.checked
                                ? [...(draft.artifact_kinds ?? []), kind]
                                : (draft.artifact_kinds ?? []).filter(
                                    (v) => v !== kind,
                                  ),
                            })
                          }
                        >
                          {kind === "github" ? "GitHub" : "Google Docs"}
                        </Chk>
                      ))}
                    </div>
                  </Field>
                  <Field
                    label="Материалы для студента"
                    hint="Markdown, PDF или DOCX, до 10 МБ на файл. Эти материалы будут доступны студенту."
                  >
                    <Inp
                      type="file"
                      multiple
                      accept=".md,.pdf,.docx"
                      onChange={(e) => {
                        setMaterials(Array.from(e.target.files ?? []));
                        setDirty(true);
                      }}
                    />
                  </Field>
                  {(materials.length > 0 ||
                    (draft.material_upload_ids ?? []).length > 0) && (
                    <div className="stack--tight">
                      {materials.map((file) => (
                        <Kv key={file.name} ink label={file.name}>
                          <span className="caption">ожидает сохранения</span>
                        </Kv>
                      ))}
                      {(draft.material_upload_ids ?? []).map((id, index) => (
                        <Kv key={id} ink label={`Материал ${index + 1}`}>
                          <BtnRow end>
                            <Btn
                              size="s"
                              variant="quiet"
                              onClick={() =>
                                void action.run(async () => {
                                  const file = await ws.download(id);
                                  window.open(
                                    file.url,
                                    "_blank",
                                    "noopener,noreferrer",
                                  );
                                })
                              }
                            >
                              Открыть
                            </Btn>
                            <Btn
                              size="s"
                              variant="quiet"
                              onClick={() =>
                                change({
                                  material_upload_ids:
                                    draft.material_upload_ids!.filter(
                                      (value) => value !== id,
                                    ),
                                })
                              }
                            >
                              Убрать
                            </Btn>
                          </BtnRow>
                        </Kv>
                      ))}
                    </div>
                  )}
                </CardBody>
              </Card>
              <div className="stack">
                <Card>
                  <CardHead title="Сроки и штраф" />
                  <CardBody>
                    <Field
                      label="Срок сдачи"
                      hint="Для каждого потока срок задаётся отдельно на шаге 3."
                    >
                      <Inp
                        disabled
                        readOnly
                        value={
                          draft.submission_deadline
                            ? dayLong(draft.submission_deadline)
                            : "не задан"
                        }
                      />
                    </Field>
                    <Field
                      group
                      label="Штраф за просрочку, баллов в день"
                      hint="Считается кодом и вычитается из итога. Ревьюер видит его отдельной строкой и не может обойти."
                    >
                      <Seg
                        value={String(draft.policy?.penalty_per_day ?? 0)}
                        onChange={(v) => policy({ penalty_per_day: Number(v) })}
                        options={choices(
                          [0, 0.5, 1],
                          draft.policy?.penalty_per_day,
                        ).map((v) => ({
                          value: String(v),
                          label: v === 0 ? "Нет" : num(v),
                        }))}
                      />
                    </Field>
                    <Field
                      group
                      label="Попыток ИИ-ревью до сдачи"
                      hint="ИИ-ревью баллов не ставит, оно показывает студенту, чего ещё не хватает."
                    >
                      <Seg
                        value={String(draft.policy?.self_review_limit ?? 0)}
                        onChange={(v) =>
                          policy({ self_review_limit: Number(v) })
                        }
                        options={choices(
                          [0, 1, 3, 5],
                          draft.policy?.self_review_limit,
                        ).map((v) => ({
                          value: String(v),
                          label: v === 0 ? "Нет" : String(v),
                        }))}
                      />
                    </Field>
                    <div className="row-2 row-2--tight">
                      <Field label="Дней на доработку">
                        <Inp
                          small
                          mono
                          type="number"
                          min={1}
                          max={365}
                          value={
                            Number.isFinite(draft.policy?.revision_days)
                              ? draft.policy?.revision_days
                              : ""
                          }
                          onChange={(e) =>
                            policy({ revision_days: e.target.valueAsNumber })
                          }
                        />
                      </Field>
                      <Field label="Максимум пересдач">
                        <Inp
                          small
                          mono
                          type="number"
                          min={0}
                          value={
                            Number.isFinite(draft.policy?.max_resubmissions)
                              ? draft.policy?.max_resubmissions
                              : ""
                          }
                          onChange={(e) =>
                            policy({
                              max_resubmissions: e.target.valueAsNumber,
                            })
                          }
                        />
                      </Field>
                    </div>
                  </CardBody>
                </Card>
                <Callout>
                  <p>
                    Задание привязано к курсу
                    {data.course ? ` «${data.course.title}»` : ""} и
                    переиспользуется в его потоках. Срок и ссылку для сдачи
                    каждый поток получает свои.
                  </p>
                </Callout>
              </div>
            </div>
            <Dock
              actions={
                <Btn variant="dark" type="submit" disabled={action.busy}>
                  Дальше: критерии
                </Btn>
              }
            >
              <span className="caption" role="status">
                {autoStatus || "Черновик сохраняется сам"}
              </span>
            </Dock>
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
                <Card>
                  <CardHead title="Критерии">
                    <Btn
                      size="s"
                      variant="dark"
                      onClick={() => {
                        const key = crypto.randomUUID();
                        change({
                          criteria: [
                            ...criteria,
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
                  </CardHead>
                  <CardBody>
                    {criteria.length === 0 && (
                      <p className="small dim">
                        Критериев пока нет. Добавьте первый: по нему будут
                        работать и модель, и ревьюер.
                      </p>
                    )}
                    {criteria.map((c, i) => {
                      const edit = (patch: Partial<W<"EditorCriterion">>) =>
                        change({
                          criteria: criteria.map((value) =>
                            value.key === c.key
                              ? { ...value, ...patch }
                              : value,
                          ),
                        });
                      const classLabel = `${CLASS_LABELS[c.check_class ?? "content"]}${
                        c.check_class === "content" && c.evaluate_quality
                          ? ", плюс качество"
                          : ""
                      }`;
                      return editingCriterion === c.key ? (
                        <Crit
                          key={c.key}
                          top={
                            <>
                              <Drag />
                              <span className="label">
                                критерий {i + 1} из {criteria.length}
                              </span>
                              <button
                                type="button"
                                className="acc__chev"
                                aria-label="Свернуть критерий"
                                onClick={() => setEditingCriterion(undefined)}
                              >
                                ▲
                              </button>
                            </>
                          }
                        >
                          <Field label="Название критерия">
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
                              rows={2}
                              value={c.description}
                              onChange={(e) =>
                                edit({ description: e.target.value })
                              }
                            />
                          </Field>
                          <Field group label="Как проверяем">
                            <Seg
                              value={c.check_class ?? "content"}
                              onChange={(value) =>
                                edit({
                                  check_class: value,
                                  evaluate_quality:
                                    value === "content"
                                      ? c.evaluate_quality
                                      : false,
                                })
                              }
                              options={(
                                ["formal", "content", "judgement"] as const
                              ).map((value) => ({
                                value,
                                label: CLASS_LABELS[value],
                              }))}
                            />
                          </Field>
                          {c.check_class === "content" && (
                            <Field
                              group
                              hint="Второй шаг после того, как модель нашла и процитировала. Считается только если первый шаг дал «да»."
                            >
                              <Chk
                                className="small"
                                checked={c.evaluate_quality ?? false}
                                onChange={(e) =>
                                  edit({ evaluate_quality: e.target.checked })
                                }
                              >
                                Ещё и оценить качество
                              </Chk>
                            </Field>
                          )}
                          <div className="crit__row">
                            <Field label="Баллов за критерий">
                              <Inp
                                small
                                mono
                                className="inp--w-88"
                                type="number"
                                required
                                min={0}
                                step="any"
                                value={
                                  Number.isFinite(c.max_points)
                                    ? c.max_points
                                    : ""
                                }
                                onChange={(e) =>
                                  edit({ max_points: e.target.valueAsNumber })
                                }
                              />
                            </Field>
                            <Field label="Шаг">
                              <Inp
                                small
                                mono
                                className="inp--w-88"
                                type="number"
                                required
                                min={0.000001}
                                step="any"
                                value={
                                  Number.isFinite(c.score_step)
                                    ? c.score_step
                                    : ""
                                }
                                onChange={(e) =>
                                  edit({ score_step: e.target.valueAsNumber })
                                }
                              />
                            </Field>
                            <Field group label="Что увидит ревьюер">
                              <Scale
                                readOnly
                                max={c.max_points ?? 0}
                                step={c.score_step ?? 0.5}
                                value={null}
                                label="Шкала"
                              />
                            </Field>
                          </div>
                          <BtnRow className="crit__actions">
                            <Btn
                              size="s"
                              variant="quiet"
                              disabled={i === 0}
                              aria-label={`Поднять критерий ${i + 1}`}
                              onClick={() => {
                                const list = [...criteria];
                                [list[i - 1], list[i]] = [list[i], list[i - 1]];
                                change({ criteria: list });
                              }}
                            >
                              ↑ Выше
                            </Btn>
                            <Btn
                              size="s"
                              variant="quiet"
                              disabled={i === criteria.length - 1}
                              aria-label={`Опустить критерий ${i + 1}`}
                              onClick={() => {
                                const list = [...criteria];
                                [list[i + 1], list[i]] = [list[i], list[i + 1]];
                                change({ criteria: list });
                              }}
                            >
                              ↓ Ниже
                            </Btn>
                            <Btn
                              size="s"
                              variant="danger"
                              onClick={() =>
                                change({
                                  criteria: criteria.filter(
                                    (value) => value.key !== c.key,
                                  ),
                                })
                              }
                            >
                              Убрать критерий
                            </Btn>
                          </BtnRow>
                        </Crit>
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
                            className="acc__h acc__h--btn"
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
                            <span className="caption">{classLabel}</span>
                            <span className="mono acc__pts">
                              {num(c.max_points)}
                            </span>
                            <span className="acc__chev" aria-hidden="true">
                              ▼
                            </span>
                          </button>
                        </div>
                      );
                    })}
                    <div className="threshold">
                      <label
                        className="small threshold__lbl"
                        htmlFor="pass-score"
                      >
                        Порог зачёта
                      </label>
                      <Inp
                        id="pass-score"
                        small
                        mono
                        className="inp--w-56 inp--center"
                        aria-label="Порог зачёта"
                        type="number"
                        required
                        min={0}
                        max={
                          Number.isFinite(draft.max_score)
                            ? draft.max_score
                            : undefined
                        }
                        step="any"
                        value={
                          Number.isFinite(draft.policy?.pass_score)
                            ? draft.policy?.pass_score
                            : ""
                        }
                        onChange={(e) =>
                          policy({ pass_score: e.target.valueAsNumber })
                        }
                      />
                      <span className="caption">
                        {plural(
                          draft.policy?.pass_score ?? 0,
                          "балл",
                          "балла",
                          "баллов",
                        )}{" "}
                        из {num(draft.max_score)} возможных
                      </span>
                    </div>
                  </CardBody>
                </Card>
                <Card>
                  <CardHead title="Рекомендации ревьюерам" />
                  <CardBody>
                    <Area
                      aria-label="Рекомендации ревьюерам"
                      className="inp--guidance"
                      value={draft.reviewer_guidance}
                      onChange={(e) =>
                        change({ reviewer_guidance: e.target.value })
                      }
                      placeholder="То, что раньше передавалось голосом на калибровочной встрече"
                    />
                  </CardBody>
                </Card>
              </div>
              <div className="stack">
                <Card>
                  <CardHead title="Эталонное решение">
                    <span className="caption">Необязательно</span>
                  </CardHead>
                  <CardBody>
                    <label className="drop">
                      <b>
                        {reference
                          ? reference.name
                          : "Перетащите файл или выберите"}
                      </b>
                      <span>PDF, Markdown или DOCX</span>
                      <input
                        className="sr-only"
                        type="file"
                        accept=".md,.pdf,.docx"
                        aria-label="Эталонное решение"
                        onChange={(e) => {
                          setReference(e.target.files?.[0]);
                          setDirty(true);
                        }}
                      />
                    </label>
                    <div className="caption drop__note">
                      Используется как пример для модели и ревьюеров при
                      калибровке. Студентам не показывается никогда.
                    </div>
                    {draft.reference_upload_id && (
                      <BtnRow className="btn-row--after">
                        <Btn
                          size="s"
                          variant="quiet"
                          onClick={() => {
                            change({ reference_upload_id: null });
                            setReference(undefined);
                          }}
                        >
                          Удалить эталон из новой версии
                        </Btn>
                      </BtnRow>
                    )}
                  </CardBody>
                </Card>
                <Card>
                  <CardHead title="Версии" />
                  <CardBody tight>
                    {versionChanged && (
                      <Kv
                        ink
                        label={`Версия ${(version?.version_number ?? 0) + 1}, черновик`}
                      >
                        вы, сейчас
                      </Kv>
                    )}
                    {[...data.history.versions]
                      .sort((a, b) => b.version_number - a.version_number)
                      .map((v) => {
                        const runsPublished =
                          data.history.course_run_publications.filter(
                            (p) => p.homework_version_id === v.id,
                          ).length;
                        return (
                          <Kv
                            key={v.id}
                            ink
                            label={`Версия ${v.version_number}${runsPublished ? ", опубликована" : ""}`}
                          >
                            {runsPublished
                              ? `${runsPublished} ${plural(runsPublished, "поток", "потока", "потоков")}`
                              : "не публиковалась"}
                          </Kv>
                        );
                      })}
                    {data.history.versions.length === 0 && !versionChanged && (
                      <p className="small dim">Сохранённых версий ещё нет.</p>
                    )}
                  </CardBody>
                </Card>
              </div>
            </div>
            <Dock
              actions={
                <Btn
                  variant="dark"
                  type="submit"
                  disabled={action.busy || !criteria.length}
                >
                  Дальше: публикация
                </Btn>
              }
            >
              <Sum
                value={num(draft.max_score)}
                of={`${plural(Math.round(draft.max_score), "балл", "балла", "баллов")} за ${criteria.length} ${plural(criteria.length, "критерий", "критерия", "критериев")}`}
              />
              <span className="caption" role="status">
                {autoStatus || "Черновик сохраняется сам"}
              </span>
            </Dock>
          </form>
        )}

        {step === 2 && (
          <div className="row-side">
            <Card>
              <CardHead
                title={`Публикация в потоке${data.courseRun ? ` «${data.courseRun.title}»` : ""}`}
                sub="Поток получает свой срок сдачи и ссылку для Stepik"
              />
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
                      throw new Error(
                        "Сначала сохраните версию, лимит и сроки.",
                      );
                    if (versionChanged)
                      throw new Error(
                        "Условия или критерии изменились. Сохраните новую версию на шаге 2.",
                      );
                    if (
                      !Number.isInteger(draft.policy.self_review_limit) ||
                      draft.policy.self_review_limit < 0
                    )
                      throw new Error(
                        "Укажите конечный целый лимит ИИ-ревью от 0.",
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
                    setVersion(
                      updated.versions.find((v) => v.id === version.id),
                    );
                  });
                }}
              >
                <CardBody>
                  {versionChanged && (
                    <Callout tone="warn" className="feedback">
                      <p>
                        Условия, критерии или закрытые материалы изменились.
                        Вернитесь на шаг 2 и сохраните новую версию перед
                        публикацией.
                      </p>
                    </Callout>
                  )}
                  <div className="row-2 row-2--tight">
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
                  </div>
                  <div className="stack--tight">
                    <Kv label="Публикуется версия">
                      {version ? version.version_number : "ещё не сохранена"}
                    </Kv>
                    <Kv label="Порог зачёта">
                      {draft.policy
                        ? `${num(draft.policy.pass_score)} из ${num(draft.max_score)}`
                        : "не задан"}
                    </Kv>
                    <Kv label="Попыток ИИ-ревью">
                      {draft.policy?.self_review_limit ?? "не задано"}
                    </Kv>
                    <Kv label="Штраф за день просрочки">
                      {draft.policy?.penalty_per_day
                        ? num(draft.policy.penalty_per_day)
                        : "нет"}
                    </Kv>
                  </div>
                </CardBody>
                <CardFoot>
                  <span>
                    {published
                      ? "Задание опубликовано, ссылка ниже."
                      : "После публикации студенты увидят задание по ссылке."}
                  </span>
                  <Btn
                    variant="pri"
                    type="submit"
                    disabled={
                      action.busy || !version || !draft.policy || versionChanged
                    }
                  >
                    Опубликовать задание
                  </Btn>
                </CardFoot>
              </form>
            </Card>
            <div className="stack">
              {published && (
                <Card hard>
                  <CardHead
                    title="Ссылка для Stepik"
                    sub="Вставьте её в шаг курса"
                  />
                  <CardBody>
                    <Field label="Ссылка">
                      <Inp mono readOnly value={publicationLink} />
                    </Field>
                    <BtnRow>
                      <Btn
                        size="s"
                        onClick={() =>
                          void navigator.clipboard.writeText(publicationLink)
                        }
                      >
                        Скопировать ссылку
                      </Btn>
                    </BtnRow>
                  </CardBody>
                </Card>
              )}
              <Card>
                <CardHead title="Публикации" />
                {data.history.course_run_publications.length === 0 ? (
                  <CardBody>
                    <p className="small dim">
                      Задание ещё не публиковалось ни в одном потоке.
                    </p>
                  </CardBody>
                ) : (
                  <CardBody tight>
                    {data.history.course_run_publications.map((p) => (
                      <Kv
                        key={p.id}
                        ink
                        label={
                          p.is_current
                            ? "Текущая публикация"
                            : "Прошлая публикация"
                        }
                      >
                        {dayLong(p.published_at)}
                        <span className="caption">
                          {" "}
                          · сдать до {dayLong(p.submission_deadline)}
                        </span>
                      </Kv>
                    ))}
                  </CardBody>
                )}
              </Card>
            </div>
          </div>
        )}
      </Main>
    </>
  );
}
