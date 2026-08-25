from adhesive_ai.engines import (
    Atom, DFTJobSpec, MDJobSpec, compute_md_observables, generate_dft_inputs,
    generate_md_inputs, parse_dft_output, parse_gromacs_xvg, parse_lammps_thermo,
    parse_neb_energies,
)


def test_dft_inputs_and_output_parsers(tmp_path):
    atoms = (Atom("Ce", 0, 0, 0), Atom("O", 1, 0, 0))
    spec = DFTJobSpec("vasp", atoms, ((8, 0, 0), (0, 8, 0), (0, 0, 18)), neb_images=3, final_atoms=(Atom("Ce", 0, 0, 0), Atom("O", 2, 0, 0)))
    files = generate_dft_inputs(spec, tmp_path / "vasp")
    assert {"00/POSCAR", "04/POSCAR", "INCAR", "KPOINTS"}.issubset(files)
    assert "IMAGES = 3" in files["INCAR"]
    result = parse_dft_output("vasp", "free energy TOTEN = -10.0\nfree energy TOTEN = -8.5\nCe3_fraction 0.24", surface_energy_ev=-5, oxygen_energy_ev=-2, neb_energies_ev=(-10.0, -8.5, -9.0))
    assert result.adsorption_energy_ev == -1.5
    assert result.reaction_barrier_ev == 1.5
    assert result.ce3_fraction == .24


def test_md_inputs_parsers_and_observables(tmp_path):
    lammps = generate_md_inputs(MDJobSpec("lammps", "system.data"), tmp_path)["in.production"]
    assert "read_data system.data" in lammps and "pair_style lj/cut/coul/long" in lammps and "pair_style opls-aa" not in lammps
    thermo = parse_lammps_thermo("Step Temp Pe Vol\n0 300 -10 100\n100 320 -11 102")
    assert thermo["Vol"].tolist() == [100, 102]
    x, y = parse_gromacs_xvg("@ title\n0 101\n1 102\n")
    assert x.tolist() == [0, 1] and y.tolist() == [101, 102]
    result = compute_md_observables([0, 50, 100, 150], [100, 101, 104, 110], [-100, -101, -102, -103])
    assert result.free_volume_fraction > 0
    assert len(result.temperatures_c) == 4


def test_qe_and_cp2k_neb_inputs_require_and_include_endpoints():
    atoms = (Atom("Ce", 0, 0, 0), Atom("O", 1, 0, 0))
    final = (Atom("Ce", 0, 0, 0), Atom("O", 2, 0, 0))
    for engine, marker in (("qe", "BEGIN_PATH_INPUT"), ("cp2k", "BAND_TYPE CI-NEB")):
        files = generate_dft_inputs(DFTJobSpec(engine, atoms, ((8, 0, 0), (0, 8, 0), (0, 0, 18)), neb_images=2, final_atoms=final))
        assert marker in next(iter(files.values()))


def test_neb_energy_parser_handles_common_image_formats_and_units():
    energies = parse_neb_energies("image 0 energy = -10.0\n1: E = -8.5\nreplica 2 energy = -9.0")
    assert energies == (-10.0, -8.5, -9.0)
    assert parse_neb_energies("image 0 energy = -1.0\nimage 1 energy = -0.5", unit="Ry")[0] < -13
