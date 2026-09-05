import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient, type W } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import { WorkspaceSubmit } from "../src/pages/WorkspaceStudent";

it("opens the newest successful self-review on reload regardless of API ordering", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const session = await ws.core.session();
  const base = await ws.studentContext(ids.publication);
  const makeRun = (
    id: string,
    created_at: string,
    feedback: string,
  ): W<"SelfReviewView"> => ({
    id,
    created_at,
    draft_revision: 1,
    artifact_id: null,
    status: "succeeded",
    disposition: "consumed",
    error_code: null,
    quota: base.quota!,
    result: {
      findings: [
        {
          criterion_id: base.criteria[0].id,
          status: "needs_attention",
          feedback,
          evidence: "",
        },
      ],
    },
  });
  const older = makeRun(
    "00000000-0000-4000-8000-000000000010",
    "2026-09-04T10:00:00Z",
    "Старый разбор",
  );
  const newer = makeRun(
    "00000000-0000-4000-8000-000000000020",
    "2026-09-05T10:00:00Z",
    "Новый разбор",
  );
  for (const runs of [
    [newer, older],
    [older, newer],
  ]) {
    vi.spyOn(ws, "studentContext").mockResolvedValue({
      ...base,
      submission_id: null,
      self_reviews: runs,
    });
    const view = render(
      <WorkspaceSubmit ws={ws} id={ids.publication} session={session} />,
    );
    const latest = await screen.findByText("Новый разбор");
    const previous = screen.getByText("Старый разбор");
    expect(latest.closest("details")).toHaveAttribute("open");
    expect(previous.closest("details")).not.toHaveAttribute("open");
    expect(
      latest.compareDocumentPosition(previous) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    view.unmount();
  }
});

it.each([
  {
    allowed: ["upload"] as W<"StudentContext">["allowed_sources"],
    label: "Markdown, PDF или DOCX, до 10 МБ",
    file: true,
  },
  {
    allowed: ["google_docs"] as W<"StudentContext">["allowed_sources"],
    label: "Ссылка на Google Docs",
    file: false,
  },
  {
    allowed: ["github"] as W<"StudentContext">["allowed_sources"],
    label: "Ссылка на репозиторий GitHub",
    file: false,
  },
])(
  "offers only configured sources: $allowed",
  async ({ allowed, label, file }) => {
    const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
    const session = await ws.core.session();
    const base = await ws.studentContext(ids.publication);
    vi.spyOn(ws, "studentContext").mockResolvedValue({
      ...base,
      draft: null,
      submission_id: null,
      self_reviews: [],
      allowed_sources: allowed,
    });
    render(<WorkspaceSubmit ws={ws} id={ids.publication} session={session} />);
    await screen.findByLabelText(label);
    expect(screen.queryByRole("button", { name: "Файлы" }) !== null).toBe(file);
    expect(screen.queryByRole("button", { name: "Ссылка" }) !== null).toBe(
      !file,
    );
  },
);

it("does not treat an older needs_changes publication as permission to resubmit a pending newer attempt", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const session = await ws.core.session();
  const context = await ws.studentContext(ids.publication);
  const first = {
    id: "00000000-0000-4000-8000-000000000010",
    sequence: 1,
    status: "needs_changes",
    submitted_at: "2026-09-04T10:00:00Z",
    comment: "Первая версия",
    artifact_id: null,
    capture_operation_id: null,
  };
  const latest = {
    ...first,
    id: "00000000-0000-4000-8000-000000000020",
    sequence: 2,
    status: "pending_review",
    submitted_at: "2026-09-05T10:00:00Z",
    comment: "Исправления отправлены",
  };
  const publication: W<"StudentReviewView"> = {
    id: "00000000-0000-4000-8000-000000000030",
    iteration_id: ids.review,
    submission_version_id: first.id,
    decision: "needs_changes",
    feedback: "Старая просьба исправить",
    published_at: "2026-09-04T11:00:00Z",
    revision_deadline: "2026-09-06T11:00:00Z",
    score: 0,
    criteria: [],
  };
  vi.spyOn(ws, "studentContext").mockResolvedValue({
    ...context,
    submission_id: ids.submission,
  });
  vi.spyOn(ws, "submission").mockResolvedValue({
    id: ids.submission,
    publication_id: ids.publication,
    course_run_id: ids.run,
    title: context.title,
    attempts: [first, latest],
    reviews: [publication],
    current_publication_id: publication.id,
  });
  const command = vi.spyOn(ws, "command");
  render(<WorkspaceSubmit ws={ws} id={ids.publication} session={session} />);
  await screen.findByText("На повторном ревью");
  expect(
    screen.getByRole("button", { name: "Отправить на ревью" }),
  ).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Отправить исправления" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Старая просьба исправить")).toBeInTheDocument();
  expect(screen.getByText("Исправления отправлены")).toBeInTheDocument();
  expect(command).not.toHaveBeenCalled();
});

it("opens a coordinator submission detail without calling student-only context", async () => {
  const { App } = await import("../src/App");
  const transport = vi.fn(createDemoTransport());
  const api = new ApiClient(transport);
  const session = await api.session();
  vi.spyOn(api, "session").mockResolvedValue({
    ...session,
    roles: ["methodologist"],
  });
  window.location.hash = `/submissions/${ids.submission}`;
  render(<App api={api} />);
  await screen.findByText("Отправленная работа");
  expect(
    transport.mock.calls.filter(([input]) =>
      String(input).includes("/student-context"),
    ),
  ).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: "ИИ-ревью" }),
  ).not.toBeInTheDocument();
});
