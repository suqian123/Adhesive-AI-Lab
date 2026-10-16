# 2026-10-15 CeO2(110) VASP failure and controlled retry

The server page reported `CeO2(110) 0/14`, current `encut/450`, electronic
convergence failure at electronic step 160. Read-only inspection of the server
confirmed `run_status.json` was failed with VASP exit code 0. The final DAV
energy change was about +1.55 eV, far from the retained `EDIFF=1E-6`; the
output cannot be counted as a converged checkpoint. The other 13 validation
points had not run. The web health endpoint was HTTP 200.

The original INCAR used `ALGO=Normal`, `NELM=160`, `LDIPOL=.TRUE.`. Its
independent DFT+U bridge stage had completed and passed the electronic
convergence check. The bridge POSCAR, POTCAR, and KPOINTS matched the final
job byte-for-byte, and its CHGCAR and WAVECAR were nonempty. The failed
top-level outputs were archived on the server at:

`/opt/adhesive-ai-lab/work/vasp_validation/ceo2-110-baseline-v1/encut/450/.scf-recovery-backups/20261015T083802Z`

`scripts/recover_ceo2_110_encut450_20261015.py` performed an exact-target
dry run, then archived mutable failed outputs, checked physical input hashes,
and restarted validation from only the verified bridge stage. The retry uses
`ALGO=All`, `TIME=0.05`, `ISEARCH=1`, `NELM=240`, slow charge mixing, and
`LREAL=.FALSE.` while retaining `ENCUT=450`, `EDIFF=1E-6`, DFT+U, dipole
correction, geometry, pseudopotentials, and k-point mesh. This follows the
[VASP advice for difficult magnetic DFT+U convergence](https://vasp.at/wiki/Troubleshooting_electronic_convergence)
and [direct-optimizer line search](https://vasp.at/wiki/ISEARCH). The retry
must still pass the original electronic and validation checks; no approval
was manufactured.

The server web service had restarted at 2026-10-14 14:18 UTC, after the
previous recovery. That left two CeO2(111) production task records marked
running although their VASP and job-runner processes had exited. The
service-start count was zero, so this was a service start rather than an
automatic restart reported by systemd. The stale rank leases were removed
by the shared rank pool on the next acquisition.

`scripts/requeue_interrupted_ceo2_111_20261015.py` archived those two
incomplete task outputs and the prior campaign record, left their original
job records for audit, then requeued them for clean input-based execution.
The backup is on the server at:

`/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.production-scf-recovery-backups/service-restart-20261015T084052Z`

At the last check, the validation runner was active at electronic step 46,
with `failed=0`, `completed=0/14`; two CeO2(111) production tasks had new
job IDs and were running. The rank pool held 16+16+16 of 48 ranks. The
campaign's `max_parallel=2` remains in place to reserve a validation slot.
Any later service restart may again interrupt child calculations, so check
actual VASP processes and rank leases before trusting persisted `running`
statuses. Reassess service and compute-process isolation after this run.

## Second (110) failure and model replacement, 09:28–09:36 UTC

The direct-optimizer retry also exhausted `NELM=240` at 09:08:55 UTC. Its
last electronic energy changes were roughly `1e-6` to `4e-6` eV, near but
not consistently below `EDIFF=1e-6`; the output is still unconverged. The
dipole moment remained about `0.75 eÅ` near the end, so the server output did
not show the huge dipole oscillations seen in the old local calculation.
The independently converged no-dipole DFT+U seed remained valid only for the
old geometry. The server still had two healthy, separately leased CeO2(111)
jobs using 32 of 48 MPI ranks; the web health endpoint returned HTTP 200.

Inspection of the old server POSCAR found outer atomic planes Ce2O2 at the
bottom and O2 at the top, with Ce2O4 interior planes. Formal Ce4+/O2−
charges make those outer planes +4 and −4. An x translation of a/4 before
ASE's `(110)` cleavage yields six Ce2O4 planes, including both outer faces.
This matches the published description of stoichiometric CeO2(110) as a
[nonpolar surface with Ce and O in each top layer](https://doi.org/10.1063/5.0292423).
The old slab is not a suitable baseline even though its latest SCF approached
the tolerance.

`src/adhesive_ai/vasp_production.py` now generates the neutral termination and
requires `surface_termination=neutral-CeO2-layers-v2` in (110) input manifests.
All 44 local VASP production and campaign-runner tests passed. The server file
matched the local pre-change file apart from these edits. A cgroup check
showed all 48 active VASP ranks, both production MPI launchers, and the (110)
validation runner were outside `adhesive-ai.service` in SSH session scopes.
The web service was restarted from PID 2652 to PID 64163. It returned HTTP
200; all 48 VASP PIDs survived and the validation runner remained active.
The new web process can now load the corrected module.

The failed 1.9 GB old matrix was renamed intact to
`/opt/adhesive-ai-lab/work/vasp_validation/ceo2-110-baseline-v1-legacy-polar-20261015T093230Z`.
The standard (110) validation root was regenerated with 14 new input sets;
all passed static input validation, had no copied charge/wavefunction, and
reported 0/14 complete and 0 failed. At 09:35 UTC a dedicated runner was
started alongside the two tracked (111) jobs within the shared 48-rank
budget. At 09:35:44 UTC it was active in the new `encut/450` initial-charge
preconvergence, with 16 new VASP ranks and electronic iteration 7.
No new output has been counted as converged or approved.
At the post-restart check, validation was in the PBE bridge stage at
electronic iteration 86. The two CeO2(111) production tasks remained active.

The neutral model's initial-charge stage completed in 15 steps. The PBE
bridge's first `ALGO=Normal` attempt oscillated and exhausted 180 steps;
its second attempt converged in 28 steps. The DFT+U bridge converged with
`ALGO=All`. The formal `encut/450` calculation then initially approached
`EDIFF=1e-6` but the dipole correction destabilized the charge density:
the dipole jumped from near zero to 95 eÅ and then exceeded 800 eÅ, while
electronic energies oscillated by thousands of eV. At step 26 the dedicated
(110) runner was cancelled, without stopping either CeO2(111) production
job. The formal output is incomplete and must not be reused. The converged
DFT+U bridge remains available as a seed for a controlled retry with an
explicit slab-center `DIPOL`, direct orbital optimization, and slower mixing.

## 2026-10-16 server reboot and controlled restart

The server booted at 04:43:16 UTC. At 08:30 UTC the CeO2(110) validation was
stopped at 0/14, and the two CeO2(111) campaign tasks still showed `running`
in `run.json` although their job-runner PIDs were gone, no `vasp_std` process
existed, and neither output passed electronic convergence. Their partial
outputs could not be accepted as completed calculations. The web service was
healthy, but persisted task status was stale after the reboot.

`scripts/recover_neutral_ceo2_110_dipole_20261015.py` verified the failed
top-level calculation's dipole divergence (maximum 1003.62 eÅ), the neutral
model, and the independently converged DFT+U seed. It archived the incomplete
top-level files at
`/opt/adhesive-ai-lab/work/vasp_validation/ceo2-110-baseline-v1/encut/450/.scf-recovery-backups/20261016T082950Z`.
It applied the controlled retry settings to all 14 jobs, retaining the physical
input hashes and the original `EDIFF=1e-6` acceptance check. The (110) runner
restarted at about 08:32 UTC from the verified DFT+U bridge. No matrix point
was marked complete or approved by this action.

`scripts/requeue_interrupted_ceo2_111_20261016.py` archived both interrupted
CeO2(111) outputs and the campaign record under
`/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.production-scf-recovery-backups/reboot-20261016-20261016T083227Z`.
It retained the old job records for audit and requeued those two tasks from
their unchanged inputs. New job IDs are `vasp-1d70bc8b98` and
`vasp-3ea271025d`. At 08:33 UTC, all three MPI jobs had launched (48/48
leased ranks, 48 VASP processes), the web health endpoint returned 200, and
the (110) formal calculation had reached electronic step 7 with a small
reported dipole (~0.000157 eÅ). This is early progress, not convergence.
At 08:35 UTC, the first `encut/450` point passed `electronic_converged`,
and the matrix advanced automatically to `encut/520` with 1/14 complete,
0 failed, and no approval. All 48 VASP processes were consuming CPU; the two
requeued CeO2(111) job records were running, and the web endpoint returned 200.
