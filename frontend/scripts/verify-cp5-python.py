"""Run regression with UTF-8 evidence and preserve the actual process exit code."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
    cwd=root, env={**os.environ, 'PYTHONIOENCODING':'utf-8'}, stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT, encoding='utf-8')
(root/'docs/cp5-python-tests.log').write_text(result.stdout, encoding='utf-8')
count = re.search(r'Ran (\d+) tests', result.stdout)
summary = {'executable':sys.executable,'exit_code':result.returncode,
    'tests':int(count[1]) if count else None,'passed':result.returncode==0}
(root/'docs/CP5_PYTHON_RESULTS.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(json.dumps(summary))
raise SystemExit(result.returncode)
