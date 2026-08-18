"""Transparent, RDKit-free molecular and formulation descriptors."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

ATOM_WEIGHTS = {
    "C": 12.011, "H": 1.008, "N": 14.007, "O": 15.999,
    "F": 18.998, "P": 30.974, "S": 32.06, "Cl": 35.45,
    "Br": 79.904, "I": 126.904,
}
POLAR_ATOMS = {"N", "O", "S", "P"}
HALOGENS = {"F", "Cl", "Br", "I"}

# Display labels are kept separate from the internal feature keys so the
# model and API can remain stable while the user-facing tables stay readable.
MOLECULE_FEATURE_LABELS = {
    "atom_count": "总原子数",
    "heavy_atom_count": "重原子数",
    "hetero_atom_count": "杂原子数",
    "ring_count": "环数量",
    "aromatic_count": "芳香原子数",
    "polar_atom_fraction": "极性原子占比",
    "halogen_fraction": "卤素占比",
    "branching_proxy": "支化程度（代理）",
    "molecular_weight_proxy": "分子量（估算）",
    "hydrogen_bond_proxy": "氢键能力（代理）",
    "flexibility_proxy": "柔性（代理）",
}

MOLECULE_KIND_LABELS = {
    "resin": "树脂",
    "tackifier": "增粘剂",
    "filler": "填料",
}

FORMULATION_FEATURE_LABELS = {
    "resin_ratio": "树脂配比",
    "tackifier_ratio": "增粘剂配比",
    "filler_ratio": "填料配比",
    "temperature_c": "温度",
    "humidity_pct": "相对湿度",
    "weighted_atom_count": "加权总原子数",
    "weighted_heavy_atom_count": "加权重原子数",
    "weighted_hetero_fraction": "加权杂原子占比",
    "weighted_ring_count": "加权环数量",
    "weighted_aromatic_count": "加权芳香原子数",
    "weighted_polar_fraction": "加权极性原子占比",
    "weighted_halogen_fraction": "加权卤素占比",
    "weighted_branching": "加权支化程度",
    "weighted_molecular_weight": "加权分子量",
    "weighted_hbond": "加权氢键能力",
    "weighted_flexibility": "加权柔性",
    "compatibility_index": "相容性指数",
    "temperature_factor": "温度因子",
    "humidity_factor": "湿度因子",
}


@dataclass(frozen=True)
class MoleculeFeatures:
    atom_count: int
    heavy_atom_count: int
    hetero_atom_count: int
    ring_count: int
    aromatic_count: int
    polar_atom_fraction: float
    halogen_fraction: float
    branching_proxy: float
    molecular_weight_proxy: float
    hydrogen_bond_proxy: float
    flexibility_proxy: float

    def as_array(self) -> np.ndarray:
        return np.array([
            self.atom_count, self.heavy_atom_count, self.hetero_atom_count,
            self.ring_count, self.aromatic_count, self.polar_atom_fraction,
            self.halogen_fraction, self.branching_proxy,
            self.molecular_weight_proxy, self.hydrogen_bond_proxy,
            self.flexibility_proxy,
        ], dtype=float)


def _tokens(smiles: str) -> list[str]:
    return re.findall(r"Cl|Br|[A-Z][a-z]?|[cnosp]", smiles or "")


def molecule_features(smiles: str) -> MoleculeFeatures:
    clean = (smiles or "").strip()
    tokens = _tokens(clean)
    atoms = [token.capitalize() for token in tokens] or ["C"]
    heavy = len(atoms)
    hetero = sum(atom in POLAR_ATOMS for atom in atoms)
    halogen = sum(atom in HALOGENS for atom in atoms)
    aromatic = sum(token.islower() for token in tokens)
    rings = max(0, sum(char.isdigit() for char in clean) // 2)
    branches = clean.count("(") + clean.count(")")
    bond_count = clean.count("=") + clean.count("#")
    weight = sum(ATOM_WEIGHTS.get(atom, 12.011) for atom in atoms)
    return MoleculeFeatures(
        atom_count=heavy + max(1, round(heavy * 1.65)),
        heavy_atom_count=heavy,
        hetero_atom_count=hetero,
        ring_count=rings,
        aromatic_count=aromatic,
        polar_atom_fraction=round(hetero / heavy, 4),
        halogen_fraction=round(halogen / heavy, 4),
        branching_proxy=round(min(1.0, branches / max(1, heavy)), 4),
        molecular_weight_proxy=round(weight, 3),
        hydrogen_bond_proxy=round(min(1.0, (hetero + clean.count("N") * 0.3) / heavy), 4),
        flexibility_proxy=round(min(1.0, max(0, heavy - rings * 2 - bond_count) / heavy), 4),
    )


def feature_names() -> list[str]:
    return [
        "resin_ratio", "tackifier_ratio", "filler_ratio", "temperature_c",
        "humidity_pct", "weighted_atom_count", "weighted_heavy_atom_count",
        "weighted_hetero_fraction", "weighted_ring_count",
        "weighted_aromatic_count", "weighted_polar_fraction",
        "weighted_halogen_fraction", "weighted_branching",
        "weighted_molecular_weight", "weighted_hbond", "weighted_flexibility",
        "compatibility_index", "temperature_factor", "humidity_factor",
    ]


def formulation_features(
    resin_smiles: str, tackifier_smiles: str, filler_smiles: str,
    resin_ratio: float, tackifier_ratio: float, filler_ratio: float,
    temperature_c: float, humidity_pct: float,
) -> dict[str, float]:
    total = resin_ratio + tackifier_ratio + filler_ratio
    if total <= 0:
        raise ValueError("配比总和必须大于 0")
    ratios = np.array([resin_ratio, tackifier_ratio, filler_ratio], dtype=float) / total
    molecules = [molecule_features(s) for s in (resin_smiles, tackifier_smiles, filler_smiles)]
    weighted = ratios @ np.vstack([m.as_array() for m in molecules])
    compatibility = 1.0 - min(
        1.0,
        abs(molecules[0].polar_atom_fraction - molecules[1].polar_atom_fraction)
        + 0.35 * abs(molecules[0].flexibility_proxy - molecules[1].flexibility_proxy),
    )
    return {
        "resin_ratio": float(ratios[0]), "tackifier_ratio": float(ratios[1]),
        "filler_ratio": float(ratios[2]), "temperature_c": float(temperature_c),
        "humidity_pct": float(humidity_pct), "weighted_atom_count": float(weighted[0]),
        "weighted_heavy_atom_count": float(weighted[1]),
        "weighted_hetero_fraction": float(weighted[2] / max(weighted[1], 1e-6)),
        "weighted_ring_count": float(weighted[3]), "weighted_aromatic_count": float(weighted[4]),
        "weighted_polar_fraction": float(weighted[5]),
        "weighted_halogen_fraction": float(weighted[6]), "weighted_branching": float(weighted[7]),
        "weighted_molecular_weight": float(weighted[8]), "weighted_hbond": float(weighted[9]),
        "weighted_flexibility": float(weighted[10]), "compatibility_index": float(compatibility),
        "temperature_factor": float(math.exp(-((temperature_c - 25.0) / 45.0) ** 2)),
        "humidity_factor": float(max(0.0, 1.0 - humidity_pct / 180.0)),
    }
