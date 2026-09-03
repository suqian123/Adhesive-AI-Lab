# CeO2/PDA VASP production baseline

The generated structures are reproducible production candidates, not validated
results.  Production execution remains locked until convergence evidence is
reviewed and recorded.

## Baseline

- Fluorite CeO2, initial lattice constant 5.411 A
- (111), (110), and (100) periodic slabs
- Three conventional-cell layers and 18 A vacuum at the starting point
- Discrete surface oxygen vacancies and protonated surface-oxygen coverage
- A covalently aryl-linked dopamine tetramer (`C32H38N4O8`) as the PDA baseline
- PAW-PBE, D3(BJ) (`IVDW=12`), spin polarization, and Dudarev Ce-4f `Ueff = 4.5 eV`
- Bottom third of the oxide slab fixed during relaxation
- Five intermediate climbing-image NEB images for atomic-oxygen tasks

Requested defect fractions are converted to the closest value representable by
the finite surface cell.  Both requested and actual fractions are recorded in
each `input_manifest.json`.

## Commands

Generate or refresh all DFT inputs in a campaign package:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_vasp_campaign.py `
  --campaign-dir work\campaign_runs\RUN_ID\package\CAMPAIGN_ID `
  --resources work\vasp_resources.json
```

Generate the first surface convergence matrix:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_vasp_campaign.py `
  --resources work\vasp_resources.json `
  --validation-root work\vasp_validation\ceo2-111-baseline-v1
```

Run or resume the convergence matrix through the dedicated WSL account:

```powershell
wsl.exe -d Ubuntu-24.04 -u vasp -- bash `
  /mnt/e/Adhesive-AI-Lab/scripts/run_vasp_convergence.sh
```

The runner skips completed jobs and records progress in
`work/vasp_validation/ceo2-111-baseline-v1/status.tsv`.  Each job first builds
a conservative DFT+U charge/wavefunction restart without the dipole field,
then restores the production INCAR (including `LDIPOL`) for the reported
energy.  This staging changes only the numerical initialization, not the
production Hamiltonian.

Monitor the queue from PowerShell:

```powershell
Get-Content work\vasp_validation\ceo2-111-baseline-v1\status.tsv -Wait
```

Run static validation:

```powershell
.\.venv\Scripts\python.exe scripts\validate_vasp_inputs.py `
  --campaign-dir work\campaign_runs\RUN_ID\package\CAMPAIGN_ID `
  --report work\vasp_validation\RUN_ID-static-report.json
```

Prepare one task without starting VASP:

```powershell
.\.venv\Scripts\python.exe scripts\run_vasp_task.py TASK_DIR\task.json --prepare-only
```

## Approval gate

The wrapper requires `work/vasp_validation/approved.json` before a production
run.  The file must contain an explicit approval and links to convergence
evidence, for example:

```json
{
  "approved": true,
  "model": "CeO2-fluorite/PDA-dopamine-tetramer-baseline-v1",
  "evidence": [
    "work/vasp_validation/ceo2-111-baseline-v1/convergence_report.json"
  ]
}
```

Approval should cover ENCUT, k points, slab thickness, vacuum, bulk lattice
constant, Ce `Ueff`, relaxed NEB endpoints, and the PDA structural assumption.
Static input validation or VASP `--dry-run` alone is not convergence evidence.

The configured `vdw_kernel.bindat` remains available but is not used by the
D3(BJ) baseline.  The nonlocal optB86b grid exceeded the local 8 GiB memory
budget even for the reduced convergence cell; this method change is recorded
in every generated manifest and approval report.
