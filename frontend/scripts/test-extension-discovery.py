"""Exercise adding a four-file widget without editing any app import list."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent))
from lumi_openui.extension_loader import ExtensionLoader
from lumi_openui.frontend_assets import FrontendBuild

widgets = (ROOT / "extensions/widgets").resolve()
probe = (widgets / "cp_probe").resolve()
assert probe.parent == widgets and probe.name == "cp_probe"
if probe.exists(): raise RuntimeError("Probe directory already exists; do not overwrite it.")
frontend = ROOT / "frontend"

def build():
    for args in (
        ["node", "--import", "tsx", "scripts/generate-openui.ts"],
        ["node", "node_modules/vite/bin/vite.js", "build"],
        ["node", "node_modules/vite/bin/vite.js", "build", "--config", "vite.processor.config.ts"],
    ):
        subprocess.run(args, cwd=frontend, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

try:
    probe.mkdir()
    source = widgets / "answer"
    for name in ("contract.py", "styles.css"):
        shutil.copyfile(source / name, probe / name)
    (probe / "manifest.json").write_text(json.dumps({"id": "cp_probe", "renderer": "component.tsx", "contract": "contract.py", "styles": "styles.css"}), encoding="utf-8")
    component = (source / "component.tsx").read_text(encoding="utf-8").replace('name: "Answer"', 'name: "CpProbe"')
    (probe / "component.tsx").write_text(component, encoding="utf-8")
    build()
    loaded = ExtensionLoader(ROOT / "extensions").load()
    assert len(loaded.widgets) == 9
    assert loaded.widget_registry.get("cp_probe").validate({"value": " test "}) == {"value": "test"}
    catalog = FrontendBuild(ROOT).browser_catalog(loaded.browser_catalog(url_prefix="/extensions"))
    assert len(catalog["widgets"]) == 9
    assert any(entry["id"] == "cp_probe" and entry["renderer"].endswith(".js") for entry in catalog["widgets"])
    import asyncio
    from lumi_openui.openui import OpenUIProcessor
    async def verify_processor():
        processor = OpenUIProcessor()
        try:
            result = await processor.parse_program('root = SurfaceProgram([probe], [])\nprobe = Slot({col:1,row:1,col_span:4,row_span:4}, CpProbe("test"))')
            assert result['plan']['surface']['blocks'][0]['widget_id'] == 'cp_probe'
        finally:
            await processor.close()
    asyncio.run(verify_processor())
    print("PASS: ninth widget discovered, schema validated, module/CSS served without app import edits.")
finally:
    # The resolved deletion target is checked against the explicitly created package.
    assert probe.resolve().parent == widgets and probe.name == "cp_probe"
    shutil.rmtree(probe)
    build()
    assert len(ExtensionLoader(ROOT / "extensions").load().widgets) == 8
    print("Restored production build/catalog to eight widgets.")
