"""Fast coarse-grained interface adsorption simulation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SimulationResult:
    steps: np.ndarray
    energy: np.ndarray
    coverage: np.ndarray
    final_positions: np.ndarray
    adhesion_work_mj_m2: float
    interface_energy_mj_m2: float
    stability_score: float


def _potential(positions: np.ndarray, attraction: float) -> float:
    z = positions[:, 2]
    substrate = -attraction * np.exp(-z / 0.7) + 0.015 * (z - 1.0) ** 2
    spread = np.var(positions[:, :2], axis=0).sum() * 0.006
    return float(np.sum(substrate) + spread)


def run_interface_simulation(
    *, compatibility_index: float, polar_fraction: float, filler_ratio: float,
    temperature_c: float, steps: int = 650, particles: int = 44, seed: int = 7,
) -> SimulationResult:
    rng = np.random.default_rng(seed)
    steps, particles = max(100, int(steps)), max(12, int(particles))
    positions = rng.uniform([-4, -4, .35], [4, 4, 3.6], size=(particles, 3))
    energies, coverages = np.empty(steps), np.empty(steps)
    thermal = np.clip((temperature_c + 30) / 120, .05, 1.25)
    attraction = .11 + .34*compatibility_index + .28*polar_fraction - .15*filler_ratio
    for step in range(steps):
        proposal = positions + rng.normal(0, .075*thermal, size=positions.shape)
        proposal[:, 2] = np.clip(proposal[:, 2], .08, 4.0)
        current, proposed = _potential(positions, attraction), _potential(proposal, attraction)
        accept = rng.random(particles) < np.exp(np.clip((current - proposed) / (.16 + thermal), -40, 0))
        accept |= proposed < current
        positions[accept] = proposal[accept]
        energies[step] = _potential(positions, attraction)
        coverages[step] = np.mean(positions[:, 2] < .55)
    tail = max(10, steps // 8)
    baseline, final_energy = max(.1, abs(float(np.median(energies[:tail])))), float(np.mean(energies[-tail:]))
    coverage = float(np.mean(coverages[-tail:]))
    stability = float(np.clip(1 - np.std(energies[-steps//3:]) / baseline, 0, 1))
    interface = max(1.0, -final_energy * 4.2)
    adhesion = max(4.0, interface * (.72 + coverage*.5))
    return SimulationResult(
        np.arange(1, steps+1), energies, coverages, positions,
        float(adhesion), float(interface), stability,
    )
