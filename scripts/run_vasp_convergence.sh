#!/usr/bin/env bash
set -eu
set -o pipefail

script_dir="${ADHESIVE_VASP_SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
export PYTHONPATH="$script_dir/../src${PYTHONPATH:+:$PYTHONPATH}"

root="${ADHESIVE_VASP_VALIDATION_ROOT:-/mnt/e/Adhesive-AI-Lab/work/vasp_validation/ceo2-111-baseline-v1}"
status_file="$root/status.tsv"
lock_dir="$root/.runner.lock"
runner_pid_file="$root/runner.pid"
runner_pgid_file="$root/runner.pgid"
runner_control_file="$root/runner_control.json"
jobs=(
  "encut/450" "encut/520" "encut/600" "encut/650"
  "kpoints/1x1x1" "kpoints/2x2x1" "kpoints/3x3x1"
  "slab-layers/2" "slab-layers/3" "slab-layers/4" "slab-layers/5"
  "vacuum/15A" "vacuum/18A" "vacuum/22A"
)

# Repeated page refreshes must never resubmit a failed SCF automatically.
for relative in "${jobs[@]}"; do
  if [[ -f "$root/$relative/run_status.json" ]] \
      && grep -Eq '"status"[[:space:]]*:[[:space:]]*"failed"' "$root/$relative/run_status.json"; then
    recovery_marker="$root/$relative/scf_recovery.json"
    if [[ ! -f "$recovery_marker" ]] \
        || ! grep -Eq '"state"[[:space:]]*:[[:space:]]*"(prepared-fresh-atomic|prepared-model-preconvergence)"' "$recovery_marker"; then
      echo "Review/archive the failed task before restarting: $relative" >&2
      exit 1
    fi
  fi
done

if ! mkdir "$lock_dir" 2>/dev/null; then
  stale_pid=""
  if [[ -f "$runner_pid_file" ]]; then
    stale_pid="$(tr -dc '0-9' < "$runner_pid_file")"
  fi
  if [[ -n "$stale_pid" ]] && kill -0 "$stale_pid" 2>/dev/null; then
    echo "A convergence runner is already active: $lock_dir" >&2
    exit 2
  fi
  rmdir "$lock_dir" 2>/dev/null || {
    echo "Unable to clear stale convergence runner lock: $lock_dir" >&2
    exit 2
  }
  mkdir "$lock_dir" || exit 2
fi

runner_exit_state="finished"
cleanup_runner() {
  local result=$?
  if [[ $result -ne 0 ]] && [[ "$runner_exit_state" == "finished" ]]; then
    runner_exit_state="failed"
  fi
  rm -f "$runner_pid_file" "$runner_pgid_file" "$runner_control_file"
  rmdir "$lock_dir" 2>/dev/null || true
  printf '{"state":"%s","updated_at":"%s"}\n' \
    "$runner_exit_state" "$(date --iso-8601=seconds)" > "$runner_control_file"
}

printf '%s\n' "$$" > "$runner_pid_file"
ps -o pgid= -p "$$" | tr -d '[:space:]' > "$runner_pgid_file"
runner_pgid="$(cat "$runner_pgid_file")"
printf '{"state":"running","pid":%s,"pgid":%s,"updated_at":"%s"}\n' \
  "$$" "$runner_pgid" "$(date --iso-8601=seconds)" > "$runner_control_file"
trap cleanup_runner EXIT
trap 'runner_exit_state="cancelled"; exit 130' INT
trap 'runner_exit_state="cancelled"; exit 143' TERM

if [[ ! -f "$status_file" ]]; then
  printf 'timestamp\tjob\tstatus\texit_code\n' > "$status_file"
fi

enable_charge_restart() {
  python3 "$script_dir/../src/adhesive_ai/vasp_checkpoint.py" --enable-charge-restart "$1"
}

