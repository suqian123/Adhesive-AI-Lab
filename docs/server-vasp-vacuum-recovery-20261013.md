# Server CeO2(111) vacuum validation recovery — 2026-10-13

The shared VASP validation shown at `127.0.0.1:18501` stopped at
`vacuum/15A`. VASP exited with code 0 after exhausting `NELM=120`, but its
OUTCAR reported that electronic self-consistency was not achieved. The runner
correctly rejected the output and kept `approved=false`.

The failed `vacuum/15A` outputs were archived in
`work/vasp_validation/ceo2-111-baseline-v1/vacuum/15A/.scf-recovery-backups/20261013T090252Z/`.
The retry started from atomic charge (`ISTART=0`, `ICHARG=2`), without reusing
the failed CHGCAR or WAVECAR. It passed the electronic convergence parser and
was marked complete at 2026-10-13 09:12:02 UTC. It changed only the electronic solver tags
`ALGO: Normal → All`, `NELM: 120 → 240`, and `TIME: absent → 0.1` in INCAR.

The unstarted `vacuum/18A` and `vacuum/22A` tasks were prepared with the same
solver settings. Their previous INCAR files are saved in each task's
`.solver-preparation-20261013/INCAR.before`; POSCAR, POTCAR, and KPOINTS
SHA-256 hashes were checked before and after the change. Verification found
only those three INCAR tags changed across all three vacuum jobs. The local
suite generator was updated for future CeO2(111) vacuum validation inputs and
synced to the server; its SHA-256 on both sides is
`35ab7971984b5ed878359f6b96d5067f0f47a0cbc14e7c117176aae57e633434`.
The relevant local tests passed (12 tests). The Streamlit health endpoint
returned `ok`.

The final server check found **14/14 completed, 0 failed**. All fourteen jobs
passed the electronic convergence parser. The analysis report passed its six
checks (completion, electronic convergence, ENCUT, k-point, vacuum, and slab
layer criteria), and the runner finished. The automated analysis wrote
`approved.json` at 2026-10-13 09:34:32 UTC with scope
`numerical VASP production baseline`; the UI reports `approved=true`.
This approval is limited to the checks and assumptions recorded in
`convergence_report.json` and `approved.json`. Failed archived outputs were
not treated as checkpoints or approval evidence. The Streamlit health
endpoint returned `ok` after completion.
