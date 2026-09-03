#!/usr/bin/env bash
set -u

root="${ADHESIVE_VASP_VALIDATION_ROOT:-/mnt/e/Adhesive-AI-Lab/work/vasp_validation/ceo2-111-baseline-v1}"
status_file="$root/status.tsv"
lock_dir="$root/.runner.lock"
runner_pid_file="$root/runner.pid"
runner_pgid_file="$root/runner.pgid"
runner_control_file="$root/runner_control.json"
jobs=(
  "encut/450" "encut/520" "encut/600"
  "kpoints/1x1x1" "kpoints/2x2x1" "kpoints/3x3x1"
  "slab-layers/2" "slab-layers/3" "slab-layers/4"
  "vacuum/15A" "vacuum/18A" "vacuum/22A"
)

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
  local incar="$1/INCAR"
  if grep -q '^ICHARG[[:space:]]*=' "$incar"; then
    sed -i 's/^ICHARG[[:space:]]*=.*/ICHARG = 1/' "$incar"
  else
    printf '\nICHARG = 1\n' >> "$incar"
  fi
  if grep -q '^NELMDL[[:space:]]*=' "$incar"; then
    sed -i 's/^NELMDL[[:space:]]*=.*/NELMDL = -1/' "$incar"
  else
    printf 'NELMDL = -1\n' >> "$incar"
  fi
  if [[ -s "$1/WAVECAR" ]]; then
    if grep -q '^ISTART[[:space:]]*=' "$incar"; then
      sed -i 's/^ISTART[[:space:]]*=.*/ISTART = 1/' "$incar"
    else
      printf 'ISTART = 1\n' >> "$incar"
    fi
  fi
}

stage_converged() {
  local stage="$1"
  [[ -s "$stage/CHGCAR" ]] && [[ -s "$stage/WAVECAR" ]] \
    && grep -q 'General timing and accounting' "$stage/OUTCAR" \
    && grep -q 'aborting loop because EDIFF is reached' "$stage/OUTCAR"
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
  if [[ $exit_code -ne 0 ]] || [[ ! -s CHGCAR ]] || ! grep -q 'General timing and accounting' OUTCAR; then
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
      && grep -q 'General timing and accounting' "$stage/step3-dftu/OUTCAR"; then
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
    cp "$directory/POSCAR" "$directory/KPOINTS" "$directory/POTCAR" "$directory/CHGCAR" "$pbe/"
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
  if [[ -f "$marker" ]] && grep -Eq '"complete"[[:space:]]*:[[:space:]]*true' "$marker" && grep -q 'General timing and accounting' "$directory/OUTCAR"; then
    printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "skipped-complete" "0" >> "$status_file"
    continue
  fi

  # Reuse a converged charge density only when atom ordering and the real-space
  # cell are identical.  This accelerates cutoff/k-point refinements without
  # carrying charge densities across different slab or vacuum geometries.
  seed=""
  reuse_converged_dftu=false
  case "$relative" in
    "encut/520") seed="encut/450" ;;
    "encut/600") seed="encut/520" ;;
    "kpoints/1x1x1") seed="encut/520" ;;
    "kpoints/2x2x1") seed="kpoints/1x1x1" ;;
    "kpoints/3x3x1") seed="kpoints/2x2x1" ;;
  esac
  if [[ -n "$seed" ]] && [[ -f "$root/$seed/CHGCAR" ]] \
      && grep -Eq '"complete"[[:space:]]*:[[:space:]]*true' "$root/$seed/run_status.json"; then
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
      && grep -q 'General timing and accounting' "$root/preconverge/base/OUTCAR" \
      && cmp -s "$root/preconverge/base/POSCAR" "$directory/POSCAR" \
      && cmp -s "$root/preconverge/base/KPOINTS" "$directory/KPOINTS" \
      && cmp -s "$root/preconverge/base/POTCAR" "$directory/POTCAR"; then
    cp "$root/preconverge/base/CHGCAR" "$directory/CHGCAR"
    enable_charge_restart "$directory"
  elif [[ -s "$directory/CHGCAR" ]]; then
    enable_charge_restart "$directory"
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
    printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "reused-converged-dftu-charge" "0" >> "$status_file"
  fi

  printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "$relative" "running" "" >> "$status_file"
  printf '{"job":"%s","status":"running","started_at":"%s","complete":false}\n' \
    "$relative" "$(date --iso-8601=seconds)" > "$marker"
  cd "$directory" || exit 3
  OMP_NUM_THREADS=1 OMP_STACKSIZE=512m /usr/bin/time -v \
    mpirun --bind-to none -np 4 /usr/local/bin/vasp_std \
    > vasp.stdout.log 2> vasp.stderr.log
  exit_code=$?
  complete=false
  status="failed"
  if [[ $exit_code -eq 0 ]] && grep -q 'General timing and accounting' OUTCAR && grep -q 'free  energy   TOTEN' OUTCAR; then
    complete=true
    status="completed"
  fi
  printf '{"job":"%s","status":"%s","exit_code":%d,"finished_at":"%s","complete":%s}\n' \
    "$relative" "$status" "$exit_code" "$(date --iso-8601=seconds)" "$complete" > "$marker"
  printf '%s\t%s\t%s\t%d\n' "$(date --iso-8601=seconds)" "$relative" "$status" "$exit_code" >> "$status_file"
  if [[ "$complete" != true ]]; then
    exit "$exit_code"
  fi
done

python3 /mnt/e/Adhesive-AI-Lab/scripts/analyze_vasp_convergence.py \
  --plan "$root/validation_plan.json" \
  --report "$root/convergence_report.json" \
  --approval "$root/approved.json"
analysis_exit=$?
if [[ $analysis_exit -eq 0 ]]; then
  printf '%s\t%s\t%s\t%s\n' "$(date --iso-8601=seconds)" "convergence-matrix" "approved" "0" >> "$status_file"
else
  printf '%s\t%s\t%s\t%d\n' "$(date --iso-8601=seconds)" "convergence-matrix" "not-approved" "$analysis_exit" >> "$status_file"
fi
exit "$analysis_exit"
