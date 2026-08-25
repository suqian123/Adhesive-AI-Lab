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


def test_external_md_output_parsing_and_input_generation(tmp_path):
    thermo = "Step Temp Pe Vol\n0 300 -10 1000\n100 300 -12 1002\n200 300 -11 1001\n"
    parsed = run_interface_simulation(
        compatibility_index=.8, polar_fraction=.25, filler_ratio=.1,
        temperature_c=25, thermo_output=thermo, particles=4,
    )
    assert parsed.engine == "lammps" and parsed.status == "parsed"
    assert len(parsed.steps) == 3 and parsed.interface_energy_mj_m2 > 0
    generated = run_interface_simulation(
        compatibility_index=.8, polar_fraction=.25, filler_ratio=.1,
        temperature_c=25, engine="lammps", workdir=tmp_path / "md", steps=100, particles=12,
    )
    assert generated.status == "input-generated"
    assert (tmp_path / "md" / "interface.data").exists()
    assert (tmp_path / "md" / "in.production").exists()

    xvg = "# energy\n@ title \"Potential\"\n0 -4\n1 -5\n"
    gromacs = run_interface_simulation(
        compatibility_index=.8, polar_fraction=.25, filler_ratio=.1,
        temperature_c=25, energy_xvg=xvg, particles=4,
    )
    assert gromacs.engine == "gromacs" and gromacs.status == "parsed"
