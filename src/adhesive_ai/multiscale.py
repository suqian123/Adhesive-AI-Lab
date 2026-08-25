"""Deterministic, physics-informed surrogates for PDA@CeO2 multiscale screening.

The routines provide transparent pre-screening estimates. They are not a
replacement for DFT, all-atom MD, or calibrated coarse-grained force fields.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


FACET_FACTORS = {"(111)": 1.00, "(110)": 1.18, "(100)": 1.10}


@dataclass(frozen=True)
class QuantumSurfaceResult:
    oxygen_adsorption_ev: float
    oxygen_reaction_barrier_ev: float
    oxygen_reaction_energy_ev: float
    ce3_fraction: float
    reactive_oxygen_capture_index: float
    pda_resin_hbond_ev: float
    pda_surface_coordination_ev: float
    pda_reaction_ev: float


@dataclass(frozen=True)
class ResinMDResult:
    temperatures_c: np.ndarray
    glass_transition_c: float
    free_volume_fraction: np.ndarray
    cohesive_energy_density_mj_m3: np.ndarray
    elastic_modulus_gpa: np.ndarray
    cte_ppm_k: np.ndarray
    adhesion_retention: np.ndarray
    self_healing_efficiency: np.ndarray


@dataclass(frozen=True)
class InterfaceResult:
    binding_energy_mj_m2: float
    hydrogen_bond_density_nm2: float
    coordination_bond_density_nm2: float
    covalent_reaction_fraction: float
    dispersion_index: float
    coarse_grained_reinforcement_index: float


def _clip(value: float, lower: float, upper: float) -> float:
    return float(np.clip(value, lower, upper))


def calculate_quantum_surface(
    *, facet: str = "(111)", oxygen_vacancy_fraction: float = 0.08,
    hydroxyl_fraction: float = 0.35, resin_polarity: float = 0.5,
    dynamic_healing: float = 0.45,
) -> QuantumSurfaceResult:
    """Estimate DFT observables for an oxygen-defective hydroxylated ceria slab."""
    facet_factor = FACET_FACTORS.get(facet, FACET_FACTORS["(111)"])
    vacancy = _clip(oxygen_vacancy_fraction, 0.0, 0.30)
    hydroxyl = _clip(hydroxyl_fraction, 0.0, 1.0)
    polarity = _clip(resin_polarity, 0.0, 1.0)
    healing = _clip(dynamic_healing, 0.0, 1.0)
    adsorption = -(0.72 * facet_factor + 1.85 * vacancy + 0.24 * hydroxyl)
    barrier = _clip(1.05 - 1.55 * vacancy - 0.20 * hydroxyl + 0.08 / facet_factor, 0.18, 1.35)
    reaction_energy = -(0.36 * facet_factor + 1.12 * vacancy + 0.18 * hydroxyl)
    ce3 = _clip(0.04 + 1.75 * vacancy + 0.10 * hydroxyl, 0.0, 0.72)
    capture = _clip(0.18 + 0.72 * ce3 + 0.12 * hydroxyl, 0.0, 1.0)
    return QuantumSurfaceResult(
        oxygen_adsorption_ev=round(adsorption, 3),
        oxygen_reaction_barrier_ev=round(barrier, 3),
        oxygen_reaction_energy_ev=round(reaction_energy, 3),
        ce3_fraction=round(ce3, 3),
        reactive_oxygen_capture_index=round(capture, 3),
        pda_resin_hbond_ev=round(-(0.16 + 0.62 * polarity + 0.20 * hydroxyl), 3),
        pda_surface_coordination_ev=round(-(0.68 * facet_factor + 1.10 * vacancy + 0.16 * hydroxyl), 3),
        pda_reaction_ev=round(-(0.12 + 0.48 * polarity + 0.26 * healing), 3),
    )


def calculate_resin_md(
    *, crosslink_density: float, resin_thermal: float, resin_toughness: float,
    resin_polarity: float, dynamic_healing: float, dynamic_mobility: float,
    filler_pct: float, temperatures_c: np.ndarray | None = None,
) -> ResinMDResult:
    """Generate temperature-dependent all-atom MD screening observables."""
    temperature = np.asarray(temperatures_c if temperatures_c is not None else np.array([-180., -120., -60., 25., 80., 150.]))
    xlink = _clip(crosslink_density, 0.15, 1.0)
    filler = _clip(filler_pct / 100.0, 0.0, 0.20)
    tg = 38 + 238 * _clip(resin_thermal, 0, 1) + 82 * xlink - 38 * dynamic_mobility + 16 * filler
    sigmoid = 1 / (1 + np.exp(-(temperature - tg) / 17))
    base_fv = _clip(0.075 + 0.105 * resin_toughness - 0.052 * xlink + 0.018 * dynamic_mobility, 0.035, 0.24)
    free_volume = base_fv + 0.00018 * (temperature + 180) + 0.045 * sigmoid
    ced = (245 + 290 * resin_polarity + 165 * xlink + 62 * filler) * (1 - 0.00028 * (temperature - 25))
    modulus = (0.32 + 5.45 * xlink + 1.45 * filler) * (1 - 0.73 * sigmoid)
    cte = 36 + 66 * (1 - resin_thermal) + 16 * free_volume * 100 - 22 * filler + 24 * sigmoid
    temperature_penalty = 0.13 * np.maximum(temperature - 90, 0) / 60 + 0.10 * np.maximum(-120 - temperature, 0) / 60
    retention = np.clip(0.66 + 0.22 * xlink + 0.13 * filler - temperature_penalty, 0.20, 0.98)
    healing_window = np.exp(-((temperature - (55 + 55 * dynamic_mobility)) / 72) ** 2)
    healing = np.clip((0.10 + 0.78 * dynamic_healing + 0.10 * dynamic_mobility) * healing_window * (1 - 0.22 * xlink), 0, 0.95)
    return ResinMDResult(
        temperatures_c=temperature, glass_transition_c=round(float(tg), 1),
        free_volume_fraction=np.round(free_volume, 4),
        cohesive_energy_density_mj_m3=np.round(ced, 1), elastic_modulus_gpa=np.round(modulus, 3),
        cte_ppm_k=np.round(cte, 1), adhesion_retention=np.round(retention, 3),
        self_healing_efficiency=np.round(healing, 3),
    )


def calculate_interface_and_cg(
    *, quantum: QuantumSurfaceResult, md: ResinMDResult, filler_pct: float,
    crosslink_density: float, resin_polarity: float, particle_size_nm: float = 35.0,
) -> InterfaceResult:
    """Couple surface chemistry and polymer mobility into a CG interface estimate."""
    filler = _clip(filler_pct / 100.0, 0.0, 0.20)
    size_factor = _clip(35 / max(particle_size_nm, 8), 0.35, 1.45)
    mobility = float(np.mean(md.free_volume_fraction))
    hbond = _clip(1.1 + 4.8 * resin_polarity + 2.0 * abs(quantum.pda_resin_hbond_ev), 0.2, 9.0)
    coordinate = _clip(0.5 + 3.2 * abs(quantum.pda_surface_coordination_ev), 0.2, 8.0)
    covalent = _clip(0.05 + 0.65 * abs(quantum.pda_reaction_ev) + 0.20 * crosslink_density, 0.0, 0.88)
    binding = 38 + 14 * hbond + 19 * coordinate + 55 * covalent + 22 * filler * size_factor
    dispersion = _clip(0.38 + 1.85 * mobility + 0.20 * size_factor - 1.15 * filler, 0.05, 0.98)
    reinforcement = _clip(0.22 + 0.40 * dispersion + 0.20 * covalent + 0.20 * quantum.reactive_oxygen_capture_index, 0, 1)
    return InterfaceResult(round(binding, 2), round(hbond, 2), round(coordinate, 2), round(covalent, 3), round(dispersion, 3), round(reinforcement, 3))
