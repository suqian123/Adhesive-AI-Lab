import numpy as np

from adhesive_ai.multiscale import calculate_interface_and_cg, calculate_quantum_surface, calculate_resin_md


def test_quantum_surface_defects_increase_ce3_and_lower_barrier():
    pristine = calculate_quantum_surface(oxygen_vacancy_fraction=0.0)
    defective = calculate_quantum_surface(oxygen_vacancy_fraction=0.18)
    assert defective.ce3_fraction > pristine.ce3_fraction
    assert defective.oxygen_reaction_barrier_ev < pristine.oxygen_reaction_barrier_ev


def test_md_and_interface_ranges():
    md = calculate_resin_md(crosslink_density=.65, resin_thermal=.8, resin_toughness=.6, resin_polarity=.7, dynamic_healing=.7, dynamic_mobility=.25, filler_pct=5)
    quantum = calculate_quantum_surface(resin_polarity=.7)
    interface = calculate_interface_and_cg(quantum=quantum, md=md, filler_pct=5, crosslink_density=.65, resin_polarity=.7)
    assert md.temperatures_c[0] == -180
    assert np.all(md.elastic_modulus_gpa > 0)
    assert 0 <= interface.dispersion_index <= 1
    assert interface.binding_energy_mj_m2 > 0
