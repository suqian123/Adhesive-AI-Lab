# CeO₂(111) h35 DFT+U dipole retry — 2026-10-26

Campaign `CL5-00016-1b4ebc3d36`, production task `dft-o-111-v00-h35`, job `vasp-3ea271025d`.

The 2026-10-22 isolated pilot passed its PBE electronic SCF stage but its subsequent DFT+U stage without slab dipole correction exhausted `NELM=240` and failed the final `EDIFF` gate. The pilot's prepared dipole stage had never run. Neither failed output nor the still-running production h35 output is an approved checkpoint or candidate result.

An independent retry was prepared at `/opt/adhesive-ai-lab/work/recovery_checks/CL5-00016-1b4ebc3d36/h35-dftu-dipole-20261026`. It copies the electronically converged PBE `CHGCAR` and `WAVECAR`, the original production `POSCAR`, `POTCAR`, `KPOINTS`, `vdw_kernel.bindat` and input manifest, and the pilot's prepared DFT+U/dipole `INCAR`. SHA-256 checks confirmed all four physical inputs match the original pilot source. Static validation passed all five checks, including the corrected (111) termination and pseudopotential order. The retry has `NSW=0`, `ISTART=1`, `ICHARG=1`, `LDAU=.TRUE.`, `LMAXMIX=6`, `LDIPOL=.TRUE.`, `IDIPOL=3`, and an explicit `DIPOL` center. It uses 16 MPI ranks through the shared 48-rank budget.

Server script: `/opt/adhesive-ai-lab/scripts/retry_ceo2_111_h35_dipole_20261026.py`. The script only marks the isolated retry `ready-for-reviewed-production-restart` after a zero process return code **and** a final electronic convergence check. On failure, it marks the retry failed. It cannot modify the campaign, stop production h35, or write candidate data.

At 2026-10-26 06:07 UTC, the retry runner (PID 800995) was active. VASP had read the PBE `WAVECAR` and charge density and entered the main electronic loop. The MPI ledger held 48/48 ranks: 16 each for h00, h35, and the retry. Both production tasks remained running, and the web endpoint returned HTTP 200. Final electronic convergence has not yet been established.

Before any production restart, verify `retry.json` reports `ready-for-reviewed-production-restart`, independently check final `OUTCAR`/`OSZICAR` convergence and checkpoint files, archive the old h35 output, then use a task-specific controlled restart. Keep h00 separate. Do not use the failed DFT+U stage or the original h35 outputs for candidate writeback.
