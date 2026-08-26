from adhesive_ai import build_candidate_library
from adhesive_ai.engines import DFTResult, MDObservables
from adhesive_ai.result_integration import apply_external_results, closed_loop_with_external_results, update_candidate_with_external_results


def test_external_results_replace_candidate_features_and_retrain():
    candidates = build_candidate_library(max_records=16, seed=2)
    candidate_id = candidates.iloc[0].candidate_id
    dft = DFTResult("vasp", -100.0, -1.41, -0.4, 0.7, .2, (-100.0,))
    md = MDObservables(385.0, .095, 515.0, 4.1, 43.0, (-180.0, 25.0), (1000.0, 1050.0))
    updated = update_candidate_with_external_results(candidates, candidate_id, dft=dft, md=md, interface={"binding_energy_mj_m2": 155.0, "covalent_bond_count": 7})
    row = updated[updated.candidate_id == candidate_id].iloc[0]
    assert row.glass_transition_c == 385.0 and row.filler_oxygen_adsorption_ev == -1.41
    assert row.interface_covalent_bond_count == 7 and row.simulation_source == "external"
    assert row.data_source == "hybrid-external"
    assert row.feature_provenance["glass_transition_c"].startswith("external:")
    assert row.feature_provenance["interface_binding_energy_mj_m2"].startswith("external:")
    loop = closed_loop_with_external_results(candidates, {candidate_id: {"dft": dft, "md": md}}, top_n=3)
    assert len(loop["shortlist"]) == 3 and loop["model"].version == "external-v1"
    assert apply_external_results(candidates, {candidate_id: {"md": md}}).iloc[0].simulation_source == "external"
