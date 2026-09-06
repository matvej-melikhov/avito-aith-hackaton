import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient, type W } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids, version } from "../src/mocks/fixtures";
import { ReviewEditor } from "../src/pages/Review";
import { moscowBoundary, moscowDate } from "../src/dateOnly";
import { nextFromMine, personalLabel } from "../src/reviewQueue";
import { ActionMessageProvider } from "../src/ActionMessage";

it("roundtrips one-day course and absence dates in Moscow, independent of browser zone", () => {
  expect(moscowBoundary("2026-09-06")).toBe("2026-09-05T21:00:00.000Z");
  expect(moscowBoundary("2026-09-06", true)).toBe("2026-09-06T20:59:59.999Z");
  expect(moscowDate(moscowBoundary("2026-09-06"))).toBe("2026-09-06");
  expect(moscowDate(moscowBoundary("2026-09-06", true))).toBe("2026-09-06");
  expect(moscowDate(null)).toBe("");
});

async function editor(published = false, coordinator = false) {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const session = {
    ...(await api.session()),
    roles: [coordinator ? "methodologist" : "reviewer"] as (
      "reviewer" | "methodologist"
    )[],
  };
  const detail = await ws.reviewDetail(ids.review);
  const context = await ws.reviewContext(ids.review);
  if (published) {
    detail.status = "published";
    detail.current_review_revision_id = ids.revision;
  }
  const view = render(
    <ActionMessageProvider>
      <ReviewEditor
        api={api}
        ws={ws}
        session={session}
        detail={detail}
        context={context}
        version={version}
        coordinator={coordinator}
        refresh={vi.fn()}
      />
    </ActionMessageProvider>,
  );
  return { api, ws, detail, context, view };
}

it("collapses the exact assignment condition without losing an edited response", async () => {
  await editor();
  const user = userEvent.setup();
  expect(screen.getByText(version.student_text)).toBeVisible();
  const feedback = screen.getByLabelText("Обратная связь студенту");
  await user.type(feedback, "Сохранить мой отзыв");
  const condition = screen
    .getByRole("heading", { name: "Условие задания" })
    .closest("section")!;
  await user.click(within(condition).getByRole("button", { name: "Свернуть" }));
  expect(screen.queryByText(version.student_text)).toBeNull();
  expect(feedback).toHaveValue("Сохранить мой отзыв");
  await user.click(
    within(condition).getByRole("button", { name: "Развернуть" }),
  );
  expect(screen.getByText(version.student_text)).toBeVisible();
});

it.each([false, true])(
  "opens an immutable correction with a reason (coordinator=%s)",
  async (coordinator) => {
    const { api, detail } = await editor(true, coordinator);
    const command = vi.spyOn(api, "command").mockResolvedValue({
      review_iteration_id: "corrected-review",
      review_case_id: ids.reviewCase,
      revision: 1,
    });
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Изменить оценку и отзыв" }),
    );
    expect(command).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Открыть новую версию" }),
    ).toBeDisabled();
    await user.type(
      screen.getByLabelText("Причина исправления"),
      "Ошибка в балле",
    );
    await user.click(
      screen.getByRole("button", { name: "Открыть новую версию" }),
    );
    expect(command).toHaveBeenCalledWith(
      "create_review_correction",
      ids.review,
      detail.revision,
      {
        published_review_revision_id: ids.revision,
        reason: "Ошибка в балле",
      },
    );
    expect(window.location.hash).toBe("#/reviews/corrected-review");
  },
);

it("does not take a pool work until the reviewer confirms; handles an empty pool", async () => {
  const { api, ws } = await editor(true);
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
  const command = vi.spyOn(api, "command");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Следующая работа" }));
  await screen.findByRole("dialog");
  expect(works.mock.calls.map(([p]) => p?.view)).toEqual(["active"]);
  expect(command).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Остаться" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Следующая работа" }));
  await user.click(
    await screen.findByRole("button", { name: "Взять из пула" }),
  );
  await screen.findByText("В пуле пока нет подходящих работ.");
  expect(works.mock.calls.map(([p]) => p?.view)).toEqual([
    "active",
    "active",
    "pool",
  ]);
  expect(command).not.toHaveBeenCalled();
});

it("checks subsequent pages of my queue and propagates a concurrent conflict", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const work = (await ws.works({ view: "pool" })).items[0];
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValueOnce({
      items: [{ ...work, submission_id: "skip" }],
      total: 2,
      offset: 0,
      limit: 1,
    })
    .mockResolvedValueOnce({ items: [work], total: 2, offset: 1, limit: 1 });
  vi.spyOn(api, "command").mockRejectedValue(
    new Error("conflict: already published"),
  );
  await expect(nextFromMine(ws, ["skip"])).rejects.toThrow("conflict");
  expect(works.mock.calls.every(([p]) => p?.view === "active")).toBe(true);
  expect(works.mock.calls[1][0]?.offset).toBe(1);
});

it("distinguishes personal assignment from active participation and hides terminal labels", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const original = (await ws.works()).items[0];
  const w: W<"WorkItem"> = {
    ...original,
    status: "pending_review",
    participant_ids: [],
    primary_reviewer_id: ids.user,
    responsible_reviewer_id: null,
  };
  expect(personalLabel(w, ids.user)).toBe("Нужно проверить");
  expect(personalLabel({ ...w, participant_ids: [ids.user] }, ids.user)).toBe(
    "Проверяю",
  );
  expect(personalLabel({ ...w, status: "passed" }, ids.user)).toBeNull();
  expect(personalLabel(w, "someone-else")).toBeNull();
});
