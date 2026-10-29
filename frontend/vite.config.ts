import { defineConfig, type Plugin, type Manifest } from "vite";
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = dirname(fileURLToPath(import.meta.url));
const framework = resolve(frontend, "..");
const extensions = resolve(framework, "extensions");
const outDir = resolve(framework, "build/frontend");
type Entry = { kind: string; id: string; source: string; styles?: string; compiled: boolean };
const discovered: Entry[] = [];
const input: Record<string, string> = { app: resolve(frontend, "index.html") };

function packageFile(root: string, value: unknown): string {
  if (typeof value !== "string" || !value || isAbsolute(value) || value.split(/[\\/]/).includes("..")) {
    throw new Error(`Invalid extension path in ${root}`);
  }
  const path = realpathSync(resolve(root, value));
  const rel = relative(realpathSync(root), path);
  if (rel.startsWith(`..${sep}`) || isAbsolute(rel)) throw new Error(`Extension path escapes ${root}`);
  return path;
}
const slash = (path: string) => path.split(sep).join("/");
for (const [kind, field] of [["widgets", "renderer"], ["effects", "handler"]] as const) {
  const root = resolve(extensions, kind);
  for (const directory of readdirSync(root, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!directory.isDirectory()) continue;
    const packageRoot = resolve(root, directory.name);
    const manifestPath = resolve(packageRoot, "manifest.json");
    if (!existsSync(manifestPath)) continue;
    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
    if (!/^[a-z]+(?:_[a-z]+)*$/.test(manifest.id) || manifest.id !== directory.name) {
      throw new Error(`Invalid extension ID in ${manifestPath}`);
    }
    const entry = packageFile(packageRoot, manifest[field]);
    const styles = manifest.styles ? packageFile(packageRoot, manifest.styles) : undefined;
    // Existing JS files remain backend validation inputs; they are not a second renderer.
    const compiled = /\.tsx?$/.test(entry);
    discovered.push({ kind, id: manifest.id, source: slash(relative(framework, entry)),
      styles: styles && slash(relative(framework, styles)), compiled });
    if (compiled) {
      input[`${kind}/${manifest.id}`] = entry;
      if (styles) input[`${kind}/${manifest.id}/styles`] = styles;
    }
  }
}

function extensionIndex(): Plugin {
  return {
    name: "lumi-extension-index",
    closeBundle() {
      const manifest: Manifest = JSON.parse(readFileSync(resolve(outDir, ".vite/manifest.json"), "utf-8"));
      const sourceToOutput: Record<string, string> = {};
      for (const item of Object.values(manifest)) {
        if (!item.src) continue;
        const source = slash(relative(framework, resolve(frontend, item.src)));
        sourceToOutput[source] = `/assets/${item.file}`;
        // Vite may combine identical CSS entries. Every original entry name
        // must still resolve to that shared output, including a newly added widget.
        for (const entry of discovered) {
          const names = (item as typeof item & { names?: string[] }).names || [];
          if (entry.styles && names.includes(`${entry.kind}/${entry.id}/styles.css`)) {
            sourceToOutput[entry.styles] = `/assets/${item.file}`;
          }
        }
      }
      for (const entry of discovered.filter(entry => entry.compiled)) {
        if (!sourceToOutput[entry.source] || (entry.styles && !sourceToOutput[entry.styles])) {
          throw new Error(`Missing build output for extension ${entry.kind}/${entry.id}`);
        }
      }
      mkdirSync(resolve(framework, "build/openui"), { recursive: true });
      writeFileSync(resolve(framework, "build/openui/extension-index.json"), JSON.stringify({
        extensions: discovered, source_to_output: sourceToOutput,
      }, null, 2));
    },
  };
}

export default defineConfig({
  root: frontend,
  base: "/assets/",
  resolve: { dedupe: ["react", "react-dom"] },
  esbuild: { jsx: "automatic" },
  plugins: [extensionIndex()],
  server: { proxy: {
    "/api": "http://127.0.0.1:8002", "/ws": { target: "ws://127.0.0.1:8002", ws: true },
    "/extensions": "http://127.0.0.1:8002", "/knowledge": "http://127.0.0.1:8002",
    "/assets/resources": "http://127.0.0.1:8002",
  } },
  build: { outDir, emptyOutDir: true, manifest: true,
    rollupOptions: { input, preserveEntrySignatures: "strict" } },
});
