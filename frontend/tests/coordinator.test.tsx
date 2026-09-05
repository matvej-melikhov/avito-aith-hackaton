import { WorkspaceWorks } from "../src/pages/WorkspaceLists";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import {
  WorkspaceCatalog,
  WorkspaceHomeworkDirectory,
  WorkspacePreferences,
} from "../src/pages/WorkspaceCatalog";
import { WorkspaceHomework } from "../src/pages/WorkspaceHomework";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";

it("autosaved wizard changes survive reopening but cannot publish the old version", async () => {
  const user = userEvent.setup();
  const api = new ApiClient(createDemoTransport());
  const session = await api.session();
  const ws = new WorkspaceClient(api);
  const view = render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  const condition = await screen.findByLabelText("Условие");
  await user.type(condition, " Уточнение условия.");
  expect(
    screen.queryByRole("button", { name: "Сохранить черновик" }),
  ).not.toBeInTheDocument();
  await screen.findByText("Изменения сохранены");
  view.unmount();
  render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  expect(
    ((await screen.findByLabelText("Условие")) as HTMLTextAreaElement).value,
  ).toContain("Уточнение условия.");
  await user.click(screen.getByRole("button", { name: "3. Публикация" }));
  expect(
    screen.getByRole("button", { name: "Опубликовать задание" }),
  ).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "2. Критерии ревью" }));
  expect(
    screen.queryByRole("button", { name: "Назад к шагу 1" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Максимальный балл")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Дальше: публикация" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Опубликовать задание" }),
    ).toBeEnabled(),
  );
});

it("the assignment directory exposes unpublished homework through a real editor link", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const original = ws.courseHomeworks;
  ws.courseHomeworks = async (courseId) => ({
    course_id: courseId,
    items:
      courseId === ids.course
        ? [
            {
              id: ids.homework,
              title: "Черновик задания",
              revision: 0,
              latest_version_number: null,
              published_run_ids: [],
            },
          ]
        : [],
  });
  render(<WorkspaceHomeworkDirectory ws={ws} />);
  await screen.findByText("Черновик задания");
  expect(screen.getByRole("link", { name: "Настроить →" })).toHaveAttribute(
    "href",
    `#/homework/${ids.homework}?run=${ids.run}`,
  );
  expect(screen.getByText("Не опубликовано")).toBeInTheDocument();
  ws.courseHomeworks = original;
});

it("reviewer settings show only courses and absence while preserving compatibility fields", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  let saved: Record<string, unknown> | undefined;
  const api = new ApiClient(async (input, init) => {
    if (init?.body) {
      const body = JSON.parse(String(init.body));
      if (body.command_name === "save_preferences") saved = body.payload;
    }
    return demo(input, init);
  });
  const ws = new WorkspaceClient(api);
  const prior = await ws.preferences();
  render(<WorkspacePreferences ws={ws} session={await api.session()} />);
  await screen.findByRole("heading", {
    name: "Курсы, которые готов проверять",
  });
  expect(screen.queryByLabelText("Минут на проверку")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Плановое время" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Статистика" })).toHaveAttribute(
    "href",
    "#/statistics",
  );
  const poolSwitch = screen.getByRole("switch", {
    name: /Показывать мне работы из пула/,
  });
  expect(poolSwitch).toBeChecked();
  await user.click(poolSwitch);
  await user.click(
    screen.getByLabelText("В пуле по моим курсам появились новые работы"),
  );
  await user.click(screen.getByRole("button", { name: "Отменить" }));
  expect(poolSwitch).toBeChecked();
  expect(
    screen.getByLabelText("В пуле по моим курсам появились новые работы"),
  ).not.toBeChecked();
  await user.click(poolSwitch);
  await user.click(
    screen.getByLabelText("В пуле по моим курсам появились новые работы"),
  );
  await user.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(saved).toBeDefined());
  expect(saved!.show_pool).toBe(false);
  expect(saved!.notifications).toEqual({
    deadline: true,
    revision: true,
    pool: true,
  });
  expect(saved!.planned_minutes).toBe(prior.value?.planned_minutes ?? 0);
  if (prior.value?.until_at) expect(saved!.until_at).toBe(prior.value.until_at);
});

