"""Traceable molecular precursors for candidate CL5-00016.

These structures define composition for model development. They are not a
crosslinked LAMMPS topology, a validated force field, or production approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors


CANDIDATE_ID = "CL5-00016"
FORMULATION_ID = "FMT-553A4410C5387F8A"
BASELINE_ID = "cl5-00016-badcy-dcdds-ctbn-v1"
BADCY_SMILES = "CC(C)(c1ccc(OC#N)cc1)c1ccc(OC#N)cc1"
DCDDS_SMILES = "N#COc1ccc(SSc2ccc(OC#N)cc2)cc1"
CTBN_BUTADIENE_UNITS = 42
CTBN_ACRYLONITRILE_UNITS = 15
CTBN_TARGET_PHR = 5.0
CYANATE_PATTERN = Chem.MolFromSmarts("[O]-[C]#[N]")
DISULFIDE_PATTERN = Chem.MolFromSmarts("[S]-[S]")
CARBOXYL_PATTERN = Chem.MolFromSmarts("[CX3](=[OX1])[OX2H1]")


def representative_ctbn_smiles(
    butadiene_units: int = CTBN_BUTADIENE_UNITS,
    acrylonitrile_units: int = CTBN_ACRYLONITRILE_UNITS,
) -> str:
    """Construct one idealized 1,4-butadiene/AN chain with two acid ends."""
    if butadiene_units < 1 or acrylonitrile_units < 1:
        raise ValueError("Both CTBN repeat counts must be positive")
    total = butadiene_units + acrylonitrile_units
    repeats = []
    for index in range(total):
        is_an = ((index + 1) * acrylonitrile_units) // total > (
            index * acrylonitrile_units
        ) // total
        repeats.append("CC(C#N)" if is_an else "CC=CC")
    smiles = "O=C(O)C" + "".join(repeats) + "C(=O)O"
    if Chem.MolFromSmiles(smiles) is None:
        raise ValueError("Representative CTBN chain could not be sanitized")
    return smiles


def _molecule(smiles: str) -> Chem.Mol:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"Invalid molecular structure: {smiles}")
    return molecule


def _identity(smiles: str) -> dict[str, object]:
    molecule = _molecule(smiles)
    return {
        "canonical_smiles": Chem.MolToSmiles(molecule),
        "formula": rdMolDescriptors.CalcMolFormula(molecule),
        "molecular_weight_g_mol": round(Descriptors.MolWt(molecule), 3),
        "cyanate_groups": len(molecule.GetSubstructMatches(CYANATE_PATTERN)),
        "disulfide_bonds": len(molecule.GetSubstructMatches(DISULFIDE_PATTERN)),
        "carboxyl_groups": len(molecule.GetSubstructMatches(CARBOXYL_PATTERN)),
    }


def composition_contract() -> dict[str, object]:
    """Give exact input identities and a finite-cell composition proposal."""
    badcy = _identity(BADCY_SMILES)
    dcdds = _identity(DCDDS_SMILES)
    ctbn_smiles = representative_ctbn_smiles()
    ctbn = _identity(ctbn_smiles)
    if (badcy["formula"], dcdds["formula"]) != ("C17H14N2O2", "C14H8N2O2S2"):
        raise ValueError("Cyanate monomer formula changed")
    if (badcy["cyanate_groups"], dcdds["cyanate_groups"], dcdds["disulfide_bonds"]) != (2, 2, 1):
        raise ValueError("Required cyanate/disulfide motifs are missing")
    if ctbn["carboxyl_groups"] != 2 or ctbn["disulfide_bonds"] != 0:
        raise ValueError("Representative CTBN end groups are incorrect")

    badcy_mass = float(badcy["molecular_weight_g_mol"])
    dcdds_mass = float(dcdds["molecular_weight_g_mol"])
    ctbn_mass = float(ctbn["molecular_weight_g_mol"])
    mole_fraction = (79.0 / badcy_mass) / (79.0 / badcy_mass + 21.0 / dcdds_mass)
    badcy_count, dcdds_count, ctbn_count = 181, 44, 1
    resin_mass = badcy_count * badcy_mass + dcdds_count * dcdds_mass
    total_cyanate_sites = 2 * (badcy_count + dcdds_count)
    conversion_targets = []
    for task_id, target in (("md-resin-x1", 0.798), ("md-resin-x2", 0.948), ("md-resin-x3", 1.0)):
        triazine_count = min(total_cyanate_sites // 3, round(target * total_cyanate_sites / 3))
        reacted = 3 * triazine_count
        conversion_targets.append({
            "task_id": task_id, "target_conversion": target,
            "triazine_rings": triazine_count, "reacted_cyanate_sites": reacted,
            "unreacted_cyanate_sites": total_cyanate_sites - reacted,
            "realizable_conversion": round(reacted / total_cyanate_sites, 6),
        })
    return {
        "baseline_id": BASELINE_ID,
        "candidate_id": CANDIDATE_ID,
        "formulation_id": FORMULATION_ID,
        "resin": "CE",
        "dynamic_unit": "Disulfide",
        "scientific_status": "molecular-precursors-defined; network-forcefield-validation-pending",
        "production_approved": False,
        "feed": {
            "basis": "100 mass parts of the two cyanate ester monomers",
            "BADCy_mass_parts": 79.0,
            "DCDDS_mass_parts": 21.0,
            "CTBN_phr": CTBN_TARGET_PHR,
            "BADCy_mole_fraction_among_cyanate_monomers": round(mole_fraction, 6),
        },
        "monomers": {
            "BADCy": {"name": "bisphenol A dicyanate", **badcy},
            "DCDDS": {
                "name": "4,4'-dicyanatodiphenyl disulfide",
                **dcdds,
            },
        },
        "CTBN": {
            "grade": "Hypro 1300X13 CTBN",
            "supplier_typical": {
                "Mn_g_mol": 3150,
                "bound_acrylonitrile_wt_pct": 26.0,
                "average_carboxyl_functionality": 1.8,
                "source": "https://products.huntsman.com/products/hypro-1300x13-ctbn",
                "batch_TDS_or_CoA_required": True,
            },
            "representative_chain": {
                **ctbn,
                "butadiene_repeat_units": CTBN_BUTADIENE_UNITS,
                "acrylonitrile_repeat_units": CTBN_ACRYLONITRILE_UNITS,
                "acrylonitrile_repeat_mass_fraction": round(
                    CTBN_ACRYLONITRILE_UNITS * 53.064 /
                    (CTBN_ACRYLONITRILE_UNITS * 53.064 + CTBN_BUTADIENE_UNITS * 54.092),
                    6,
                ),
                "assumptions": [
                    "all butadiene units represented as 1,4 links without assigned cis/trans stereochemistry",
                    "acrylonitrile units evenly spaced; actual product has a distribution",
                    "one molecule has two carboxyl ends; supplier functionality 1.8 is an ensemble average",
                    "no CTBN-cyanate covalent coupling assigned",
                ],
            },
        },
        "finite_cell_proposal": {
            "BADCy_molecules": badcy_count,
            "DCDDS_molecules": dcdds_count,
            "representative_CTBN_chains": ctbn_count,
            "BADCy_wt_pct_in_resin": round(100 * badcy_count * badcy_mass / resin_mass, 4),
            "DCDDS_wt_pct_in_resin": round(100 * dcdds_count * dcdds_mass / resin_mass, 4),
            "CTBN_phr": round(100 * ctbn_count * ctbn_mass / resin_mass, 4),
            "available_cyanate_sites": total_cyanate_sites,
            "crosslink_conversion_interpretation": "user-approved modeling assumption: fraction of cyanate sites trimerized",
            "conversion_targets": conversion_targets,
        },
        "production_gaps": [
            "experimental verification of assumed cyanate conversion targets",
            "cyanate trimerization network topology and validated bonded parameters",
            "RESP or equivalently validated charges for all reacted/unreacted species",
            "CTBN sequence, stereochemistry, batch acid value, and coupling/phase morphology",
            "disulfide exchange reaction model and validation",
            "equilibration, density, mechanical, thermal, and replica validation",
            "PDA@CeO2 interface force field and approved DFT reference surfaces",
        ],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_3d(molecule: Chem.Mol, sdf_path: Path, pdb_path: Path, seed: int) -> str:
    with_hydrogens = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = seed
    if AllChem.EmbedMolecule(with_hydrogens, parameters) != 0:
        raise RuntimeError(f"3D embedding failed: {sdf_path.name}")
    if AllChem.MMFFHasAllMoleculeParams(with_hydrogens):
        AllChem.MMFFOptimizeMolecule(with_hydrogens, maxIters=1000)
        geometry_method = "ETKDGv3 + MMFF94"
    elif AllChem.UFFHasAllMoleculeParams(with_hydrogens):
        AllChem.UFFOptimizeMolecule(with_hydrogens, maxIters=1000)
        geometry_method = "ETKDGv3 + UFF"
    else:
        geometry_method = "ETKDGv3 only"
    writer = Chem.SDWriter(str(sdf_path))
    writer.write(with_hydrogens)
    writer.close()
    Chem.MolToPDBFile(with_hydrogens, str(pdb_path))
    if Chem.SDMolSupplier(str(sdf_path), removeHs=False)[0] is None:
        raise RuntimeError(f"Generated SDF is unreadable: {sdf_path}")
    return geometry_method


def prepare_baseline(output_dir: str | Path) -> dict[str, object]:
    """Write deterministic monomer geometry and a non-production contract."""
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    components_dir = directory / "components"
    components_dir.mkdir(exist_ok=True)
    contract = composition_contract()
    contract_path = directory / "structure_contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    artifacts = {"structure_contract": contract_path}
    methods = {}
    for index, (name, smiles) in enumerate((("BADCy", BADCY_SMILES), ("DCDDS", DCDDS_SMILES))):
        sdf = components_dir / f"{name}.sdf"
        pdb = components_dir / f"{name}.pdb"
        methods[name] = _write_3d(_molecule(smiles), sdf, pdb, 20261029 + index)
        artifacts[f"{name}_sdf"] = sdf
        artifacts[f"{name}_pdb"] = pdb
    ctbn_path = components_dir / "CTBN_1300X13_representative.smiles"
    ctbn_path.write_text(representative_ctbn_smiles() + "\n", encoding="utf-8")
    artifacts["CTBN_representative_smiles"] = ctbn_path
    manifest = {
        "baseline_id": BASELINE_ID,
        "candidate_id": CANDIDATE_ID,
        "formulation_id": FORMULATION_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scientific_status": contract["scientific_status"],
        "production_approved": False,
        "geometry_methods": methods,
        "artifacts": {
            name: {"relative_path": path.relative_to(directory).as_posix(), "sha256": _sha256(path)}
            for name, path in sorted(artifacts.items())
        },
    }
    manifest_path = directory / "baseline_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"contract": contract, "manifest": manifest, "path": str(manifest_path)}