stage_converged() {
  local stage="$1"
  [[ -s "$stage/CHGCAR" ]] && [[ -s "$stage/WAVECAR" ]] \
    && grep -q 'General timing and accounting' "$stage/OUTCAR" \
    && grep -q 'aborting loop because EDIFF is reached' "$stage/OUTCAR"
}

job_converged() {
  python3 "$script_dir/../src/adhesive_ai/vasp_checkpoint.py" "$1"
}

stage_complete() {
  local stage="$1"
  [[ -f "$stage/run_status.json" ]] \
    && grep -Eq '"complete"[[:space:]]*:[[:space:]]*true' "$stage/run_status.json" \
    && stage_converged "$stage"
}

reset_stage_outputs() {
  local stage="$1"
  rm -f "$stage"/{CHG,CHGCAR,CONTCAR,DOSCAR,EIGENVAL,IBZKPT,OSZICAR,OUTCAR,PCDAT,PROCAR,REPORT,WAVECAR,XDATCAR,vasprun.xml,stage.stdout.log,stage.stderr.log}
}

preconverge_charge() {
  local directory="$1"
  local pre="$directory/.preconverge"
  mkdir -p "$pre"
  cp "$directory/POSCAR" "$directory/KPOINTS" "$directory/POTCAR" "$pre/"
  awk '
    /^MAGMOM[[:space:]]*=/ { next }
    /^NELMDL[[:space:]]*=/ { next }
    /^AMIX[[:space:]]*=/ { next }
    /^BMIX[[:space:]]*=/ { next }
    /^AMIX_MAG[[:space:]]*=/ { next }
    /^BMIX_MAG[[:space:]]*=/ { next }
    /^LDIPOL[[:space:]]*=/ { next }
    /^IDIPOL[[:space:]]*=/ { next }
    /^LDAU/ { next }
    /^LMAXMIX[[:space:]]*=/ { next }
    /^LORBIT[[:space:]]*=/ { next }
    /^ICHARG[[:space:]]*=/ { next }
    /^EDIFFG[[:space:]]*=/ { next }
    /^EDIFF[[:space:]]*=/ { print "EDIFF = 0.1"; next }
    /^ISPIN[[:space:]]*=/ { print "ISPIN = 1"; next }
    /^ALGO[[:space:]]*=/ { print "ALGO = Fast"; next }
    /^NELM[[:space:]]*=/ { print "NELM = 120"; next }
    { print }
  ' "$directory/INCAR" > "$pre/INCAR"
  cd "$pre" || return 3
  OMP_NUM_THREADS=1 OMP_STACKSIZE=512m mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
    > preconverge.stdout.log 2> preconverge.stderr.log
  local exit_code=$?
  if [[ $exit_code -ne 0 ]] || [[ ! -s CHGCAR ]] || ! job_converged "$pre"; then
    return 1
  fi
  cp CHGCAR "$directory/CHGCAR"
  enable_charge_restart "$directory"
}

