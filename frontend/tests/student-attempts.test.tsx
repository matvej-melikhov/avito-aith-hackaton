import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { ids } from "../src/mocks/fixtures";
import { createDemoTransport } from "../src/mocks/transport";
import { WorkspaceSubmissionDetail } from "../src/pages/WorkspaceSubmissionDetail";

it("shows the immutable artifact and comment inside every submission attempt", async () => {
  const objectUrl = `blob:${window.location.origin}/${crypto.randomUUID()}`;
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue(objectUrl);
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const draft = await ws.command("save_work_draft", ids.publication, 0, {
    artifact_url: "",
    upload_id: (
      await ws.command("upload_artifact", ids.user, 0, {
        filename: "attempt-2.md",
        media_type: "text/markdown",
        content_base64: btoa("# Second attempt"),
        private: false,
      })
    ).id,
    comment: "Исправил обработку ошибок.",
  });
  await ws.command("submit_work_draft", draft.id, draft.revision, {});

  render(<WorkspaceSubmissionDetail ws={ws} id={ids.submission} />);

  expect(
    await screen.findByRole("link", { name: "attempt-2.md ↗" }),
  ).toBeVisible();
  expect(screen.getByText("Исправил обработку ошибок.")).toBeVisible();

  await userEvent.setup().click(screen.getByText(/^Попытка 1,/));
  expect(await screen.findByRole("link", { name: "work.md ↗" })).toBeVisible();
  expect(screen.getByRole("link", { name: "attempt-2.md ↗" })).toHaveAttribute(
    "href",
    objectUrl,
  );
  createObjectURL.mockRestore();
});
