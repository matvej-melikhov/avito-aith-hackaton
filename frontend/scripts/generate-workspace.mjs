import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";
const source = new URL(
  "../../specs/005-workspace-completion/contracts/openapi.json",
  import.meta.url,
);
const schema = JSON.parse(await readFile(source, "utf8"));
const routes = {},
  inputs = [],
  results = [];
for (const [path, methods] of Object.entries(schema.paths))
  for (const [method, operation] of Object.entries(methods)) {
    const name = operation["x-command-name"];
    if (!name) continue;
    routes[name] = { path, method: method.toUpperCase() };
    const ref = operation.requestBody.content["application/json"].schema.$ref
      .split("/")
      .at(-1);
    const payload = schema.components.schemas[ref].properties.payload.$ref
      .split("/")
      .at(-1);
    const response = Object.entries(operation.responses)
      .find(([status]) => status.startsWith("2"))[1]
      .content["application/json"].schema.$ref.split("/")
      .at(-1);
    inputs.push(`${name}: W<'${payload}'>;`);
    results.push(`${name}: W<'${response}'>;`);
  }
const files = {
  "workspace-schema.ts": astToString(await openapiTS(source)),
  "workspace-routes.ts": `// Generated from workspace v2 OpenAPI.\nimport type {components} from './workspace-schema';\nexport type W<K extends keyof components['schemas']> = components['schemas'][K];\nexport const workspaceRoutes = ${JSON.stringify(routes, null, 2)} as const;\nexport interface WorkspaceInputs {${inputs.join("\n")}}\nexport interface WorkspaceResults {${results.join("\n")}}\n`,
};
for (const [name, content] of Object.entries(files)) {
  const dest = new URL(`../src/api/${name}`, import.meta.url);
  if (process.argv.includes("--check")) {
    if ((await readFile(dest, "utf8").catch(() => "")) !== content)
      throw new Error(`Stale ${name}`);
  } else await writeFile(dest, content);
}
console.log("Workspace client contracts verified");
