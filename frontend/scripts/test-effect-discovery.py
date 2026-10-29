"""Verify three-file effect auto-discovery, then restore the six-effect build."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent))
from lumi_openui.extension_loader import ExtensionLoader
from lumi_openui.frontend_assets import FrontendBuild

effects = (ROOT / 'extensions/effects').resolve()
probe = (effects / 'cp_probe').resolve()
assert probe.parent == effects and probe.name == 'cp_probe'
if probe.exists(): raise RuntimeError('Probe already exists; refusing to overwrite.')

def build():
    subprocess.run(['node', 'node_modules/vite/bin/vite.js', 'build'], cwd=ROOT / 'frontend',
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

try:
    probe.mkdir()
    for name in ('effect.tsx', 'styles.css'):
        shutil.copyfile(effects / 'pulse' / name, probe / name)
    manifest = json.loads((effects / 'pulse/manifest.json').read_text(encoding='utf-8'))
    manifest['id'] = 'cp_probe'
    (probe / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    build()
    loaded = ExtensionLoader(ROOT / 'extensions').load()
    catalog = FrontendBuild(ROOT).browser_catalog(loaded.browser_catalog(url_prefix='/extensions'))
    assert len(catalog['effects']) == 7
    entry = next(e for e in catalog['effects'] if e['id'] == 'cp_probe')
    assert entry['handler'].endswith('.js') and entry['styles'].endswith('.css')
    print('PASS: seventh three-file effect discovered and compiled without app import edits.')
finally:
    assert probe.resolve().parent == effects and probe.name == 'cp_probe'
    shutil.rmtree(probe)
    build()
    restored = ExtensionLoader(ROOT / 'extensions').load().browser_catalog(url_prefix='/extensions')
    assert len(FrontendBuild(ROOT).browser_catalog(restored)['effects']) == 6
    print('Restored production build/catalog to six effects.')
