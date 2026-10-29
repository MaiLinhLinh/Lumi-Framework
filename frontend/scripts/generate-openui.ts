import { readFileSync, readdirSync, writeFileSync, mkdirSync, realpathSync } from "node:fs";
import { resolve, dirname, relative, isAbsolute, sep } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { z } from "zod/v4";
import { createLumiLibrary, type WidgetModules } from "../src/surface/library";

const framework = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const modules: WidgetModules = new Map();
const schemas: Record<string, any> = {};
const entries: Record<string, any> = {};
const hash = (file: string) => createHash("sha256").update(readFileSync(file)).digest("hex");
const inputs: Record<string, string> = {};
function track(directory: string) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = resolve(directory, entry.name);
    if (entry.isDirectory()) track(file);
    else if (/\.(tsx?|json)$/.test(entry.name)) inputs[relative(framework, file).split(sep).join("/")] = hash(file);
  }
}
track(resolve(framework, "extensions/widgets"));
track(resolve(framework, "frontend/src/runtime"));
inputs["frontend/src/surface/library.tsx"] = hash(resolve(framework, "frontend/src/surface/library.tsx"));
for (const file of ["frontend/package-lock.json", "frontend/vite.config.ts", "frontend/scripts/generate-openui.ts"]) inputs[file] = hash(resolve(framework, file));
for (const directory of readdirSync(resolve(framework, "extensions/widgets")).sort()) {
  const root = resolve(framework, "extensions/widgets", directory);
  const manifest = JSON.parse(readFileSync(resolve(root, "manifest.json"), "utf8"));
  if (manifest.id !== directory || !/^[a-z]+(?:_[a-z]+)*$/.test(directory) || typeof manifest.renderer !== "string" || isAbsolute(manifest.renderer) || manifest.renderer.split(/[\\/]/).includes("..")) throw new Error(`Unsafe widget manifest ${directory}`);
  const renderer = realpathSync(resolve(root, manifest.renderer));
  const within = relative(realpathSync(root), renderer);
  if (isAbsolute(within) || within.startsWith(`..${sep}`) || !renderer.endsWith(".tsx")) throw new Error(`Unsafe renderer ${directory}`);
  const module = await import(pathToFileURL(renderer).href);
  if (!module.widget || !Array.isArray(module.interactionActions)) throw new Error(`Invalid widget ${directory}`);
  modules.set(directory, module);
  const shape = { ...module.widget.props.shape };
  // Children are OpenUI expressions and belong to PlanBlock.children, not props.
  delete shape.children;
  const schema = Object.keys(shape).length === Object.keys(module.widget.props.shape).length
    ? module.widget.props : z.strictObject(shape);
  schemas[directory] = z.toJSONSchema(schema, { io: "input" });
  entries[directory] = { name: module.widget.name, interaction_actions: module.interactionActions,
    source_hash: hash(resolve(root, manifest.renderer)), manifest_hash: hash(resolve(root, "manifest.json")) };
}
const library = createLumiLibrary(modules);
const out = resolve(framework, "build/openui");
mkdirSync(out, { recursive: true });
writeFileSync(resolve(out, "widget-schemas.json"), JSON.stringify({ schemas, entries, inputs }, null, 2));
writeFileSync(resolve(out, "library-spec.json"), JSON.stringify(library.toSpec(), null, 2));
writeFileSync(resolve(out, "library-schema.json"), JSON.stringify(library.toJSONSchema(), null, 2));
writeFileSync(resolve(out, "library-prompt.txt"), library.prompt());
const spec = library.toSpec();
writeFileSync(resolve(out, "widget-definitions.json"), JSON.stringify(Object.fromEntries(
  Object.entries(entries).map(([id, entry]) => [id, { ...spec.components[entry.name], name: entry.name, props_schema: schemas[id] }]),
), null, 2));
console.log(`Generated OpenUI schemas/spec for ${modules.size} widgets`);
