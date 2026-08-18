import numpy as np

from adhesive_ai.simulation import run_interface_simulation


def test_simulation_shapes_and_ranges():
    result = run_interface_simulation(
        compatibility_index=.8, polar_fraction=.25, filler_ratio=.1,
        temperature_c=25, steps=120, particles=20,
    )
    assert len(result.steps) == 120
    assert result.energy.shape == (120,)
    assert result.final_positions.shape == (20, 3)
    assert np.isfinite(result.adhesion_work_mj_m2)
    assert 0 <= result.stability_score <= 1
