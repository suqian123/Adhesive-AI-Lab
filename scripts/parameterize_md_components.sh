#!/usr/bin/env bash
set -euo pipefail

baseline_dir="${1:-/mnt/e/Adhesive-AI-Lab/work/md_baselines/odpa-oda-catechol-pdba-v1}"
component_dir="${baseline_dir}/components"
parameter_dir="${baseline_dir}/gaff2-am1bcc"
mkdir -p "${parameter_dir}"

for component in ODPA ODA DABA_DA PDBA DOPAMINE; do
    work_dir="${parameter_dir}/${component}"
    mkdir -p "${work_dir}"
    if ! antechamber \
        -i "${component_dir}/${component}.sdf" -fi sdf \
        -o "${work_dir}/${component}.mol2" -fo mol2 \
        -at gaff2 -c bcc -nc 0 -rn "${component:0:3}" -pf y \
        >"${work_dir}/antechamber.stdout.log" \
        2>"${work_dir}/antechamber.stderr.log"; then
        printf 'parameterization-failed\n' >"${work_dir}/status.txt"
        continue
    fi
    parmchk2 \
        -i "${work_dir}/${component}.mol2" -f mol2 \
        -o "${work_dir}/${component}.frcmod" -s gaff2 \
        >"${work_dir}/parmchk2.stdout.log" \
        2>"${work_dir}/parmchk2.stderr.log"
    printf 'gaff2-am1bcc-generated; validation-pending\n' >"${work_dir}/status.txt"
done
