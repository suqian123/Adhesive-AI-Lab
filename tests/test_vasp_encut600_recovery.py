"""Recovery archives failed outputs without changing the physical contract."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_vasp_encut600_recovery.py"
spec = importlib.util.spec_from_file_location("encut600_recovery", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture(params=(600, 650))
def failed_baseline(tmp_path, monkeypatch, request):
    cutoff = request.param
    root = tmp_path / "ceo2-111-baseline-v1"
    job = root / f"encut/{cutoff}"
    job.mkdir(parents=True)
    (root / "clean_baseline.json").write_text(json.dumps({"surface_termination": "O-Ce-O-trilayers-v2"}))
    (root / "validation_plan.json").write_text(json.dumps({"facet": "(111)"}))
    (job / "run_status.json").write_text(json.dumps({"status": "failed", "complete": False, "exit_code": 0}))
    (job / "OUTCAR").write_text("Iteration 1(120)\nelectronic self-consistency was not achieved\nEDIFF was not reached (unconverged)\nGeneral timing and accounting\n")
    (job / "INCAR").write_text(f"ENCUT = {cutoff}\nEDIFF = 1E-6\nLDAUU = 4.5 0\nIVDW = 12\nALGO = Normal\nNELM = 120\n")
    for name in ("CHGCAR", "WAVECAR", "OSZICAR", "POSCAR", "POTCAR", "KPOINTS"):
        (job / name).write_bytes((name + " original data").encode())
    monkeypatch.setattr(recovery, "_ensure_no_vasp_process_is_running", lambda: None)
    return root, job


def test_recovery_archives_failed_checkpoint_and_preserves_input_contract(failed_baseline):
    root, job = failed_baseline
    checksums = {name: hashlib.sha256((job / name).read_bytes()).hexdigest() for name in ("POSCAR", "POTCAR", "KPOINTS")}
    original_incar = (job / "INCAR").read_bytes()
    original_outcar = (job / "OUTCAR").read_bytes()
    result = recovery.prepare(root, cutoff=int(job.name))
    backup = Path(result["backup_directory"])
    assert (backup / "INCAR").read_bytes() == original_incar
    assert (backup / "OUTCAR").read_bytes() == original_outcar
    for name in ("CHGCAR", "WAVECAR", "OSZICAR"):
        assert not (job / name).exists()
        assert (backup / name).read_bytes() == (name + " original data").encode()
    assert checksums == {name: hashlib.sha256((job / name).read_bytes()).hexdigest() for name in checksums}
    incar = (job / "INCAR").read_text()
    for unchanged in (f"ENCUT = {job.name}", "EDIFF = 1E-6", "LDAUU = 4.5 0", "IVDW = 12"):
        assert unchanged in incar
    assert "ALGO = All" in incar and "NELM = 240" in incar
    assert "ISTART = 0" in incar and "ICHARG = 2" in incar
    assert result["attempt"] == 1
    with pytest.raises(RuntimeError, match="expected failed"):
        recovery.prepare(root, cutoff=int(job.name))


def test_recovery_refuses_a_different_facet_before_archiving(failed_baseline):
    root, job = failed_baseline
    (root / "validation_plan.json").write_text(json.dumps({"facet": "(110)"}))
    with pytest.raises(RuntimeError, match="O-Ce-O"):
        recovery.prepare(root, cutoff=int(job.name))
    assert (job / "OUTCAR").exists()
    assert not (job / ".scf-recovery-backups").exists()


def test_recovery_refuses_existing_retry_before_archiving(failed_baseline):
    root, job = failed_baseline
    (job / "scf_recovery.json").write_text('{"attempt":1}')
    with pytest.raises(RuntimeError, match="already prepared"):
        recovery.prepare(root, cutoff=int(job.name))
    assert (job / "CHGCAR").exists()
    assert not (job / ".scf-recovery-backups").exists()


def test_recovery_refuses_mismatched_cutoff_before_archiving(failed_baseline):
    root, job = failed_baseline
    (job / "INCAR").write_text("ENCUT = 520\n")
    with pytest.raises(RuntimeError, match="cutoff does not match"):
        recovery.prepare(root, cutoff=int(job.name))
    assert (job / "OUTCAR").exists()
    assert not (job / ".scf-recovery-backups").exists()


def test_recovery_validates_server_ncore_before_archiving(failed_baseline, monkeypatch):
    root, job = failed_baseline
    monkeypatch.setenv("ADHESIVE_VASP_NCORE", "0")
    with pytest.raises(ValueError, match="positive"):
        recovery.prepare(root, cutoff=int(job.name))
    assert (job / "OUTCAR").exists()
    assert not (job / ".scf-recovery-backups").exists()
