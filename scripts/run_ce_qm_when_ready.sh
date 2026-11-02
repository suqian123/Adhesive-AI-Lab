#!/usr/bin/env bash
set -euo pipefail
cd /opt/adhesive-ai-lab
prefix=/opt/adhesive-ai-lab/engines/md-parameterization-20261029/env
root=work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/qm_references
mkdir -p "$root"
state() {
    .venv/bin/python -B - "$root/workflow_state.json" "$1" <<'PY'
import json,os,sys
from datetime import datetime,timezone
from pathlib import Path
p=Path(sys.argv[1]); t=p.with_suffix('.tmp')
t.write_text(json.dumps({'state':sys.argv[2],'updated_at':datetime.now(timezone.utc).isoformat(),'production_approved':False}))
os.replace(t,p)
PY
}
trap 'state failed; exit 1' ERR
state waiting-for-toolchain
ready=0
for attempt in $(seq 1 480); do
    if [ -f engines/md-bootstrap-20261029/environment-explicit.txt ] && [ -x "$prefix/bin/resp" ] && [ -x "$prefix/bin/packmol" ]; then
        "$prefix/bin/python" -c 'import pyscf,geometric'
        ready=1
        break
    fi
    active=$(systemctl show adhesive-md-toolchain-20261029 -p ActiveState --value || true)
    if [ "$active" = failed ]; then
        state toolchain-failed
        exit 1
    fi
    sleep 15
done
if [ "$ready" -ne 1 ]; then state toolchain-timeout; exit 1; fi
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
state quantum-running
"$prefix/bin/python" -u scripts/run_ce_qm_references.py > "$root/runner.stdout.log" 2> "$root/runner.stderr.log"
state quantum-references-complete
