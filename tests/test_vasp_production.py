import gzip
import io
import json
import tarfile
import numpy as np
import pytest

from ase.neighborlist import neighbor_list

from adhesive_ai.vasp_production import (
    build_ceo2_model,
    dopamine_tetramer,
    prepare_campaign_dft_task,
    validate_vasp_input_set,
    write_convergence_suite,
    VaspBaseline,
)
from adhesive_ai.vasp_resources import sha256_file


def _resource_config(tmp_path):
    archive = tmp_path / "potpaw_PBE.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        for symbol in ("Ce", "O", "C", "N", "H", "B"):
            content = f"TITEL = PAW_PBE {symbol} test\nEnd of Dataset\n".encode()
            info = tarfile.TarInfo(f"{symbol}/POTCAR")
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    kernel_gz = tmp_path / "vdw_kernel.bindat.gz"
    with gzip.open(kernel_gz, "wb") as stream:
        stream.write(b"kernel-data\n")
    kernel = tmp_path / "vdw_kernel.bindat"
    with gzip.open(kernel_gz, "rb") as source:
        kernel.write_bytes(source.read())
    config = tmp_path / "resources.json"
    config.write_text(json.dumps({
        "potcar_archive": str(archive),
        "potcar_archive_sha256": sha256_file(archive),
        "potcar_variants": {symbol: symbol for symbol in ("Ce", "O", "C", "N", "H", "B")},
        "vdw_kernel": str(kernel),
        "vdw_kernel_sha256": sha256_file(kernel),
    }), encoding="utf-8")
    return config


def test_ceo2_surface_realizes_discrete_defect_coverages_without_overlap():
    for facet in ("(111)", "(110)", "(100)"):
        atoms, metadata = build_ceo2_model(facet, vacancy_fraction=0.08, hydroxyl_fraction=0.35)
        close_i, close_j = neighbor_list("ij", atoms, cutoff=0.60)
        assert len(close_i) == len(close_j) == 0
        assert metadata["vacancy_count"] >= 1
        assert metadata["hydroxyl_count"] >= 1
        assert metadata["scientific_status"] == "pending-convergence-validation"
        assert len(atoms) > 100


def test_dopamine_tetramer_is_covalently_linked_four_unit_baseline():
    oligomer = dopamine_tetramer()
    assert oligomer.get_chemical_formula() == "C32H38N4O8"
    assert len(oligomer) == 82


@pytest.mark.parametrize("ncore", [2, 4])
def test_campaign_task_writes_dft_u_vdw_and_neb_inputs(tmp_path, monkeypatch, ncore):
    monkeypatch.setenv("ADHESIVE_VASP_NCORE", str(ncore))
    resources = _resource_config(tmp_path)
    task = {
        "task_id": "dft-o-111-v08-h35",
        "calculation_kind": "dft",
        "objective": "atomic-oxygen-surface-chemistry",
        "conditions": {"facet": "(111)", "oxygen_vacancy_fraction": 0.08, "hydroxyl_fraction": 0.35},
    }
    output = tmp_path / "task"
    manifest = prepare_campaign_dft_task(task, output, resources=resources)

    assert {"POSCAR", "INCAR", "KPOINTS", "POTCAR", "vdw_kernel.bindat"}.issubset(
        path.name for path in output.iterdir()
    )
    incar = (output / "INCAR").read_text(encoding="utf-8")
    assert "LDAUU = 4.5" in incar
    assert "IVDW = 12" in incar
    assert "GGA = PE" in incar
    assert f"NCORE = {ncore}" in incar
    assert manifest["ncore"] == ncore and manifest["neb"]["ncore"] == ncore
    assert f"NCORE = {ncore}" in (output / "neb/INCAR").read_text()
    assert (output / "neb" / "00" / "POSCAR").is_file()
    assert (output / "neb" / "06" / "POSCAR").is_file()
    assert manifest["scientific_status"] == "pending-convergence-validation"
    assert validate_vasp_input_set(output, resources=resources)["valid"] is True


