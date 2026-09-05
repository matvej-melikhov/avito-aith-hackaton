import { readFileSync } from "node:fs";
import { parse } from "yaml";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { it, expect } from "vitest";
import * as f from "../src/mocks/fixtures";
import { ApiClient } from "../src/api/client";
import { createDemoTransport } from "../src/mocks/transport";
const openapi = parse(
  readFileSync("../specs/001-backend-core/contracts/openapi.yaml", "utf8"),
);
const ajv = new Ajv2020({ strict: false });
addFormats(ajv);
ajv.addSchema({
  $id: "urn:frontend-test:openapi",
  components: openapi.components,
});
function check(name: string, data: unknown) {
  const validate = ajv.compile({
    $ref: `urn:frontend-test:openapi#/components/schemas/${name}`,
  });
  expect(validate(data), JSON.stringify(validate.errors)).toBe(true);
}
it("uses fixtures that satisfy frozen response schemas", () => {
  for (const [name, value] of Object.entries({
    Session: f.session,
    Organization: f.organization,
    Course: f.course,
    CourseRun: f.run,
    HomeworkSummary: f.homework,
    HomeworkVersionSummary: f.version,
    HomeworkHistory: f.history,
    ReviewDetail: f.review,
    SubmissionHistory: f.submission,
    Operation: f.operation(),
  }))
    check(name, value);
});
it("demo response projections still satisfy schemas after commands", async () => {
  const api = new ApiClient(createDemoTransport());
  const op = await api.command("start_ai_review", f.ids.review, 0, {});
  check("Operation", op);
  check("ReviewDetail", await api.review(f.ids.review));
  const capability = await api.command(
    "preflight_submission",
    f.ids.publication,
    0,
    { artifact_url: "https://github.com/example/demo" },
  );
  check("ArtifactCapability", capability);
  const submitted = await api.command("submit_work", f.ids.submission, 0, {
    artifact_reference_id: capability.artifact_reference_id!,
  });
  check("SubmissionVersionCreated", submitted);
  check("SubmissionHistory", await api.submission(f.ids.submission));
});
