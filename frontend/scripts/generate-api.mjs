import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import openapiTS, { astToString } from "openapi-typescript";
import { compile } from "json-schema-to-typescript";
import { parse } from "yaml";
const root = new URL(
  "../../specs/001-backend-core/contracts/",
  import.meta.url,
);
const apiText = await readFile(new URL("openapi.yaml", root), "utf8");
const commandText = await readFile(
  new URL("command.schema.json", root),
  "utf8",
);
const command = JSON.parse(commandText);
const variants = command.$defs.command_variant.oneOf.map((v) => v.properties);
const payloads = {
  title: "CommandPayloads",
  type: "object",
  additionalProperties: false,
  required: variants.map((v) => v.command_name.const),
  properties: Object.fromEntries(
    variants.map((v) => [v.command_name.const, v.payload]),
  ),
  $defs: command.$defs,
};
const payloadTypes = await compile(payloads, "CommandPayloads", {
  bannerComment: "",
  additionalProperties: false,
  ignoreMinAndMaxItems: true,
});
const targets = Object.fromEntries(
  variants.map((v) => [v.command_name.const, v.revision_target.const]),
);
const api = parse(apiText);
const routes = {};
const results = [];
for (const [path, methods] of Object.entries(api.paths)) {
  for (const [method, operation] of Object.entries(methods)) {
    const name = operation["x-command-name"];
    if (!name) continue;
    routes[name] = { path, method: method.toUpperCase() };
    const response = Object.entries(operation.responses).find(([status]) =>
      status.startsWith("2"),
    )[1];
    const ref = response.content?.["application/json"]?.schema?.$ref;
    results.push(
      `${name}: ${ref ? `components['schemas']['${ref.split("/").at(-1)}']` : "void"};`,
    );
  }
}
const hash = createHash("sha256")
  .update(apiText)
  .update(commandText)
  .digest("hex");
const files = {
  "schema.ts": astToString(await openapiTS(new URL("openapi.yaml", root))),
  "commands.ts": `// Generated from frozen contracts. Run npm run generate:api.\n${payloadTypes}\nexport const revisionTargets = ${JSON.stringify(targets, null, 2)} as const;\nexport type CommandName = keyof CommandPayloads;\nexport type WireCommand<K extends CommandName = CommandName> = { request_id: string; idempotency_key: string; command_name: K; revision_target: typeof revisionTargets[K]; target_id: string; expected_revision: number; payload: CommandPayloads[K] };\n`,
  "contract-hash.txt": `${hash}\n`,
  "routes.ts": `// Generated from frozen OpenAPI.\nimport type { components } from './schema';\nexport const commandRoutes = ${JSON.stringify(routes, null, 2)} as const;\nexport interface CommandResults { ${results.join("\n")} }\n`,
};
for (const [name, content] of Object.entries(files)) {
  const dest = new URL(`../src/api/${name}`, import.meta.url);
  if (process.argv.includes("--check")) {
    if ((await readFile(dest, "utf8").catch(() => "")) !== content)
      throw new Error(`${name} is stale; run npm run generate:api`);
  } else await writeFile(dest, content);
}
console.log(
  `Frozen API types ${process.argv.includes("--check") ? "verified" : "generated"}: ${hash}`,
);
