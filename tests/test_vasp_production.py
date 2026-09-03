import gzip
import io
import json
import tarfile

from ase.neighborlist import neighbor_list

from adhesive_ai.vasp_production import (
    build_ceo2_model,
    dopamine_tetramer,
    prepare_campaign_dft_task,
    validate_vasp_input_set,
    write_convergence_suite,
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


def test_campaign_task_writes_dft_u_vdw_and_neb_inputs(tmp_path):
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
    assert (output / "neb" / "00" / "POSCAR").is_file()
    assert (output / "neb" / "06" / "POSCAR").is_file()
    assert manifest["scientific_status"] == "pending-convergence-validation"
    assert validate_vasp_input_set(output, resources=resources)["valid"] is True


def test_convergence_suite_records_the_requested_surface_facet(tmp_path):
    resources = _resource_config(tmp_path)

    plan = write_convergence_suite(tmp_path / "ceo2-110", facet="(110)", resources=resources)

    assert plan["facet"] == "(110)"
    assert plan["job_count"] == 12
    first_manifest = json.loads((tmp_path / "ceo2-110" / "encut" / "450" / "input_manifest.json").read_text(encoding="utf-8"))
    assert first_manifest["facet"] == "(110)"
