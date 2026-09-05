import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { ReviewerQueue } from "../src/pages/ReviewerQueue";
import { createDemoTransport } from "../src/mocks/transport";

it("keeps my work and available pool on separate pages", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 });
  const { rerender } = render(<ReviewerQueue ws={ws} mode="active" />);
  await screen.findByText("У вас пока нет активных проверок.");
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.queryByLabelText("Поиск")).not.toBeInTheDocument();
  expect(works.mock.calls.some(([params]) => params?.view === "active")).toBe(
    true,
  );
  rerender(<ReviewerQueue ws={ws} mode="pool" />);
  await waitFor(() =>
    expect(works.mock.calls.some(([params]) => params?.view === "pool")).toBe(
      true,
    ),
  );
  const pool = screen.getAllByRole("table")[0];
  expect(
    within(pool)
      .getAllByRole("columnheader")
      .map((h) => h.textContent),
  ).toEqual(["Студент", "Задание", "Курс", "Сдана", "В пуле", ""]);
  expect(
    works.mock.calls.every(([params]) => params?.priority === undefined),
  ).toBe(true);
  await userEvent.type(screen.getByLabelText("Поиск"), "Тест");
  await waitFor(() =>
    expect(works.mock.calls.some(([params]) => params?.q === "Тест")).toBe(
      true,
    ),
  );
});
it.each([false, true])(
  "opens pool work and records participation only when needed: joined=%s",
  async (joined) => {
    const api = new ApiClient(createDemoTransport());
    const ws = new WorkspaceClient(api);
    const session = await api.session();
    const all = await ws.works();
    const item = all.items[0];
    const detail = await api.review(item.review_iteration_id!);
    const w = {
      ...item,
      status: "in_review",
      review_submission_version_id: item.submission_version_id,
      participant_ids: joined ? [session.user_id] : [],
    };
    vi.spyOn(ws, "works").mockResolvedValue({
      items: [w],
      total: 1,
      offset: 0,
      limit: 20,
    });
    vi.spyOn(api, "review").mockResolvedValue({
      ...detail,
      responsibility_events: joined
        ? [
            {
              id: crypto.randomUUID(),
              actor_id: session.user_id,
              reviewer_id: session.user_id,
              action: "joined",
              occurred_at: new Date().toISOString(),
            },
          ]
        : [],
    });
    const command = vi
      .spyOn(api, "command")
      .mockImplementation(async () => ({}) as never);
    render(<ReviewerQueue ws={ws} mode="pool" />);
    const button = await screen.findByRole("button", {
      name: joined ? "Продолжить" : "Начать проверку",
    });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
    await waitFor(() =>
      expect(window.location.hash).toContain(
        `/reviews/${item.review_iteration_id}`,
      ),
    );
    expect(
      command.mock.calls.filter(
        ([name]) => name === "record_review_responsibility",
      ),
    ).toHaveLength(joined ? 0 : 1);
  },
);
