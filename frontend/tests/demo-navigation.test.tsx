import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { ApiClient, type Role } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { WorkspaceSubmit } from "../src/pages/WorkspaceStudent";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";

afterEach(() => sessionStorage.clear());

async function roleApi(role: Role) {
  const api = new ApiClient(createDemoTransport());
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({ ...session, roles: [role] });
  return api;
}

it("opens the existing submission workspace from student list and old submission links", async () => {
  const api = await roleApi("student");
  window.location.hash = "/works";
  const first = render(<App api={api} />);
  const link = await screen.findByRole("link", {
    name: "HTTP-сервис коротких ссылок",
  });
  expect(link).toHaveAttribute("href", `#/prepare/${ids.publication}`);
  first.unmount();
  window.location.hash = `/submissions/${ids.submission}?attempt=${ids.submissionVersion}`;
  const { container } = render(<App api={api} />);
  await screen.findByRole("heading", { name: "HTTP-сервис коротких ссылок" });
  expect(container.querySelector(".band--submit")).not.toBeNull();
  expect(screen.getByText("Ваша работа")).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Открыть страницу сдачи" }),
  ).toBeNull();
});

it("keeps the staff-only submission view separate from the student form", async () => {
  window.location.hash = `/submissions/${ids.submission}`;
  const { container } = render(<App api={await roleApi("methodologist")} />);
  await screen.findByRole("heading", { name: "HTTP-сервис коротких ссылок" });
  expect(container.querySelector(".band--submit")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Отправить на ревью" }),
  ).toBeNull();
});

it("opens the requested old attempt without unlocking an answer already under review", async () => {
  const api = await roleApi("student");
  const ws = new WorkspaceClient(api);
  const base = await ws.submission(ids.submission);
  const first = { ...base.attempts[0], comment: "Комментарий первой попытки" };
  const second = {
    ...first,
    id: "second-attempt",
    sequence: 2,
    comment: "Комментарий второй попытки",
  };
  vi.spyOn(ws, "submission").mockResolvedValue({
    ...base,
    attempts: [first, second],
    current_publication_id: "old-return",
    reviews: [
      {
        id: "old-return",
        iteration_id: ids.review,
        submission_version_id: first.id,
        published_at: "2026-09-01T10:00:00Z",
        score: 0,
        feedback: "Исправьте первую попытку",
        criteria: [],
        decision: "needs_changes",
        revision_deadline: "2026-09-10T10:00:00Z",
      },
    ],
  });
  const command = vi.spyOn(ws, "command");
  render(
    <WorkspaceSubmit
      ws={ws}
      id={ids.publication}
      session={await api.session()}
      attemptId={first.id}
    />,
  );
  const oldComment = await screen.findByText("Комментарий первой попытки");
  expect(oldComment.closest("details")).toHaveAttribute("open");
  expect(
    screen.getByText("Комментарий второй попытки").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    screen.getByRole("button", { name: "Отправить на ревью" }),
  ).toBeDisabled();
  expect(
    screen.getByLabelText("Ссылка на репозиторий или Google Docs"),
  ).toBeDisabled();
  expect(command).not.toHaveBeenCalled();
});

it("returns to my work with a persistent success message after releasing responsibility", async () => {
  const api = await roleApi("reviewer");
  const command = vi.spyOn(api, "command");
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Снять с себя проверку" }),
  );
  await waitFor(() => expect(window.location.hash).toBe("#/works"));
  expect(
    await screen.findByText(
      "Вы сняли с себя проверку. Если других ревьюеров нет, работа вернётся в пул.",
    ),
  ).toBeVisible();
  expect(command).toHaveBeenCalledWith(
    "record_review_responsibility",
    ids.review,
    0,
    { action: "released" },
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Закрыть сообщение" }),
  );
  expect(
    screen.queryByText(
      "Вы сняли с себя проверку. Если других ревьюеров нет, работа вернётся в пул.",
    ),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Войти в режим проверки" }),
  ).toBeNull();
});

it("stays on the review and reports an unsuccessful release", async () => {
  const api = await roleApi("reviewer");
  vi.spyOn(api, "command").mockRejectedValue(
    new Error("Проверка изменилась. Обновите данные."),
  );
  window.location.hash = `/reviews/${ids.review}`;
  render(<App api={api} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Снять с себя проверку" }),
  );
  await screen.findByText("Проверка изменилась. Обновите данные.");
  expect(window.location.hash).toBe(`#/reviews/${ids.review}`);
  expect(
    screen.queryByText(
      "Вы сняли с себя проверку. Если других ревьюеров нет, работа вернётся в пул.",
    ),
  ).toBeNull();
});

function loginApi(enabled: boolean, fail = false) {
  const transport = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/v1/session"))
      return new Response(
        JSON.stringify({ code: "unauthenticated", message: "Войдите" }),
        { status: 401 },
      );
    if (String(input).endsWith("/v1/auth/local/identities")) {
      if (fail) throw new Error("offline");
      return new Response(
        JSON.stringify({
          enabled,
          items: enabled
            ? [
                {
                  key: "coordinator",
                  label: "Координатор",
                  roles: ["methodologist"],
                },
              ]
            : [],
        }),
      );
    }
    throw new Error(`Unexpected request: ${input}`);
  });
  return { api: new ApiClient(transport), transport };
}

it("shows the Stepik demo explanation without starting OAuth or navigating away", async () => {
  const { api, transport } = loginApi(true);
  render(<App api={api} />);
  await screen.findByText("Локальный стенд");
  expect(
    screen.queryByText("Выберите участника для входа на локальный стенд."),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Войти через Stepik" }),
  );
  const modal = screen.getByRole("dialog");
  expect(
    within(modal).getByText(/Вход через Stepik пока не подключён/),
  ).toBeVisible();
  expect(
    transport.mock.calls.some(([url]) => String(url).includes("/stepik/start")),
  ).toBe(false);
  await userEvent.click(within(modal).getByRole("button", { name: "Понятно" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole("button", { name: "Координатор" })).toBeVisible();
});

it("retains the actual Stepik authorization link outside the local demo", async () => {
  render(<App api={loginApi(false).api} />);
  const link = await screen.findByRole("link", { name: "Войти через Stepik" });
  await waitFor(() => expect(link).toHaveAttribute("aria-disabled", "false"));
  expect(link).toHaveAttribute("href", "/api/v1/auth/stepik/start");
});

it("explains a failure to load available login methods and does not enable OAuth blindly", async () => {
  render(<App api={loginApi(false, true).api} />);
  await screen.findByText("Не удалось проверить доступные способы входа.");
  expect(
    screen.getByRole("link", { name: "Войти через Stepik" }),
  ).toHaveAttribute("aria-disabled", "true");
  expect(
    screen.getByRole("button", { name: "Повторить загрузку способов входа" }),
  ).toBeVisible();
});
