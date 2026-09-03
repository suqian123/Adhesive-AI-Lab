"""Versioned atomistic MD baseline for boronic-ester polyimide/CeO2 interfaces.

The baseline fixes chemical identities and parameterization requirements. It
does not promote generated structures to production until force-field and
property validation evidence has been approved.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors


MD_BASELINE_VERSION = "odpa-oda-catechol-pdba-v1"
MD_BASELINE_ROOT = Path("work/md_baselines") / MD_BASELINE_VERSION


@dataclass(frozen=True)
class ChemicalComponent:
    component_id: str
    name: str
    role: str
    smiles: str
    mole_fraction: float | None = None


@dataclass(frozen=True)
class MDStructureBaseline:
    baseline_id: str
    resolution: str
    resin_family: str
    chain_end: str
    repeat_units_per_chain: int
    chains_per_cell: int
    catechol_diamine_fraction: float
    dynamic_crosslinker: str
    components: tuple[ChemicalComponent, ...]
    organic_force_field: str
    organic_charge_model: str
    organic_sensitivity_force_field: str
    boronate_parameterization: str
    ceria_force_field: str
    interface_cross_interactions: str
    dynamic_bond_method: str
    scientific_status: str


DEFAULT_MD_BASELINE = MDStructureBaseline(
    baseline_id=MD_BASELINE_VERSION,
    resolution="all-atom",
    resin_family="ODPA-ODA polyimide with pendant catechol groups",
    chain_end="amino-terminated",
    repeat_units_per_chain=8,
    chains_per_cell=6,
    catechol_diamine_fraction=0.20,
    dynamic_crosslinker="1,4-phenylenediboronic acid (PDBA)",
    components=(
        ChemicalComponent(
            "ODPA",
            "4,4'-oxydiphthalic anhydride",
            "dianhydride",
            "O=C1OC(=O)c2ccc(Oc3ccc4C(=O)OC(=O)c4c3)cc12",
            1.0,
        ),
        ChemicalComponent(
            "ODA",
            "4,4'-oxydianiline",
            "primary diamine",
            "Nc1ccc(Oc2ccc(N)cc2)cc1",
            0.80,
        ),
        ChemicalComponent(
            "DABA_DA",
            "3,5-diaminobenzamide-dopamine conjugate",
            "catechol-bearing diamine",
            "Nc1cc(N)cc(C(=O)NCCc2ccc(O)c(O)c2)c1",
            0.20,
        ),
        ChemicalComponent(
            "PDBA",
            "1,4-phenylenediboronic acid",
            "dynamic bifunctional crosslinker",
            "OB(O)c1ccc(B(O)O)cc1",
            None,
        ),
        ChemicalComponent(
            "DOPAMINE",
            "dopamine",
            "PDA atomistic reference fragment",
            "NCCc1ccc(O)c(O)c1",
            None,
        ),
    ),
    organic_force_field="GAFF2 with custom boronate extension",
    organic_charge_model="RESP at HF/6-31G* electrostatic potential",
    organic_sensitivity_force_field="OPLS-AA with the same RESP charge targets",
    boronate_parameterization="QM equilibrium geometry, Hessian-derived bonded terms, and torsion scans",
    ceria_force_field="CeO2 IP10a shell-model Buckingham baseline",
    interface_cross_interactions="fit to project VASP adsorption and binding-energy surfaces",
    dynamic_bond_method="LAMMPS fix bond/react with mapped pre/post boronic-ester templates",
    scientific_status="structure-defined; force-field-validation-pending",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _component_molecule(component: ChemicalComponent) -> Chem.Mol:
    molecule = Chem.MolFromSmiles(component.smiles)
    if molecule is None:
        raise ValueError(f"Invalid baseline SMILES for {component.component_id}")
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = 20260901
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        raise RuntimeError(f"RDKit embedding failed for {component.component_id}")
    if AllChem.UFFHasAllMoleculeParams(molecule):
        AllChem.UFFOptimizeMolecule(molecule, maxIters=2000)
    molecule.SetProp("_Name", component.component_id)
    molecule.SetProp("baseline_role", component.role)
    molecule.SetProp("canonical_smiles", Chem.MolToSmiles(Chem.RemoveHs(molecule)))
    return molecule


def _component_by_id(component_id: str, baseline: MDStructureBaseline) -> ChemicalComponent:
    return next(component for component in baseline.components if component.component_id == component_id)


def _anhydride_groups(molecule: Chem.Mol) -> list[tuple[int, tuple[int, int]]]:
    groups = []
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() != 8 or atom.GetDegree() != 2:
            continue
        carbonyls = tuple(neighbor.GetIdx() for neighbor in atom.GetNeighbors())
        if not all(
            molecule.GetAtomWithIdx(index).GetAtomicNum() == 6
            and any(
                bond.GetBondType() == Chem.BondType.DOUBLE
                and bond.GetOtherAtom(molecule.GetAtomWithIdx(index)).GetAtomicNum() == 8
                for bond in molecule.GetAtomWithIdx(index).GetBonds()
            )
            for index in carbonyls
        ):
            continue
        groups.append((atom.GetIdx(), tuple(sorted(carbonyls))))
    return sorted(groups, key=lambda item: item[0])


def _primary_amine_indices(molecule: Chem.Mol) -> list[int]:
    indices = []
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() != 7:
            continue
        hydrogen_count = sum(neighbor.GetAtomicNum() == 1 for neighbor in atom.GetNeighbors())
        if hydrogen_count == 2:
            indices.append(atom.GetIdx())
    return sorted(indices)


def build_polyimide_oligomer(
    *,
    repeat_units: int = 8,
    catechol_diamine_fraction: float = 0.20,
    baseline: MDStructureBaseline = DEFAULT_MD_BASELINE,
    embed: bool = True,
) -> tuple[Chem.Mol, dict[str, Any]]:
    """Build an amino-terminated ODPA/ODA oligomer with pendant catechol diamines."""
    if repeat_units < 1:
        raise ValueError("repeat_units must be positive")
    if not 0.0 <= catechol_diamine_fraction <= 1.0:
        raise ValueError("catechol_diamine_fraction must be between zero and one")

    odpa = Chem.AddHs(Chem.MolFromSmiles(_component_by_id("ODPA", baseline).smiles))
    oda = Chem.AddHs(Chem.MolFromSmiles(_component_by_id("ODA", baseline).smiles))
    daba_da = Chem.AddHs(Chem.MolFromSmiles(_component_by_id("DABA_DA", baseline).smiles))
    diamine_count = repeat_units + 1
    catechol_count = min(diamine_count, max(1, round(diamine_count * catechol_diamine_fraction)))
    if catechol_count == diamine_count:
        catechol_positions = set(range(diamine_count))
    else:
        available = list(range(1, max(2, diamine_count - 1))) or [0]
        catechol_positions = {
            available[round(index * (len(available) - 1) / max(1, catechol_count - 1))]
            for index in range(catechol_count)
        }
        for position in range(diamine_count):
            if len(catechol_positions) >= catechol_count:
                break
            catechol_positions.add(position)

    fragments: list[Chem.Mol] = []
    fragment_kinds: list[str] = []
    for index in range(diamine_count):
        use_catechol = index in catechol_positions
        fragments.append(Chem.Mol(daba_da if use_catechol else oda))
        fragment_kinds.append("DABA_DA" if use_catechol else "ODA")
        if index < repeat_units:
            fragments.append(Chem.Mol(odpa))
            fragment_kinds.append("ODPA")

    combined = fragments[0]
    offsets = [0]
    for fragment in fragments[1:]:
        offsets.append(combined.GetNumAtoms())
        combined = Chem.CombineMols(combined, fragment)
    editable = Chem.RWMol(combined)
    remove_indices: set[int] = set()

    diamine_fragments = [index for index, kind in enumerate(fragment_kinds) if kind in {"ODA", "DABA_DA"}]
    odpa_fragments = [index for index, kind in enumerate(fragment_kinds) if kind == "ODPA"]
    for index, odpa_fragment_index in enumerate(odpa_fragments):
        odpa_groups = _anhydride_groups(fragments[odpa_fragment_index])
        if len(odpa_groups) != 2:
            raise RuntimeError(f"Expected two ODPA anhydride groups, found {len(odpa_groups)}")
        connection_specs = (
            (diamine_fragments[index], 1, odpa_groups[0]),
            (diamine_fragments[index + 1], 0, odpa_groups[1]),
        )
        for diamine_fragment_index, amine_position, (bridge_oxygen, carbonyls) in connection_specs:
            amines = _primary_amine_indices(fragments[diamine_fragment_index])
            if len(amines) != 2:
                raise RuntimeError(f"Expected two primary amines, found {len(amines)}")
            nitrogen = offsets[diamine_fragment_index] + amines[amine_position]
            hydrogen_indices = [
                neighbor.GetIdx()
                for neighbor in editable.GetAtomWithIdx(nitrogen).GetNeighbors()
                if neighbor.GetAtomicNum() == 1
            ]
            if len(hydrogen_indices) != 2:
                raise RuntimeError("Imide closure requires a primary amine with two hydrogens")
            for carbonyl in carbonyls:
                editable.AddBond(nitrogen, offsets[odpa_fragment_index] + carbonyl, Chem.BondType.SINGLE)
            remove_indices.update(hydrogen_indices)
            remove_indices.add(offsets[odpa_fragment_index] + bridge_oxygen)

    for atom_index in sorted(remove_indices, reverse=True):
        editable.RemoveAtom(atom_index)
    oligomer = editable.GetMol()
    Chem.SanitizeMol(oligomer)
    if embed:
        parameters = AllChem.ETKDGv3()
        parameters.randomSeed = 20260901
        parameters.useRandomCoords = True
        if AllChem.EmbedMolecule(oligomer, parameters) != 0:
            raise RuntimeError("RDKit failed to embed the polyimide oligomer")
        if AllChem.UFFHasAllMoleculeParams(oligomer):
            AllChem.UFFOptimizeMolecule(oligomer, maxIters=1000)
    oligomer.SetProp("_Name", f"ODPA-ODA-catechol-DP{repeat_units}")
    metadata = {
        "repeat_units": repeat_units,
        "diamine_count": diamine_count,
        "catechol_diamine_count": len(catechol_positions),
        "catechol_diamine_fraction": len(catechol_positions) / diamine_count,
        "chain_end": "amino-terminated",
        "formal_charge": int(Chem.GetFormalCharge(oligomer)),
        "molecular_formula": rdMolDescriptors.CalcMolFormula(oligomer),
        "atom_count": oligomer.GetNumAtoms(),
    }
    return oligomer, metadata


def baseline_contract(baseline: MDStructureBaseline = DEFAULT_MD_BASELINE) -> dict[str, Any]:
    components = []
    for component in baseline.components:
        molecule = Chem.MolFromSmiles(component.smiles)
        if molecule is None:
            raise ValueError(f"Invalid baseline SMILES for {component.component_id}")
        components.append({
            **asdict(component),
            "canonical_smiles": Chem.MolToSmiles(molecule),
            "molecular_formula": rdMolDescriptors.CalcMolFormula(molecule),
            "molecular_weight_g_mol": round(float(Descriptors.MolWt(molecule)), 6),
        })
    return {
        **asdict(baseline),
        "components": components,
        "reaction_contract": {
            "polyimide_formation": {
                "reactants": ["ODPA", "ODA or DABA_DA"],
                "operation": "two anhydride-to-imide closures per ODPA; retain terminal amines",
            },
            "dynamic_crosslink": {
                "reactants": ["DABA_DA catechol", "PDBA boronic acid"],
                "operation": "reversible five-membered catechol boronic ester formation/exchange",
                "topology_engine": "LAMMPS fix bond/react",
            },
            "interface": {
                "organic": "crosslinked resin plus dopamine tetramer PDA baseline",
                "inorganic": "CeO2 (111) slab with project vacancy/hydroxylation settings",
            },
        },
        "validation_contract": {
            "structure": [
                "all molecules pass RDKit sanitization",
                "formal charge is integral and cell charge is neutral",
                "crosslink fraction matches task target within 0.02",
            ],
            "force_field": [
                "no missing GAFF2 atom, bond, angle, improper, or torsion types",
                "boronate geometry and torsion scans meet QM fitting thresholds",
                "CeO2 lattice, surface, and vacancy properties pass IP10a reference checks",
            ],
            "bulk_md": [
                "three independently packed replicas per crosslink level",
                "density plateau and energy drift checks pass",
                "Tg, modulus, and CTE are compared with experimental references",
            ],
            "interface_md": [
                "three independently packed interface replicas",
                "no unphysical penetration or charge imbalance",
                "binding-energy trend is calibrated to project VASP results",
            ],
        },
        "references": [
            {"topic": "ODPA-ODA atomistic force-field comparison", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC13363942/"},
            {"topic": "boronate Amber parameterization", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7249141/"},
            {"topic": "CeO2 IP10a potential", "url": "https://doi.org/10.1021/acs.chemmater.2c03019"},
            {"topic": "LAMMPS bond/react", "url": "https://docs.lammps.org/fix_bond_react.html"},
        ],
    }


def toolchain_status() -> dict[str, Any]:
    import shutil

    commands = ("antechamber", "parmchk2", "tleap", "packmol")
    found = {command: shutil.which(command) for command in commands}
    return {
        "commands": found,
        "gaff2_ready": all(found[name] for name in ("antechamber", "parmchk2", "tleap")),
        "packing_ready": bool(found["packmol"]),
    }


def provision_bulk_md_inputs(
    task: Any,
    output_dir: str | Path,
    *,
    baseline_dir: str | Path = MD_BASELINE_ROOT,
) -> dict[str, Any]:
    """Provision a traceable pre-production resin cell without granting approval."""
    source_dir = Path(baseline_dir).expanduser().resolve() / "polyimide-cell"
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    sources = {
        "system.data": source_dir / "system.data",
        "forcefield.production": source_dir / "forcefield.production",
    }
    missing_sources = [name for name, path in sources.items() if not path.is_file()]
    if missing_sources:
        return {"provisioned": False, "missing_sources": missing_sources}

    copied = []
    for name, source in sources.items():
        destination = directory / name
        if not destination.exists():
            shutil.copy2(source, destination)
            copied.append(name)

    if hasattr(task, "conditions"):
        conditions = dict(task.conditions)
    else:
        conditions = dict(task.get("conditions") or {})
    manifest = {
        "baseline_id": MD_BASELINE_VERSION,
        "resolution": "all-atom",
        "model": "six-chain ODPA-ODA/DABA-dopamine DP8 amorphous pre-crosslink cell",
        "target_crosslink_density": conditions.get("crosslink_density"),
        "charge_model": "Gasteiger topology-development fallback",
        "scientific_status": "static-valid; crosslink-RESP-property-validation-pending",
        "production_approved": False,
        "limitations": [
            "PDBA crosslinker and boronic-ester reaction topology are not yet inserted",
            "RESP charges have not replaced topology-development fallback charges",
            "density, Tg, modulus, CTE, and replica convergence validation are pending",
        ],
        "artifacts": {
            name: {
                "source": str(source),
                "source_sha256": _sha256(source),
                "task_sha256": _sha256(directory / name),
            }
            for name, source in sources.items()
        },
    }
    manifest_path = directory / "md_input_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"provisioned": True, "copied": copied, "manifest": manifest_path}


def provision_interface_md_inputs(
    task: Any,
    output_dir: str | Path,
    *,
    baseline_dir: str | Path = MD_BASELINE_ROOT,
) -> dict[str, Any]:
    """Provision the versioned resin/PDA@CeO2 interface precursor."""
    source_dir = Path(baseline_dir).expanduser().resolve() / "interface-cell"
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    sources = {
        "interface.data": source_dir / "interface.data",
        "forcefield.production": source_dir / "forcefield.production",
    }
    missing_sources = [name for name, path in sources.items() if not path.is_file()]
    if missing_sources:
        return {"provisioned": False, "missing_sources": missing_sources}

    copied = []
    for name, source in sources.items():
        destination = directory / name
        if not destination.exists():
            shutil.copy2(source, destination)
            copied.append(name)
    if hasattr(task, "conditions"):
        conditions = dict(task.conditions)
    else:
        conditions = dict(task.get("conditions") or {})
    manifest = {
        "baseline_id": MD_BASELINE_VERSION,
        "resolution": "all-atom coordinates with incomplete interface force-field topology",
        "model": "DP8 resin plus CeO2(111)/dopamine-tetramer DFT coordinate baseline",
        "target_filler_pct": conditions.get("filler_pct"),
        "scientific_status": "static-valid; interface-parameterization-property-validation-pending",
        "production_approved": False,
        "limitations": [
            "PDA tetramer bonded GAFF2/RESP topology is pending",
            "CeO2 IP10a core-shell parameters are pending; present Ce/O terms are readability-only",
            "organic-inorganic cross interactions await VASP fitting",
            "resin PDBA crosslinks and RESP charges are pending",
        ],
        "artifacts": {
            name: {
                "source": str(source),
                "source_sha256": _sha256(source),
                "task_sha256": _sha256(directory / name),
            }
            for name, source in sources.items()
        },
    }
    manifest_path = directory / "md_input_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"provisioned": True, "copied": copied, "manifest": manifest_path}


def prepare_md_structure_baseline(
    output_dir: str | Path = "work/md_baselines/odpa-oda-catechol-pdba-v1",
    *,
    baseline: MDStructureBaseline = DEFAULT_MD_BASELINE,
    embed_oligomer: bool = True,
) -> dict[str, Any]:
    """Write versioned structures and parameterization/validation contracts."""
    directory = Path(output_dir).expanduser().resolve()
    component_dir = directory / "components"
    component_dir.mkdir(parents=True, exist_ok=True)

    contract = baseline_contract(baseline)
    contract_path = directory / "structure_contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

    paths: dict[str, Path] = {"contract": contract_path}
    for component in baseline.components:
        molecule = _component_molecule(component)
        sdf_path = component_dir / f"{component.component_id}.sdf"
        pdb_path = component_dir / f"{component.component_id}.pdb"
        writer = Chem.SDWriter(str(sdf_path))
        writer.write(molecule)
        writer.close()
        Chem.MolToPDBFile(molecule, str(pdb_path))
        paths[f"{component.component_id}_sdf"] = sdf_path
        paths[f"{component.component_id}_pdb"] = pdb_path

    oligomer, oligomer_metadata = build_polyimide_oligomer(
        repeat_units=baseline.repeat_units_per_chain,
        catechol_diamine_fraction=baseline.catechol_diamine_fraction,
        baseline=baseline,
        embed=embed_oligomer,
    )
    oligomer_dir = directory / "oligomers"
    oligomer_dir.mkdir(parents=True, exist_ok=True)
    oligomer_sdf = oligomer_dir / f"polyimide_dp{baseline.repeat_units_per_chain}.sdf"
    oligomer_pdb = oligomer_dir / f"polyimide_dp{baseline.repeat_units_per_chain}.pdb"
    writer = Chem.SDWriter(str(oligomer_sdf))
    writer.write(oligomer)
    writer.close()
    Chem.MolToPDBFile(oligomer, str(oligomer_pdb))
    oligomer_metadata_path = oligomer_dir / "oligomer_metadata.json"
    oligomer_metadata_path.write_text(json.dumps(oligomer_metadata, indent=2), encoding="utf-8")
    paths.update(
        oligomer_sdf=oligomer_sdf,
        oligomer_pdb=oligomer_pdb,
        oligomer_metadata=oligomer_metadata_path,
    )

    parameterization = {
        "baseline_id": baseline.baseline_id,
        "primary": {
            "organic_force_field": baseline.organic_force_field,
            "charge_model": baseline.organic_charge_model,
            "required_tools": ["AmberTools antechamber", "parmchk2", "tleap", "Packmol", "ParmEd"],
        },
        "sensitivity": baseline.organic_sensitivity_force_field,
        "boronate_qm_targets": {
            "geometry": "optimized reactant and cyclic ester structures",
            "hessian": "bond and angle force constants",
            "torsions": "B-O-C-C and O-B-O-C relaxed scans at 15 degree intervals",
        },
        "ceria": baseline.ceria_force_field,
        "interface_fit": baseline.interface_cross_interactions,
        "toolchain": toolchain_status(),
        "production_gate": "blocked until parameter and property validation reports are approved",
    }
    parameterization_path = directory / "parameterization_plan.json"
    parameterization_path.write_text(json.dumps(parameterization, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["parameterization_plan"] = parameterization_path

    reaction_template = {
        "method": "fix bond/react",
        "reaction": "catechol-boronic-ester exchange",
        "pre_template": "pending GAFF2/RESP atom typing",
        "post_template": "pending GAFF2/RESP atom typing",
        "mapping": "preserve B, catechol O/C, aromatic substituent, and local partial-charge total",
        "approved": False,
    }
    reaction_path = directory / "boronic_reaction_contract.json"
    reaction_path.write_text(json.dumps(reaction_template, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["reaction_contract"] = reaction_path

    manifest = {
        "baseline_id": baseline.baseline_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scientific_status": baseline.scientific_status,
        "production_approved": False,
        "artifacts": {
            name: {"path": str(path), "sha256": _sha256(path)}
            for name, path in sorted(paths.items())
        },
    }
    manifest_path = directory / "baseline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["manifest"] = manifest_path
    return {"baseline": baseline, "paths": paths, "manifest": manifest}
