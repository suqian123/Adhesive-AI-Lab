import json

from rdkit import Chem

from adhesive_ai.ce_disulfide_baseline import composition_contract, prepare_baseline


def test_candidate_composition_and_discrete_conversions():
    contract = composition_contract()
    cell = contract["finite_cell_proposal"]
    assert contract["monomers"]["BADCy"]["formula"] == "C17H14N2O2"
    assert contract["monomers"]["DCDDS"]["disulfide_bonds"] == 1
    assert abs(cell["BADCy_wt_pct_in_resin"] - 79) < 0.3
    assert abs(cell["CTBN_phr"] - 5) < 0.1
    assert [item["triazine_rings"] for item in cell["conversion_targets"]] == [120, 142, 150]
    assert all(item["reacted_cyanate_sites"] % 3 == 0 for item in cell["conversion_targets"])
    assert contract["production_approved"] is False


def test_precursors_are_readable_and_never_claim_production(tmp_path):
    result = prepare_baseline(tmp_path / "baseline")
    manifest = json.loads((tmp_path / "baseline" / "baseline_manifest.json").read_text())
    for name in ("BADCy", "DCDDS"):
        mol = Chem.SDMolSupplier(str(tmp_path / "baseline" / "components" / f"{name}.sdf"), removeHs=False)[0]
        assert mol is not None and mol.GetNumConformers() == 1
    assert result["manifest"]["production_approved"] is False
    assert manifest["artifacts"]["structure_contract"]["sha256"]
    assert not (tmp_path / "baseline" / "system.data").exists()
