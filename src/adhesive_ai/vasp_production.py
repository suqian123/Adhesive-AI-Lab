"""Reproducible baseline VASP models for CeO2/PDA calculations.

The generated structures are production *candidates*.  They remain marked as
pending until explicit convergence calculations have been parsed and approved.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from ase import Atoms
from ase.build import bulk, surface
from ase.constraints import FixAtoms
from ase.io import write
from ase.io import read as ase_read
from ase.neighborlist import neighbor_list
from rdkit import Chem
from rdkit.Chem import AllChem

from .vasp_resources import install_vasp_resources, load_vasp_resource_config, poscar_species, sha256_file


FACET_INDICES = {"(111)": (1, 1, 1), "(110)": (1, 1, 0), "(100)": (1, 0, 0)}
BASE_REPEATS = {"(111)": (2, 2, 1), "(110)": (3, 2, 1), "(100)": (2, 2, 1)}
PDA_REPEATS = {"(111)": (3, 3, 1), "(110)": (3, 3, 1), "(100)": (4, 3, 1)}
ELEMENT_ORDER = {name: index for index, name in enumerate(("Ce", "O", "C", "N", "H", "B"))}


@dataclass(frozen=True)
class VaspBaseline:
    lattice_constant_a: float = 5.411
    slab_layers: int = 3
    vacuum_a: float = 18.0
    cutoff_ev: int = 520
    ce_u_eff_ev: float = 4.5
    force_tolerance_ev_a: float = 0.02
    neb_images: int = 5
    ncore: int = 2
    kpar: int = 1
    ce_initial_moment: float = 1.0


def _surface_oxygen_indices(atoms: Atoms, tolerance_a: float = 0.35) -> list[int]:
    oxygen = [index for index, atom in enumerate(atoms) if atom.symbol == "O"]
    if not oxygen:
        raise ValueError("CeO2 slab contains no oxygen")
    top = max(float(atoms.positions[index, 2]) for index in oxygen)
    return [index for index in oxygen if top - float(atoms.positions[index, 2]) <= tolerance_a]


def _spaced_sites(atoms: Atoms, indices: Sequence[int], count: int) -> list[int]:
    """Select deterministic, laterally separated surface sites."""
    candidates = list(indices)
    if count <= 0:
        return []
    if count > len(candidates):
        raise ValueError("Requested more surface sites than are available")
    selected = [min(candidates, key=lambda index: tuple(atoms.positions[index, :2]))]
    lateral_cell = np.asarray(atoms.cell[:2, :2])
    lateral_inverse = np.linalg.inv(lateral_cell)

    def lateral_distance(first: int, second: int) -> float:
        fractional = (atoms.positions[first, :2] - atoms.positions[second, :2]) @ lateral_inverse
        fractional -= np.rint(fractional)
        return float(np.linalg.norm(fractional @ lateral_cell))

    while len(selected) < count:
        remaining = [index for index in candidates if index not in selected]
        selected.append(max(
            remaining,
            key=lambda index: min(
                lateral_distance(index, chosen)
                for chosen in selected
            ),
        ))
    return selected


def dopamine_tetramer() -> Atoms:
    """Build a deterministic catechol-rich, aryl-linked dopamine tetramer."""
    monomer = Chem.AddHs(Chem.MolFromSmiles("NCCc1ccc(O)c(O)c1"))
    aromatic_h_sites = [
        atom.GetIdx()
        for atom in monomer.GetAtoms()
        if atom.GetIsAromatic() and any(neighbor.GetAtomicNum() == 1 for neighbor in atom.GetNeighbors())
    ]
    if len(aromatic_h_sites) < 2:
        raise RuntimeError("Unable to identify dopamine oligomerization sites")
    left_site, right_site = aromatic_h_sites[0], aromatic_h_sites[-1]
    atom_count = monomer.GetNumAtoms()
    combined = monomer
    for _ in range(3):
        combined = Chem.CombineMols(combined, monomer)
    editable = Chem.RWMol(combined)
    hydrogens_to_remove: list[int] = []
    for unit in range(3):
        left = unit * atom_count + right_site
        right = (unit + 1) * atom_count + left_site
        for carbon in (left, right):
            hydrogen = next(
                neighbor.GetIdx()
                for neighbor in editable.GetAtomWithIdx(carbon).GetNeighbors()
                if neighbor.GetAtomicNum() == 1
            )
            hydrogens_to_remove.append(hydrogen)
        editable.AddBond(left, right, Chem.BondType.SINGLE)
    for index in sorted(hydrogens_to_remove, reverse=True):
        editable.RemoveAtom(index)
    molecule = editable.GetMol()
    Chem.SanitizeMol(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = 20260831
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        raise RuntimeError("RDKit failed to embed the dopamine tetramer")
    AllChem.UFFOptimizeMolecule(molecule, maxIters=1000)
    conformer = molecule.GetConformer()
    coordinates = np.array([list(conformer.GetAtomPosition(index)) for index in range(molecule.GetNumAtoms())])
    coordinates -= coordinates.mean(axis=0)
    _, eigenvectors = np.linalg.eigh(np.cov(coordinates.T))
    coordinates = coordinates @ eigenvectors[:, ::-1]
    coordinates[:, 2] -= coordinates[:, 2].min()
    return Atoms([atom.GetSymbol() for atom in molecule.GetAtoms()], positions=coordinates)


def build_ceo2_model(
    facet: str,
    *,
    vacancy_fraction: float = 0.0,
    hydroxyl_fraction: float = 0.0,
    objective: str = "atomic-oxygen-surface-chemistry",
    settings: VaspBaseline = VaspBaseline(),
    repeat_override: tuple[int, int, int] | None = None,
) -> tuple[Atoms, dict[str, Any]]:
    if facet not in FACET_INDICES:
        raise ValueError(f"Unsupported CeO2 facet: {facet}")
    if not 0.0 <= vacancy_fraction <= 0.30 or not 0.0 <= hydroxyl_fraction <= 1.0:
        raise ValueError("Vacancy and hydroxyl fractions are outside supported bounds")
    repeat = repeat_override or BASE_REPEATS[facet]
    if objective == "pda-resin-and-surface-binding" and repeat_override is None:
        repeat = PDA_REPEATS[facet]
    crystal = bulk("CeO2", "fluorite", a=settings.lattice_constant_a, cubic=True)
    slab = surface(crystal, FACET_INDICES[facet], settings.slab_layers, vacuum=settings.vacuum_a, periodic=True)
    slab = slab.repeat(repeat)
    slab.set_tags(np.ones(len(slab), dtype=int))

    original_sites = _surface_oxygen_indices(slab)
    vacancy_count = 0 if vacancy_fraction == 0 else max(1, int(round(vacancy_fraction * len(original_sites))))
    removed_sites = _spaced_sites(slab, original_sites, vacancy_count)
    vacancy_positions = [slab.positions[index].copy() for index in removed_sites]
    if removed_sites:
        del slab[sorted(removed_sites, reverse=True)]

    remaining_sites = _surface_oxygen_indices(slab)
    hydroxyl_count = 0 if hydroxyl_fraction == 0 else max(1, int(round(hydroxyl_fraction * len(remaining_sites))))
    protonated = _spaced_sites(slab, remaining_sites, hydroxyl_count)
    if protonated:
        hydrogens = Atoms(
            "H" * len(protonated),
            positions=[slab.positions[index] + (0.0, 0.0, 0.98) for index in protonated],
            tags=[2] * len(protonated),
        )
        slab += hydrogens

    adsorbate_index: int | None = None
    if objective == "atomic-oxygen-surface-chemistry":
        top_z = float(slab.positions[:, 2].max())
        if vacancy_positions:
            target = vacancy_positions[0]
        else:
            cerium = [index for index, atom in enumerate(slab) if atom.symbol == "Ce"]
            top_ce = max(float(slab.positions[index, 2]) for index in cerium)
            site = min(
                (index for index in cerium if top_ce - float(slab.positions[index, 2]) < 0.35),
                key=lambda index: tuple(slab.positions[index, :2]),
            )
            target = slab.positions[site].copy()
        target[2] = top_z + 1.80
        slab += Atoms("O", positions=[target], tags=[3])
        adsorbate_index = len(slab) - 1
    elif objective == "pda-resin-and-surface-binding":
        oligomer = dopamine_tetramer()
        oligomer.set_tags([4] * len(oligomer))
        cell_center = 0.5 * (slab.cell[0] + slab.cell[1])
        oligomer.positions[:, 0] += cell_center[0] - oligomer.positions[:, 0].mean()
        oligomer.positions[:, 1] += cell_center[1] - oligomer.positions[:, 1].mean()
        oligomer.positions[:, 2] += float(slab.positions[:, 2].max()) + 2.20
        slab += oligomer
    elif objective != "surface-convergence":
        raise ValueError(f"Unsupported DFT objective: {objective}")

    slab_z = slab.positions[np.array(slab.get_tags()) == 1, 2]
    fixed_cutoff = float(slab_z.min() + (slab_z.max() - slab_z.min()) / 3.0)
    slab.set_constraint(FixAtoms(indices=[
        index for index, atom in enumerate(slab)
        if atom.tag == 1 and float(atom.position[2]) <= fixed_cutoff
    ]))
    metadata = {
        "model": "CeO2-fluorite/PDA-dopamine-tetramer-baseline-v1",
        "scientific_status": "pending-convergence-validation",
        "facet": facet,
        "miller_indices": FACET_INDICES[facet],
        "repeat": repeat,
        "slab_layers": settings.slab_layers,
        "vacuum_a": settings.vacuum_a,
        "surface_oxygen_sites": len(original_sites),
        "requested_vacancy_fraction": vacancy_fraction,
        "actual_vacancy_fraction": vacancy_count / len(original_sites),
        "requested_hydroxyl_fraction": hydroxyl_fraction,
        "actual_hydroxyl_fraction": hydroxyl_count / len(remaining_sites),
        "vacancy_count": vacancy_count,
        "hydroxyl_count": hydroxyl_count,
        "adsorbate_index": adsorbate_index,
        "atom_count": len(slab),
    }
    return slab, metadata


def _ordered_atoms(atoms: Atoms) -> Atoms:
    indices = sorted(
        range(len(atoms)),
        key=lambda index: (ELEMENT_ORDER.get(atoms[index].symbol, 100), atoms[index].symbol, index),
    )
    return atoms[indices]


def _species_and_counts(atoms: Atoms) -> tuple[list[str], list[int]]:
    species: list[str] = []
    counts: list[int] = []
    for symbol in atoms.get_chemical_symbols():
        if not species or species[-1] != symbol:
            species.append(symbol)
            counts.append(1)
        else:
            counts[-1] += 1
    return species, counts


def _kpoint_mesh(atoms: Atoms) -> tuple[int, int, int]:
    lengths = atoms.cell.lengths()
    return max(1, int(round(20.0 / lengths[0]))), max(1, int(round(20.0 / lengths[1]))), 1


def _incar(
    atoms: Atoms,
    species: Sequence[str],
    *,
    settings: VaspBaseline,
    neb: bool = False,
    static: bool = False,
) -> str:
    ldau_l = [3 if symbol == "Ce" else -1 for symbol in species]
    ldau_u = [settings.ce_u_eff_ev if symbol == "Ce" else 0.0 for symbol in species]
    magnetic = [
        2.0 if atom.symbol == "O" and atom.tag == 3
        else settings.ce_initial_moment if atom.symbol == "Ce"
        else 0.0
        for atom in atoms
    ]
    values: list[tuple[str, object]] = [
        ("SYSTEM", "PDA@CeO2 production-baseline"), ("PREC", "Accurate"),
        ("ENCUT", settings.cutoff_ev), ("EDIFF", "1E-6"), ("EDIFFG", f"-{settings.force_tolerance_ev_a:g}"),
        ("ISPIN", 2), ("MAGMOM", " ".join(f"{value:g}" for value in magnetic)),
        ("ISMEAR", 0), ("SIGMA", 0.05), ("ALGO", "Normal"), ("NELM", 160), ("NELMDL", -5),
        ("AMIX", 0.2), ("BMIX", 0.0001), ("AMIX_MAG", 0.8), ("BMIX_MAG", 0.0001),
        ("NCORE", settings.ncore), ("KPAR", settings.kpar),
        ("ISIF", 2), ("IBRION", -1 if static else 3 if neb else 2), ("NSW", 0 if static else 200),
        ("ISYM", 0), ("LREAL", "Auto"), ("LASPH", ".TRUE."), ("ADDGRID", ".TRUE."),
        ("LWAVE", ".FALSE."), ("LCHARG", ".TRUE."), ("LORBIT", 11),
        ("LDIPOL", ".TRUE."), ("IDIPOL", 3),
        ("LDAU", ".TRUE."), ("LDAUTYPE", 2),
        ("LDAUL", " ".join(map(str, ldau_l))),
        ("LDAUU", " ".join(f"{value:g}" for value in ldau_u)),
        ("LDAUJ", " ".join("0" for _ in species)), ("LMAXMIX", 6),
        ("GGA", "PE"), ("IVDW", 12),
    ]
    if neb:
        values.extend((("IMAGES", settings.neb_images), ("SPRING", -5), ("LCLIMB", ".TRUE."), ("IOPT", 3)))
    return "\n".join(f"{key} = {value}" for key, value in values) + "\n"


def write_vasp_model(
    atoms: Atoms,
    output_dir: str | Path,
    *,
    metadata: Mapping[str, Any],
    resources: str | Path = "work/vasp_resources.json",
    settings: VaspBaseline = VaspBaseline(),
    cutoff_ev: int | None = None,
    kpoints: tuple[int, int, int] | None = None,
    static: bool = False,
) -> dict[str, Any]:
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    ordered = _ordered_atoms(atoms)
    species, counts = _species_and_counts(ordered)
    selected_settings = VaspBaseline(**{**settings.__dict__, **({"cutoff_ev": cutoff_ev} if cutoff_ev else {})})
    write(directory / "POSCAR", ordered, format="vasp", direct=True, vasp5=True, ignore_constraints=False)
    (directory / "INCAR").write_text(_incar(ordered, species, settings=selected_settings, static=static), encoding="utf-8")
    mesh = kpoints or _kpoint_mesh(ordered)
    (directory / "KPOINTS").write_text(
        f"Gamma mesh\n0\nGamma\n{mesh[0]} {mesh[1]} {mesh[2]}\n0 0 0\n", encoding="utf-8",
    )
    installed = install_vasp_resources(directory, resources)
    manifest = {
        **dict(metadata), "species": species, "species_counts": counts,
        "functional": "PBE-D3(BJ)", "ce_u_eff_ev": selected_settings.ce_u_eff_ev,
        "vdw_kernel_installed": True, "vdw_kernel_used": False,
        "encut_ev": selected_settings.cutoff_ev, "kpoints": mesh,
        "resource_hashes": {
            "potcar_sha256": installed["potcar_sha256"],
            "vdw_kernel_sha256": installed["vdw_kernel_sha256"],
        },
    }
    (directory / "input_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return manifest


def write_neb_model(
    atoms: Atoms,
    output_dir: str | Path,
    *,
    metadata: Mapping[str, Any],
    resources: str | Path = "work/vasp_resources.json",
    settings: VaspBaseline = VaspBaseline(),
) -> dict[str, Any]:
    adsorbates = [index for index, atom in enumerate(atoms) if atom.symbol == "O" and atom.tag == 3]
    if len(adsorbates) != 1:
        raise ValueError("NEB baseline requires exactly one tagged atomic-oxygen adsorbate")
    final = _ordered_atoms(atoms)
    adsorbate = next(index for index, atom in enumerate(final) if atom.symbol == "O" and atom.tag == 3)
    initial = final.copy()
    initial.positions[adsorbate, 2] += 3.0
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    species, _ = _species_and_counts(final)
    (directory / "INCAR").write_text(_incar(final, species, settings=settings, neb=True), encoding="utf-8")
    mesh = _kpoint_mesh(final)
    (directory / "KPOINTS").write_text(
        f"Gamma mesh\n0\nGamma\n{mesh[0]} {mesh[1]} {mesh[2]}\n0 0 0\n", encoding="utf-8",
    )
    total_images = settings.neb_images + 2
    for image in range(total_images):
        fraction = image / (total_images - 1)
        interpolated = initial.copy()
        interpolated.positions = initial.positions + fraction * (final.positions - initial.positions)
        image_dir = directory / f"{image:02d}"
        image_dir.mkdir(parents=True, exist_ok=True)
        write(image_dir / "POSCAR", interpolated, format="vasp", direct=True, vasp5=True, ignore_constraints=False)
    installed = install_vasp_resources(directory, resources)
    manifest = {
        **dict(metadata), "workflow": "CI-NEB", "images": settings.neb_images,
        "functional": "PBE-D3(BJ)", "ce_u_eff_ev": settings.ce_u_eff_ev,
        "vdw_kernel_installed": True, "vdw_kernel_used": False,
        "initial_adsorbate_height_delta_a": 3.0, "kpoints": mesh,
        "scientific_status": "pending-endpoint-relaxation-and-convergence",
        "resource_hashes": {
            "potcar_sha256": installed["potcar_sha256"],
            "vdw_kernel_sha256": installed["vdw_kernel_sha256"],
        },
    }
    (directory / "input_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return manifest


def prepare_campaign_dft_task(
    task: Mapping[str, Any],
    output_dir: str | Path,
    *,
    resources: str | Path = "work/vasp_resources.json",
    settings: VaspBaseline = VaspBaseline(),
) -> dict[str, Any]:
    conditions = dict(task.get("conditions", {}))
    objective = str(task["objective"])
    atoms, structure = build_ceo2_model(
        str(conditions.get("facet", "(111)")),
        vacancy_fraction=float(conditions.get("oxygen_vacancy_fraction", 0.0)),
        hydroxyl_fraction=float(conditions.get("hydroxyl_fraction", 0.0)),
        objective=objective,
        settings=settings,
    )
    metadata = {"task_id": task.get("task_id"), "objective": objective, **structure}
    manifest = write_vasp_model(atoms, output_dir, metadata=metadata, resources=resources, settings=settings)
    if objective == "atomic-oxygen-surface-chemistry":
        manifest["neb"] = write_neb_model(
            atoms, Path(output_dir) / "neb", metadata=metadata, resources=resources, settings=settings,
        )
    return manifest


def write_convergence_suite(
    output_root: str | Path,
    *,
    facet: str = "(111)",
    resources: str | Path = "work/vasp_resources.json",
) -> dict[str, Any]:
    """Write a facet-specific surface convergence matrix without launching VASP."""
    if facet not in FACET_INDICES:
        raise ValueError(f"Unsupported CeO2 facet: {facet}")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    jobs: list[dict[str, Any]] = []

    baseline = VaspBaseline(ncore=2, kpar=1, ce_initial_moment=0.0)
    convergence_repeat = (1, 1, 1)
    atoms, metadata = build_ceo2_model(
        facet, objective="surface-convergence", settings=baseline,
        repeat_override=convergence_repeat,
    )
    for cutoff in (450, 520, 600):
        directory = root / "encut" / str(cutoff)
        manifest = write_vasp_model(
            atoms, directory, metadata={**metadata, "validation_axis": "encut"},
            resources=resources, settings=baseline, cutoff_ev=cutoff, kpoints=(1, 1, 1), static=True,
        )
        jobs.append({"path": str(directory), **manifest})
    for mesh in ((1, 1, 1), (2, 2, 1), (3, 3, 1)):
        directory = root / "kpoints" / "x".join(map(str, mesh))
        manifest = write_vasp_model(
            atoms, directory, metadata={**metadata, "validation_axis": "kpoints"},
            resources=resources, settings=baseline, kpoints=mesh, static=True,
        )
        jobs.append({"path": str(directory), **manifest})
    for layers in (2, 3, 4):
        settings = VaspBaseline(slab_layers=layers, ncore=2, kpar=1, ce_initial_moment=0.0)
        candidate, candidate_metadata = build_ceo2_model(
            facet, objective="surface-convergence", settings=settings,
            repeat_override=convergence_repeat,
        )
        directory = root / "slab-layers" / str(layers)
        manifest = write_vasp_model(
            candidate, directory, metadata={**candidate_metadata, "validation_axis": "slab_layers"},
            resources=resources, settings=settings, kpoints=(1, 1, 1), static=True,
        )
        jobs.append({"path": str(directory), **manifest})
    for vacuum in (15.0, 18.0, 22.0):
        settings = VaspBaseline(vacuum_a=vacuum, ncore=2, kpar=1, ce_initial_moment=0.0)
        candidate, candidate_metadata = build_ceo2_model(
            facet, objective="surface-convergence", settings=settings,
            repeat_override=convergence_repeat,
        )
        directory = root / "vacuum" / f"{vacuum:g}A"
        manifest = write_vasp_model(
            candidate, directory, metadata={**candidate_metadata, "validation_axis": "vacuum"},
            resources=resources, settings=settings, kpoints=(1, 1, 1), static=True,
        )
        jobs.append({"path": str(directory), **manifest})

    plan = {
        "scientific_status": "pending-convergence-calculations",
        "facet": facet,
        "job_count": len(jobs),
        "acceptance": {
            "total_energy_change_ev_per_atom_max": 0.001,
            "force_ev_a_max": baseline.force_tolerance_ev_a,
            "convergence_comparison": "final-two-points",
            "bulk_lattice_and_u_eff_status": "user-confirmed-assumptions-not-fitted",
            "require_neb_endpoint_relaxation": True,
            "slab_second_difference_ev_max": 0.05,
        },
        "jobs": jobs,
    }
    (root / "validation_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    return plan


def validate_vasp_input_set(
    directory: str | Path,
    *,
    resources: str | Path = "work/vasp_resources.json",
) -> dict[str, Any]:
    """Perform static structural and resource checks without claiming convergence."""
    root = Path(directory).expanduser().resolve()
    required = ("POSCAR", "INCAR", "KPOINTS", "POTCAR", "vdw_kernel.bindat", "input_manifest.json")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        return {"directory": str(root), "valid": False, "missing": missing}
    atoms = ase_read(root / "POSCAR", format="vasp")
    close_i, close_j = neighbor_list("ij", atoms, cutoff=0.60)
    species = poscar_species(root / "POSCAR")
    titles = []
    for line in (root / "POTCAR").read_text(encoding="utf-8", errors="replace").splitlines():
        if "TITEL" in line and "=" in line:
            words = line.split("=", 1)[1].split()
            titles.append(next((word for word in words if word in species), ""))
    incar = (root / "INCAR").read_text(encoding="utf-8")
    required_tags = ("LDAU = .TRUE.", "IVDW = 12", "GGA = PE", "LDIPOL = .TRUE.")
    resource_config = load_vasp_resource_config(resources)
    checks = {
        "no_contacts_below_0_60_a": len(close_i) == 0 and len(close_j) == 0,
        "potcar_species_order": titles == species,
        "vdw_kernel_checksum": sha256_file(root / "vdw_kernel.bindat") == resource_config["vdw_kernel_sha256"],
        "required_incar_tags": all(tag in incar for tag in required_tags),
    }
    neb = root / "neb"
    if neb.is_dir():
        image_dirs = sorted(path for path in neb.iterdir() if path.is_dir() and path.name.isdigit())
        checks["neb_images_complete"] = len(image_dirs) >= 3 and all((path / "POSCAR").is_file() for path in image_dirs)
    return {
        "directory": str(root), "valid": all(checks.values()), "atom_count": len(atoms),
        "species": species, "potcar_titles": titles, "checks": checks,
        "scientific_status": "static-input-valid; pending-convergence-validation",
    }
