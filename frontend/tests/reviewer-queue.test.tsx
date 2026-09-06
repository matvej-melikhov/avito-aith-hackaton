import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { ReviewerQueue } from "../src/pages/ReviewerQueue";
import { createDemoTransport } from "../src/mocks/transport";

function client() {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 });
  return { ws, works };
}

it("shows only active work on the works page", async () => {
  const { ws, works } = client();
  render(<ReviewerQueue ws={ws} />);
  await screen.findByText("У вас пока нет активных проверок.");
  expect(screen.getByRole("heading", { name: "Активные" })).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Пул" }),
  ).not.toBeInTheDocument();
  expect(works.mock.calls.every(([params]) => params?.view === "active")).toBe(
    true,
  );
  expect(
    works.mock.calls.every(([params]) => params?.priority === undefined),
  ).toBe(true);
});

it("shows the pool on its own page without a search field", async () => {
  const { ws, works } = client();
  render(<ReviewerQueue ws={ws} pool />);
  await screen.findByText("По этим условиям работ нет.");
  expect(
    screen.getByRole("heading", { level: 1, name: "Пул" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Активные" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Поиск")).not.toBeInTheDocument();
  expect(works.mock.calls.every(([params]) => params?.view === "all")).toBe(
    true,
  );
  const pool = screen.getByRole("table");
  expect(
    within(pool)
      .getAllByRole("columnheader")
      .map((h) => h.textContent),
  ).toEqual(["Студент", "Задание", "Курс", "Сдана", "В пуле", ""]);
});

it("filters the pool by course run", async () => {
  const { ws, works } = client();
  render(<ReviewerQueue ws={ws} pool />);
  await screen.findByText("По этим условиям работ нет.");
  const runs = await screen.findByLabelText("Поток");
  const option = within(runs)
    .getAllByRole("option")
    .find((o) => (o as HTMLOptionElement).value !== "")!;
  await userEvent.selectOptions(runs, option);
  await waitFor(() =>
    expect(
      works.mock.calls.some(
        ([params]) =>
          params?.course_run_id === (option as HTMLOptionElement).value,
      ),
    ).toBe(true),
  );
});
