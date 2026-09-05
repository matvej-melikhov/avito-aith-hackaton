import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { App } from "../src/App";
import { ApiClient } from "../src/api/client";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
it("opens the recommended work and requires a saved draft plus human confirmation", async () => {
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
  const save = await screen.findByRole("button", {
    name: "Сохранить черновик",
  });
  const publish = screen.getByRole("button", { name: "Опубликовать ревью" });
  expect(save).toBeDisabled();
  expect(publish).toBeDisabled();
  await user.clear(screen.getByLabelText("Баллы: HTTP API и обработка ошибок"));
  await user.type(
    screen.getByLabelText("Баллы: HTTP API и обработка ошибок"),
    "8",
  );
  await user.type(
    screen.getByLabelText("Обоснование"),
    "Проверены успешные и ошибочные ответы",
  );
  await user.type(
    screen.getByLabelText("Обратная связь студенту"),
    "Хорошая работа. Добавьте тест редиректа.",
  );
  await user.click(save);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Опубликовать ревью" }),
    ).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Опубликовать ревью" }));
  expect(commandNames).not.toContain("publish_review");
  await user.click(
    screen.getByRole("button", { name: "Подтвердить публикацию" }),
  );
  await screen.findByText("Опубликовано");
  expect(commandNames.filter((n) => n === "publish_review")).toHaveLength(1);
});
it("clears preflight capability when the artifact URL changes", async () => {
  const user = userEvent.setup();
  const api = new ApiClient(createDemoTransport());
  window.location.hash = `/submit/${ids.run}/${ids.publication}`;
  // Student-only session: no UI selector can manufacture extra roles.
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["student"],
  });
  render(<App api={api} />);
  const input = await screen.findByLabelText("GitHub или Google Docs");
  await user.type(input, "https://github.com/example/one");
  await user.click(screen.getByRole("button", { name: "Подготовить сдачу" }));
  await screen.findByRole("button", { name: "Сдать работу" });
  await user.clear(input);
  await user.type(input, "https://github.com/example/two");
  expect(
    screen.queryByRole("button", { name: "Сдать работу" }),
  ).not.toBeInTheDocument();
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
    screen.queryByRole("button", { name: "Опубликовать ревью" }),
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
    String(input).endsWith("/revisions")
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
  await screen.findByLabelText("Обоснование");
  await user.type(screen.getByLabelText("Обоснование"), "Ручная проверка");
  await user.type(
    screen.getByLabelText("Обратная связь студенту"),
    "Не потерять этот текст",
  );
  await user.click(screen.getByRole("button", { name: "Сохранить черновик" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Обратная связь студенту")).toHaveValue(
    "Не потерять этот текст",
  );
  expect(
    screen.getByRole("button", { name: "Опубликовать ревью" }),
  ).toBeDisabled();
});
it("keeps the coordinator review screen read-only even when the API permits editing", async () => {
  const api = new ApiClient(createDemoTransport());
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["methodologist"],
  });
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} />);
  await screen.findByLabelText("Обоснование");
  expect(screen.getByLabelText("Обоснование")).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Опубликовать ревью" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Взять в работу" }),
  ).not.toBeInTheDocument();
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
  expect(
    transport.mock.calls.filter(([url]) => String(url).includes("/v2/works")),
  ).toHaveLength(1);
});
