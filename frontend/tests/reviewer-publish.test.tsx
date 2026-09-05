import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import { ReviewEditor } from "../src/pages/Review";
it.each([false, true])(
  "refreshes outcome revision and refuses a changed draft: changed=%s",
  async (changed) => {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute("open");
    };
    const api = new ApiClient(createDemoTransport());
    const ws = new WorkspaceClient(api);
    const original = await api.review(ids.review);
    const context = await ws.reviewContext(ids.review);
    const session = await api.session();
    const revisionId = crypto.randomUUID();
    const detail = { ...original, current_review_revision_id: revisionId };
    vi.spyOn(ws, "reviewDetail").mockResolvedValue({
      ...detail,
      revision: detail.revision + 1,
      current_review_revision_id: changed ? crypto.randomUUID() : revisionId,
    });
    const command = vi
      .spyOn(ws, "command")
      .mockImplementation(
        async () => ({ id: ids.review, revision: 1 }) as never,
      );
    render(
      <ReviewEditor
        api={api}
        ws={ws}
        detail={detail}
        context={context}
        session={session}
        version={{
          id: context.homework_version_id,
          criteria: context.criteria,
          max_score: context.max_score,
          student_text: context.student_text,
        }}
        refresh={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Зачесть" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Подтвердить публикацию" }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Подтвердить публикацию" }),
    );
    if (changed) {
      await screen.findByText(/Коллега изменил черновик/);
      expect(
        command.mock.calls.some(
          ([name]) => name === "publish_workspace_review",
        ),
      ).toBe(false);
      return;
    }
    await waitFor(() =>
      expect(command).toHaveBeenCalledWith(
        "publish_workspace_review",
        ids.review,
        detail.revision + 1,
        { review_revision_id: revisionId, apply_penalty: true },
      ),
    );
  },
);
it("keeps published review immutable and starts a reasoned correction", async () => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const original = await api.review(ids.review);
  const context = await ws.reviewContext(ids.review);
  const session = await api.session();
  const detail = {
    ...original,
    status: "published" as const,
    current_review_revision_id: crypto.randomUUID(),
  };
  const command = vi
    .spyOn(api, "command")
    .mockImplementation(
      async () => ({ review_iteration_id: ids.review }) as never,
    );
  render(
    <ReviewEditor
      api={api}
      ws={ws}
      detail={detail}
      context={context}
      session={session}
      readOnly
      onEdit={() => {}}
      version={{
        id: context.homework_version_id,
        criteria: context.criteria,
        max_score: context.max_score,
        student_text: context.student_text,
      }}
      refresh={() => {}}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Редактировать проверку" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Добавить ещё требование" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Создать исправление" }),
  );
  await userEvent.type(
    screen.getByLabelText("Причина исправления"),
    "Уточняем оценку",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Открыть новую версию" }),
  );
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith(
      "create_review_correction",
      ids.review,
      detail.revision,
      {
        published_review_revision_id: detail.current_review_revision_id,
        reason: "Уточняем оценку",
      },
    ),
  );
});
it("hides join for an already participating reviewer", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const original = await api.review(ids.review);
  const context = await ws.reviewContext(ids.review);
  const session = await api.session();
  const detail = {
    ...original,
    responsibility_events: [
      {
        id: crypto.randomUUID(),
        reviewer_id: session.user_id,
        actor_id: session.user_id,
        action: "joined" as const,
        occurred_at: new Date().toISOString(),
      },
    ],
  };
  render(
    <ReviewEditor
      api={api}
      ws={ws}
      detail={detail}
      context={context}
      session={session}
      refresh={() => {}}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Присоединиться" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Коллеги могут подключаться/),
  ).not.toBeInTheDocument();
});
