#!/usr/bin/env bash
set -eu
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ranks="${ADHESIVE_MPI_RANKS:-4}"
if [[ ! "$ranks" =~ ^[1-9][0-9]*$ ]]; then
    echo "ADHESIVE_MPI_RANKS must be a positive integer" >&2
    exit 2
fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_STACKSIZE=512m
exec /usr/bin/mpirun --bind-to none -np "$ranks" "$root/engines/vasp-6.4.2/bin/vasp_std" "$@"
