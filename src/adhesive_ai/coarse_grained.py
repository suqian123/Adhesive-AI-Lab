"""PDA@CeO2 coarse-grained topology and LAMMPS model generation.

Parameters are an explicitly labelled starting point and require calibration
against atomistic PMFs, DFT binding energies, and experiment before use for
quantitative predictions.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations_with_replacement
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class CGBead:
    name: str
    mass: float
    charge: float
    sigma_angstrom: float
    epsilon_kcal_mol: float


@dataclass(frozen=True)
class CGPair:
    first: str
    second: str
    epsilon_kcal_mol: float
    sigma_angstrom: float
    cutoff_angstrom: float = 12.0


@dataclass(frozen=True)
class CGBond:
    first: str
    second: str
    spring_kcal_mol_a2: float
    length_angstrom: float


@dataclass(frozen=True)
class CGForceField:
    name: str
    beads: tuple[CGBead, ...]
    pairs: tuple[CGPair, ...]
    bonds: tuple[CGBond, ...]
    source: str
    calibration_status: str


@dataclass(frozen=True)
class CGInterfaceModel:
    box_angstrom: tuple[float, float, float]
    resin_beads: int
    ceria_particles: int
    pda_coverage: float
    oxygen_vacancy_fraction: float
    bead_counts: dict[str, int]
    force_field: CGForceField
    lammps_data: str
    lammps_input: str


def build_pda_ceo2_force_field(
    *, pda_coverage: float = 0.65, oxygen_vacancy_fraction: float = 0.08,
    calibration_status: str = "initial-physics-informed",
) -> CGForceField:
    """Create an explicit full pair matrix and bond set for PDA@CeO2 CG work."""
    coverage = float(np.clip(pda_coverage, 0.0, 1.0))
    vacancy = float(np.clip(oxygen_vacancy_fraction, 0.0, 0.30))
    beads = (
        CGBead("R", 56.0, 0.0, 4.20, 0.24), CGBead("PDA_C", 36.0, 0.10, 3.80, 0.34),
        CGBead("PDA_N", 14.0, -0.20, 3.30, 0.42 + 0.20 * coverage),
        CGBead("Ce", 140.1, 1.10 + 0.35 * vacancy, 3.00, 0.18),
        CGBead("O", 16.0, -0.55, 3.10, 0.16), CGBead("Ovac", 1.0, -0.20, 2.80, 0.10),
    )
    explicit = {
        tuple(sorted(("R", "PDA_N"))): (0.52, 3.75), tuple(sorted(("R", "PDA_C"))): (0.38, 4.00),
        tuple(sorted(("R", "Ce"))): (0.48, 3.60), tuple(sorted(("R", "O"))): (0.30, 3.65),
        tuple(sorted(("R", "Ovac"))): (0.14, 3.50), tuple(sorted(("PDA_N", "Ce"))): (0.92 + 0.50 * vacancy, 3.15),
        tuple(sorted(("PDA_N", "O"))): (0.64, 3.20), tuple(sorted(("PDA_N", "Ovac"))): (0.28, 3.05),
        tuple(sorted(("PDA_C", "Ce"))): (0.56, 3.40), tuple(sorted(("PDA_C", "O"))): (0.42, 3.45),
        tuple(sorted(("PDA_C", "Ovac"))): (0.20, 3.20), tuple(sorted(("Ce", "O"))): (0.20, 3.05),
        tuple(sorted(("Ce", "Ovac"))): (0.12, 2.90),
    }
    lookup = {bead.name: bead for bead in beads}
    pairs = []
    for left, right in combinations_with_replacement((bead.name for bead in beads), 2):
        epsilon, sigma = explicit.get(tuple(sorted((left, right))), (float(np.sqrt(lookup[left].epsilon_kcal_mol * lookup[right].epsilon_kcal_mol)), float((lookup[left].sigma_angstrom + lookup[right].sigma_angstrom) / 2)))
        pairs.append(CGPair(left, right, epsilon, sigma))
    bonds = (
        CGBond("R", "R", 12.0, 4.20), CGBond("PDA_C", "PDA_N", 18.0, 1.35),
        CGBond("PDA_N", "Ce", 24.0 + 8.0 * vacancy, 2.35), CGBond("Ce", "O", 40.0, 2.15),
        CGBond("Ce", "Ovac", 8.0, 2.15),
    )
    return CGForceField("PDA@CeO2-CG-v2", beads, tuple(pairs), bonds, "PDA@CeO2 CG topology with explicit resin/PDA/ceria interactions", calibration_status)


def _add_atom(atoms: list[tuple[int, int, int, float, float, float, float]], molecule: int, bead_type: int, charge: float, x: float, y: float, z: float) -> int:
    atom_id = len(atoms) + 1
    atoms.append((atom_id, molecule, bead_type, charge, x, y, z))
    return atom_id


def build_cg_interface_model(
    *, resin_beads: int = 240, ceria_particles: int = 16, box_angstrom: tuple[float, float, float] = (120.0, 120.0, 80.0),
    pda_coverage: float = 0.65, oxygen_vacancy_fraction: float = 0.08, chain_length: int = 12,
    force_field: CGForceField | None = None,
) -> CGInterfaceModel:
    """Build resin chains and PDA-coated defective ceria clusters for LAMMPS."""
    if resin_beads < 1 or ceria_particles < 1 or chain_length < 2:
        raise ValueError("resin_beads, ceria_particles, and chain_length must be positive")
    field = force_field or build_pda_ceo2_force_field(pda_coverage=pda_coverage, oxygen_vacancy_fraction=oxygen_vacancy_fraction)
    bead_types = {bead.name: index + 1 for index, bead in enumerate(field.beads)}
    bead_data = {bead.name: bead for bead in field.beads}
    lx, ly, lz = (float(value) for value in box_angstrom)
    atoms: list[tuple[int, int, int, float, float, float, float]] = []
    bonds: list[tuple[int, int, int, int]] = []
    bond_types = {(bond.first, bond.second): index + 1 for index, bond in enumerate(field.bonds)}
    bond_types.update({(bond.second, bond.first): index + 1 for index, bond in enumerate(field.bonds)})
    molecule = 0
    for start in range(0, resin_beads, chain_length):
        molecule += 1
        previous = None
        for index in range(min(chain_length, resin_beads - start)):
            atom_id = _add_atom(atoms, molecule, bead_types["R"], bead_data["R"].charge, 5 + (start // chain_length * 9) % (lx - 10), 5 + index * 4.2, 30 + (start // chain_length * 5) % (lz - 40))
            if previous is not None:
                bonds.append((len(bonds) + 1, bond_types[("R", "R")], previous, atom_id))
            previous = atom_id
    oxygen_sites = 6
    pda_per_particle = max(1, round(4 * np.clip(pda_coverage, 0, 1)))
    for particle in range(ceria_particles):
        molecule += 1
        cx, cy = 12 + (particle * 17) % (lx - 24), 12 + (particle * 13) % (ly - 24)
        ce_id = _add_atom(atoms, molecule, bead_types["Ce"], bead_data["Ce"].charge, cx, cy, 8.0)
        for site in range(oxygen_sites):
            angle = 2 * np.pi * site / oxygen_sites
            kind = "Ovac" if site < round(oxygen_sites * np.clip(oxygen_vacancy_fraction, 0, 0.30)) else "O"
            oxygen_id = _add_atom(atoms, molecule, bead_types[kind], bead_data[kind].charge, cx + 2.15 * np.cos(angle), cy + 2.15 * np.sin(angle), 8.0 + (1.1 if site % 2 else -1.1))
            bonds.append((len(bonds) + 1, bond_types[("Ce", kind)], ce_id, oxygen_id))
        for coat in range(pda_per_particle):
            angle = 2 * np.pi * coat / pda_per_particle
            carbon_id = _add_atom(atoms, molecule, bead_types["PDA_C"], bead_data["PDA_C"].charge, cx + 5.0 * np.cos(angle), cy + 5.0 * np.sin(angle), 10.5)
            nitrogen_id = _add_atom(atoms, molecule, bead_types["PDA_N"], bead_data["PDA_N"].charge, cx + 3.2 * np.cos(angle), cy + 3.2 * np.sin(angle), 9.2)
            bonds.append((len(bonds) + 1, bond_types[("PDA_C", "PDA_N")], carbon_id, nitrogen_id))
            bonds.append((len(bonds) + 1, bond_types[("PDA_N", "Ce")], nitrogen_id, ce_id))
    counts = {name: sum(atom[2] == bead_types[name] for atom in atoms) for name in bead_types}
    data = f"PDA@CeO2 coarse-grained interface\n\n{len(atoms)} atoms\n{len(bonds)} bonds\n{len(field.beads)} atom types\n{len(field.bonds)} bond types\n\n0.0 {lx:.3f} xlo xhi\n0.0 {ly:.3f} ylo yhi\n0.0 {lz:.3f} zlo zhi\n\nMasses\n\n"
    data += "\n".join(f"{bead_types[bead.name]} {bead.mass:.5f} # {bead.name}" for bead in field.beads)
    data += "\n\nAtoms # full\n\n" + "\n".join(f"{atom_id} {mol} {atom_type} {charge:.6f} {x:.6f} {y:.6f} {z:.6f}" for atom_id, mol, atom_type, charge, x, y, z in atoms)
    data += "\n\nBonds\n\n" + "\n".join(f"{bond_id} {bond_type} {left} {right}" for bond_id, bond_type, left, right in bonds) + "\n"
    pair_lines = "\n".join(f"pair_coeff {bead_types[pair.first]} {bead_types[pair.second]} {pair.epsilon_kcal_mol:.6f} {pair.sigma_angstrom:.6f}" for pair in field.pairs)
    bond_lines = "\n".join(f"bond_coeff {index + 1} {bond.spring_kcal_mol_a2:.6f} {bond.length_angstrom:.6f}" for index, bond in enumerate(field.bonds))
    script = f"""clear
units real
atom_style full
read_data interface.data
pair_style lj/cut/coul/long 12.0
kspace_style pppm 1.0e-4
pair_modify mix arithmetic
{pair_lines}
bond_style harmonic
{bond_lines}
neighbor 2.0 bin
thermo 1000
thermo_style custom step temp pe etotal vol press
fix ensemble all npt temp 300.0 300.0 100.0 iso 1.0 1.0 1000.0
run 100000
"""
    return CGInterfaceModel((lx, ly, lz), resin_beads, ceria_particles, float(pda_coverage), float(oxygen_vacancy_fraction), counts, field, data, script)


def write_cg_interface_model(model: CGInterfaceModel, output_dir: str | Path) -> dict[str, Path]:
    """Write LAMMPS data and input files for a CG interface run."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    data_path, input_path = directory / "interface.data", directory / "in.cg"
    data_path.write_text(model.lammps_data, encoding="utf-8")
    input_path.write_text(model.lammps_input, encoding="utf-8")
    return {"data": data_path, "input": input_path}
