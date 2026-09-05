import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { WorkspaceSearch } from "../src/pages/WorkspaceSearch";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";

it("anchors both search groups and closes with Escape or an outside click", async () => {
  const user = userEvent.setup();
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  ws.search = async () => ({
    students: [],
    homeworks: [
      { id: ids.homework, title: "Лабораторная", course_run_id: ids.run },
    ],
  });
  render(
    <>
      <WorkspaceSearch ws={ws} />
      <button>Вне поиска</button>
    </>,
  );
  const input = screen.getByLabelText("ID студента или название задания");
  await user.type(input, "Лабораторная");
  await screen.findByRole("link", { name: "Лабораторная" });
  expect(screen.getByText("Студенты")).toBeInTheDocument();
  expect(screen.getByText("Задания")).toBeInTheDocument();
  expect(
    screen.getByText("Работ с таким ID студента нет."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByText("Задания")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Вне поиска" }));
  await user.click(input);
  await user.keyboard("{Enter}");
  await waitFor(() =>
    expect(window.location.hash).toBe(
      `#/homework/${ids.homework}?run=${ids.run}`,
    ),
  );
  await user.click(screen.getByRole("button", { name: "Вне поиска" }));
  await user.click(input);
  expect(screen.getByText("Задания")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Вне поиска" }));
  expect(screen.queryByText("Задания")).not.toBeInTheDocument();
});
