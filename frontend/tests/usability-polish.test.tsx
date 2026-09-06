import {
  render,
  screen,
  within,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { RecommendationExplanation, QueuePage } from "../src/pages/Courses";
import { ScorePreview } from "../src/pages/WorkspaceHomework";
import { HomeworkPage } from "../src/pages/Homework";
import { PeoplePage, MemberIdentity } from "../src/pages/People";
import { ApiClient } from "../src/api/client";
import { createDemoTransport } from "../src/mocks/transport";
import { history, homework, ids, version } from "../src/mocks/fixtures";

it("explains known recommendation facts without exposing internal scheduling fields", () => {
  const { container } = render(
    <RecommendationExplanation
      reasons={[
        "review_deadline:2026-09-15T12:00:00Z",
        "submitted_at:2026-09-10T12:00:00Z",
        "same_reviewer_continuation",
        "assigned_minutes:120",
        "unknown:new",
      ]}
    />,
  );
  expect(screen.getByText(/Проверить до/)).toBeVisible();
  expect(screen.getByText(/Работа сдана/)).toBeVisible();
  expect(screen.getByText(/Вы уже проверяли предыдущую попытку/)).toBeVisible();
  expect(container.textContent).not.toMatch(
    /review_deadline|submitted_at|assigned_minutes|unknown/,
  );
});

it("uses a neutral explanation for unknown reasons and malformed dates", () => {
  render(
    <RecommendationExplanation
      reasons={[
        "review_deadline:invalid",
        "submitted_at:",
        "planned_minutes:0",
      ]}
    />,
  );
  expect(
    screen.getByText("Для вас подобрана работа из этого потока."),
  ).toBeVisible();
  expect(screen.queryByText(/Invalid Date|Проверить до/)).toBeNull();
});

it.each([
  [5, 0.5, "5"],
  [1, 0.25, "0,25"],
  [1, 0.125, "0,125"],
  [1, 0.3, "1"],
])(
  "shows exact short-scale values and endpoint: max=%s step=%s",
  (maximum, step, visible) => {
    render(<ScorePreview maximum={maximum} step={step} />);
    expect(
      within(
        screen.getByRole("group", { name: "Предпросмотр шкалы" }),
      ).getByText(visible, { exact: true }),
    ).toBeVisible();
    expect(screen.getByText(/Число от 0 до/)).toBeVisible();
  },
);

it("compresses a long scale and rejects a nonpositive step", () => {
  const { rerender } = render(<ScorePreview maximum={100} step={0.25} />);
  expect(screen.getByText("… 100")).toBeVisible();
  expect(screen.getByRole("group").children).toHaveLength(3);
  rerender(<ScorePreview maximum={5} step={0} />);
  expect(screen.getByText("Укажите баллы и положительный шаг.")).toBeVisible();
  expect(screen.queryByRole("group")).toBeNull();
});

it("reads the version from the selected run instead of a newer version from another run", async () => {
  const api = new ApiClient(createDemoTransport());
  vi.spyOn(api, "homework").mockResolvedValue({
    ...history,
    versions: [
      version,
      {
        ...version,
        id: "other-version",
        version_number: 9,
        student_text: "Чужая новая версия",
      },
    ],
    course_run_publications: [
      ...history.course_run_publications,
      {
        ...history.course_run_publications[0],
        id: "other-publication",
        course_run_id: "other-run",
        published_at: "2030-01-01T00:00:00Z",
      },
    ],
  });
  vi.spyOn(api, "homeworks").mockResolvedValue({ items: [homework] });
  const command = vi.spyOn(api, "command");
  const { container } = render(
    <HomeworkPage api={api} id={ids.homework} run={ids.run} role="reviewer" />,
  );
  await screen.findByRole("heading", { name: homework.title });
  expect(screen.getByText(version.student_text)).toBeVisible();
  expect(screen.queryByText("Чужая новая версия")).toBeNull();
  expect(container.textContent).not.toContain("2030");
  expect(container.querySelector("form,input,textarea,fieldset")).toBeNull();
  expect(command).not.toHaveBeenCalled();
});

it("does not substitute another version when the published one is missing", async () => {
  const api = new ApiClient(createDemoTransport());
  vi.spyOn(api, "homeworks").mockResolvedValue({
    items: [{ ...homework, current_version_id: "missing" }],
  });
  render(
    <HomeworkPage api={api} id={ids.homework} run={ids.run} role="reviewer" />,
  );
  await screen.findByText(
    "Опубликованная версия задания в этом потоке недоступна.",
  );
  expect(screen.queryByText(version.student_text)).toBeNull();
});

it("distinguishes duplicate or unavailable names and lets the user reveal the full identifier", async () => {
  const { rerender } = render(
    <MemberIdentity
      name="Анна"
      id="12345678-abcd-1234-abcd-123456789012"
      duplicate
    />,
  );
  expect(screen.getByText("Анна")).toBeVisible();
  expect(screen.getByText("ID: 12345678")).toBeVisible();
  await userEvent.click(screen.getByText("ID участника"));
  expect(
    screen.getByText("12345678-abcd-1234-abcd-123456789012"),
  ).toBeVisible();
  rerender(<MemberIdentity id="12345678-abcd-1234-abcd-123456789012" />);
  expect(screen.getByText("Имя недоступно")).toBeVisible();
});

it("keeps membership data available when directory loading fails and recovers on retry", async () => {
  const demo = createDemoTransport();
  let directoryCalls = 0;
  const api = new ApiClient(async (input, init) => {
    if (String(input).endsWith("/v2/directory")) {
      directoryCalls++;
      if (directoryCalls === 1) throw new Error("offline");
      return new Response(
        JSON.stringify({
          items: [{ id: ids.user, display_name: "Анна", roles: ["reviewer"] }],
        }),
      );
    }
    return demo(input, init);
  });
  render(<PeoplePage api={api} />);
  await screen.findByText("Имя недоступно");
  expect(screen.getByRole("table")).toBeVisible();
  await userEvent.click(
    screen.getByRole("button", { name: "Повторить загрузку имён" }),
  );
  await screen.findByText("Анна");
  expect(directoryCalls).toBe(2);
});

it("preserves role updates and invitation payloads through the refreshed controls", async () => {
  const api = new ApiClient(createDemoTransport());
  const command = vi.spyOn(api, "command");
  render(<PeoplePage api={api} />);
  await userEvent.click(
    await screen.findByLabelText("Студент", { exact: true }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Сохранить роли" }));
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith(
      "change_membership_roles",
      ids.membership,
      0,
      { roles: ["reviewer", "methodologist"] },
    ),
  );
  await userEvent.type(screen.getByLabelText("Email"), "reviewer@example.org");
  await userEvent.selectOptions(
    screen.getByLabelText("Роль", { exact: true }),
    "reviewer",
  );
  const expiration = screen.getByLabelText("Действует до");
  // Native datetime-local entry is represented by a browser change event.
  fireEvent.change(expiration, { target: { value: "2030-01-01T12:00" } });
  await userEvent.click(screen.getByRole("button", { name: "Пригласить" }));
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith(
      "create_invitation",
      ids.organization,
      0,
      {
        email: "reviewer@example.org",
        role: "reviewer",
        expires_at: new Date("2030-01-01T12:00").toISOString(),
      },
    ),
  );
});

it("keeps the original command when opening a recommendation", async () => {
  const api = new ApiClient(createDemoTransport());
  const next = await api.next(ids.run);
  const command = vi.spyOn(api, "command");
  render(<QueuePage api={api} id={ids.run} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Открыть проверку" }),
  );
  await waitFor(() =>
    expect(command).toHaveBeenCalledWith(
      "open_review_iteration",
      next!.review_case_id,
      next!.review_case_revision,
      { submission_version_id: next!.submission_version_id },
    ),
  );
});
