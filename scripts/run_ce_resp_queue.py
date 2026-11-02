"""Run RESP reference calculations as each immutable QM geometry becomes ready."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1'
OUTPUT = BASE/'resp_references'
OUTPUT.mkdir(exist_ok=True)
NAMES = ('BADCy','DCDDS','triazine_reference')


def status(state, **kwargs):
    path = OUTPUT/'workflow_state.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'state':state,'updated_at':datetime.now(timezone.utc).isoformat(),
                                    'production_approved':False,**kwargs}, indent=2))
    os.replace(temporary, path)


try:
    status('adapter-validation-running')
    subprocess.run([sys.executable,str(ROOT/'scripts/run_ce_resp.py'),'--smoke'], check=True)
    deadline = time.monotonic()+72*3600
    while time.monotonic() < deadline:
        pending = []
        for name in NAMES:
            existing = OUTPUT/name/'state.json'
            if existing.exists():
                result = json.loads(existing.read_text())
                if result['state'] != 'resp-fit-complete-review-required':
                    raise RuntimeError(f'{name}: existing RESP state needs review')
                for relative, expected in result['input_hashes'].items():
                    import hashlib
                    if hashlib.sha256((BASE/relative).read_bytes()).hexdigest() != expected:
                        raise RuntimeError(f'{name}: completed RESP input changed')
                continue
            geometry = BASE/'qm_references'/name/'state.json'
            if geometry.exists():
                current = json.loads(geometry.read_text())['state']
                if current == 'failed':
                    raise RuntimeError(f'{name}: upstream geometry failed')
                if current == 'qm-geometry-converged':
                    status('resp-running', molecule=name)
                    subprocess.run([sys.executable,str(ROOT/'scripts/run_ce_resp.py'),'--name',name], check=True)
                    continue
            pending.append(name)
        if not pending:
            status('molecular-resp-complete-review-required')
            break
        status('waiting-for-geometries', molecules=pending)
        time.sleep(60)
    else:
        raise TimeoutError('RESP queue exceeded 72 hours')
except Exception as exc:
    status('failed', error=str(exc))
    raise
