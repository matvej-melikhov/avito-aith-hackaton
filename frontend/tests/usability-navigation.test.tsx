import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import {
  WorkspaceCatalog,
  WorkspaceAssignments,
} from "../src/pages/WorkspaceCatalog";
import { WorkspaceHomework } from "../src/pages/WorkspaceHomework";
import { WorkspaceWorks } from "../src/pages/WorkspaceLists";
import { WorkspaceSubmissionDetail } from "../src/pages/WorkspaceSubmissionDetail";
import { CoursePage } from "../src/pages/Courses";
import { StudentWorks } from "../src/pages/StudentWorks";

afterEach(() => sessionStorage.clear());

it("student header offers existing courses and the authenticated user's own name", async () => {
  const user = userEvent.setup();
  const demo = createDemoTransport();
  const api = new ApiClient(async (input, init) =>
    String(input).endsWith("/v2/profile")
      ? new Response(
          JSON.stringify({ user_id: ids.user, display_name: "Алексей Иванов" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      : demo(input, init),
  );
  vi.spyOn(api, "session").mockResolvedValue({
    ...(await api.session()),
    roles: ["student"],
  });
  window.location.hash = "#/home";
  render(<App api={api} />);
  expect(
    await screen.findByRole("link", { name: "Мои курсы" }),
  ).toHaveAttribute("href", "#/courses");
  await user.click(screen.getByLabelText("Ваш профиль"));
  expect(await screen.findByText("Алексей Иванов")).toBeVisible();
});

it("course title navigates to its scoped overview and editing is explicit", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const course = (await ws.catalog()).courses[0];
  render(<WorkspaceCatalog ws={ws} mode="courses" />);
  expect(
    await screen.findByRole("link", { name: course.title }),
  ).toHaveAttribute("href", `#/dashboard?course=${course.id}`);
  expect(
    screen.getByRole("button", { name: `Редактировать курс ${course.title}` }),
  ).toBeInTheDocument();
});

it("existing publication link is available on editor reload without republishing", async () => {
  const user = userEvent.setup();
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  render(
    <WorkspaceHomework
      ws={ws}
      id={ids.homework}
      run={ids.run}
      session={await api.session()}
    />,
  );
  await user.click(
    await screen.findByRole("button", { name: "3. Публикация" }),
  );
  expect(screen.getByLabelText("Ссылка для Stepik")).toHaveValue(
    `${window.location.origin}/#/prepare/${ids.publication}`,
  );
});

it("pool to registry keeps a valid selected run instead of choosing the first run", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const catalog = await ws.catalog();
  const selected = catalog.course_runs[1];
  sessionStorage.setItem("review-ui-selected-run", selected.id);
  window.location.hash = "#/coord-pool";
  const view = render(
    <WorkspaceWorks ws={ws} role="methodologist" coordinatorPool />,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Поток")).toHaveValue(selected.id),
  );
  view.unmount();
  window.location.hash = "#/registry";
  render(<WorkspaceWorks ws={ws} role="methodologist" />);
  await waitFor(() =>
    expect(screen.getByLabelText("Поток")).toHaveValue(selected.id),
  );
});

it("returned work list uses its revision deadline and labels the date", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const base = (await ws.studentWorks()).items[0];
  vi.spyOn(ws, "studentWorks").mockResolvedValue({
    items: [
      {
        ...base,
        status: "needs_changes",
        submission_deadline: "2026-09-12T12:00:00Z",
        revision_deadline: "2026-09-08T12:00:00Z",
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  });
  render(<StudentWorks ws={ws} />);
  expect(await screen.findByText("Исправления до")).toBeInTheDocument();
  expect(screen.getByText(/08\.09/)).toBeInTheDocument();
  expect(screen.queryByText(/12\.09/)).not.toBeInTheDocument();
});

it("primary reviewer confirmation survives the assignment row refetch", async () => {
  const user = userEvent.setup();
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const values = await ws.assignments(ids.run);
  const people = await ws.directory();
  const reviewer = people.items.find((p) => p.roles.includes("reviewer"))!;
  render(<WorkspaceAssignments ws={ws} runId={ids.run} />);
  const input = await screen.findByLabelText(
    `Ревьюер: ${values.items[0].student_name}`,
  );
  await user.selectOptions(input, reviewer.id);
  await user.click(input.closest("tr")!.querySelector("button")!);
  await waitFor(() =>
    expect(
      screen.getByLabelText(`Ревьюер: ${values.items[0].student_name}`),
    ).toHaveValue(reviewer.id),
  );
  await waitFor(() =>
    expect(
      screen.getByText(
        `${reviewer.display_name} назначен для следующих работ. Начатые проверки не изменены.`,
      ),
    ).toBeInTheDocument(),
  );
});

it("course scope updates when directory changes into the existing overview", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const catalog = await ws.catalog();
  const target = catalog.courses[1];
  window.location.hash = "#/courses";
  const view = render(<WorkspaceCatalog ws={ws} mode="courses" />);
  await screen.findByRole("link", { name: target.title });
  window.location.hash = `#/dashboard?course=${target.id}`;
  view.rerender(<WorkspaceCatalog ws={ws} mode="overview" />);
  await waitFor(() =>
    expect(screen.getByLabelText("Курс")).toHaveValue(target.id),
  );
});

it("student-result projection shows the current review status for coordinator without rendering draft feedback", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const current = await api.submission(ids.submission);
  const iteration = current.review_iterations[0];
  vi.spyOn(api, "submission").mockResolvedValue({
    ...current,
    review_iterations: [
      {
        ...iteration,
        submission_version_id: current.current_submission_version_id!,
        status: "in_review",
      },
    ],
  });
  render(
    <WorkspaceSubmissionDetail
      ws={ws}
      id={ids.submission}
      role="methodologist"
    />,
  );
  expect(await screen.findByText("На проверке")).toBeInTheDocument();
  expect(screen.queryByText("Обратная связь студенту")).not.toBeInTheDocument();
});

it("course homework format reflects upload-only publication policy", async () => {
  const demo = createDemoTransport();
  const api = new ApiClient(async (input, init) => {
    const response = await demo(input, init);
    if (String(input).includes("/student-context"))
      throw new Error("Coordinator must not request student-only context");
    if (String(input).endsWith("/sources") && response.ok) {
      const data = await response.json();
      return new Response(
        JSON.stringify({ ...data, allowed_sources: ["upload"] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return response;
  });
  render(<CoursePage api={api} id={ids.run} role="methodologist" />);
  expect(await screen.findByText("Только файлы")).toBeInTheDocument();
  expect(screen.queryByText("github, google_docs")).not.toBeInTheDocument();
});
