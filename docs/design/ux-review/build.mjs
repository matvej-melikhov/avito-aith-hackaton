// Builds a self-contained comparison from the current screens, without altering them.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const dir = path.dirname(fileURLToPath(import.meta.url));
const design = path.dirname(dir);
const source = fs.readFileSync(path.join(design, 'screens.html'), 'utf8');
const css = source.match(/<!-- css:start -->\s*<style>([\s\S]*?)<\/style>\s*<!-- css:end -->/)[1];
const screens = [...source.matchAll(/<section class="screen" id="([^"]+)">([\s\S]*?)<\/section>/g)].map(([, id, section]) => {
  const title = section.match(/<h2>(.*?)<\/h2>/)[1];
  const html = section.match(/<div class="shot"[^>]*><div class="vp">([\s\S]*)<\/div><\/div>\s*$/)?.[1];
  if (!html) throw new Error(`Cannot extract screen ${id}`);
  return {id, title, html};
});
if (screens.length !== 22) throw new Error(`Expected 22 screens, found ${screens.length}; review inventory before rebuilding.`);
const data = {screens, css, sha:crypto.createHash('sha256').update(source).digest('hex'), generated:new Date().toISOString()};
let output = fs.readFileSync(path.join(dir, 'viewer.html'), 'utf8');
const embed = value => value.replace(/<\/script/gi, '<\\/script');
output = output.replace('/* AUDIT_CASES */', () => fs.readFileSync(path.join(dir, 'cases.js'), 'utf8'));
output = output.replace('/* AUDIT_STYLES */', () => fs.readFileSync(path.join(dir, 'viewer.css'), 'utf8'));
output = output.replace('/* PROPOSAL_STYLES */', () => JSON.stringify(fs.readFileSync(path.join(dir, 'proposal.css'), 'utf8')));
output = output.replace('/* SOURCE_DATA */', () => embed(JSON.stringify(data)));
fs.writeFileSync(path.join(design, 'ux-review.html'), output);
console.log(`Built ${screens.length} source snapshots · ${Math.round(Buffer.byteLength(output)/1024)} KB · SHA ${data.sha.slice(0,12)}`);
