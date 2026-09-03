import json

from rdkit import Chem

from adhesive_ai.md_production import (
    DEFAULT_MD_BASELINE, baseline_contract, build_polyimide_oligomer,
    prepare_md_structure_baseline, provision_bulk_md_inputs, provision_interface_md_inputs,
)


def test_md_baseline_has_explicit_sanitizable_components():
    contract = baseline_contract()
    assert contract["baseline_id"] == "odpa-oda-catechol-pdba-v1"
    assert contract["resolution"] == "all-atom"
    assert {item["component_id"] for item in contract["components"]} == {
        "ODPA", "ODA", "DABA_DA", "PDBA", "DOPAMINE",
    }
    assert all(Chem.MolFromSmiles(item["canonical_smiles"]) is not None for item in contract["components"])
    assert DEFAULT_MD_BASELINE.scientific_status.endswith("validation-pending")


def test_prepare_md_structure_baseline_writes_hashed_3d_assets(tmp_path):
    result = prepare_md_structure_baseline(tmp_path / "baseline", embed_oligomer=False)

    paths = result["paths"]
    assert paths["contract"].is_file()
    assert paths["ODPA_sdf"].is_file()
    assert paths["PDBA_pdb"].is_file()
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["production_approved"] is False
    assert manifest["artifacts"]["ODPA_sdf"]["sha256"]
    plan = json.loads(paths["parameterization_plan"].read_text(encoding="utf-8"))
    assert plan["production_gate"].startswith("blocked")


def test_polyimide_builder_closes_imides_and_retains_amino_chain_ends():
    oligomer, metadata = build_polyimide_oligomer(
        repeat_units=2, catechol_diamine_fraction=1 / 3, embed=False,
    )

    primary_amines = [
        atom for atom in oligomer.GetAtoms()
        if atom.GetAtomicNum() == 7
        and sum(neighbor.GetAtomicNum() == 1 for neighbor in atom.GetNeighbors()) == 2
    ]
    imide_nitrogens = [
        atom for atom in oligomer.GetAtoms()
        if atom.GetAtomicNum() == 7
        and sum(neighbor.GetAtomicNum() == 6 for neighbor in atom.GetNeighbors()) == 3
    ]
    assert metadata["formal_charge"] == 0
    assert metadata["catechol_diamine_count"] == 1
    assert len(primary_amines) == 2
    assert len(imide_nitrogens) == 4


def test_bulk_input_provisioning_is_traceable_and_never_approves(tmp_path):
    cell = tmp_path / "baseline" / "polyimide-cell"
    cell.mkdir(parents=True)
    (cell / "system.data").write_text("LAMMPS data\n", encoding="utf-8")
    (cell / "forcefield.production").write_text("pair_style lj/cut 12\n", encoding="utf-8")
    output = tmp_path / "task"

    result = provision_bulk_md_inputs(
        {"conditions": {"crosslink_density": 0.72}}, output, baseline_dir=tmp_path / "baseline",
    )

    assert result["provisioned"] is True
    assert (output / "system.data").read_text(encoding="utf-8") == "LAMMPS data\n"
    manifest = json.loads((output / "md_input_manifest.json").read_text(encoding="utf-8"))
    assert manifest["target_crosslink_density"] == 0.72
    assert manifest["production_approved"] is False
    assert manifest["artifacts"]["system.data"]["task_sha256"]


def test_interface_input_provisioning_records_force_field_limitations(tmp_path):
    cell = tmp_path / "baseline" / "interface-cell"
    cell.mkdir(parents=True)
    (cell / "interface.data").write_text("LAMMPS interface\n", encoding="utf-8")
    (cell / "forcefield.production").write_text("pair_style hybrid\n", encoding="utf-8")
    output = tmp_path / "task"

    result = provision_interface_md_inputs(
        {"conditions": {"filler_pct": 4.5}}, output, baseline_dir=tmp_path / "baseline",
    )

    assert result["provisioned"] is True
    manifest = json.loads((output / "md_input_manifest.json").read_text(encoding="utf-8"))
    assert manifest["target_filler_pct"] == 4.5
    assert manifest["production_approved"] is False
    assert any("IP10a" in item for item in manifest["limitations"])
