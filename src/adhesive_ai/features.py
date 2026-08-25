"""Descriptors for high-temperature self-healing adhesive virtual screening."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re

import numpy as np

RESIN_SYSTEMS = {
    "CE": {"label": "氰酸酯（CE）", "thermal": 0.82, "toughness": 0.43, "polarity": 0.53, "crosslink": 0.84, "cte": 0.37},
    "PN": {"label": "邻苯二甲腈", "thermal": 0.96, "toughness": 0.38, "polarity": 0.60, "crosslink": 0.95, "cte": 0.24},
    "PI": {"label": "聚酰亚胺（PI）", "thermal": 0.93, "toughness": 0.58, "polarity": 0.74, "crosslink": 0.70, "cte": 0.28},
    "Silicone": {"label": "硅橡胶", "thermal": 0.55, "toughness": 0.94, "polarity": 0.32, "crosslink": 0.43, "cte": 0.89},
    "PU": {"label": "聚氨酯（PU）", "thermal": 0.50, "toughness": 0.86, "polarity": 0.77, "crosslink": 0.52, "cte": 0.66},
}

DYNAMIC_UNITS = {
    "None": {"label": "无动态修复单元", "healing": 0.04, "mobility": 0.00},
    "Disulfide": {"label": "二硫键", "healing": 0.76, "mobility": 0.24},
    "DielsAlder": {"label": "Diels-Alder 可逆键", "healing": 0.73, "mobility": 0.16},
    "Boronic": {"label": "硼酸酯动态键", "healing": 0.82, "mobility": 0.28},
    "Ionic": {"label": "离子/氢键簇", "healing": 0.67, "mobility": 0.31},
}
CURING_SYSTEMS = {"Thermal": "热固化", "Catalytic": "催化固化", "Stepwise": "分步固化"}

FEATURE_LABELS = {
    "resin_thermal": "树脂耐热因子", "resin_toughness": "树脂低温韧性因子", "resin_polarity": "树脂极性/官能团因子",
    "crosslink_density": "交联密度", "filler_pct": "PDA@CeO₂ 含量", "dynamic_healing": "动态修复能力",
    "dynamic_mobility": "动态链段迁移因子", "cure_factor": "固化工艺因子", "tg_c": "玻璃化转变温度 Tg",
    "free_volume": "自由体积分数", "cohesive_energy_density": "内聚能密度", "elastic_modulus_gpa": "弹性模量",
    "cte_ppm_k": "热膨胀系数", "chain_mobility": "链段运动能力", "ao_adsorption_ev": "原子氧吸附能",
    "radical_capture": "自由基捕获能力", "interface_binding_mj_m2": "界面结合能", "interface_covalent_bonds": "界面共价键数量",
}

# The original SMILES-based UI and surrogate model retain this stable feature
# contract. Candidate-database descriptors are deliberately kept separate.
LEGACY_FEATURE_NAMES = (
    "resin_ratio", "tackifier_ratio", "filler_ratio", "temperature_c", "humidity_pct",
    "weighted_atom_count", "weighted_heavy_atom_count", "weighted_hetero_fraction",
    "weighted_ring_count", "weighted_aromatic_count", "weighted_polar_fraction",
    "weighted_halogen_fraction", "weighted_branching", "weighted_molecular_weight",
    "weighted_hbond", "weighted_flexibility", "compatibility_index",
    "temperature_factor", "humidity_factor",
)
LEGACY_FEATURE_LABELS = {
    "resin_ratio": "树脂配比", "tackifier_ratio": "增粘剂配比", "filler_ratio": "填料配比",
    "temperature_c": "温度", "humidity_pct": "相对湿度", "weighted_atom_count": "加权总原子数",
    "weighted_heavy_atom_count": "加权重原子数", "weighted_hetero_fraction": "加权杂原子占比",
    "weighted_ring_count": "加权环数", "weighted_aromatic_count": "加权芳香原子数",
    "weighted_polar_fraction": "加权极性原子占比", "weighted_halogen_fraction": "加权卤素占比",
    "weighted_branching": "加权支化程度", "weighted_molecular_weight": "加权分子量",
    "weighted_hbond": "加权氢键能力", "weighted_flexibility": "加权柔性",
    "compatibility_index": "相容性指数", "temperature_factor": "温度因子", "humidity_factor": "湿度因子",
}

# Backward-compatible labels used by the current Streamlit view.
FORMULATION_FEATURE_LABELS = LEGACY_FEATURE_LABELS
MOLECULE_FEATURE_LABELS = {
    "atom_count": "总原子数", "heavy_atom_count": "重原子数", "hetero_atom_count": "杂原子数",
    "ring_count": "环数", "aromatic_count": "芳香原子数", "polar_atom_fraction": "极性原子占比",
    "halogen_fraction": "卤素占比", "branching_proxy": "支化程度（代理）",
    "molecular_weight_proxy": "分子量（估算）", "hydrogen_bond_proxy": "氢键能力（代理）",
    "flexibility_proxy": "柔性（代理）",
}
MOLECULE_KIND_LABELS = {"resin": "树脂", "tackifier": "增粘剂", "filler": "填料"}
ATOM_WEIGHTS = {"C": 12.011, "H": 1.008, "N": 14.007, "O": 15.999, "Si": 28.085, "S": 32.06, "Cl": 35.45}
POLAR_ATOMS = {"N", "O", "S", "P"}
HALOGENS = {"F", "Cl", "Br", "I"}

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
        return np.array(list(asdict(self).values()), dtype=float)

def molecule_features(smiles: str) -> MoleculeFeatures:
    clean = (smiles or "").strip()
    tokens = re.findall(r"Cl|Br|[A-Z][a-z]?|[cnosp]", clean)
    atoms = [token.capitalize() for token in tokens] or ["C"]
    heavy = len(atoms)
    hetero = sum(atom in POLAR_ATOMS for atom in atoms)
    halogen = sum(atom in HALOGENS for atom in atoms)
    aromatic = sum(token.islower() for token in tokens)
    rings = sum(char.isdigit() for char in clean) // 2
    bonds = clean.count("=") + clean.count("#")
    return MoleculeFeatures(
        atom_count=heavy + max(1, round(heavy * 1.65)), heavy_atom_count=heavy, hetero_atom_count=hetero,
        ring_count=rings, aromatic_count=aromatic, polar_atom_fraction=round(hetero / heavy, 4),
        halogen_fraction=round(halogen / heavy, 4), branching_proxy=round(min(1, clean.count("(") / heavy), 4),
        molecular_weight_proxy=round(sum(ATOM_WEIGHTS.get(atom, 12.011) for atom in atoms), 3),
        hydrogen_bond_proxy=round(min(1, hetero / heavy), 4),
        flexibility_proxy=round(min(1, max(0, heavy - 2 * rings - bonds) / heavy), 4),
    )

@dataclass(frozen=True)
class Formulation:
    candidate_id: str
    resin: str
    blend_resin: str | None
    blend_fraction: float
    dynamic_unit: str
    cure_system: str
    catalyst: str
    toughener_pct: float
    filler_pct: float
    crosslink_density: float

    def record(self) -> dict[str, object]:
        row = asdict(self)
        row.update(resin_name=RESIN_SYSTEMS[self.resin]["label"], dynamic_name=DYNAMIC_UNITS[self.dynamic_unit]["label"], cure_name=CURING_SYSTEMS[self.cure_system])
        return row

def _candidate_formulation_features(formulation: Formulation) -> dict[str, float]:
    base = RESIN_SYSTEMS[formulation.resin]
    blend = RESIN_SYSTEMS[formulation.blend_resin] if formulation.blend_resin else base
    fraction = formulation.blend_fraction if formulation.blend_resin else 0.0
    props = {key: (1 - fraction) * base[key] + fraction * blend[key] for key in base if key != "label"}
    dynamic = DYNAMIC_UNITS[formulation.dynamic_unit]
    cure_factor = {"Thermal": .76, "Catalytic": .84, "Stepwise": .93}[formulation.cure_system]
    xlink = float(np.clip(formulation.crosslink_density + .12 * cure_factor - .08 * dynamic["mobility"], .20, 1))
    filler, toughener = formulation.filler_pct / 100, formulation.toughener_pct / 100
    tg = 55 + 285 * props["thermal"] + 78 * xlink - 55 * toughener - 28 * dynamic["mobility"]
    free_volume = np.clip(.075 + .13 * props["toughness"] + .05 * toughener + .035 * dynamic["mobility"] - .065 * xlink, .035, .27)
    ced = 205 + 215 * props["polarity"] + 125 * xlink + 45 * filler
    modulus = np.clip(.25 + 5.6 * xlink + 1.35 * filler - .85 * toughener, .12, 8.5)
    cte = 32 + 88 * props["cte"] + 20 * free_volume - 18 * filler
    mobility = np.clip(.17 + .5 * props["toughness"] + .34 * dynamic["mobility"] + .16 * toughener - .40 * xlink, .03, 1)
    return {"resin_thermal": props["thermal"], "resin_toughness": props["toughness"], "resin_polarity": props["polarity"], "crosslink_density": xlink, "filler_pct": formulation.filler_pct, "dynamic_healing": dynamic["healing"], "dynamic_mobility": dynamic["mobility"], "cure_factor": cure_factor, "tg_c": float(tg), "free_volume": float(free_volume), "cohesive_energy_density": float(ced), "elastic_modulus_gpa": float(modulus), "cte_ppm_k": float(cte), "chain_mobility": float(mobility)}

def feature_names() -> list[str]:
    return list(LEGACY_FEATURE_NAMES)

def candidate_feature_names() -> list[str]:
    """Descriptors persisted by the high-temperature candidate database."""
    return list(FEATURE_LABELS)

def formulation_features(*args: object) -> dict[str, float]:
    """Support both new candidate records and the legacy Streamlit form signature."""
    if len(args) == 1 and isinstance(args[0], Formulation):
        return _candidate_formulation_features(args[0])
    if len(args) != 8:
        raise TypeError("formulation_features expects a Formulation or 8 legacy form arguments")
    resin_smiles, tackifier_smiles, filler_smiles, resin_ratio, tackifier_ratio, filler_ratio, temperature_c, humidity_pct = args
    total = float(resin_ratio) + float(tackifier_ratio) + float(filler_ratio)
    if total <= 0:
        raise ValueError("配比总和必须大于 0")
    ratios = np.array([float(resin_ratio), float(tackifier_ratio), float(filler_ratio)]) / total
    molecules = [molecule_features(str(value)) for value in (resin_smiles, tackifier_smiles, filler_smiles)]
    weighted = ratios @ np.vstack([item.as_array() for item in molecules])
    compatibility = 1 - min(1, abs(molecules[0].polar_atom_fraction - molecules[1].polar_atom_fraction) + .35 * abs(molecules[0].flexibility_proxy - molecules[1].flexibility_proxy))
    return {
        "resin_ratio": float(ratios[0]), "tackifier_ratio": float(ratios[1]), "filler_ratio": float(ratios[2]),
        "temperature_c": float(temperature_c), "humidity_pct": float(humidity_pct),
        "weighted_atom_count": float(weighted[0]), "weighted_heavy_atom_count": float(weighted[1]),
        "weighted_hetero_fraction": float(weighted[2] / max(weighted[1], 1e-6)), "weighted_ring_count": float(weighted[3]),
        "weighted_aromatic_count": float(weighted[4]), "weighted_polar_fraction": float(weighted[5]),
        "weighted_halogen_fraction": float(weighted[6]), "weighted_branching": float(weighted[7]),
        "weighted_molecular_weight": float(weighted[8]), "weighted_hbond": float(weighted[9]),
        "weighted_flexibility": float(weighted[10]), "compatibility_index": float(compatibility),
        "temperature_factor": float(math.exp(-((float(temperature_c) - 25) / 45) ** 2)),
        "humidity_factor": float(max(0, 1 - float(humidity_pct) / 180)),
    }
