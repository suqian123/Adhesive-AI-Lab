# Server compute recovery, 2026-10-27

Campaign: `CL5-00016-1b4ebc3d36`. All timestamps below are UTC unless specified.

## Findings

At 2026-10-26 09:50:18, the server journal recorded `Power key pressed` and `Powering Off`. The CeO2(111) h00 and h35 production VASP runners and the earlier isolated h35 dipole retry stopped at that time. The server was subsequently rebooted twice. The production job records were `failed`, while the campaign `run.json` still showed both tasks as `running`. Neither production directory has a nonempty `CHGCAR` or `WAVECAR`, and neither has a normally completed, electronically converged final `OUTCAR`.

The CeO2(100) shared validation restarted on 2026-10-27 but exhausted `NELM=160` at `encut/450`; it is now marked failed with no approval. It was not restarted in this intervention.

## Controlled actions

- Verified h00's last saved `CONTCAR` is parseable, matches the original 145-atom composition and cell, and has no atom pair closer than 0.90 A. It is used **only as geometry**, not as an electronically converged result. Created an isolated static SCF at `/opt/adhesive-ai-lab/work/recovery_checks/CL5-00016-1b4ebc3d36/h00-contcar-scf-20261027`, using original potentials, k-points, dispersion kernel and a fresh atomic charge. The slab dipole center is explicit.
- Verified the 2026-10-22 h35 PBE stage remains electronically converged; its nonempty `CHGCAR` and `WAVECAR` were copied into a new isolated DFT+U/dipole retry at `/opt/adhesive-ai-lab/work/recovery_checks/CL5-00016-1b4ebc3d36/h35-dftu-dipole-20261027`. The interrupted 2026-10-26 retry was not reused as a checkpoint.
- Both prepared input sets passed static structural/resource checks, including corrected (111) termination and potential order. The physical inputs were hash-checked.
- Backed up the campaign state and two job records to `/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.production-scf-recovery-backups/poweroff-reconcile-20261027T095441Z`. Updated the two stale production task states to `failed` and held the remaining 12 pending tasks so status reconciliation cannot dispatch unrelated production jobs.
- Started both isolated diagnostics using 16 MPI ranks each, under the shared 48-rank budget. Neither script can advance the campaign, approve production, or write candidate data. A zero VASP return code alone is insufficient: each diagnostic requires a final electronic convergence check before marking itself `ready-for-reviewed-production-restart`.

## Verification

At 2026-10-27 09:57 UTC, the h00 diagnostic runner (PID 36961) had entered VASP's main loop, and the h35 diagnostic runner (PID 37142) had read its PBE `WAVECAR` and charge density. The MPI ledger showed 32/48 ranks allocated, with 32 `vasp_std` processes. Both runners were alive and their outputs were updating. The web endpoint returned HTTP 200. Final convergence had **not** yet been established.

The campaign page now reports the two old production jobs as failed and 24 tasks blocked, including the 12 temporary recovery holds. A new production attempt must be prepared only after the corresponding diagnostic passes and its final outputs/checkpoints are independently reviewed. No interrupted output is approved for candidate writeback.
