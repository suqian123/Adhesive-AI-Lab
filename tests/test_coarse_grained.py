from adhesive_ai.coarse_grained import build_cg_interface_model, build_pda_ceo2_force_field, write_cg_interface_model


def test_cg_force_field_and_lammps_model(tmp_path):
    field = build_pda_ceo2_force_field(pda_coverage=.7, oxygen_vacancy_fraction=.1)
    assert any(pair.first == "PDA_N" and pair.second == "Ce" for pair in field.pairs)
    model = build_cg_interface_model(resin_beads=20, ceria_particles=4, force_field=field)
    paths = write_cg_interface_model(model, tmp_path)
    assert paths["data"].exists() and model.bead_counts["PDA_N"] > 0 and model.bead_counts["O"] > 0
    assert "Bonds" in model.lammps_data and "pair_coeff 2 4" in model.lammps_input and "bond_coeff" in model.lammps_input
