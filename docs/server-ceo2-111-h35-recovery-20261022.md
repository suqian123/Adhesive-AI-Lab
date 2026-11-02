# CeO₂(111) h35 production recovery, 2026-10-22

Campaign: `CL5-00016-1b4ebc3d36`; affected task: `dft-o-111-v00-h35`; original job: `vasp-3ea271025d`.

## Finding

At the start of this intervention, the original h35 VASP process was still running after about six days. It had reached 11 ionic steps but none had met the final electronic `EDIFF` threshold. Its output contained large dipole fluctuations; `CHGCAR` and `WAVECAR` were empty. The input enabled slab dipole correction without an explicit `DIPOL` center. The original structure, `POTCAR` and `KPOINTS` passed static validation. These findings do not establish a converged production result or a valid restart checkpoint.

The other running (111) job, `dft-o-111-v00-h00`, remains separate. No production geometry, physical input or candidate data was changed by the diagnostic.

## Actions applied

- Created an isolated four-stage SCF pilot at `/opt/adhesive-ai-lab/work/recovery_checks/CL5-00016-1b4ebc3d36/h35-20261022`. It uses the original physical inputs and cutoff, and tests fixed-charge, PBE, DFT+U, then DFT+U with an explicit slab dipole center. Each next stage requires a normally terminated, electronically converged preceding stage and nonempty `CHGCAR` and `WAVECAR`.
- The initial fixed-charge stage uses reciprocal-space projection; the three not-yet-started stages were set to `LREAL = Auto`, matching the original production input and VASP's large-supercell runtime advice. Their `INCAR` files and the server script were verified before those stages began.
- Launched the pilot with 16 MPI ranks. It is diagnostic only: the script stops at `ready-for-reviewed-production-restart` and cannot automatically mark the original task complete or write candidate results.
- Added VASP convergence checks to `jobs.parse_job_result` and `campaign_runner.advance_campaign_run`. A zero exit code alone no longer authorizes production completion or downstream aggregation. The relevant 37 local tests passed.
- Backed up the server code at `/opt/adhesive-ai-lab/work/server-code-backups/convergence-guard-20261022T075344Z`, deployed the two changed Python modules and restarted only the web service. The campaign supervisor was separately restarted to load the new rule, while both production job runners and the pilot remained alive.

## Latest observed state

At 2026-10-22 08:06 UTC (16:06 China time), the pilot was in `01-fixed-charge` and had printed its second `DAV` iteration. The 48-rank server allocation was full: 16 ranks each for h00, h35 and the pilot. Campaign status was 2 running, 12 pending, 12 blocked, 0 completed. The web endpoint returned HTTP 200 after the supervisor reload.

The original h35 task has **not** been restarted or approved. Wait for all four pilot stages to pass, then inspect final `OUTCAR` and checkpoint hashes before archiving old h35 output and performing a task-specific, controlled restart. If any stage fails, adjust the isolated pilot and repeat; do not seed production from an unconverged stage.

Read-only check: `outputs/server-performance-20261012/admin-actions/check_h35_pilot_20261022.sh` through the SSH admin request helper.
