import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import {
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
    screen.getByLabelText("В пуле по моим курсам появилось что-то новое"),
  );
  await user.click(screen.getByRole("button", { name: "Отменить" }));
  expect(poolSwitch).toBeChecked();
  expect(
    screen.getByLabelText("В пуле по моим курсам появилось что-то новое"),
  ).not.toBeChecked();
  await user.click(poolSwitch);
  await user.click(
    screen.getByLabelText("В пуле по моим курсам появилось что-то новое"),
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
  await user.click(screen.getByLabelText("Ещё и оценить качество"));
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
  expect(screen.getByLabelText("Ещё и оценить качество")).toBeChecked();
});
