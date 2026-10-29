"""Record completed CP3 checks and preserve screenshots outside test-results."""
import base64
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
original = ROOT.parents[1] / 'final_framework'
report = json.loads((ROOT / 'frontend/test-results/report.json').read_text(encoding='utf-8'))
assert report['stats']['expected'] == 10 and report['stats']['unexpected'] == 0
manifest = json.loads((ROOT / 'docs/CP1_COPY_MANIFEST.json').read_text(encoding='utf-8'))
changed = [entry['path'] for entry in manifest['files'] if hashlib.sha256(
    (original / entry['path']).read_bytes()).hexdigest() != entry['source_sha256']]
assert not changed, changed

css_pairs = [(ROOT/'frontend/src/ui/app.css', original/'web/app.css'),
             (ROOT/'frontend/src/surface/surface.css', original/'web/surface_document.css')]
for kind in ['widgets', 'effects']:
    css_pairs.extend((path, original/path.relative_to(ROOT)) for path in (ROOT/'extensions'/kind).glob('*/styles.css'))
assert all(a.read_bytes() == b.read_bytes() for a,b in css_pairs)

pixels = []
def visit(suite):
    for spec in suite.get('specs', []):
        for test in spec['tests']:
            assert test['status'] == 'expected'
            for result in test['results']:
                for attachment in result.get('attachments', []):
                    if 'pixel' not in attachment['name']: continue
                    value = json.loads(base64.b64decode(attachment['body'])) if 'body' in attachment else json.loads(Path(attachment['path']).read_text())
                    pixels.append({'test':spec['title'], 'name':attachment['name'], **value})
    for child in suite.get('suites', []): visit(child)
for suite in report['suites']: visit(suite)

qa = ROOT/'docs/CP3_QA'
qa.mkdir(exist_ok=True)
screenshots = []
for directory in (ROOT/'frontend/test-results').iterdir():
    if not directory.is_dir() or not directory.name.startswith(('cp2-original-', 'cp3-all-six-')): continue
    width = directory.name.rsplit('-', 1)[-1]
    for source in directory.glob('*.png'):
        if source.name.startswith('test-failed'): continue
        dest = qa/f'{source.stem}-{width}.png'
        shutil.copyfile(source,dest)
        screenshots.append(str(dest.relative_to(ROOT/'docs')).replace('\\','/'))

verification = {
    'date':'2026-10-20',
    'python_executable':r'C:\Users\ASUS\anaconda3\envs\LumiMultiAgent\python.exe',
    'build':{'typescript':'passed','production_vite':'passed','widget_count':8,'effect_count':6},
    'python_tests':{'passed':10,'failed':0},
    'browser_tests':report['stats'],
    'audio_scope':'Real AudioPlayback/AudioContext, silent PCM via mocked WebSocket; no provider/microphone',
    'pixel_comparisons':pixels,
    'discovery_test':{'three_file_seventh_effect':'passed','application_import_list_edited':False,'restored_effect_count':6},
    'source_preservation':{'checked_files':len(manifest['files']),'changed_original_files':changed,'css_pairs_equal':len(css_pairs)},
    'installations':[],
    'observations':['One earlier CP2 768px raster check exceeded the unchanged 1/255 threshold: 10 border pixels changed, maximum 2/255 at (699,526). DOM/computed styles matched. Diagnostic rerun recorded separately here; threshold not relaxed.'],
    'screenshots':sorted(screenshots),
    'next_checkpoint':'CP4 not started; awaiting user authorization',
}
(ROOT/'docs/CP3_VERIFICATION.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'original_files_checked':len(manifest['files']),'css_pairs_equal':len(css_pairs),'pixel_comparisons':pixels,'screenshots':len(screenshots)},ensure_ascii=False))
