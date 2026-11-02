# Why the CeO2(110) validation did not write back candidate data

At 2026-10-17 03:29 UTC the server had a valid `(110)` numerical baseline:
15/15 electronically converged points, all convergence thresholds passed, and
`approved.json` present. This shared baseline is not a candidate calculation.
Candidate database writeback occurs in `integrate_campaign_run` after a
campaign reaches `completed` or `partial` with parseable production results.

Campaign `CL5-00016-1b4ebc3d36` for candidate `CL5-00016` was still `running`,
with `integrated_at=null`, two `(111)` production VASP tasks running, five
other `(111)` tasks pending, and seven `(110)` tasks incorrectly blocked. The
two running tasks had no completed OUTCAR; one had exhausted 240 electronic
iterations in an ionic step and neither had nonempty CHGCAR/WAVECAR. Their
partial outputs are not eligible for writeback.

The `(110)` tasks were generated before the neutral-surface correction. Their
manifests still said `legacy-v1`, `ENCUT=520`, three slab layers. The current
validator requires `neutral-CeO2-layers-v2`, so `resume_approved_vasp_tasks`
correctly refused to release them despite the shared approval. The approved
settings are 650 eV, four layers, and 18 Å vacuum.

`scripts/migrate_approved_ceo2_110_tasks_20261017.py` generated and validated
seven replacement inputs in a staging directory, retained each task identity
and command, and archived the old input directories at
`/opt/adhesive-ai-lab/work/campaign_runs/CL5-00016-1b4ebc3d36/.legacy-110-inputs-20261017T033240Z`.
It then called the standard approval-resume path. A later independent check
found all seven `(110)` tasks `pending`, statically valid, with the neutral
termination, 650 eV cutoff, and four layers. Their execution awaits an open
campaign slot (`max_parallel=2`). The two `(111)` VASP processes remained live.

`platform_page.py` now labels approved validation explicitly and explains that
shared validation unlocks production tasks; candidate writeback follows only
after production tasks finish and are aggregated. It no longer hard-codes a
12-point validation matrix in the blocked-state help text. The web service
restart at 03:35 UTC returned HTTP 200; all 32 VASP ranks remained running
outside its cgroup. The campaign still had `integrated_at=null`, as expected.
