import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient, type W } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import { ReviewEditor } from "../src/pages/Review";
it.each(["untouched", "edited", "saved", "readonly"] as const)(
  "AI arrival respects %s review controls",
  async (mode) => {
    const api = new ApiClient(createDemoTransport());
    const ws = new WorkspaceClient(api);
    const original = await api.review(ids.review);
    const context = await ws.reviewContext(ids.review);
    const session = await api.session();
    const criterion = context.criteria[0];
    const saved = mode === "saved" || mode === "readonly";
    const detail = {
      ...original,
      current_review_revision_id: saved ? crypto.randomUUID() : null,
      criterion_decisions: saved
        ? [
            {
              criterion_id: criterion.id,
              points: 2,
              decision: "manual" as const,
              reason: "Сохранённое решение",
            },
          ]
        : [],
    };
    let finish!: (v: W<"ReviewAssistView">) => void;
    vi.spyOn(ws, "reviewAssist").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.spyOn(ws, "gradePreview").mockResolvedValue({
      raw_score: 0,
      final_score: 0,
      penalty: 0,
      penalty_days: 0,
      penalty_rate: 0,
      pass_score: 5,
      policy_revision: 1,
    });
    const command = vi
      .spyOn(ws, "command")
      .mockImplementation(async () => ({}) as never);
    render(
      <ReviewEditor
        api={api}
        ws={ws}
        detail={detail}
        context={context}
        session={session}
        readOnly={mode === "readonly"}
        version={{
          id: context.homework_version_id,
          criteria: context.criteria,
          max_score: context.max_score,
          student_text: context.student_text,
        }}
        refresh={() => {}}
      />,
    );
    if (mode === "edited") {
      await userEvent.clear(screen.getByLabelText(`Баллы: ${criterion.title}`));
      await userEvent.type(
        screen.getByLabelText(`Баллы: ${criterion.title}`),
        "3",
      );
      await userEvent.type(
        screen.getByLabelText("Обратная связь студенту"),
        "Мой отзыв",
      );
    }
    const runId = crypto.randomUUID();
    await act(async () =>
      finish({
        id: runId,
        revision: 1,
        created_at: new Date().toISOString(),
        error_code: null,
        status: "succeeded",
        result: {
          feedback_draft: "Отзыв модели",
          suggestions: [
            {
              criterion_id: criterion.id,
              status: "suggested",
              proposed_points: 5,
              reason: "Обоснование модели",
              confidence: "high",
            },
          ],
        },
      }),
    );
    expect(
      screen.queryByRole("button", { name: "Принять все" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Принять предложение" }),
    ).not.toBeInTheDocument();
    if (mode === "readonly") {
      expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
      return;
    }
    expect(screen.getByLabelText(`Баллы: ${criterion.title}`)).toHaveValue(
      mode === "untouched" ? 5 : mode === "edited" ? 3 : 2,
    );
    if (mode === "edited")
      expect(screen.getByLabelText("Обратная связь студенту")).toHaveValue(
        "Мой отзыв",
      );
    if (mode === "untouched") {
      const resultScore = () =>
        Array.from(
          screen
            .getByRole("heading", { name: "Результат" })
            .closest("section")!
            .querySelectorAll("dt"),
        ).find((el) => el.textContent === "Итог")!.nextElementSibling!
          .textContent;
      await waitFor(() => expect(resultScore()).toMatch(/^5 из 10/));
      expect(
        screen.queryByLabelText("Своё требование"),
      ).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "Добавить своё требование" }),
      );
      expect(screen.getByLabelText("Своё требование")).toBeInTheDocument();
      expect(screen.getByLabelText("Обратная связь студенту")).toHaveValue(
        "Отзыв модели",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Сохранить черновик" }),
      );
      await waitFor(() =>
        expect(command).toHaveBeenCalledWith(
          "save_workspace_review",
          ids.review,
          detail.revision,
          expect.objectContaining({
            ai_run_id: runId,
            draft: expect.objectContaining({
              criterion_decisions: expect.arrayContaining([
                expect.objectContaining({ points: 5, decision: "accepted" }),
              ]),
            }),
          }),
        ),
      );
      await userEvent.clear(screen.getByLabelText(`Баллы: ${criterion.title}`));
      await userEvent.type(
        screen.getByLabelText(`Баллы: ${criterion.title}`),
        "2.75",
      );
      expect(resultScore()).toMatch(/^2,75 из 10/);
    }
  },
);
