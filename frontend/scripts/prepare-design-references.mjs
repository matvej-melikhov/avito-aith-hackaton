// Prepare isolated, unscaled reference screens for browser comparison.
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const root = new URL("../../", import.meta.url);
const design = new URL("docs/design/", root);
const output = new URL("frontend/.cache/design-references/", root);
const source = await readFile(new URL("screens.html", design), "utf8");
const document = new JSDOM(source).window.document;
const fonts = [...document.querySelectorAll('link[rel="stylesheet"]')]
  .map((link) => link.outerHTML)
  .join("\n");
const css = await Promise.all(
  ["tokens.css", "components.css"].map((name) =>
    readFile(new URL(name, design), "utf8"),
  ),
);
await mkdir(output, { recursive: true });
const manifest = {
  sourceSha256: createHash("sha256").update(source).digest("hex"),
  screens: [],
};
for (const screen of document.querySelectorAll("section.screen")) {
  const viewport = screen.querySelector(".vp");
  if (!viewport) throw new Error(`Missing reference viewport: ${screen.id}`);
  const html = `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reference ${screen.id}</title>${fonts}<style>${css.join("\n")}</style><body>${viewport.innerHTML}</body></html>`;
  const file = new URL(`${screen.id}.html`, output);
  await writeFile(file, html);
  manifest.screens.push({ id: screen.id, path: fileURLToPath(file) });
}
await writeFile(
  new URL("manifest.json", output),
  JSON.stringify(manifest, null, 2) + "\n",
);
console.log(JSON.stringify(manifest, null, 2));
