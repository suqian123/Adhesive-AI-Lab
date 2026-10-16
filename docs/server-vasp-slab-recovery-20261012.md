# Server CeO₂(111) slab validation recovery — 2026-10-12

The shared validation shown at `127.0.0.1:18501` stopped twice after VASP exited
with code 0 but did not meet `EDIFF=1E-6`. The runner correctly rejected both
outputs and withheld production approval.

| Job | Original solver | Failure | Controlled outcome |
| --- | --- | --- | --- |
| `slab-layers/2` | `ALGO=Normal`, `NELM=120` | Electronic self-consistency not achieved | Archived failed outputs; fresh atomic-charge `ALGO=All`, `TIME=0.1`, `NELM=240`, `NCORE=4` passed at 2026-10-12 09:05 UTC |
| `slab-layers/4` | `ALGO=Normal`, `NELM=120` | Electronic self-consistency not achieved with large energy oscillations | Archived failed outputs; same fresh retry passed at about 2026-10-12 12:11 UTC in 61 electronic steps |

Failed CHGCAR/WAVECAR files were moved to timestamped backup directories and
were never used as restart checkpoints. The four-layer backup is
`work/vasp_validation/ceo2-111-baseline-v1/slab-layers/4/.scf-recovery-backups/20261012T115712Z/`.
The recovery changed only `ALGO`, `NELM`, and `TIME` in its INCAR; `EDIFF`,
geometry, pseudopotentials, k-point mesh and other physical settings stayed
the same. The runner used 16 MPI ranks with NUMA binding and `NCORE=4`.

The unstarted `slab-layers/5` input was prepared with the same direct solver,
with its original INCAR copied to `.solver-preparation-20261012/INCAR.before`.
The POSCAR, POTCAR and KPOINTS SHA-256 hashes were checked before and after.
The suite generator now applies the direct solver to new four- and five-layer
CeO₂(111) convergence inputs; other validation points keep their prior solver.

At the latest check, the runner had completed **10/14** jobs with **0 failures**
and was running `slab-layers/5`. `approved=false` remains in force until the
full scientific validation and separate approval finish. The Streamlit service
health endpoint returned `ok`. Local tests for the generator and recovery
helper: **12 passed**.
