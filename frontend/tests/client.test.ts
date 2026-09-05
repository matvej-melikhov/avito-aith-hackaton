import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../src/api/client";
import { createDemoTransport } from "../src/mocks/transport";
import { ids } from "../src/mocks/fixtures";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import schema from "../../specs/001-backend-core/contracts/command.schema.json";
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
const validate = ajv.compile(schema);
const ok = () =>
  new Response(JSON.stringify({ id: ids.homework, revision: 0 }), {
    status: 201,
    headers: { "Content-Type": "application/json" },
  });
describe("REST command boundary", () => {
  it("sends exact frozen command, cookie credentials and no client actor", async () => {
    const transport = vi.fn(async () => ok());
    const api = new ApiClient(transport);
    await api.command("create_homework", ids.run, 3, { title: "API" });
    const [url, init] = transport.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(`/api/v1/course-runs/${ids.run}/homeworks`);
    expect(init.credentials).toBe("same-origin");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(validate(body), JSON.stringify(validate.errors)).toBe(true);
    expect(body.expected_revision).toBe(3);
    expect(body).not.toHaveProperty("actor");
    expect(body).not.toHaveProperty("organization_id");
  });
  it("reuses the exact receipt after a lost response and creates a new one after success", async () => {
    const transport = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockImplementation(async () => ok());
    const api = new ApiClient(transport);
    await expect(
      api.command("create_homework", ids.run, 0, { title: "API" }),
    ).rejects.toMatchObject({ status: 0 });
    await api.command("create_homework", ids.run, 0, { title: "API" });
    await api.command("create_homework", ids.run, 0, { title: "API" });
    expect(transport.mock.calls[0][1].body).toBe(
      transport.mock.calls[1][1].body,
    );
    expect(transport.mock.calls[2][1].body).not.toBe(
      transport.mock.calls[1][1].body,
    );
  });
  it("surfaces 409 and never retries mutations automatically", async () => {
    const transport = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            code: "revision_conflict",
            message: "conflict",
            action: null,
          }),
          { status: 409 },
        ),
    );
    const api = new ApiClient(transport);
    await expect(
      api.command("save_review_revision", ids.review, 0, {
        feedback: "",
        criterion_decisions: [],
        review_notes: [],
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(transport).toHaveBeenCalledTimes(1);
  });
  it("does not turn an HTTP outage into fixture data", async () => {
    const api = new ApiClient(
      async () => new Response("upstream failed", { status: 502 }),
    );
    await expect(api.courses()).rejects.toMatchObject({
      status: 502,
      code: "http_error",
    });
  });
  it("supports empty 204 responses", async () => {
    const api = new ApiClient(async () => new Response(null, { status: 204 }));
    expect(
      await api.request("/v1/session", { method: "DELETE" }),
    ).toBeUndefined();
  });
  it("handles uncertain success response parsing with the same receipt", async () => {
    const transport = vi
      .fn()
      .mockResolvedValueOnce(new Response("broken", { status: 201 }))
      .mockImplementation(async () => ok());
    const api = new ApiClient(transport);
    await expect(
      api.command("create_homework", ids.run, 0, { title: "x" }),
    ).rejects.toMatchObject({ status: 0 });
    await api.command("create_homework", ids.run, 0, { title: "x" });
    expect(transport.mock.calls[0][1].body).toBe(
      transport.mock.calls[1][1].body,
    );
  });
});
it("validates a full preflight → submit and draft → human publish against the frozen wire schema", async () => {
  const demo = createDemoTransport();
  const api = new ApiClient(async (input, init) => {
    if (init?.body) {
      const body = JSON.parse(String(init.body));
      expect(validate(body), JSON.stringify(validate.errors)).toBe(true);
    }
    return demo(input, init);
  });
  const capability = await api.command(
    "preflight_submission",
    ids.publication,
    0,
    { artifact_url: "https://github.com/example/test" },
  );
  await api.command(
    "submit_work",
    capability.submission_id,
    capability.submission_revision,
    { artifact_reference_id: capability.artifact_reference_id! },
  );
  expect((await api.submission(ids.submission)).versions).toHaveLength(1);
  const save = await api.command("save_review_revision", ids.review, 0, {
    feedback: "Проверено",
    criterion_decisions: [
      {
        criterion_id: ids.criterion,
        points: 8,
        decision: "manual",
        reason: "Есть обработка ошибок",
      },
    ],
    review_notes: [],
  });
  await api.command(
    "publish_review",
    ids.review,
    save.review_iteration_revision,
    { review_revision_id: save.review_revision_id },
  );
  expect((await api.review(ids.review)).status).toBe("published");
  expect(
    (await api.submission(ids.submission)).publications[0].total_score,
  ).toBe(8);
});
