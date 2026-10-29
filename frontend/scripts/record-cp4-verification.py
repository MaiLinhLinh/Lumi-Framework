"""Preserve completed CP4 QA without overwriting earlier checkpoint evidence."""
import base64
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
original = ROOT.parents[1] / 'final_framework'
report = json.loads((ROOT/'frontend/test-results/report.json').read_text(encoding='utf-8'))
assert report['stats']['expected'] == 11 and report['stats']['unexpected'] == 0
manifest = json.loads((ROOT/'docs/CP1_COPY_MANIFEST.json').read_text(encoding='utf-8'))
changed = [entry['path'] for entry in manifest['files'] if hashlib.sha256(
    (original/entry['path']).read_bytes()).hexdigest() != entry['source_sha256']]
assert not changed, changed
css_pairs = [(ROOT/'frontend/src/ui/app.css', original/'web/app.css'),
             (ROOT/'frontend/src/surface/surface.css', original/'web/surface_document.css')]
for kind in ['widgets', 'effects']:
    css_pairs.extend((path, original/path.relative_to(ROOT)) for path in (ROOT/'extensions'/kind).glob('*/styles.css'))
assert all(a.read_bytes() == b.read_bytes() for a,b in css_pairs)
qa = ROOT/'docs/CP4_QA'
qa.mkdir(exist_ok=True)
pixels = []
ascii_saved = False

def visit(suite):
    global ascii_saved
    for spec in suite.get('specs', []):
        for test in spec['tests']:
            assert test['status'] == 'expected'
            for result in test['results']:
                for attachment in result.get('attachments', []):
                    if 'pixel' not in attachment['name'] and 'ascii' not in attachment['name'].lower():
                        continue
                    data = base64.b64decode(attachment['body']) if 'body' in attachment else Path(attachment['path']).read_bytes()
                    if 'pixel' in attachment['name']:
                        pixels.append({'test':spec['title'], 'name':attachment['name'], **json.loads(data)})
                    else:
                        (qa/'ascii-map.txt').write_bytes(data)
                        ascii_saved = True
    for child in suite.get('suites', []):
        visit(child)

for suite in report['suites']:
    visit(suite)
assert ascii_saved
screenshots = list((ROOT/'frontend/test-results').glob('cp4-*/cp4-story.png'))
assert len(screenshots) == 1
shutil.copyfile(screenshots[0], qa/'cp4-story.png')
verification = {
    'date':'2026-10-20',
    'python_executable':r'C:\Users\ASUS\anaconda3\envs\LumiMultiAgent\python.exe',
    'build':{'typescript':'passed','production_vite':'passed','node_processor':'passed','widget_count':8,'effect_count':6},
    'python_tests':{'passed':23,'failed':0,'command':'python -m unittest discover -s tests -v'},
    'browser_tests':report['stats'],
    'pixel_comparisons':pixels,
    'discovery_test':{'ninth_widget_frontend_and_node_processor':'passed','application_import_list_edited':False,'restored_widget_count':8},
    'source_preservation':{'checked_files':len(manifest['files']),'changed_original_files':changed,'css_pairs_equal':len(css_pairs)},
    'scope':'Create only. Provider responses and external task executors mocked; actual parser, adapter, preflight, presenter, scheduler and barrier exercised. No real provider/microphone session.',
    'installations':[],
    'screenshots':['CP4_QA/cp4-story.png'],
    'ascii_map':'CP4_QA/ascii-map.txt',
    'next_checkpoint':'CP5 not started; awaiting user authorization',
}
(ROOT/'docs/CP4_VERIFICATION.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(verification,ensure_ascii=False))
