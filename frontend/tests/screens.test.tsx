import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { App } from "../src/App";
import { ApiClient } from "../src/api/client";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
it("opens the recommended work and requires a saved draft plus human confirmation", async () => {
  sessionStorage.setItem("review-mode", "1");
  const user = userEvent.setup();
  const demo = createDemoTransport();
  const commandNames: string[] = [];
  const api = new ApiClient(async (input, init) => {
    if (init?.body)
      commandNames.push(JSON.parse(String(init.body)).command_name);
    return demo(input, init);
  });
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} demo />);
  await screen.findByLabelText("Обратная связь студенту");
  expect(
    screen.queryByRole("button", { name: "Сохранить черновик" }),
  ).toBeNull();
  const publish = screen.getByRole("button", { name: "Зачесть" });
  expect(screen.queryByText("Режим проверки")).toBeNull();
  expect(screen.queryByRole("button", { name: "Пропустить" })).toBeNull();
  expect(publish).toBeDisabled();
  // Балл ставится пилюлей шкалы, как на Р5.
  await user.click(
    within(
      screen.getByRole("group", {
        name: "Баллы: HTTP API и обработка ошибок, шкала",
      }),
    ).getByRole("button", { name: "0,5" }),
  );
  await user.type(
    screen.getByLabelText("Обратная связь студенту"),
    "Хорошая работа. Добавьте тест редиректа.",
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Зачесть" })).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Зачесть" }));
  expect(commandNames).not.toContain("publish_workspace_review");
  await user.click(
    screen.getByRole("button", { name: "Подтвердить публикацию" }),
  );
  await screen.findByText("Зачтена");
  expect(window.location.hash).toBe(`#/reviews/${ids.review}`);
  expect(
    await screen.findByRole("button", { name: "Следующая работа" }),
  ).toBeEnabled();
  expect(commandNames).not.toContain("record_review_responsibility");
  sessionStorage.removeItem("review-mode");
  expect(
    commandNames.filter((n) => n === "publish_workspace_review"),
  ).toHaveLength(1);
});
it("submits the latest saved source without requiring a self-review", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  let submitted = false;
  const transport = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await demo(input, init);
      if (
        init?.body &&
        JSON.parse(String(init.body)).command_name === "submit_work_draft" &&
        response.ok
      )
        submitted = true;
      if (String(input).includes("/student-context") && !submitted)
        return new Response(
          JSON.stringify({ ...(await response.json()), submission_id: null }),
          { status: response.status },
        );
      return response;
    },
  );
  const api = new ApiClient(transport);
  window.location.hash = `/submit/${ids.run}/${ids.publication}`;
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["student"],
  });
  render(<App api={api} />);
  const input = await screen.findByLabelText(
    "Ссылка на репозиторий или Google Docs",
  );
  await user.type(input, "https://github.com/example/one");
  await user.clear(input);
  await user.type(input, "https://github.com/example/two");
  await user.click(screen.getByRole("button", { name: "Отправить на ревью" }));
  await screen.findByRole("heading", { name: "HTTP-сервис коротких ссылок" });
  await waitFor(() => expect(window.location.hash).toContain("/submissions/"));
  const commands = transport.mock.calls
    .filter(([, init]) => init?.body)
    .map(([, init]) => JSON.parse(String(init!.body)));
  const draft = commands
    .slice()
    .reverse()
    .find((c) => c.command_name === "save_work_draft");
  expect(draft.payload.artifact_url).toBe("https://github.com/example/two");
  expect(commands.some((c) => c.command_name === "start_self_review")).toBe(
    false,
  );
  expect(commands.some((c) => c.command_name === "prepare_work_draft")).toBe(
    true,
  );
  expect(commands.some((c) => c.command_name === "submit_work_draft")).toBe(
    true,
  );
  expect(screen.queryByLabelText("Роль")).not.toBeInTheDocument();
});
it("a student cannot mount the reviewer editor from a direct URL", async () => {
  const api = new ApiClient(createDemoTransport());
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["student"],
  });
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} />);
  await screen.findByText(/Страница недоступна/);
  expect(
    screen.queryByRole("button", { name: "Зачесть" }),
  ).not.toBeInTheDocument();
});
it("shows login for 401 without falling back to demo data", async () => {
  const api = new ApiClient(
    async () =>
      new Response(
        JSON.stringify({
          code: "unauthenticated",
          message: "login",
          action: null,
        }),
        { status: 401 },
      ),
  );
  render(<App api={api} />);
  await screen.findByRole("heading", { name: "Войти в рабочее пространство" });
  expect(screen.queryByText("Go · осень 2026")).not.toBeInTheDocument();
});
it("does not erase edited feedback when save fails with a conflict", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  const api = new ApiClient(async (input, init) =>
    init?.method === "POST" && String(input).endsWith("/draft")
      ? new Response(
          JSON.stringify({
            code: "conflict",
            message: "conflict",
            action: null,
          }),
          { status: 409 },
        )
      : demo(input, init),
  );
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} />);
  await screen.findByLabelText("Обратная связь студенту");
  await user.type(
    screen.getByLabelText("Обратная связь студенту"),
    "Не потерять этот текст",
  );
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Обратная связь студенту")).toHaveValue(
    "Не потерять этот текст",
  );
  expect(screen.getByRole("button", { name: "Зачесть" })).toBeDisabled();
});
it("lets the coordinator edit and publish without taking reviewer responsibility", async () => {
  const api = new ApiClient(createDemoTransport());
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["methodologist"],
  });
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} />);
  expect(
    await screen.findByLabelText("Баллы: HTTP API и обработка ошибок"),
  ).toBeEnabled();
  expect(screen.getByLabelText("Обратная связь студенту")).toBeEnabled();
  expect(screen.getByRole("button", { name: "Зачесть" })).toBeInTheDocument();
  expect(screen.queryByText("только просмотр")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Снять с себя проверку" }),
  ).toBeNull();
});
it("expires a resource session once without an automatic authentication retry loop", async () => {
  const demo = createDemoTransport();
  const transport = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes("/v2/works")
        ? new Response(
            JSON.stringify({
              code: "unauthenticated",
              message: "expired",
              action: null,
            }),
            { status: 401 },
          )
        : demo(input, init),
  );
  render(<App api={new ApiClient(transport)} />);
  await screen.findByRole("heading", { name: "Войти в рабочее пространство" });
  // Список и два счётчика запрашиваются по одному разу; после 401 повторов нет.
  expect(
    transport.mock.calls.filter(([url]) => String(url).includes("/v2/works")),
  ).toHaveLength(3);
});