preconverge_model() {
  local directory="$1"
  local stage="$directory/.model-preconverge"
  local marker="$stage/run_status.json"
  mkdir -p "$stage"
  if [[ -f "$marker" ]] && grep -Eq '"complete"[[:space:]]*:[[:space:]]*true' "$marker" \
      && [[ -s "$stage/step3-dftu/CHGCAR" ]] && [[ -s "$stage/step3-dftu/WAVECAR" ]] \
      && job_converged "$stage/step3-dftu"; then
    cp "$stage/step3-dftu/CHGCAR" "$stage/step3-dftu/WAVECAR" "$directory/"
    enable_charge_restart "$directory"
    return 0
  fi

  local fixed="$stage/step1-fixed-charge"
  local pbe="$stage/step2-pbe"
  local dftu="$stage/step3-dftu"
  mkdir -p "$fixed" "$pbe" "$dftu"

  # VASP's recommended magnetic DFT+U sequence starts with fixed atomic charge,
  # then converges PBE orbitals with ALGO=All before adding the U correction.
  printf '{"status":"running","complete":false}\n' > "$marker"
  if [[ ! -s "$directory/CHGCAR" ]] && ! stage_complete "$fixed"; then
    reset_stage_outputs "$fixed"
    cp "$directory/POSCAR" "$directory/KPOINTS" "$directory/POTCAR" "$fixed/"
    awk '
      /^LDAU/ { next }
      /^LMAXMIX[[:space:]]*=/ { next }
      /^LDIPOL[[:space:]]*=/ { next }
      /^IDIPOL[[:space:]]*=/ { next }
      /^ICHARG[[:space:]]*=/ { next }
      /^ISTART[[:space:]]*=/ { next }
      /^TIME[[:space:]]*=/ { next }
      /^EDIFFG[[:space:]]*=/ { next }
      /^EDIFF[[:space:]]*=/ { print "EDIFF = 1E-3"; next }
      /^ENCUT[[:space:]]*=/ { print "ENCUT = 400"; next }
      /^PREC[[:space:]]*=/ { print "PREC = Normal"; next }
      /^NELM[[:space:]]*=/ { print "NELM = 120"; next }
      /^NELMDL[[:space:]]*=/ { next }
      /^ALGO[[:space:]]*=/ { print "ALGO = Normal"; next }
      /^LORBIT[[:space:]]*=/ { next }
      /^LWAVE[[:space:]]*=/ { print "LWAVE = .TRUE."; next }
      /^LCHARG[[:space:]]*=/ { print "LCHARG = .TRUE."; next }
      { print }
      END { print "ICHARG = 12" }
    ' "$directory/INCAR" > "$fixed/INCAR"
    printf '{"status":"running","complete":false}\n' > "$fixed/run_status.json"
    cd "$fixed" || return 3
    OMP_NUM_THREADS=1 OMP_STACKSIZE=512m mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
      > stage.stdout.log 2> stage.stderr.log
    local exit_code=$?
    if [[ $exit_code -ne 0 ]] || ! stage_converged "$fixed"; then
      printf '{"status":"failed","exit_code":%d,"complete":false}\n' "$exit_code" > run_status.json
      printf '{"status":"failed","exit_code":%d,"complete":false}\n' "$exit_code" > "$marker"
      return 1
    fi
    printf '{"status":"completed","exit_code":0,"complete":true}\n' > run_status.json
  fi

  if ! stage_complete "$pbe"; then
    reset_stage_outputs "$pbe"
    # A clean three-stage recovery deliberately removes the top-level
    # CHGCAR/WAVECAR.  In that case PBE must start from the verified
    # fixed-charge stage, rather than silently creating an empty CHGCAR.
    local pbe_charge="$directory/CHGCAR"
    if [[ ! -s "$pbe_charge" ]]; then
      pbe_charge="$fixed/CHGCAR"
    fi
    if [[ ! -s "$pbe_charge" ]]; then
      echo "Missing converged charge density for PBE bridge: $directory" >&2
      return 1
    fi
    cp "$directory/POSCAR" "$directory/KPOINTS" "$directory/POTCAR" "$pbe_charge" "$pbe/"
    awk '
    /^LDAU/ { next }
    /^LMAXMIX[[:space:]]*=/ { next }
    /^MAGMOM[[:space:]]*=/ { next }
    /^AMIX_MAG[[:space:]]*=/ { next }
    /^BMIX_MAG[[:space:]]*=/ { next }
    /^LDIPOL[[:space:]]*=/ { next }
    /^IDIPOL[[:space:]]*=/ { next }
    /^ICHARG[[:space:]]*=/ { next }
    /^ISTART[[:space:]]*=/ { next }
    /^TIME[[:space:]]*=/ { next }
    /^EDIFFG[[:space:]]*=/ { next }
    /^EDIFF[[:space:]]*=/ { print "EDIFF = 1E-3"; next }
    /^NELMDL[[:space:]]*=/ { next }
    /^AMIX[[:space:]]*=/ { print "AMIX = 0.05"; next }
    /^BMIX[[:space:]]*=/ { print "BMIX = 0.0001"; next }
    /^AMIN[[:space:]]*=/ { next }
    /^MAXMIX[[:space:]]*=/ { next }
    /^NELM[[:space:]]*=/ { print "NELM = 180"; next }
    /^ISPIN[[:space:]]*=/ { print "ISPIN = 1"; next }
    /^ALGO[[:space:]]*=/ { print "ALGO = Normal"; next }
    /^LWAVE[[:space:]]*=/ { print "LWAVE = .TRUE."; next }
    /^LCHARG[[:space:]]*=/ { print "LCHARG = .TRUE."; next }
    { print }
    END { print "ISTART = 0"; print "ICHARG = 1"; print "NELM = 180"; print "AMIN = 0.01"; print "MAXMIX = 80" }
    ' "$directory/INCAR" > "$pbe/INCAR"
    cd "$pbe" || return 3
    exit_code=1
    for attempt in 1 2 3; do
      printf '{"status":"running","attempt":%d,"complete":false}\n' "$attempt" > run_status.json
      OMP_NUM_THREADS=1 OMP_STACKSIZE=512m mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
        > stage.stdout.log 2> stage.stderr.log
      exit_code=$?
      if stage_converged "$pbe"; then
        break
      fi
      if [[ $exit_code -ne 0 ]] || [[ ! -s CHGCAR ]] || [[ ! -s WAVECAR ]]; then
        break
      fi
    done
    if [[ $exit_code -ne 0 ]] || ! stage_converged "$pbe"; then
      printf '{"status":"failed","exit_code":%d,"complete":false}\n' "$exit_code" > run_status.json
      printf '{"status":"failed","exit_code":%d,"complete":false}\n' "$exit_code" > "$marker"
      return 1
    fi
    printf '{"status":"completed","exit_code":0,"complete":true}\n' > run_status.json
  fi

  if ! stage_complete "$dftu"; then
    if [[ ! -s "$dftu/CHGCAR" ]] || [[ ! -s "$dftu/WAVECAR" ]]; then
      reset_stage_outputs "$dftu"
      cp "$pbe/POSCAR" "$pbe/KPOINTS" "$pbe/POTCAR" "$pbe/CHGCAR" "$pbe/WAVECAR" "$dftu/"
    fi
    awk '
    /^LDIPOL[[:space:]]*=/ { next }
    /^IDIPOL[[:space:]]*=/ { next }
    /^ICHARG[[:space:]]*=/ { next }
    /^ISTART[[:space:]]*=/ { next }
    /^TIME[[:space:]]*=/ { next }
    /^EDIFFG[[:space:]]*=/ { next }
    /^EDIFF[[:space:]]*=/ { print "EDIFF = 1E-4"; next }
    /^NELMDL[[:space:]]*=/ { next }
    /^ALGO[[:space:]]*=/ { print "ALGO = All"; next }
    /^LWAVE[[:space:]]*=/ { print "LWAVE = .TRUE."; next }
    /^LCHARG[[:space:]]*=/ { print "LCHARG = .TRUE."; next }
    { print }
    END { print "ISTART = 1"; print "ICHARG = 1"; print "TIME = 0.05" }
    ' "$directory/INCAR" > "$dftu/INCAR"
    cd "$dftu" || return 3
    exit_code=1
    for attempt in 1 2 3; do
      printf '{"status":"running","attempt":%d,"complete":false}\n' "$attempt" > run_status.json
      OMP_NUM_THREADS=1 OMP_STACKSIZE=512m mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
        > stage.stdout.log 2> stage.stderr.log
      exit_code=$?
      if stage_converged "$dftu"; then
        break
      fi
      if [[ $exit_code -ne 0 ]] || [[ ! -s CHGCAR ]] || [[ ! -s WAVECAR ]]; then
        break
      fi
    done
    if [[ $exit_code -ne 0 ]] || ! stage_converged "$dftu"; then
      printf '{"status":"failed","exit_code":%d,"complete":false}\n' "$exit_code" > run_status.json
      printf '{"status":"failed","exit_code":%d,"complete":false}\n' "$exit_code" > "$marker"
      return 1
    fi
    printf '{"status":"completed","exit_code":0,"complete":true}\n' > run_status.json
  fi
  printf '{"status":"completed","exit_code":0,"complete":true}\n' > "$marker"
  cp "$dftu/CHGCAR" "$dftu/WAVECAR" "$directory/"
  enable_charge_restart "$directory"
}

