from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import numpy as np


SECTION_NAMES = (
    "Masses", "Pair Coeffs", "Bond Coeffs", "Angle Coeffs", "Dihedral Coeffs",
    "Improper Coeffs", "Atoms # full", "Bonds", "Angles", "Dihedrals", "Impropers",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_data(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    indices = [(index, line.strip()) for index, line in enumerate(lines) if line.strip() in SECTION_NAMES]
    if not indices:
        raise ValueError(f"No LAMMPS sections found in {path}")
    preamble = lines[: indices[0][0]]
    sections: dict[str, list[str]] = {}
    for position, (start, name) in enumerate(indices):
        end = indices[position + 1][0] if position + 1 < len(indices) else len(lines)
        sections[name] = [line for line in lines[start + 1:end] if line.strip()]
    return preamble, sections


def _replace_header(preamble: list[str], added_atoms: int, zhi: float) -> list[str]:
    output = []
    for index, line in enumerate(preamble):
        if index == 0:
            output.append("GAFF2 resin plus CeO2(111)/PDA coordinate interface precursor")
        elif re.fullmatch(r"\s*\d+\s+atoms\s*", line):
            output.append(f"{int(line.split()[0]) + added_atoms} atoms")
        elif re.fullmatch(r"\s*\d+\s+atom types\s*", line):
            output.append("16 atom types")
        elif line.strip().endswith("zlo zhi"):
            output.append(f"0.0 {zhi:.8f} zlo zhi")
        else:
            output.append(line)
    return output


def build_interface(polymer_data: Path, output_dir: Path) -> dict[str, Path]:
    from adhesive_ai.vasp_production import build_ceo2_model

    surface, surface_metadata = build_ceo2_model(
        "(111)", objective="pda-resin-and-surface-binding",
    )
    preamble, sections = _parse_data(polymer_data)
    atom_lines = [line.split() for line in sections["Atoms # full"]]
    max_atom_id = max(int(fields[0]) for fields in atom_lines)
    max_molecule_id = max(int(fields[1]) for fields in atom_lines)

    positions = np.array(surface.positions, dtype=float)
    slab_mask = np.array(surface.get_tags()) == 1
    slab_center = positions[slab_mask, :2].mean(axis=0)
    positions[:, 0] += 45.0 - slab_center[0]
    positions[:, 1] += 45.0 - slab_center[1]
    positions[:, 2] += 3.0 - positions[slab_mask, 2].min()

    polymer_coordinates = np.array(
        [[float(fields[4]), float(fields[5]), float(fields[6])] for fields in atom_lines],
        dtype=float,
    )
    polymer_coordinates[:, 0] += 5.0 - polymer_coordinates[:, 0].min()
    polymer_coordinates[:, 1] += 5.0 - polymer_coordinates[:, 1].min()
    polymer_coordinates[:, 2] += positions[:, 2].max() + 3.0 - polymer_coordinates[:, 2].min()
    for fields, coordinate in zip(atom_lines, polymer_coordinates, strict=True):
        fields[4:7] = [f"{value:.10f}" for value in coordinate]
    zhi = float(max(polymer_coordinates[:, 2].max(), positions[:, 2].max()) + 5.0)

    atom_type_by_element = {"C": 3, "H": 5, "N": 9, "O": 13}
    added_lines = []
    slab_count = 0
    pda_count = 0
    for offset, atom in enumerate(surface):
        is_slab = atom.tag == 1
        if is_slab:
            atom_type = 15 if atom.symbol == "Ce" else 16
            charge = 4.0 if atom.symbol == "Ce" else -2.0
            molecule_id = max_molecule_id + 1
            slab_count += 1
        else:
            atom_type = atom_type_by_element[atom.symbol]
            charge = 0.0
            molecule_id = max_molecule_id + 2
            pda_count += 1
        x, y, z = positions[offset]
        added_lines.append(
            f"{max_atom_id + offset + 1} {molecule_id} {atom_type} {charge:.10f} "
            f"{x:.10f} {y:.10f} {z:.10f}"
        )

    sections["Atoms # full"] = [" ".join(fields) for fields in atom_lines] + added_lines
    sections["Masses"].extend(("15 140.11600000 # Ce4+ provisional core", "16 15.99900000 # O2- provisional ion"))
    sections["Pair Coeffs"].extend((
        "15 0.0100000000 3.0000000000 # Ce4+ provisional LJ readability term",
        "16 0.1500000000 3.0000000000 # O2- provisional LJ readability term",
    ))
    preamble = _replace_header(preamble, len(surface), zhi)

    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "interface.data"
    output_lines = preamble
    for section_name in SECTION_NAMES:
        if section_name not in sections:
            continue
        output_lines.extend((section_name, "", *sections[section_name], ""))
    data_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="ascii")

    forcefield_path = output_dir / "forcefield.production"
    forcefield_path.write_text(
        "# Static-readability precursor only; scientific production approval is intentionally blocked.\n"
        "pair_style lj/cut/coul/long 12.0\n"
        "pair_modify mix arithmetic\n"
        "bond_style harmonic\n"
        "angle_style harmonic\n"
        "dihedral_style fourier\n"
        "improper_style cvff\n"
        "special_bonds amber\n"
        "kspace_style pppm 1.0e-4\n",
        encoding="ascii",
    )
    validation_input = output_dir / "static_validation.in"
    validation_input.write_text(
        "clear\nunits real\natom_style full\nboundary p p p\n"
        "include forcefield.production\nread_data interface.data\nrun 0\n",
        encoding="ascii",
    )
    manifest = {
        "baseline_id": "odpa-oda-catechol-pdba-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolution": "all-atom coordinates with incomplete interface force-field topology",
        "model": "ODPA-ODA/DABA-dopamine DP8 resin plus CeO2(111)/dopamine-tetramer DFT baseline",
        "counts": {"resin_atoms": max_atom_id, "ceria_slab_atoms": slab_count, "pda_atoms": pda_count},
        "surface_metadata": surface_metadata,
        "scientific_status": "static-readability-precursor; interface-parameterization-pending",
        "production_approved": False,
        "limitations": [
            "PDA coordinates are present but PDA tetramer bonds and GAFF2/RESP terms are pending",
            "CeO2 uses formal charges and LJ readability terms, not the required IP10a shell model",
            "organic-CeO2 cross interactions await project VASP fitting",
            "resin is the un-crosslinked Gasteiger-charge precursor rather than the target PDBA/RESP network",
        ],
        "artifacts": {},
    }
    manifest_path = output_dir / "interface_input_manifest.json"
    for path in (data_path, forcefield_path, validation_input):
        manifest["artifacts"][path.name] = {"path": str(path), "sha256": _sha256(path)}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"data": data_path, "forcefield": forcefield_path, "validation": validation_input, "manifest": manifest_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a traceable atomistic resin/PDA@CeO2 interface precursor")
    parser.add_argument("--polymer-data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build_interface(args.polymer_data.resolve(), args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
