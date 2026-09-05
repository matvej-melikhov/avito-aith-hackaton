import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import { ReviewEditor } from "../src/pages/Review";
it.each([false, true])(
  "refreshes outcome revision and refuses a changed draft: changed=%s",
  async (changed) => {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute("open");
    };
    const api = new ApiClient(createDemoTransport());
    const ws = new WorkspaceClient(api);
    const original = await api.review(ids.review);
    const context = await ws.reviewContext(ids.review);
    const session = await api.session();
    const revisionId = crypto.randomUUID();
    const detail = { ...original, current_review_revision_id: revisionId };
    vi.spyOn(ws, "reviewDetail").mockResolvedValue({
      ...detail,
      revision: detail.revision + 1,
      current_review_revision_id: changed ? crypto.randomUUID() : revisionId,
    });
    const command = vi
      .spyOn(ws, "command")
      .mockImplementation(
        async () => ({ id: ids.review, revision: 1 }) as never,
      );
    render(
      <ReviewEditor
        api={api}
        ws={ws}
        detail={detail}
        context={context}
        session={session}
        version={{
          id: context.homework_version_id,
          criteria: context.criteria,
          max_score: context.max_score,
          student_text: context.student_text,
        }}
        refresh={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Зачесть" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Подтвердить публикацию" }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Подтвердить публикацию" }),
    );
    if (changed) {
      await screen.findByText(/Коллега изменил черновик/);
      expect(
        command.mock.calls.some(
          ([name]) => name === "publish_workspace_review",
        ),
      ).toBe(false);
      return;
    }
    await waitFor(() =>
      expect(command).toHaveBeenCalledWith(
        "publish_workspace_review",
        ids.review,
        detail.revision + 1,
        { review_revision_id: revisionId, apply_penalty: true },
      ),
    );
  },
);