for relative in "${jobs[@]}"; do
  directory="$root/$relative"
  marker="$directory/run_status.json"
  if [[ -f "$marker" ]] && grep -Eq '"complete"[[:space:]]*:[[:space:]]*true' "$marker" && job_converged "$directory"; then
    printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "skipped-complete" "0" >> "$status_file"
    continue
  fi

  # Reuse a converged charge density only when atom ordering, real-space grid
  # and cutoff are identical. A different ENCUT changes the FFT grid, so its
  # CHGCAR/WAVECAR must never seed another ENCUT point.
  seed=""
  reuse_converged_dftu=false
  charge_source="reused-converged-dftu-charge"
  recovery_marker="$directory/scf_recovery.json"
  case "$relative" in
    "kpoints/1x1x1") seed="encut/520" ;;
    "kpoints/2x2x1") seed="kpoints/1x1x1" ;;
    "kpoints/3x3x1") seed="kpoints/2x2x1" ;;
  esac
  # A prepared SCF recovery must retain the stalled job's own charge density.
  # In particular, do not overwrite it with the lower k-point seed.
  if [[ -s "$recovery_marker" ]] \
      && grep -Eq '"state"[[:space:]]*:[[:space:]]*"prepared"' "$recovery_marker"; then
    if [[ ! -s "$directory/CHGCAR" ]] || ! job_converged "$directory"; then
      printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "recovery-checkpoint-missing" "1" >> "$status_file"
      exit 1
    fi
    enable_charge_restart "$directory"
    reuse_converged_dftu=true
    printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "reusing-stalled-scf-checkpoint" "0" >> "$status_file"
  elif [[ -s "$recovery_marker" ]] \
      && grep -Eq '"state"[[:space:]]*:[[:space:]]*"prepared-fresh-atomic"' "$recovery_marker"; then
    if [[ -s "$directory/CHGCAR" ]] || [[ -s "$directory/WAVECAR" ]]; then
      echo "Fresh recovery must not retain CHGCAR or WAVECAR: $directory" >&2
      exit 1
    fi
    reuse_converged_dftu=true
    charge_source="fresh-atomic-charge"
  elif [[ -s "$recovery_marker" ]] \
      && grep -Eq '"state"[[:space:]]*:[[:space:]]*"prepared-model-preconvergence"' "$recovery_marker"; then
    # A second recovery is deliberately clean: the driver archived every
    # unverified output, so regenerate the DFT+U seed through the fixed-charge,
    # PBE, and DFT+U bridge stages before the final calculation.
    if [[ -s "$directory/CHGCAR" ]] || [[ -s "$directory/WAVECAR" ]]; then
      echo "Second recovery must not retain CHGCAR or WAVECAR: $directory" >&2
      exit 1
    fi
    reuse_converged_dftu=false
    charge_source="forced-three-stage-preconvergence"
  elif [[ -n "$seed" ]] && [[ -s "$root/$seed/CHGCAR" ]] \
      && job_converged "$root/$seed" \
      && cmp -s "$root/$seed/POSCAR" "$directory/POSCAR" \
      && cmp -s "$root/$seed/POTCAR" "$directory/POTCAR"; then
    cp "$root/$seed/CHGCAR" "$directory/CHGCAR"
    rm -f "$directory/WAVECAR"
    if [[ -s "$root/$seed/WAVECAR" ]] \
        && cmp -s "$root/$seed/POSCAR" "$directory/POSCAR" \
        && cmp -s "$root/$seed/KPOINTS" "$directory/KPOINTS" \
        && cmp -s "$root/$seed/POTCAR" "$directory/POTCAR"; then
      cp "$root/$seed/WAVECAR" "$directory/WAVECAR"
    fi
    enable_charge_restart "$directory"
    reuse_converged_dftu=true
  elif [[ "$relative" == "encut/450" ]] \
      && [[ -s "$root/preconverge/base/CHGCAR" ]] \
      && job_converged "$root/preconverge/base" \
      && cmp -s "$root/preconverge/base/POSCAR" "$directory/POSCAR" \
      && cmp -s "$root/preconverge/base/KPOINTS" "$directory/KPOINTS" \
      && cmp -s "$root/preconverge/base/POTCAR" "$directory/POTCAR"; then
    cp "$root/preconverge/base/CHGCAR" "$directory/CHGCAR"
    enable_charge_restart "$directory"
  elif [[ -s "$directory/CHGCAR" ]] && job_converged "$directory"; then
    enable_charge_restart "$directory"
  elif [[ -f "$root/clean_baseline.json" ]]; then
    # A freshly generated, reviewed structure uses atomic charge initially.
    # Do not load any surviving charge/wavefunction from an unsuccessful run.
    if [[ -s "$directory/CHGCAR" ]] || [[ -s "$directory/WAVECAR" ]]; then
      echo "Unverified checkpoint present; archive before restarting: $directory" >&2
      exit 1
    fi
    reuse_converged_dftu=true
    charge_source="fresh-atomic-charge"
  else
    preconverge_charge "$directory" || {
      printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "preconvergence-failed" "1" >> "$status_file"
      exit 1
    }
  fi

  if [[ "$reuse_converged_dftu" != true ]]; then
    preconverge_model "$directory" || {
      printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "model-preconvergence-failed" "1" >> "$status_file"
      exit 1
    }
  else
    printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "$charge_source" "0" >> "$status_file"
  fi

  printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "running" "" >> "$status_file"
  printf '{"job":"%s","status":"running","started_at":"%s","complete":false}\n' \
    "$relative" "$(date --iso-8601=seconds)" > "$marker"
  cd "$directory" || exit 3
  exit_code=0
  OMP_NUM_THREADS=1 OMP_STACKSIZE=512m /usr/bin/time -v \
    mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
    > vasp.stdout.log 2> vasp.stderr.log || exit_code=$?
  complete=false
  status="failed"
  if [[ $exit_code -eq 0 ]] && job_converged "$directory"; then
    complete=true
    status="completed"
  fi
  printf '{"job":"%s","status":"%s","exit_code":%d,"finished_at":"%s","complete":%s}\n' \
    "$relative" "$status" "$exit_code" "$(date --iso-8601=seconds)" "$complete" > "$marker"
  printf '%s\t%s\t%s\t%d\n' "$(date --iso-8601=seconds)" "$relative" "$status" "$exit_code" >> "$status_file"
  if [[ "$complete" != true ]]; then
    runner_exit_state="failed"
    exit 1
  fi
done

analysis_exit=0
python3 "$script_dir/analyze_vasp_convergence.py" \
  --plan "$root/validation_plan.json" \
  --report "$root/convergence_report.json" \
  --approval "$root/approved.json" || analysis_exit=$?
if [[ $analysis_exit -eq 0 ]]; then
  printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "convergence-matrix" "approved" "0" >> "$status_file"
else
  printf '%s\t%s\t%s\t%d\n' "$(date --iso-8601=seconds)" "convergence-matrix" "not-approved" "$analysis_exit" >> "$status_file"
fi
exit "$analysis_exit"