it("criterion settings autosave, derive total and reopen a single expanded form", async () => {
  const user = userEvent.setup();
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const session = await api.session();
  const view = render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  await screen.findByLabelText("Условие");
  await user.click(screen.getByRole("button", { name: "2. Критерии ревью" }));
  await user.click(screen.getByRole("button", { name: "Добавить критерий" }));
  expect(screen.getAllByLabelText("Название критерия")).toHaveLength(1);
  await user.type(
    screen.getByLabelText("Название критерия"),
    "Качество объяснения",
  );
  await user.clear(screen.getByLabelText("Баллов за критерий"));
  await user.type(screen.getByLabelText("Баллов за критерий"), "2.5");
  await user.clear(screen.getByLabelText("Шаг"));
  await user.type(screen.getByLabelText("Шаг"), "0.25");
  await user.click(screen.getByLabelText("Оценивать качество"));
  expect(screen.getByLabelText("Порог зачёта")).toHaveAttribute("max", "12.5");
  await screen.findByText("Изменения сохранены");
  view.unmount();
  render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  await screen.findByLabelText("Условие");
  await user.click(screen.getByRole("button", { name: "2. Критерии ревью" }));
  await user.click(screen.getByRole("button", { name: /Качество объяснения/ }));
  expect(screen.getAllByLabelText("Название критерия")).toHaveLength(1);
  expect(screen.getByLabelText("Шаг")).toHaveValue(0.25);
  expect(screen.getByLabelText("Оценивать качество")).toBeChecked();
});

it("coordinator pool requests unfinished scope and registry exposes completed results and export", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const base = (await ws.works()).items[0];
  const calls: string[] = [];
  ws.works = async (params = {}) => {
    calls.push(String(params.view));
    const pending = {
      ...base,
      title: "Ожидающая работа",
      status: "pending_review",
      score: null,
    };
    const completed = {
      ...base,
      submission_id: "00000000-0000-4000-8000-000000999999",
      title: "Завершённая работа",
      status: "passed",
      score: 8.5,
    };
    const items = params.view === "pool" ? [pending] : [pending, completed];
    return { items, total: items.length, limit: 20, offset: 0 };
  };
  const view = render(
    <WorkspaceWorks ws={ws} role="methodologist" coordinatorPool />,
  );
  await screen.findByText("Ожидающая работа");
  expect(calls).toContain("pool");
  expect(screen.queryByText("Завершённая работа")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Выгрузить" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Напомнить ревьюерам" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "Участие ревьюеров" }),
  ).toBeInTheDocument();
  view.rerender(<WorkspaceWorks ws={ws} role="methodologist" />);
  await screen.findByText("Завершённая работа");
  expect(calls).toContain("all");
  expect(screen.getByRole("button", { name: "Выгрузить" })).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "Опубликованный балл" }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByRole("link", { name: "Результат для студента" }),
  ).toHaveLength(2);
});

it("export has immutable flow context and connected audience and format controls", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  let exported: Record<string, unknown> | undefined;
  const api = new ApiClient(async (input, init) => {
    if (init?.body) {
      const body = JSON.parse(String(init.body));
      if (body.command_name === "create_export") exported = body.payload;
    }
    return demo(input, init);
  });
  const ws = new WorkspaceClient(api);
  window.location.hash = `#/registry?run=${ids.run}`;
  render(<WorkspaceWorks ws={ws} role="methodologist" />);
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Поток") as HTMLSelectElement).options.length,
    ).toBeGreaterThan(1),
  );
  await user.click(screen.getByRole("button", { name: "Выгрузить" }));
  await screen.findByRole("dialog", { name: "Выгрузка" });
  expect(screen.getByText(/Все задания, поток/)).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Для студентов — копия" }),
  );
  expect(screen.queryByLabelText("ID ревьюера")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "XLSX" }));
  await user.click(
    screen.getByRole("button", { name: "Выгрузить с пустым баллом" }),
  );
  await user.click(screen.getByRole("button", { name: "Подготовить файл" }));
  await waitFor(() =>
    expect(exported).toMatchObject({
      course_run_id: ids.run,
      audience: "students",
      format: "xlsx",
      include_unpublished: true,
      columns: ["student_id", "score", "status"],
    }),
  );
});

