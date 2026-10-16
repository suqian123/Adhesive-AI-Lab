#!/usr/bin/env bash
set -eu
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$root/.venv/bin/python" "$root/deploy/compute/mpi_launch.py" --engine vasp -- "$@"
