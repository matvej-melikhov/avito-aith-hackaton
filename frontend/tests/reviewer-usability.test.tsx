import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import { ReviewPage, ReviewEditor } from "../src/pages/Review";
import { WorkspaceNotifications } from "../src/WorkspaceNotifications";
import { ArtifactLink } from "../src/pages/WorkspaceReview";

it("explains missing reasons, opens the criterion, and saves without replacing the accordion", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const session = await api.session();
  const scroll = vi.fn();
  HTMLElement.prototype.scrollIntoView = scroll;
  render(<ReviewPage api={api} ws={ws} id={ids.review} session={session} />);
  await screen.findByRole("heading", { name: "Оценка по критериям" });
  expect(
    screen.getByRole("button", { name: "Сохранить черновик" }),
  ).toBeDisabled();
  const missing = screen.getByRole("button", {
    name: /HTTP API и обработка ошибок: добавьте обоснование/,
  });
  await userEvent.click(missing);
  const textarea = screen.getByLabelText("Обоснование");
  const accordion = textarea.closest("details")!;
  expect(accordion.open).toBe(true);
  expect(scroll).toHaveBeenCalled();
  await userEvent.type(textarea, "Проверено вручную");
  await userEvent.click(
    screen.getByRole("button", { name: "Сохранить черновик" }),
  );
  await screen.findByText("Черновик сохранён.");
  expect(screen.getByLabelText("Обоснование").closest("details")).toBe(
    accordion,
  );
  expect(accordion.open).toBe(true);
  expect(screen.getByRole("button", { name: "Зачесть" })).toBeEnabled();
  expect(
    screen.getByRole("heading", { name: "Условие задания" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Получить ИИ-ревью" }),
  ).toBeInTheDocument();
});
it("uses the authorized directory for coordinator names but never requests it for reviewer-only", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const detail = await ws.reviewDetail(ids.review);
  const context = await ws.reviewContext(ids.review);
  const session = await api.session();
  const person = crypto.randomUUID();
  detail.responsibility_events = [
    {
      id: crypto.randomUUID(),
      actor_id: person,
      reviewer_id: person,
      action: "joined",
      occurred_at: new Date().toISOString(),
    },
  ];
  const directory = vi.spyOn(ws, "directory").mockResolvedValue({
    items: [
      { id: person, display_name: "Елена Смирнова", roles: ["reviewer"] },
    ],
  });
  const props = { api, ws, detail, context, refresh: () => {} };
  const { unmount } = render(
    <ReviewEditor
      {...props}
      session={{ ...session, roles: ["methodologist"] }}
      readOnly
    />,
  );
  await screen.findByText(/Елена Смирнова/);
  unmount();
  directory.mockClear();
  render(
    <ReviewEditor {...props} session={{ ...session, roles: ["reviewer"] }} />,
  );
  await screen.findByRole("heading", { name: "Работа" });
  expect(directory).not.toHaveBeenCalled();
});
it("labels the global notice with course and run and marks it read only on explicit close", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const catalog = await ws.catalog();
  const run = catalog.course_runs[0];
  const course = catalog.courses.find((c) => c.id === run.course_id)!;
  const id = crypto.randomUUID();
  vi.spyOn(ws, "notifications").mockResolvedValue({
    items: [
      {
        id,
        course_run_id: run.id,
        created_at: new Date().toISOString(),
        read: false,
        text: "Студент прислал новую версию работы.",
      },
    ],
  });
  const command = vi
    .spyOn(ws, "command")
    .mockImplementation(async () => ({}) as never);
  render(<WorkspaceNotifications ws={ws} />);
  await screen.findByText(`${course.title} · ${run.title}`);
  expect(command).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Закрыть уведомление" }),
  );
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith("read_notification", id, 0, {}),
  );
});
it("signed artifact links preserve filenames unless review supplies an action label", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  vi.spyOn(ws, "download").mockResolvedValue({
    url: "https://example.com/signed",
    expires_at: new Date().toISOString(),
    filename: "Работа.md",
  });
  const { rerender } = render(<ArtifactLink ws={ws} id="artifact" />);
  expect(
    await screen.findByRole("link", { name: "Работа.md ↗" }),
  ).toHaveAttribute("href", "https://example.com/signed");
  rerender(<ArtifactLink ws={ws} id="artifact" label="Открыть ↗" />);
  expect(screen.getByRole("link", { name: "Открыть ↗" })).toHaveAttribute(
    "href",
    "https://example.com/signed",
  );
});

it("published human reasons and revision deadline remain visible while AI evidence is separate", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const original = await ws.reviewDetail(ids.review);
  const context = await ws.reviewContext(ids.review);
  const session = await api.session();
  const criterion = context.criteria[0];
  const revisionId = crypto.randomUUID();
  vi.spyOn(ws, "reviewAssist").mockResolvedValue({
    id: crypto.randomUUID(),
    revision: 1,
    created_at: new Date().toISOString(),
    error_code: null,
    status: "succeeded",
    result: {
      suggestions: [
        {
          criterion_id: criterion.id,
          status: "suggested",
          proposed_points: 5,
          reason: "Причина модели",
          confidence: "high",
          evidence: ["Цитата модели"],
        },
      ],
    },
  });
  render(
    <ReviewEditor
      api={api}
      ws={ws}
      session={session}
      refresh={() => {}}
      detail={{
        ...original,
        status: "published",
        current_review_revision_id: revisionId,
        current_review_revision: {
          id: revisionId,
          review_iteration_id: ids.review,
          revision_number: 1,
          author_user_id: session.user_id,
          feedback: "Отзыв человека",
          total_score: 4,
          created_at: new Date().toISOString(),
        },
        criterion_decisions: [
          {
            criterion_id: criterion.id,
            points: 4,
            decision: "manual",
            reason: "Обоснование человека",
          },
        ],
      }}
      context={{
        ...context,
        outcome: {
          decision: "needs_changes",
          reason: "Нужны тесты",
          revision_deadline: "2026-09-08T15:00:00Z",
        },
      }}
      version={{
        id: context.homework_version_id,
        student_text: context.student_text,
        max_score: context.max_score,
        criteria: context.criteria,
      }}
    />,
  );
  expect(await screen.findByText("Обоснование человека")).toBeVisible();
  expect(screen.getByText(/Исправления до/)).toBeVisible();
  const proposal = await screen.findByText(
    "Предложение ИИ до решения ревьюера",
  );
  expect(proposal.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(proposal);
  expect(screen.getByText("Причина модели")).toBeVisible();
  expect(screen.getByText("Цитата модели")).toBeVisible();
});