def test_convergence_suite_records_the_requested_surface_facet(tmp_path, monkeypatch):
    monkeypatch.setenv("ADHESIVE_VASP_NCORE", "4")
    resources = _resource_config(tmp_path)

    plan = write_convergence_suite(tmp_path / "ceo2-110", facet="(110)", resources=resources)

    assert plan["facet"] == "(110)"
    assert plan["job_count"] == 14
    first_manifest = json.loads((tmp_path / "ceo2-110" / "encut" / "450" / "input_manifest.json").read_text(encoding="utf-8"))
    assert first_manifest["facet"] == "(110)"
    assert first_manifest["surface_termination"] == "neutral-CeO2-layers-v2"
    assert first_manifest["ncore"] == 4
    assert "NCORE = 4" in (tmp_path / "ceo2-110/encut/450/INCAR").read_text()
    first_manifest["surface_termination"] = "legacy-v1"
    (tmp_path / "ceo2-110/encut/450/input_manifest.json").write_text(json.dumps(first_manifest))
    assert validate_vasp_input_set(tmp_path / "ceo2-110/encut/450", resources=resources)["valid"] is False


@pytest.mark.parametrize("layers", [2, 3, 4, 5])
def test_111_clean_slab_has_neutral_oxygen_terminated_trilayers(layers):
    atoms, metadata = build_ceo2_model(
        "(111)", objective="surface-convergence", repeat_override=(1, 1, 1),
        settings=VaspBaseline(slab_layers=layers),
    )
    symbols = np.array(atoms.get_chemical_symbols())
    z = atoms.positions[:, 2]
    assert len(atoms) == layers * 12
    assert set(symbols[np.isclose(z, z.min())]) == {"O"}
    assert set(symbols[np.isclose(z, z.max())]) == {"O"}
    assert abs(np.dot(np.where(symbols == "Ce", 4, -2), z)) < 1e-8
    distances = atoms.get_all_distances(mic=True)
    np.fill_diagonal(distances, np.inf)
    assert distances.min() > 2.3
    assert metadata["surface_termination"] == "O-Ce-O-trilayers-v2"


@pytest.mark.parametrize("layers", [2, 3, 4, 5])
def test_110_clean_slab_has_neutral_ceo2_outer_layers(layers):
    atoms, metadata = build_ceo2_model(
        "(110)", objective="surface-convergence", repeat_override=(1, 1, 1),
        settings=VaspBaseline(slab_layers=layers),
    )
    symbols = np.array(atoms.get_chemical_symbols())
    z = atoms.positions[:, 2]
    planes = np.unique(np.round(z, 7))
    assert len(atoms) == layers * 12
    assert len(planes) == layers * 2
    for plane in planes:
        mask = np.isclose(z, plane)
        assert (np.count_nonzero(mask & (symbols == "Ce")),
                np.count_nonzero(mask & (symbols == "O"))) == (2, 4)
    assert abs(np.dot(np.where(symbols == "Ce", 4, -2), z)) < 1e-8
    assert metadata["surface_termination"] == "neutral-CeO2-layers-v2"


def test_legacy_111_inputs_cannot_be_released_by_new_approval(tmp_path):
    resources = _resource_config(tmp_path)
    write_convergence_suite(tmp_path / "validation", resources=resources)
    directory = tmp_path / "validation" / "encut" / "450"
    assert (tmp_path / "validation" / "clean_baseline.json").is_file()
    incar = (directory / "INCAR").read_text()
    assert "ICHARG = 2" in incar and "LWAVE = .TRUE." in incar
    assert "EDIFF = 1E-6" in incar and "LDAUU = 4.5 0" in incar
    slab_three = (tmp_path / "validation" / "slab-layers" / "3" / "INCAR").read_text()
    slab_four = (tmp_path / "validation" / "slab-layers" / "4" / "INCAR").read_text()
    slab_five = (tmp_path / "validation" / "slab-layers" / "5" / "INCAR").read_text()
    assert "ALGO = Normal" in slab_three and "NELM = 120" in slab_three
    for large_slab in (slab_four, slab_five):
        assert "ALGO = All" in large_slab and "NELM = 240" in large_slab
        assert "TIME = 0.1" in large_slab and "EDIFF = 1E-6" in large_slab
    for vacuum in ("15A", "18A", "22A"):
        vacuum_incar = (tmp_path / "validation" / "vacuum" / vacuum / "INCAR").read_text()
        assert "ALGO = All" in vacuum_incar and "NELM = 240" in vacuum_incar
        assert "TIME = 0.1" in vacuum_incar and "EDIFF = 1E-6" in vacuum_incar
    manifest_path = directory / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("surface_termination")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert validate_vasp_input_set(directory, resources=resources)["valid"] is False
