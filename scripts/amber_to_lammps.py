from __future__ import annotations

import argparse
import math
from pathlib import Path

import parmed as pmd


def _type_map(items, key):
    values = sorted({key(item) for item in items})
    return {value: index + 1 for index, value in enumerate(values)}


def _phase_sign(phase: float) -> int:
    normalized = float(phase) % 360.0
    if min(abs(normalized), abs(normalized - 360.0)) < 1.0e-2:
        return 1
    if abs(normalized - 180.0) < 1.0e-2:
        return -1
    raise ValueError(f"CVFF improper requires a 0/180 degree phase, received {phase}")


def convert(prmtop: Path, coordinates: Path, output: Path) -> None:
    structure = pmd.load_file(str(prmtop), str(coordinates))
    atom_key = lambda atom: str(atom.type)
    bond_key = lambda bond: (round(float(bond.type.k), 8), round(float(bond.type.req), 8))
    angle_key = lambda angle: (round(float(angle.type.k), 8), round(float(angle.type.theteq), 8))
    dihedral_key = lambda item: (
        round(float(item.type.phi_k), 8), int(round(float(item.type.per))), round(float(item.type.phase), 6),
    )
    improper_key = lambda item: (
        round(float(item.type.phi_k), 8), _phase_sign(float(item.type.phase)), int(round(float(item.type.per))),
    )

    proper_dihedrals = [item for item in structure.dihedrals if not item.improper]
    improper_dihedrals = [item for item in structure.dihedrals if item.improper]
    atom_types = _type_map(structure.atoms, atom_key)
    bond_types = _type_map(structure.bonds, bond_key)
    angle_types = _type_map(structure.angles, angle_key)
    dihedral_types = _type_map(proper_dihedrals, dihedral_key)
    improper_types = _type_map(improper_dihedrals, improper_key)

    box = structure.box
    if box is None or any(abs(float(angle) - 90.0) > 1.0e-6 for angle in box[3:]):
        raise ValueError("Only orthorhombic Amber cells are supported")
    coordinates_array = structure.coordinates
    if coordinates_array is None:
        raise ValueError("Amber coordinates are missing")

    representative_atoms = {}
    for atom in structure.atoms:
        representative_atoms.setdefault(atom_key(atom), atom)

    lines = [
        "GAFF2 polyimide topology converted from Amber by amber_to_lammps.py",
        "",
        f"{len(structure.atoms)} atoms",
        f"{len(structure.bonds)} bonds",
        f"{len(structure.angles)} angles",
        f"{len(proper_dihedrals)} dihedrals",
        f"{len(improper_dihedrals)} impropers",
        "",
        f"{len(atom_types)} atom types",
        f"{len(bond_types)} bond types",
        f"{len(angle_types)} angle types",
        f"{len(dihedral_types)} dihedral types",
        f"{len(improper_types)} improper types",
        "",
        f"0.0 {float(box[0]):.8f} xlo xhi",
        f"0.0 {float(box[1]):.8f} ylo yhi",
        f"0.0 {float(box[2]):.8f} zlo zhi",
        "",
        "Masses",
        "",
    ]
    for name, type_id in sorted(atom_types.items(), key=lambda item: item[1]):
        atom = representative_atoms[name]
        lines.append(f"{type_id} {float(atom.mass):.8f} # {name}")

    lines.extend(["", "Pair Coeffs", ""])
    for name, type_id in sorted(atom_types.items(), key=lambda item: item[1]):
        atom_type = representative_atoms[name].atom_type
        epsilon = float(atom_type.epsilon)
        sigma = 2.0 * float(atom_type.rmin) / (2.0 ** (1.0 / 6.0))
        lines.append(f"{type_id} {epsilon:.10f} {sigma:.10f} # {name}")

    lines.extend(["", "Bond Coeffs", ""])
    for (force, equilibrium), type_id in sorted(bond_types.items(), key=lambda item: item[1]):
        lines.append(f"{type_id} {force:.10f} {equilibrium:.10f}")

    lines.extend(["", "Angle Coeffs", ""])
    for (force, equilibrium), type_id in sorted(angle_types.items(), key=lambda item: item[1]):
        lines.append(f"{type_id} {force:.10f} {equilibrium:.10f}")

    lines.extend(["", "Dihedral Coeffs", ""])
    for (force, periodicity, phase), type_id in sorted(dihedral_types.items(), key=lambda item: item[1]):
        lines.append(f"{type_id} 1 {force:.10f} {periodicity} {phase:.10f}")

    lines.extend(["", "Improper Coeffs", ""])
    for (force, sign, periodicity), type_id in sorted(improper_types.items(), key=lambda item: item[1]):
        lines.append(f"{type_id} {force:.10f} {sign} {periodicity}")

    lines.extend(["", "Atoms # full", ""])
    for atom in structure.atoms:
        x, y, z = coordinates_array[atom.idx]
        molecule_id = int(atom.residue.idx) + 1
        lines.append(
            f"{atom.idx + 1} {molecule_id} {atom_types[atom_key(atom)]} {float(atom.charge):.10f} "
            f"{float(x):.10f} {float(y):.10f} {float(z):.10f}"
        )

    lines.extend(["", "Bonds", ""])
    for index, bond in enumerate(structure.bonds, start=1):
        lines.append(
            f"{index} {bond_types[bond_key(bond)]} {bond.atom1.idx + 1} {bond.atom2.idx + 1}"
        )

    lines.extend(["", "Angles", ""])
    for index, angle in enumerate(structure.angles, start=1):
        lines.append(
            f"{index} {angle_types[angle_key(angle)]} {angle.atom1.idx + 1} "
            f"{angle.atom2.idx + 1} {angle.atom3.idx + 1}"
        )

    lines.extend(["", "Dihedrals", ""])
    for index, item in enumerate(proper_dihedrals, start=1):
        lines.append(
            f"{index} {dihedral_types[dihedral_key(item)]} {item.atom1.idx + 1} {item.atom2.idx + 1} "
            f"{item.atom3.idx + 1} {item.atom4.idx + 1}"
        )

    lines.extend(["", "Impropers", ""])
    for index, item in enumerate(improper_dihedrals, start=1):
        lines.append(
            f"{index} {improper_types[improper_key(item)]} {item.atom1.idx + 1} {item.atom2.idx + 1} "
            f"{item.atom3.idx + 1} {item.atom4.idx + 1}"
        )
    output.write_text("\n".join(lines) + "\n", encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an Amber topology to a LAMMPS full-style data file")
    parser.add_argument("prmtop", type=Path)
    parser.add_argument("coordinates", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    convert(args.prmtop, args.coordinates, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

