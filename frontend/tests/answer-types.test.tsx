import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { WorkspaceHomework } from "../src/pages/WorkspaceHomework";
import { WorkspaceSubmit } from "../src/pages/WorkspaceStudent";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";

it.each([
  ["Ссылки", ["upload"]],
  ["Файлы", ["github", "google_docs"]],
] as const)(
  "persists the independent answer selection after disabling %s",
  async (label, expected) => {
    const api = new ApiClient(createDemoTransport());
    const ws = new WorkspaceClient(api);
    const session = await api.session();
    const first = render(
      <WorkspaceHomework
        ws={ws}
        id={ids.homework}
        run={ids.run}
        session={session}
      />,
    );
    const group = await screen.findByRole("group", {
      name: "Что прикрепляет студент для ответа",
    });
    expect(
      within(group).getByRole("checkbox", { name: "Ссылки" }),
    ).toBeChecked();
    expect(
      within(group).getByRole("checkbox", { name: "Файлы" }),
    ).toBeChecked();
    await userEvent.click(within(group).getByRole("checkbox", { name: label }));
    await waitFor(async () =>
      expect(
        (await ws.editorDraft(ids.homework, ids.run)).value?.allowed_sources,
      ).toEqual(expected),
    );
    first.unmount();
    render(
      <WorkspaceHomework
        ws={ws}
        id={ids.homework}
        run={ids.run}
        session={session}
      />,
    );
    const restored = await screen.findByRole("group", {
      name: "Что прикрепляет студент для ответа",
    });
    expect(
      within(restored).getByRole("checkbox", { name: label }),
    ).not.toBeChecked();
    await userEvent.click(
      screen.getByRole("button", { name: "2. Критерии ревью" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Дальше: публикация" }),
    );
    expect(
      await screen.findByRole("button", { name: "Опубликовать задание" }),
    ).toBeEnabled();
    const history = await api.homework(ids.homework);
    const latest = [...history.versions].sort(
      (a, b) => b.version_number - a.version_number,
    )[0];
    expect((await ws.privateHomework(latest.id)).allowed_sources).toEqual(
      expected,
    );
    expect(latest.artifact_kinds.length).toBeGreaterThan(0);
  },
);

it("does not prepare a saved link after the assignment switches to files only", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  await ws.command("save_work_draft", ids.publication, 0, {
    artifact_url: "https://github.com/acme/repo",
    upload_id: null,
    comment: "Черновик",
  });
  const context = await ws.studentContext(ids.publication);
  vi.spyOn(ws, "studentContext").mockResolvedValue({
    ...context,
    submission_id: null,
    allowed_sources: ["upload"],
  });
  const command = vi.spyOn(ws, "command");
  render(
    <WorkspaceSubmit
      ws={ws}
      id={ids.publication}
      session={await api.session()}
    />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Отправить на ревью" }),
  );
  await screen.findByText("Приложите файл работы: Markdown, PDF или DOCX.");
  expect(
    command.mock.calls.some(([name]) => name === "prepare_work_draft"),
  ).toBe(false);
});

it("keeps both types selectable and prevents saving an empty set", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const session = await api.session();
  const command = vi.spyOn(ws, "command");
  render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={session}
    />,
  );
  const group = await screen.findByRole("group", {
    name: "Что прикрепляет студент для ответа",
  });
  await userEvent.click(
    within(group).getByRole("checkbox", { name: "Ссылки" }),
  );
  await userEvent.click(within(group).getByRole("checkbox", { name: "Файлы" }));
  expect(screen.getByText("Выберите хотя бы один тип ответа.")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Дальше: критерии" }),
  );
  await screen.findByText(
    "Выберите хотя бы один тип ответа: ссылки или файлы.",
  );
  expect(
    command.mock.calls.some(
      ([name, , , payload]) =>
        name === "save_editor_draft" &&
        "allowed_sources" in payload &&
        payload.allowed_sources?.length === 0,
    ),
  ).toBe(false);
  await userEvent.click(
    within(group).getByRole("checkbox", { name: "Ссылки" }),
  );
  await userEvent.click(within(group).getByRole("checkbox", { name: "Файлы" }));
  await waitFor(async () =>
    expect(
      (await ws.editorDraft(ids.homework, ids.run)).value?.allowed_sources,
    ).toEqual(["upload", "github", "google_docs"]),
  );
});

it.each([
  [["upload"], false, true],
  [["github", "google_docs"], true, false],
  [["upload", "github", "google_docs"], true, true],
] as const)(
  "shows only permitted submission controls for %j",
  async (sources, links, files) => {
    const api = new ApiClient(createDemoTransport());
    const ws = new WorkspaceClient(api);
    await ws.command("save_private_homework", ids.homeworkVersion, 0, {
      reviewer_guidance: "",
      allowed_sources: [...sources],
    });
    const context = await ws.studentContext(ids.publication);
    expect(context.allowed_sources).toEqual(sources);
    render(
      <WorkspaceSubmit
        ws={ws}
        id={ids.publication}
        session={await api.session()}
      />,
    );
    await screen.findByText("Ваша работа");
    expect(
      !!screen.queryByRole("button", {
        name: "Ссылка",
      }),
    ).toBe(links);
    expect(!!screen.queryByRole("button", { name: "Файл" })).toBe(files);
  },
);
