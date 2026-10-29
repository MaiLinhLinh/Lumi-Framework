import { defineConfig } from "vite";
import { readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import { resolve, dirname, relative, isAbsolute, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const frontend = dirname(fileURLToPath(import.meta.url)), root = resolve(frontend, '..');
const id = 'virtual:lumi-widget-modules';
export default defineConfig({
  root: frontend,
  ssr: { noExternal: true },
  resolve: { dedupe: ['react', 'react-dom'] },
  plugins: [{
    name: 'lumi-processor-widgets',
    resolveId(source) { if (source === id) return '\0'+id; },
    load(source) {
      if (source !== '\0'+id) return;
      const imports:string[] = [], entries:string[] = [];
      for (const directory of readdirSync(resolve(root,'extensions/widgets')).sort()) {
        const packageRoot = realpathSync(resolve(root,'extensions/widgets',directory));
        const manifest = JSON.parse(readFileSync(resolve(packageRoot,'manifest.json'),'utf8'));
        if (manifest.id !== directory || !/^[a-z]+(?:_[a-z]+)*$/.test(directory)
          || typeof manifest.renderer !== 'string' || isAbsolute(manifest.renderer) || manifest.renderer.split(/[\\/]/).includes('..')) throw Error('Unsafe processor widget entry');
        const path = realpathSync(resolve(packageRoot,manifest.renderer)), rel=relative(packageRoot,path);
        if (isAbsolute(rel) || rel.startsWith('..'+sep) || !path.endsWith('.tsx')) throw Error('Unsafe processor widget path');
        const name = `widget${imports.length}`;
        imports.push(`import * as ${name} from ${JSON.stringify(path.replaceAll('\\','/'))};`);
        entries.push(`[${JSON.stringify(directory)},${name}]`);
      }
      return imports.join('\n')+`\nexport const modules = new Map([${entries.join(',')}]);`;
    },
    closeBundle() {
      const inputs = {...JSON.parse(readFileSync(resolve(root,'build/openui/widget-schemas.json'),'utf8')).inputs};
      for (const path of ['frontend/vite.processor.config.ts', ...readdirSync(resolve(frontend,'processor')).map(name=>'frontend/processor/'+name)]) {
        inputs[path]=createHash('sha256').update(readFileSync(resolve(root,path))).digest('hex');
      }
      for (const name of ['widget-schemas.json','widget-definitions.json','library-spec.json','library-schema.json']) {
        const path='build/openui/'+name;
        inputs[path]=createHash('sha256').update(readFileSync(resolve(root,path))).digest('hex');
      }
      writeFileSync(resolve(root,'build/processor/manifest.json'),JSON.stringify({protocol:1,entry:'worker.mjs',inputs},null,2));
    },
  }],
  build: { ssr: resolve(frontend,'processor/worker.ts'), outDir: resolve(root,'build/processor'), emptyOutDir:true,
    rollupOptions: {output:{entryFileNames:'worker.mjs',format:'es'}} },
});
