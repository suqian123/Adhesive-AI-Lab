# Server compute restart, 2026-10-28

Campaign: `CL5-00016-1b4ebc3d36`. Times in this note are UTC.

Before restart, no `vasp_std` processes or MPI leases were active. The campaign was `failed` with two failed CeO2(111) production jobs and 24 blocked tasks. The CeO2(111) h00 isolated static SCF completed normally and passed `electronic_converged`; the h35 DFT+U/dipole diagnostic exhausted 240 electronic iterations without convergence. The CeO2(100) shared validation failed at `encut/450` after 160 iterations and remains unapproved.

Only h00 was eligible for a controlled production restart. `scripts/restart_ceo2_111_h00_20261028.py` copied the saved geometry and the converged pilot's `CHGCAR`/`WAVECAR` to a new campaign attempt directory. It retained the original physical inputs, used `ISTART=1`, `ICHARG=1`, and the pilot's explicit dipole center, and passed structural, potential-order, and resource checks. The previous `run.json` and job record were backed up to `/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.production-scf-recovery-backups/h00-restart-20261028T073307Z`.

At approximately 07:33 UTC, job `vasp-7736f133dc` was submitted from `/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/recovery_attempts/dft-o-111-v00-h00-20261028`. The campaign and task were `running`; 16 VASP ranks held a 16/48 MPI lease. The solver read `WAVECAR` and charge density and entered its main loop with no stderr output. This is a running calculation, not a converged result or candidate writeback approval. The other recovery holds remain in place.

The h35 and CeO2(100) attempts were not restarted with their failed inputs. They require separate electronic-convergence recovery and independent review before production can continue.