it("homework title and allowed submission modes autosave and publish together", async () => {
  const user = userEvent.setup();
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const session = await api.session();
  const view = render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  const name = await screen.findByLabelText("Название");
  await user.clear(name);
  await user.type(name, "Задание с файлом");
  await user.click(screen.getByRole("button", { name: "Только файлы" }));
  await screen.findByText("Изменения сохранены");
  view.unmount();
  render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  expect(await screen.findByLabelText("Название")).toHaveValue(
    "Задание с файлом",
  );
  expect(screen.getByRole("button", { name: "Только файлы" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await user.click(screen.getByRole("button", { name: "2. Критерии ревью" }));
  await user.click(screen.getByRole("button", { name: "Дальше: публикация" }));
  await screen.findByRole("button", { name: "Опубликовать задание" });
  const history = await api.homework(ids.homework);
  const version = [...history.versions].sort(
    (a, b) => b.version_number - a.version_number,
  )[0];
  expect((await ws.privateHomework(version.id)).allowed_sources).toEqual([
    "upload",
  ]);
});

it("team export preserves selected homework scope and additional result columns", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  let exported: Record<string, unknown> | undefined;
  const api = new ApiClient(async (input, init) => {
    if (init?.body) {
      const body = JSON.parse(String(init.body));
      if (body.command_name === "create_export") exported = body.payload;
    }
    return demo(input, init);
  });
  const ws = new WorkspaceClient(api);
  const homework = (await ws.courseHomeworks(ids.course)).items.find(
    (item) => item.id === ids.homework,
  )!;
  window.location.hash = `#/registry?run=${ids.run}`;
  render(<WorkspaceWorks ws={ws} role="methodologist" />);
  await screen.findByRole("option", { name: homework.title });
  await user.selectOptions(screen.getByLabelText("Задание"), ids.homework);
  await user.click(screen.getByRole("button", { name: "Выгрузить" }));
  expect(
    await screen.findByRole("dialog", { name: "Выгрузка" }),
  ).toHaveTextContent(homework.title);
  await user.click(screen.getByLabelText("Баллы по каждому критерию"));
  await user.click(screen.getByLabelText("Ссылка на работу"));
  await user.click(screen.getByRole("button", { name: "Подготовить файл" }));
  await waitFor(() =>
    expect(exported).toMatchObject({
      course_run_id: ids.run,
      homework_id: ids.homework,
      audience: "team",
      columns: expect.arrayContaining(["criterion_points", "artifact_url"]),
    }),
  );
});

it("coordinator opening an unreviewed work is read-only and pool filters reach server", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  const mutations: string[] = [];
  const api = new ApiClient(async (input, init) => {
    if (init?.body) mutations.push(JSON.parse(String(init.body)).command_name);
    return demo(input, init);
  });
  const ws = new WorkspaceClient(api);
  const base = (await ws.works()).items[0];
  const requests: NonNullable<Parameters<WorkspaceClient["works"]>[0]>[] = [];
  ws.works = async (params = {}) => {
    requests.push(params);
    return {
      items: [
        {
          ...base,
          status: "pending_review",
          review_iteration_id: null,
          review_submission_version_id: null,
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    };
  };
  render(<WorkspaceWorks ws={ws} role="methodologist" coordinatorPool />);
  await screen.findByRole("button", { name: "Открыть проверку" });
  await user.click(screen.getByLabelText("Ждут ревьюера больше 3 дней"));
  await waitFor(() =>
    expect(
      requests.some(
        (params) => params.stuck === "true" && params.view === "pool",
      ),
    ).toBe(true),
  );
  await user.click(screen.getByRole("button", { name: "Открыть проверку" }));
  expect(window.location.hash).toBe(`#/submissions/${base.submission_id}`);
  expect(mutations).not.toContain("open_work");
});

it("registry has eight counted status tabs and source history columns", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  render(<WorkspaceWorks ws={ws} role="methodologist" />);
  const tabs = await screen.findByRole("navigation", {
    name: "Статусы домашних работ",
  });
  expect(within(tabs).getAllByRole("button")).toHaveLength(8);
  expect(screen.queryByLabelText("Статус")).not.toBeInTheDocument();
  await screen.findByRole("columnheader", { name: "Ревьюер" });
  expect(
    screen.getByRole("columnheader", { name: "Сдана" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("columnheader", { name: "Обновлена" }),
  ).toBeInTheDocument();
});

it("selected typical failures append to course description without replacing course fields", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  let update: Record<string, unknown> | undefined;
  const api = new ApiClient(async (input, init) => {
    if (init?.body) {
      const body = JSON.parse(String(init.body));
      if (body.command_name === "update_course") update = body.payload;
    }
    return demo(input, init);
  });
  const ws = new WorkspaceClient(api);
  const before = (await ws.catalog()).courses.find(
    (course) => course.id === ids.course,
  )!;
  ws.insights = async () => ({
    status_counts: {
      all: 3,
      draft: 0,
      pending_review: 1,
      in_review: 1,
      needs_changes: 0,
      repeat_review: 0,
      passed: 1,
      failed: 0,
    },
    pool_metrics: {
      waiting: 1,
      submitted: 3,
      stuck: 1,
      active_reviewers: 1,
      total_reviewers: 2,
      average_wait_minutes: 1440,
    },
    typical_failures: [
      {
        criterion_id: ids.criterion,
        title: "Нет обработки ошибок",
        failed: 2,
        reviewed: 3,
        ratio: 2 / 3,
      },
    ],
  });
  window.location.hash = `#/coord-pool?run=${ids.run}`;
  render(<WorkspaceWorks ws={ws} role="methodologist" coordinatorPool />);
  await user.click(await screen.findByLabelText("Нет обработки ошибок"));
  await user.click(
    screen.getByRole("button", { name: "Добавить в описание курса" }),
  );
  await waitFor(() =>
    expect(update).toMatchObject({
      title: before.title,
      owner_id: before.owner_id,
      stepik_url: before.stepik_url ?? null,
    }),
  );
  expect(update!.description).toContain(before.description);
  expect(update!.description).toContain(
    "Нет обработки ошибок: не выполнили 2 из 3.",
  );
});

it("draft-only registry rows open readonly metadata instead of a null submission route", async () => {
  const user = userEvent.setup();
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const base = (await ws.works()).items[0];
  ws.works = async () => ({
    items: [
      {
        ...base,
        title: "Несданный черновик",
        submission_id: null,
        draft_id: ids.homework,
        status: "draft",
        submitted_at: null,
        review_iteration_id: null,
        attempt: 0,
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  });
  window.location.hash = `#/registry?run=${ids.run}`;
  render(<WorkspaceWorks ws={ws} role="methodologist" />);
  await user.click(
    await screen.findByRole("button", { name: "Открыть работу" }),
  );
  expect(
    await screen.findByRole("dialog", { name: "Черновик работы" }),
  ).toHaveTextContent("Черновик · ещё не сдана");
  expect(window.location.hash).not.toContain("null");
  expect(
    screen.queryByRole("link", { name: "Результат для студента" }),
  ).not.toBeInTheDocument();
});

it("overview active counts and drilldowns share reviewing scope without a ninth status tab", async () => {
  const user = userEvent.setup();
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const base = (await ws.works()).items[0];
  const requests: NonNullable<Parameters<WorkspaceClient["works"]>[0]>[] = [];
  ws.works = async (params = {}) => {
    requests.push(params);
    return {
      items: params.state === "reviewing" ? [base] : [],
      total: params.state === "reviewing" ? 7 : 0,
      limit: Number(params.limit ?? 20),
      offset: 0,
    };
  };
  const overview = render(<WorkspaceCatalog ws={ws} />);
  const label = await screen.findByText("работ на проверке");
  const tile = label.closest("a")!;
  expect(tile).toHaveAttribute("href", "#/registry?state=reviewing");
  expect(within(tile).getByText("7")).toBeInTheDocument();
  expect(requests.some((params) => params.state === "in_review")).toBe(false);
  overview.unmount();
  requests.length = 0;
  window.location.hash = "#/registry?state=reviewing";
  render(<WorkspaceWorks ws={ws} role="methodologist" />);
  expect(await screen.findByText("На проверке")).toBeInTheDocument();
  const tabs = await screen.findByRole("navigation", {
    name: "Статусы домашних работ",
  });
  expect(within(tabs).getAllByRole("button")).toHaveLength(8);
  expect(requests.some((params) => params.state === "reviewing")).toBe(true);
  await user.click(screen.getByRole("button", { name: "Сбросить фильтр" }));
  await waitFor(() =>
    expect(requests.some((params) => params.state === "")).toBe(true),
  );
  expect(screen.queryByText("На проверке")).not.toBeInTheDocument();
});
