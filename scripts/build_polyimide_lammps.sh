#!/usr/bin/env bash
set -euo pipefail

baseline_dir="${1:-/mnt/e/Adhesive-AI-Lab/work/md_baselines/odpa-oda-catechol-pdba-v1}"
output_dir="${2:-${baseline_dir}/polyimide-cell}"
mkdir -p "${output_dir}"

oligomer_sdf="${baseline_dir}/oligomers/polyimide_dp8.sdf"
mol2="${output_dir}/polyimide_dp8.mol2"
frcmod="${output_dir}/polyimide_dp8.frcmod"
pdb="${output_dir}/polyimide_dp8.pdb"

# Gasteiger charges provide a runnable topology-development artifact only.
# Production approval remains gated on RESP charges and property validation.
if [[ ! -s "${mol2}" || ! -s "${frcmod}" || ! -s "${pdb}" ]]; then
    antechamber \
        -i "${oligomer_sdf}" -fi sdf \
        -o "${mol2}" -fo mol2 \
        -at gaff2 -c gas -nc 0 -rn POL -pf y \
        >"${output_dir}/antechamber.stdout.log" \
        2>"${output_dir}/antechamber.stderr.log"
    parmchk2 \
        -i "${mol2}" -f mol2 \
        -o "${frcmod}" -s gaff2 \
        >"${output_dir}/parmchk2.stdout.log" \
        2>"${output_dir}/parmchk2.stderr.log"
    antechamber -i "${mol2}" -fi mol2 -o "${pdb}" -fo pdb -pf y
fi

cat >"${output_dir}/packmol.inp" <<EOF
tolerance 2.2
filetype pdb
output ${output_dir}/packed.pdb
seed 20260901

structure ${pdb}
  number 6
  inside box 2.0 2.0 2.0 88.0 88.0 88.0
end structure
EOF
if [[ ! -s "${output_dir}/packed.pdb" ]]; then
    packmol <"${output_dir}/packmol.inp" >"${output_dir}/packmol.stdout.log"
fi

cat >"${output_dir}/tleap.in" <<EOF
source leaprc.gaff2
loadamberparams ${frcmod}
POL = loadmol2 ${mol2}
system = loadpdb ${output_dir}/packed.pdb
set system box { 90.0 90.0 90.0 }
check system
saveamberparm system ${output_dir}/system.prmtop ${output_dir}/system.inpcrd
savepdb system ${output_dir}/system.pdb
quit
EOF
if [[ ! -s "${output_dir}/system.prmtop" || ! -s "${output_dir}/system.inpcrd" ]]; then
    tleap -f "${output_dir}/tleap.in" >"${output_dir}/tleap.stdout.log"
fi

python /mnt/e/Adhesive-AI-Lab/scripts/amber_to_lammps.py \
    "${output_dir}/system.prmtop" "${output_dir}/system.inpcrd" "${output_dir}/system.data"

cat >"${output_dir}/forcefield.production" <<'EOF'
pair_style lj/cut/coul/long 12.0
pair_modify mix arithmetic
bond_style harmonic
angle_style harmonic
dihedral_style fourier
improper_style cvff
special_bonds amber
kspace_style pppm 1.0e-4
EOF

cat >"${output_dir}/static_validation.in" <<'EOF'
clear
units real
atom_style full
boundary p p p
include forcefield.production
read_data system.data
thermo 1
thermo_style custom step atoms temp pe ke etotal vol press
run 0
EOF

cat >"${output_dir}/generation_status.json" <<'EOF'
{
  "model": "ODPA-ODA/DABA-dopamine DP8, six-chain amorphous starting cell",
  "organic_force_field": "GAFF2",
  "charge_model": "Gasteiger topology-development fallback",
  "scientific_status": "generated; RESP-and-property-validation-pending",
  "production_approved": false
}
EOF
