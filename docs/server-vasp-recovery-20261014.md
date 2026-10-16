# 2026-10-14 CeO2 VASP recovery

Campaign `CL5-00016-1b4ebc3d36` had one failed and six running CeO2(111)
production jobs. One failed with `EDDDAV: Call to ZHEGV failed`; three jobs
occupied all 48 MPI ranks for approximately 21 hours with unstable electronic
energies. The CeO2(110) shared validation plan was 0/14 and had no runner.

The one-off recovery script `scripts/recover_ceo2_111_campaign_20261014.py`
archived each task's prior mutable VASP output and the campaign record. It
stopped only the seven pinned old jobs, retained original POSCAR/POTCAR/KPOINTS,
and reset INCAR to `ALGO=All`, `TIME=0.1`, `ISEARCH=1`, `NELM=240`,
`ISTART=0`, `ICHARG=2`, `LWAVE=.TRUE.`. The previous CHGCAR/WAVECAR/CONTCAR
were not reused. It started CeO2(110) validation before resuming the campaign.
The backup is on the server at:

`/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.production-scf-recovery-backups/campaign111-20261014T063050Z`

The rank pool has no priority queue. After the first two validation stages,
production acquired all 48 ranks, leaving validation waiting. The second
one-off script, `scripts/reserve_vasp_validation_ranks_20261014.py`, archived
and deferred four campaign jobs and set that campaign's `max_parallel` to 2.
Its backup is:

`/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.production-scf-recovery-backups/validation-rank-reservation-20261014T063917Z`

At the last check, two production jobs and the CeO2(110) DFT+U validation
stage each held 16 ranks. The validation stage had live VASP output; production
had active VASP processes but had not yet recorded a new electronic step. The
web health endpoint returned HTTP 200. The 0/14 figure counts completed
validation cases; it remains zero until the first case converges and passes.

`vasp_convergence_progress()` now also recognizes the legacy `.preconverge`
charge stage while the runner is there, so that early phase can display as
active. Local and server copies of `src/adhesive_ai/campaign_runner.py` were
synced without restarting `adhesive-ai.service` or its VASP child processes.

The recovery is a restart of computation, not scientific approval. Keep
production results gated by electronic convergence and by the shared facet
validation. The campaign remains limited to two parallel tasks to reserve
16 ranks for validation; reassess that limit after all required facet
validations are complete.
