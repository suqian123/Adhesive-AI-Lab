"""The generic first-retry helper only changes electronic solver controls."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_vasp_validation_scf_recovery.py"
spec = importlib.util.spec_from_file_location("validation_scf_recovery", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def failed_slab(tmp_path, monkeypatch):
    root = tmp_path / "ceo2-111-baseline-v1"
    job = root / "slab-layers/2"
    job.mkdir(parents=True)
    (root / "clean_baseline.json").write_text(json.dumps({"surface_termination": "O-Ce-O-trilayers-v2"}))
    (root / "validation_plan.json").write_text(json.dumps({"facet": "(111)", "jobs": [{"path": "slab-layers/2"}]}))
    (job / "run_status.json").write_text(json.dumps({"status": "failed", "complete": False}))
    (job / "OUTCAR").write_text("electronic self-consistency was not achieved\n")
    (job / "INCAR").write_text("ENCUT = 520\nLDAU = .TRUE.\nALGO = Normal\nNELM = 120\nNCORE = 1\n")
    for name in ("CHGCAR", "WAVECAR", "OSZICAR", "POSCAR", "POTCAR", "KPOINTS"):
        (job / name).write_bytes((name + " original").encode())
    monkeypatch.setattr(recovery, "_ensure_no_vasp_process_is_running", lambda: None)
    return root, job


def test_first_recovery_archives_failed_outputs_and_preserves_physical_inputs(failed_slab):
    root, job = failed_slab
    hashes = {name: hashlib.sha256((job / name).read_bytes()).hexdigest() for name in ("POSCAR", "POTCAR", "KPOINTS")}
    result = recovery.prepare(root, job_name="slab-layers/2")
    backup = Path(result["backup_directory"])
    assert (backup / "OUTCAR").is_file()
    assert not (job / "CHGCAR").exists() and not (job / "WAVECAR").exists()
    assert hashes == {name: hashlib.sha256((job / name).read_bytes()).hexdigest() for name in hashes}
    incar = (job / "INCAR").read_text()
    assert "ENCUT = 520" in incar and "LDAU = .TRUE." in incar
    assert "ALGO = All" in incar and "NELM = 240" in incar and "NCORE = 4" in incar
    assert result["state"] == "prepared-fresh-atomic"


def test_recovery_rejects_undeclared_task_without_archiving(failed_slab):
    root, job = failed_slab
    with pytest.raises(RuntimeError, match="not declared"):
        recovery.prepare(root, job_name="slab-layers/3")
    assert (job / "OUTCAR").exists()
    assert not (job / ".scf-recovery-backups").exists()