it("prepares the first URL draft before self-review and updates server quota without submitting", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  let saved = false;
  const commandNames: string[] = [];
  const transport = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.body) {
        const command = JSON.parse(String(init.body));
        commandNames.push(command.command_name);
        if (command.command_name === "save_work_draft") saved = true;
      }
      const response = await demo(input, init);
      if (String(input).includes("/student-context") && !saved) {
        const context = await response.json();
        // Первый черновик: у работы ещё нет ни сдачи, ни попыток.
        return new Response(
          JSON.stringify({
            ...context,
            draft: null,
            quota: null,
            submission_id: null,
          }),
          { status: 200 },
        );
      }
      return response;
    },
  );
  const api = new ApiClient(transport);
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["student"],
  });
  window.location.hash = `/prepare/${ids.publication}`;
  render(<App api={api} />);
  const check = await screen.findByRole("button", {
    name: "Проверить перед сдачей",
  });
  expect(check).toBeEnabled();
  await user.type(
    screen.getByLabelText("Ссылка на репозиторий или Google Docs"),
    "https://github.com/example/first-draft",
  );
  await user.click(check);
  await waitFor(() => expect(commandNames).toContain("start_self_review"));
  expect(commandNames.indexOf("save_work_draft")).toBeLessThan(
    commandNames.indexOf("prepare_work_draft"),
  );
  expect(commandNames.indexOf("prepare_work_draft")).toBeLessThan(
    commandNames.indexOf("start_self_review"),
  );
  await waitFor(
    () =>
      expect(screen.getByText(/Получено результатов: 1/)).toBeInTheDocument(),
    { timeout: 5000 },
  );
  expect(commandNames).not.toContain("submit_work_draft");
  expect(commandNames).not.toContain("submit_work");
  expect(window.location.hash).toBe(`#/prepare/${ids.publication}`);
});
