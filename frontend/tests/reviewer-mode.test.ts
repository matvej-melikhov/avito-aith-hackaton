import { expect, it, vi } from "vitest";
import { ApiClient } from "../src/api/client";
import { WorkspaceClient } from "../src/api/workspace";
import { createDemoTransport } from "../src/mocks/transport";
import { nextPoolWork } from "../src/pages/reviewMode";
it("takes the first next eligible server row across pool pages without client ranking or writes", async () => {
  const api = new ApiClient(createDemoTransport());
  const ws = new WorkspaceClient(api);
  const [current] = (await ws.works()).items;
  const first = {
    ...current,
    submission_id: crypto.randomUUID(),
    review_iteration_id: crypto.randomUUID(),
    review_deadline: "2030-01-01T00:00:00Z",
  };
  const second = {
    ...first,
    submission_id: crypto.randomUUID(),
    review_iteration_id: crypto.randomUUID(),
    review_deadline: "2020-01-01T00:00:00Z",
  };
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValueOnce({
      items: Array.from({ length: 20 }, () => current),
      total: 22,
      offset: 0,
      limit: 20,
    })
    .mockResolvedValueOnce({
      items: [first, second],
      total: 22,
      offset: 20,
      limit: 20,
    });
  const writes = vi.spyOn(api, "command");
  expect(
    await nextPoolWork(ws, current.review_iteration_id!, current.submission_id),
  ).toEqual(first);
  expect(works.mock.calls).toEqual([
    [{ view: "pool", limit: 20, offset: 0 }],
    [{ view: "pool", limit: 20, offset: 20 }],
  ]);
  expect(writes).not.toHaveBeenCalled();
});
it("reports exhausted eligible pool without retrying the same page", async () => {
  const ws = new WorkspaceClient(new ApiClient(createDemoTransport()));
  const works = vi
    .spyOn(ws, "works")
    .mockResolvedValue({ items: [], total: 0, offset: 0, limit: 20 });
  expect(await nextPoolWork(ws, "current")).toBeNull();
  expect(works).toHaveBeenCalledTimes(1);
});
