"""Candidate-library builder for high-temperature adhesive systems."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import product
import json

import numpy as np
import pandas as pd

from .features import Formulation, CURING_SYSTEMS, DYNAMIC_UNITS, RESIN_SYSTEMS, formulation_features


CANDIDATE_LIBRARY_VERSION = "candidate-library-v3"


def _formulation_id(contract: dict[str, object]) -> str:
    """Return a stable identifier for chemistry/process identity, independent of ranking order."""
    canonical = json.dumps(contract, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "FMT-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16].upper()


@dataclass(frozen=True)
class ResinVariant:
    family: str
    variant: str
    architecture: str
    functionality: int
    thermal_bias: float
    toughness_bias: float
    polarity_bias: float
    cte_bias: float
    space_bias: float


RESIN_VARIANTS: dict[str, tuple[ResinVariant, ...]] = {
    "CE": (
        ResinVariant("CE", "rigid_triazine", "rigid aromatic CE", 3, 0.10, 0.02, 0.05, -0.03, 0.08),
        ResinVariant("CE", "flexible_bridge", "bridged CE", 2, 0.03, 0.08, 0.01, 0.02, 0.03),
        ResinVariant("CE", "high_functionality", "high-functionality CE", 4, 0.13, -0.01, 0.07, -0.05, 0.10),
    ),
    "PN": (
        ResinVariant("PN", "rigid_linear", "rigid PN", 2, 0.12, -0.01, 0.03, -0.04, 0.09),
        ResinVariant("PN", "ether_linked", "ether-linked PN", 2, 0.05, 0.05, 0.04, -0.01, 0.05),
        ResinVariant("PN", "star_like", "star-like PN", 3, 0.14, -0.02, 0.05, -0.05, 0.11),
    ),
    "PI": (
        ResinVariant("PI", "rigid_imide", "rigid PI", 2, 0.08, 0.06, 0.06, -0.02, 0.07),
        ResinVariant("PI", "flexible_imide", "semi-flexible PI", 2, 0.05, 0.10, 0.03, 0.01, 0.04),
        ResinVariant("PI", "fluorinated", "fluorinated PI", 2, 0.11, 0.01, 0.07, -0.05, 0.12),
    ),
    "Silicone": (
        ResinVariant("Silicone", "methyl_silicone", "methyl silicone", 2, -0.02, 0.14, -0.03, 0.10, 0.02),
        ResinVariant("Silicone", "phenyl_silicone", "phenyl silicone", 2, 0.03, 0.08, 0.01, 0.04, 0.05),
        ResinVariant("Silicone", "hybrid_siloxane", "hybrid siloxane", 3, 0.00, 0.12, 0.00, 0.06, 0.03),
    ),
    "PU": (
        ResinVariant("PU", "polyether_pu", "polyether PU", 2, 0.02, 0.12, 0.05, 0.03, 0.05),
        ResinVariant("PU", "polyester_pu", "polyester PU", 2, 0.05, 0.08, 0.07, 0.01, 0.04),
        ResinVariant("PU", "hard_segment", "hard-segment PU", 3, 0.08, 0.04, 0.06, -0.01, 0.08),
    ),
}

CURING_AGENTS = {
    "CE": ("phenolic", "imidazole-activated", "maleimide"),
    "PN": ("aromatic diamine", "phenolic", "benzoxazine"),
    "PI": ("dianhydride", "diamine", "amino-terminated"),
    "Silicone": ("hydrosilane", "alkoxy-silane", "condensation"),
    "PU": ("isocyanate", "polyol-chain", "blocked-isocyanate"),
}

CATALYSTS = ("none", "imidazole", "amine-salt", "organometallic")
TOUGHENER_TYPES = ("none", "rubber", "core-shell", "thermoplastic")
FILLER_LEVELS = (0.0, 1.0, 3.0, 5.0, 8.0, 12.0)
BLEND_FRACTIONS = (0.0, 0.15, 0.30)
CURING_WINDOWS = {
    "CE": (180.0, 240.0),
    "PN": (220.0, 300.0),
    "PI": (280.0, 360.0),
    "Silicone": (110.0, 180.0),
    "PU": (70.0, 130.0),
}


def _blend_partner(family: str) -> str | None:
    partners = {"CE": "PI", "PN": "CE", "PI": "PN", "Silicone": "PU", "PU": "Silicone"}
    return partners.get(family)


def _family_bias(family: str) -> ResinVariant:
    return RESIN_VARIANTS[family][0]


def _score_to_class(score: float) -> str:
    if score >= 78:
        return "A"
    if score >= 64:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _compose_candidate(
    *,
    candidate_id: str,
    variant: ResinVariant,
    dynamic_unit: str,
    cure_system: str,
    catalyst: str,
    toughener_type: str,
    toughener_pct: float,
    filler_pct: float,
    blend_fraction: float,
) -> dict[str, object]:
    blend_resin = _blend_partner(variant.family) if blend_fraction > 0 else None
    agent_index = (variant.functionality + int(filler_pct) + int(toughener_pct)) % len(CURING_AGENTS[variant.family])
    curing_agent = CURING_AGENTS[variant.family][agent_index]
    cure_low, cure_high = CURING_WINDOWS[variant.family]
    cure_factor = {"Thermal": 0.76, "Catalytic": 0.84, "Stepwise": 0.93}[cure_system]
    catalyst_factor = {"none": 0.0, "imidazole": 0.06, "amine-salt": 0.05, "organometallic": 0.08}[catalyst]
    toughener_factor = toughener_pct / 20.0
    filler_factor = filler_pct / 12.0

    crosslink_density = float(np.clip(0.28 + 0.16 * variant.functionality + 0.10 * cure_factor + catalyst_factor - 0.08 * toughener_factor - 0.04 * blend_fraction, 0.20, 1.0))
    formulation = Formulation(
        candidate_id=candidate_id,
        resin=variant.family,
        blend_resin=blend_resin,
        blend_fraction=float(blend_fraction),
        dynamic_unit=dynamic_unit,
        cure_system=cure_system,
        catalyst=catalyst,
        toughener_pct=float(toughener_pct),
        filler_pct=float(filler_pct),
        crosslink_density=crosslink_density,
    )
    formulation_contract = {
        "resin": variant.family,
        "resin_variant": variant.variant,
        "blend_resin": blend_resin,
        "blend_fraction": float(blend_fraction),
        "dynamic_unit": dynamic_unit,
        "cure_system": cure_system,
        "curing_agent": curing_agent,
        "catalyst": catalyst,
        "toughener_type": toughener_type,
        "toughener_pct": float(toughener_pct),
        "filler_type": "PDA@CeO2",
        "filler_surface": "PDA-coated CeO2",
        "filler_pct": float(filler_pct),
        "crosslink_density": round(crosslink_density, 8),
    }
    features = formulation_features(formulation)
    effective_crosslink = float(features["crosslink_density"])
    resin_label = RESIN_SYSTEMS[variant.family]["label"]
    dynamic_label = DYNAMIC_UNITS[dynamic_unit]["label"]
    cure_label = CURING_SYSTEMS[cure_system]

    resin_thermal = float(np.clip(features["resin_thermal"] + variant.thermal_bias, 0.0, 1.0))
    resin_toughness = float(np.clip(features["resin_toughness"] + variant.toughness_bias, 0.0, 1.0))
    resin_polarity = float(np.clip(features["resin_polarity"] + variant.polarity_bias, 0.0, 1.0))
    free_volume = float(np.clip(features["free_volume"] + 0.02 * variant.toughness_bias + 0.01 * toughener_factor, 0.03, 0.30))
    cte_ppm_k = float(np.clip(features["cte_ppm_k"] + 18 * variant.cte_bias - 12 * filler_factor, 15, 120))
    chain_mobility = float(np.clip(features["chain_mobility"] + 0.05 * toughener_factor + 0.03 * variant.toughness_bias, 0.03, 1.0))
    # Adsorption is exothermic by convention; strength is handled as |E_ads| below.
    ao_adsorption_ev = -float(np.clip(0.42 + 0.58 * resin_polarity + 0.18 * features["dynamic_healing"] - 0.12 * filler_factor + 0.09 * variant.space_bias, 0.05, 1.8))
    radical_capture = float(np.clip(0.28 + 0.44 * features["dynamic_healing"] + 0.10 * resin_polarity + 0.06 * variant.space_bias, 0.05, 1.2))
    interface_binding = float(np.clip(1.8 + 5.4 * resin_polarity + 3.0 * effective_crosslink + 0.55 * filler_pct / 2.0 - 0.4 * toughener_factor, 0.8, 18.0))
    interface_bonds = int(round(np.clip(1.2 + 3.6 * effective_crosslink + 1.4 * cure_factor + 0.5 * variant.functionality - 0.35 * blend_fraction * 10, 0, 14)))
    thermal_resistance = float(np.clip(0.28 * resin_thermal + 0.28 * effective_crosslink + 0.18 * cure_factor + 0.16 * variant.thermal_bias + 0.10 * (1 - blend_fraction), 0.0, 1.0))
    low_temp_toughness = float(np.clip(0.32 * resin_toughness + 0.22 * features["dynamic_healing"] + 0.18 * chain_mobility + 0.14 * (1 - free_volume) + 0.14 * toughener_factor, 0.0, 1.0))
    adhesion_strength = float(np.clip(2.8 + 0.55 * interface_binding + 5.4 * effective_crosslink + 0.12 * filler_pct - 0.42 * toughener_pct, 0.5, 45.0))
    self_healing = float(np.clip(12 + 62 * features["dynamic_healing"] + 10 * features["dynamic_mobility"] + 6 * variant.space_bias + 3.5 * cure_factor - 0.4 * filler_pct, 5, 100))
    # Multi-objective outputs used by the regression/classification loop.
    wide_temp_adhesion = float(np.clip(
        2.2 + 11.5 * thermal_resistance + 8.0 * low_temp_toughness
        + 0.32 * interface_binding + 0.10 * filler_pct - 0.11 * abs(cte_ppm_k - 55),
        0.5, 42.0,
    ))
    atomic_oxygen_retention = float(np.clip(
        48 + 25 * thermal_resistance + 20 * np.clip(radical_capture / 1.2, 0, 1)
        + 12 * np.clip(abs(ao_adsorption_ev) / 1.8, 0, 1) + 8 * variant.space_bias,
        5, 100,
    ))
    uv_retention = float(np.clip(
        46 + 30 * resin_thermal + 11 * resin_polarity + 12 * variant.space_bias
        + 5 * np.clip(interface_bonds / 14, 0, 1) - 0.35 * filler_pct,
        5, 100,
    ))
    am_feasibility = float(np.clip(
        100 * (0.38 + 0.28 * (1 - effective_crosslink) + 0.18 * resin_toughness
               + 0.12 * np.clip(1 - filler_pct / 15, 0, 1) + 0.04 * (1 - blend_fraction)),
        5, 100,
    ))
    space_stability = float(np.clip(
        0.24 * thermal_resistance
        + 0.18 * (1 - np.clip((cte_ppm_k - 20) / 90.0, 0, 1))
        + 0.20 * np.clip(abs(ao_adsorption_ev) / 1.8, 0, 1)
        + 0.18 * np.clip(radical_capture / 1.2, 0, 1)
        + 0.20 * np.clip(interface_bonds / 14.0, 0, 1),
        0.0,
        1.0,
    ))
    overall = float(np.clip(
        100 * (
            0.16 * thermal_resistance
            + 0.14 * low_temp_toughness
            + 0.18 * np.clip(adhesion_strength / 45.0, 0, 1)
            + 0.14 * np.clip(self_healing / 100.0, 0, 1)
            + 0.12 * np.clip(atomic_oxygen_retention / 100.0, 0, 1)
            + 0.10 * np.clip(uv_retention / 100.0, 0, 1)
            + 0.08 * np.clip(am_feasibility / 100.0, 0, 1)
            + 0.08 * space_stability
        ),
        0,
        100,
    ))

    row: dict[str, object] = {
        "candidate_id": candidate_id,
        "formulation_id": _formulation_id(formulation_contract),
        "candidate_library_version": CANDIDATE_LIBRARY_VERSION,
        "formulation_contract": formulation_contract,
        "resin": variant.family,
        "blend_resin": blend_resin,
        "blend_fraction": float(blend_fraction),
        "dynamic_unit": dynamic_unit,
        "cure_system": cure_system,
        "catalyst": catalyst,
        "toughener_pct": float(toughener_pct),
        "filler_pct": float(filler_pct),
        "crosslink_density": crosslink_density,
        "resin_name": resin_label,
        "dynamic_name": dynamic_label,
        "cure_name": cure_label,
        "resin_variant": variant.variant,
        "resin_architecture": variant.architecture,
        "resin_functionality": variant.functionality,
        "curing_agent": curing_agent,
        "toughener_type": toughener_type,
        "filler_type": "PDA@CeO₂",
        "filler_surface": "PDA-coated CeO₂",
        "functional_group_type": {"CE": "cyanate/phenolic", "PN": "phthalonitrile/nitrile-triazine", "PI": "imide/amine", "Silicone": "siloxane", "PU": "urethane"}[variant.family],
        "curing_temperature_c": float(np.clip(cure_low + 28 * effective_crosslink + 2.5 * toughener_pct - 1.5 * filler_pct, 60, cure_high)),
        "curing_time_h": float(np.clip(1.2 + 0.55 * (1 - effective_crosslink) + 0.08 * toughener_pct + 0.06 * filler_pct, 0.5, 12.0)),
        "post_cure_temperature_c": float(np.clip(cure_low + 48 + 16 * blend_fraction, 80, cure_high + 45)),
        "post_cure_time_h": float(np.clip(1.0 + 0.30 * cure_factor + 0.04 * filler_pct, 0.5, 10.0)),
        "mixing_temperature_c": float(np.clip(50 + 14 * toughener_factor + 6 * filler_factor, 20, 140)),
        "vacuum_degassing_min": float(np.clip(15 + 6 * blend_fraction * 10 + 1.6 * toughener_pct, 10, 90)),
        "structure_params": {
            "architecture": variant.architecture,
            "functionality": variant.functionality,
            "thermal_bias": variant.thermal_bias,
            "toughness_bias": variant.toughness_bias,
            "polarity_bias": variant.polarity_bias,
            "cte_bias": variant.cte_bias,
            "space_bias": variant.space_bias,
            "crosslink_density": effective_crosslink,
            "free_volume": free_volume,
            "cohesive_energy_density": float(features["cohesive_energy_density"]),
            "elastic_modulus_gpa": float(features["elastic_modulus_gpa"]),
            "cte_ppm_k": cte_ppm_k,
            "chain_mobility": chain_mobility,
            "ao_adsorption_ev": ao_adsorption_ev,
            "radical_capture": radical_capture,
            "interface_binding_mj_m2": interface_binding,
            "interface_covalent_bonds": interface_bonds,
            "functional_group_type": {"CE": "cyanate/phenolic", "PN": "phthalonitrile/nitrile-triazine", "PI": "imide/amine", "Silicone": "siloxane", "PU": "urethane"}[variant.family],
        },
        "process_conditions": {
            "curing_temperature_c": float(np.clip(cure_low + 28 * crosslink_density + 2.5 * toughener_pct - 1.5 * filler_pct, 60, cure_high)),
            "curing_time_h": float(np.clip(1.2 + 0.55 * (1 - crosslink_density) + 0.08 * toughener_pct + 0.06 * filler_pct, 0.5, 12.0)),
            "post_cure_temperature_c": float(np.clip(cure_low + 48 + 16 * blend_fraction, 80, cure_high + 45)),
            "post_cure_time_h": float(np.clip(1.0 + 0.30 * cure_factor + 0.04 * filler_pct, 0.5, 10.0)),
            "mixing_temperature_c": float(np.clip(50 + 14 * toughener_factor + 6 * filler_factor, 20, 140)),
            "vacuum_degassing_min": float(np.clip(15 + 6 * blend_fraction * 10 + 1.6 * toughener_pct, 10, 90)),
        },
        "performance_targets": {
            "thermal_resistance_index": thermal_resistance,
            "low_temp_toughness_index": low_temp_toughness,
            "adhesion_strength_mpa": adhesion_strength,
            "self_healing_efficiency_pct": self_healing,
            "space_environment_stability_index": space_stability,
            "multi_objective_score": overall,
            "screening_class": _score_to_class(overall),
        },
    }
    row.update(features)
    row["crosslink_density"] = effective_crosslink
    row["multi_objective_score"] = overall
    row["screening_class"] = row["performance_targets"]["screening_class"]
    row["thermal_resistance_index"] = thermal_resistance
    row["low_temp_toughness_index"] = low_temp_toughness
    row["adhesion_strength_mpa"] = adhesion_strength
    row["self_healing_efficiency_pct"] = self_healing
    row["space_environment_stability_index"] = space_stability
    row["glass_transition_c"] = float(features["tg_c"])
    row["glass_transition_temperature_c"] = float(features["tg_c"])
    row["free_volume"] = free_volume
    row["free_volume_fraction"] = free_volume
    row["chain_mobility"] = chain_mobility
    row["cohesive_energy_density_mj_m3"] = float(features["cohesive_energy_density"])
    row["cohesive_energy_density"] = float(features["cohesive_energy_density"])
    row["elastic_modulus_gpa"] = float(features["elastic_modulus_gpa"])
    row["elastic_modulus"] = float(features["elastic_modulus_gpa"])
    row["cte_ppm_k"] = cte_ppm_k
    row["thermal_expansion_coefficient_ppm_k"] = cte_ppm_k
    row["filler_oxygen_adsorption_ev"] = ao_adsorption_ev
    row["oxygen_adsorption_energy_ev"] = ao_adsorption_ev
    row["filler_radical_capture_index"] = radical_capture
    row["radical_capture_capability"] = radical_capture
    row["interface_binding_energy_mj_m2"] = interface_binding
    row["interface_binding_energy"] = interface_binding
    row["interface_covalent_bond_count"] = interface_bonds
    row["wide_temp_adhesion_mpa"] = wide_temp_adhesion
    row["healing_efficiency_pct"] = self_healing
    row["atomic_oxygen_retention_pct"] = atomic_oxygen_retention
    row["uv_retention_pct"] = uv_retention
    row["am_feasibility"] = am_feasibility
    row["curing_agent"] = curing_agent
    row["data_source"] = "physics-informed-proxy"
    row["scientific_data_tier"] = "proxy-screening"
    row["feature_provenance"] = {
        name: "physics-informed-proxy"
        for name in (
            "functional_group_type", "crosslink_density", "glass_transition_c", "free_volume_fraction",
            "chain_mobility", "cohesive_energy_density_mj_m3", "elastic_modulus_gpa", "cte_ppm_k",
            "filler_oxygen_adsorption_ev", "filler_radical_capture_index",
            "interface_binding_energy_mj_m2", "interface_covalent_bond_count",
        )
    }
    row["target_provenance"] = {
        name: "physics-informed-proxy"
        for name in (
            "wide_temp_adhesion_mpa", "healing_efficiency_pct", "atomic_oxygen_retention_pct",
            "uv_retention_pct", "am_feasibility",
        )
    }
    return row


def build_candidate_library(max_records: int = 720, seed: int = 7) -> pd.DataFrame:
    """Generate a structured candidate database for simulation and ML."""
    rng = np.random.default_rng(seed)
    combos = [
        (variant, dynamic_unit, cure_system, catalyst, toughener_type, toughener_pct, filler_pct, blend_fraction)
        for variant in (v for family in RESIN_VARIANTS.values() for v in family)
        for dynamic_unit in DYNAMIC_UNITS
        for cure_system in CURING_SYSTEMS
        for catalyst in CATALYSTS
        for toughener_type, toughener_pct in zip(TOUGHENER_TYPES, (0.0, 5.0, 10.0, 15.0))
        for filler_pct in FILLER_LEVELS
        for blend_fraction in BLEND_FRACTIONS
    ]
    if not combos:
        return pd.DataFrame()
    rng.shuffle(combos)
    selected = combos[: max(1, min(int(max_records), len(combos)))]
    records = [
        _compose_candidate(
            candidate_id=f"CL-{idx:05d}",
            variant=variant,
            dynamic_unit=dynamic_unit,
            cure_system=cure_system,
            catalyst=catalyst,
            toughener_type=toughener_type,
            toughener_pct=toughener_pct,
            filler_pct=filler_pct,
            blend_fraction=blend_fraction,
        )
        for idx, (variant, dynamic_unit, cure_system, catalyst, toughener_type, toughener_pct, filler_pct, blend_fraction) in enumerate(selected, start=1)
    ]
    frame = pd.DataFrame.from_records(records)
    ordered = [
        "candidate_id", "formulation_id", "candidate_library_version", "resin", "resin_variant", "resin_architecture", "resin_functionality",
        "blend_resin", "blend_fraction", "dynamic_unit", "cure_system", "catalyst", "curing_agent",
        "toughener_type", "toughener_pct", "filler_type", "filler_surface", "filler_pct",
        "curing_temperature_c", "curing_time_h", "post_cure_temperature_c", "post_cure_time_h",
        "mixing_temperature_c", "vacuum_degassing_min", "crosslink_density", "thermal_resistance_index",
        "low_temp_toughness_index", "adhesion_strength_mpa", "self_healing_efficiency_pct",
        "space_environment_stability_index", "wide_temp_adhesion_mpa", "healing_efficiency_pct",
        "atomic_oxygen_retention_pct", "uv_retention_pct", "am_feasibility",
        "multi_objective_score", "screening_class",
    ]
    ordered.extend([name for name in frame.columns if name not in ordered])
    return frame.loc[:, ordered].sort_values("multi_objective_score", ascending=False).reset_index(drop=True)


def save_candidate_library(max_records: int = 720, seed: int = 7) -> pd.DataFrame:
    """Generate the candidate library and persist each row via the MySQL helper."""
    from .database import save_candidate

    frame = build_candidate_library(max_records=max_records, seed=seed)
    for row in frame.to_dict("records"):
        save_candidate(row)
    return frame
