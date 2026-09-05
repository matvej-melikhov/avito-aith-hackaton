import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { ReviewerQueue } from "../src/pages/ReviewerQueue";
import { createDemoTransport } from "../src/mocks/transport";

it("keeps active work and shared pool on screen together without exclusive tabs", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 });
  render(<ReviewerQueue ws={ws} />);
  await screen.findByText("У вас пока нет активных проверок.");
  expect(screen.getByRole("heading", { name: "Активные" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Пул" })).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Мои студенты" }),
  ).not.toBeInTheDocument();
  expect(works.mock.calls.some(([params]) => params?.view === "active")).toBe(
    true,
  );
  expect(works.mock.calls.some(([params]) => params?.view === "all")).toBe(
    true,
  );
  const pool = screen.getAllByRole("table")[1];
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
