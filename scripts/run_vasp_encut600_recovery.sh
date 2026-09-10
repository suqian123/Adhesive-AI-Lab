#!/usr/bin/env bash
set -eu
set -o pipefail

script_dir="\${ADHESIVE_VASP_SCRIPT_DIR:-$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)}"
export PYTHONPATH="$script_dir/../src\${PYTHONPATH:+:$PYTHONPATH}"
root="\${ADHESIVE_VASP_VALIDATION_ROOT:-/mnt/e/Adhesive-AI-Lab/work/vasp_validation/ceo2-111-baseline-v1}"
job="$root/encut/600"
marker="$job/run_status.json"
recovery="$job/scf_recovery.json"
lock="$job/.encut600-recovery.lock"

if [[ -d "$root/.runner.lock" ]]; then
  echo "A convergence runner is already active: $root/.runner.lock" >&2
  exit 2
fi
if [[ ! -f "$recovery" ]] || ! grep -Eq '"state"[[:space:]]*:[[:space:]]*"prepared-fresh-atomic"' "$recovery"; then
  echo "ENCUT=600 fresh recovery has not been prepared" >&2
  exit 2
fi
if [[ -s "$job/CHGCAR" ]] || [[ -s "$job/WAVECAR" ]]; then
  echo "Fresh ENCUT=600 recovery must not retain CHGCAR or WAVECAR" >&2
  exit 2
fi
if ! mkdir "$lock" 2>/dev/null; then
  echo "ENCUT=600 recovery is already active" >&2
  exit 2
fi
cleanup() { rmdir "$lock" 2>/dev/null || true; }
trap cleanup EXIT

printf '{"job":"encut/600","status":"running","started_at":"%s","complete":false}\n' \
  "$(date --iso-8601=seconds)" > "$marker"
cd "$job"
exit_code=0
OMP_NUM_THREADS=1 OMP_STACKSIZE=512m /usr/bin/time -v \
  mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
  > vasp.stdout.log 2> vasp.stderr.log || exit_code=$?

complete=false
status="failed"
if [[ $exit_code -eq 0 ]] && python3 "$script_dir/../src/adhesive_ai/vasp_checkpoint.py" "$job"; then
  complete=true
  status="completed"
fi
printf '{"job":"encut/600","status":"%s","exit_code":%d,"finished_at":"%s","complete":%s}\n' \
  "$status" "$exit_code" "$(date --iso-8601=seconds)" "$complete" > "$marker"
printf '%s\t%s\t%s\t%d\n' "$(date --iso-8601=seconds)" "encut/600" "$status" "$exit_code" >> "$root/status.tsv"
[[ "$complete" == true ]]
