"""Collect completed CP5 evidence without overwriting prior checkpoints."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
from PIL import Image

root = Path(__file__).resolve().parents[2]
original = root.parents[1]/'final_framework'
report = json.loads((root/'frontend/test-results/report.json').read_text(encoding='utf-8'))
python = json.loads((root/'docs/CP5_PYTHON_RESULTS.json').read_text(encoding='utf-8'))
assert report['stats']['expected']==12 and report['stats']['unexpected']==1
assert python['passed'] and python['tests']==40 and python['exit_code']==0
manifest = json.loads((root/'docs/CP1_COPY_MANIFEST.json').read_text(encoding='utf-8'))
changed = [entry['path'] for entry in manifest['files'] if hashlib.sha256((original/entry['path']).read_bytes()).hexdigest()!=entry['source_sha256']]
assert not changed, changed
css = [(root/'frontend/src/ui/app.css', original/'web/app.css'),
       (root/'frontend/src/surface/surface.css', original/'web/surface_document.css')]
for kind in ['widgets','effects']:
    css.extend((path,original/path.relative_to(root)) for path in (root/'extensions'/kind).glob('*/styles.css'))
assert all(a.read_bytes()==b.read_bytes() for a,b in css)
qa = root/'docs/CP5_QA'
qa.mkdir(exist_ok=True)
pixels = []
attachments = []
failed = []
def visit(suite):
    for spec in suite.get('specs',[]):
        for test in spec['tests']:
            if test['status']!='expected':
                assert spec['title']=='original surface DOM, geometry and pixels 768'
                failed.append(spec['title'])
            for result in test['results']:
                for item in result.get('attachments',[]):
                    name = item['name']
                    if 'pixel' not in name and not name.startswith('cp5-'):
                        continue
                    data = base64.b64decode(item['body']) if 'body' in item else Path(item['path']).read_bytes()
                    if 'pixel' in name:
                        pixels.append({'test':spec['title'],'name':name,**json.loads(data)})
                    else:
                        path = qa/(name+'.oui' if name.endswith('program') else name+'.txt')
                        path.write_bytes(data)
                        attachments.append(path.name)
    for child in suite.get('suites',[]):
        visit(child)
for suite in report['suites']:
    visit(suite)
screenshots = list((root/'frontend/test-results').glob('cp5-*/*.png'))
assert len(screenshots)==2
for path in screenshots:
    shutil.copyfile(path,qa/path.name)
assert 'cp5-accepted-program.oui' in attachments
raster_dir = root/'frontend/test-results/cp2-original-surface-DOM-geometry-and-pixels-768'
for name in ['original.png','react.png']:
    shutil.copyfile(raster_dir/name,qa/('final-raster-'+name.replace('.png','-768.png')))
pair = [Image.open(raster_dir/name).convert('RGB') for name in ['original.png','react.png']]
assert pair[0].size==pair[1].size
changed_pixels = []
max_channel = 0
width = pair[0].width
for i,(a,b) in enumerate(zip(pair[0].get_flattened_data(),pair[1].get_flattened_data())):
    delta = max(abs(x-y) for x,y in zip(a,b))
    if delta:
        changed_pixels.append({'x':i%width,'y':i//width,'max_channel_delta':delta})
        max_channel = max(max_channel,delta)
raster = {'changed_pixels':len(changed_pixels),'max_channel_delta':max_channel,'first_20_pixels':changed_pixels[:20]}
assert len(failed)==1
verification = {'date':'2026-10-20','python':python,'browser':report['stats'],
    'build':{'typescript':'passed','frontend':'passed','node_processor':'passed','widgets':8,'effects':6},
    'source_preservation':{'files_checked':len(manifest['files']),'changed_original_files':changed,'css_pairs_equal':len(css)},
    'pixel_comparisons':pixels,'known_raster_failure':raster,'failed_checks':failed,'screenshots':[path.name for path in screenshots],
    'attachments':sorted(set(attachments)),
    'scope':'Real OpenUI parser/evaluator/merge, Python Assembly/compiler/scheduler/barrier/Live completion, browser interaction/effect. Provider responses and retrieval/media/WebSocket endpoints mocked; no real provider/microphone.',
    'observations':['CP2 768px raster check reached max 2/255 in both full runs (unchanged threshold 1/255). DOM/geometry/styles passed before the pixel assertion. Initial and final evidence preserved in CP5_QA. Initial CP5 assertion event name corrected to panel:interaction; isolated CP5 2/2 and both CP5 tests in final full run passed.'],
    'installations':[], 'next_checkpoint':'CP6 not started; awaiting user authorization'}
(root/'docs/CP5_VERIFICATION.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(verification,ensure_ascii=False))
