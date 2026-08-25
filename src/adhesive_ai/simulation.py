"""LAMMPS/GROMACS interface-simulation adapter.

``run_interface_simulation`` keeps the legacy result contract used by the UI,
but the backend is now an external MD job. When an engine output is supplied,
observables are parsed from it. Without an executable/output, the function
returns an explicitly labelled deterministic fallback so offline screening
continues to work without presenting it as an MD result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Sequence

import numpy as np

from .coarse_grained import build_cg_interface_model, write_cg_interface_model
from .engines import MDJobSpec, generate_md_inputs, parse_gromacs_xvg, parse_lammps_thermo, run_external_command


@dataclass
class SimulationResult:
    steps: np.ndarray
    energy: np.ndarray
    coverage: np.ndarray
    final_positions: np.ndarray
    adhesion_work_mj_m2: float
    interface_energy_mj_m2: float
    stability_score: float
    engine: str = "fallback"
    status: str = "proxy-fallback"
    job_directory: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


def _empty_positions(particles: int) -> np.ndarray:
    return np.zeros((max(1, int(particles)), 3), dtype=float)


def _from_series(
    steps: Sequence[float], energy: Sequence[float], *, coverage: Sequence[float] | None = None,
    positions: np.ndarray | None = None, particles: int = 44, engine: str, status: str,
    area_nm2: float = 100.0, energy_unit: str = "kcal/mol", metadata: dict[str, object] | None = None,
) -> SimulationResult:
    step_values = np.asarray(steps, dtype=float)
    energy_values = np.asarray(energy, dtype=float)
    if step_values.size == 0 or energy_values.size == 0 or step_values.size != energy_values.size:
        raise ValueError("MD output must contain matched non-empty step and energy series")
    coverage_values = np.asarray(coverage if coverage is not None else np.zeros_like(energy_values), dtype=float)
    if coverage_values.size != energy_values.size:
        coverage_values = np.resize(coverage_values, energy_values.size)
    positions_array = np.asarray(positions, dtype=float) if positions is not None else _empty_positions(particles)
    tail = max(3, len(energy_values) // 8)
    baseline = max(1e-8, abs(float(np.median(energy_values[:tail]))))
    final_energy = float(np.mean(energy_values[-tail:]))
    stability = float(np.clip(1 - np.std(energy_values[-max(3, len(energy_values) // 3):]) / baseline, 0, 1))
    coverage_value = float(np.clip(np.mean(coverage_values[-tail:]), 0, 1))
    # 1 kcal mol-1 nm-2 = 69.478 mJ m-2; 1 kJ mol-1 nm-2 = 16.6054 mJ m-2.
    unit_factor = {"kcal/mol": 69.478, "kcal": 69.478, "kj/mol": 16.6054, "kj": 16.6054}.get(energy_unit.lower(), 1.0)
    interface = max(0.0, -final_energy * unit_factor / max(float(area_nm2), 1e-6))
    adhesion = max(0.0, interface * (0.72 + 0.5 * coverage_value))
    return SimulationResult(
        steps=np.asarray(step_values, dtype=float), energy=energy_values, coverage=coverage_values,
        final_positions=positions_array, adhesion_work_mj_m2=float(adhesion),
        interface_energy_mj_m2=float(interface), stability_score=stability,
        engine=engine, status=status, metadata=metadata or {},
    )


def _fallback(*, compatibility_index: float, polar_fraction: float, filler_ratio: float, temperature_c: float, steps: int, particles: int, seed: int) -> SimulationResult:
    """Deterministic offline fallback; never labelled as an external MD result."""
    rng = np.random.default_rng(seed)
    count = max(12, int(particles))
    length = max(100, int(steps))
    thermal = np.clip((temperature_c + 30) / 120, .05, 1.25)
    attraction = .11 + .34 * compatibility_index + .28 * polar_fraction - .15 * filler_ratio
    series = -abs(attraction) * np.exp(-np.linspace(0, 5, length)) - .02 * attraction + rng.normal(0, .001, length)
    coverage = np.clip((.12 + .55 * attraction) * (1 - np.exp(-np.linspace(0, 4, length))), 0, 1)
    positions = rng.uniform([-4, -4, .25], [4, 4, 2.5], size=(count, 3))
    return _from_series(np.arange(1, length + 1), series, coverage=coverage, positions=positions, particles=count, engine="fallback", status="proxy-fallback", metadata={"reason": "No completed LAMMPS/GROMACS output supplied", "thermal_factor": float(thermal)})


def run_interface_simulation(
    *, compatibility_index: float, polar_fraction: float, filler_ratio: float,
    temperature_c: float, steps: int = 650, particles: int = 44, seed: int = 7,
    engine: str | None = None, workdir: str | Path | None = None, command: Sequence[str] | None = None,
    thermo_output: str | None = None, energy_xvg: str | None = None,
    coverage: Sequence[float] | None = None, final_positions: np.ndarray | None = None,
    area_nm2: float = 100.0,
) -> SimulationResult:
    """Generate/run/parse an external LAMMPS or GROMACS interface job.

    Pass ``thermo_output`` for parsed LAMMPS output, or ``energy_xvg`` for a
    GROMACS energy series. ``workdir`` creates a CG interface data file and
    engine input; ``command`` optionally runs an installed executable.
    """
    selected = (engine or os.getenv("ADHESIVE_MD_ENGINE", "fallback")).lower().replace(" ", "")
    if thermo_output is not None:
        thermo = parse_lammps_thermo(thermo_output)
        energy_name = next((name for name in ("Pe", "PotEng", "E_pair", "Energy") if name in thermo), None)
        if energy_name is None:
            raise ValueError("LAMMPS thermo output needs Pe, PotEng, E_pair, or Energy")
        step_name = "Step" if "Step" in thermo else next(iter(thermo))
        return _from_series(thermo[step_name], thermo[energy_name], coverage=coverage, positions=final_positions, particles=particles, engine="lammps", status="parsed", area_nm2=area_nm2, metadata={"source": "thermo_output", "columns": tuple(thermo)})
    if energy_xvg is not None:
        x, y = parse_gromacs_xvg(energy_xvg)
        return _from_series(x, y, coverage=coverage, positions=final_positions, particles=particles, engine="gromacs", status="parsed", area_nm2=area_nm2, energy_unit="kj/mol", metadata={"source": "energy_xvg", "energy_unit": "kJ/mol"})
    if selected not in {"lammps", "gromacs", "gmx"}:
        return _fallback(compatibility_index=compatibility_index, polar_fraction=polar_fraction, filler_ratio=filler_ratio, temperature_c=temperature_c, steps=steps, particles=particles, seed=seed)
    if workdir is None:
        raise ValueError("LAMMPS/GROMACS adapter requires workdir when no completed output is supplied")
    directory = Path(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    model = build_cg_interface_model(resin_beads=max(12, particles), ceria_particles=max(1, int(max(1, particles) / 12)))
    write_cg_interface_model(model, directory)
    if selected == "lammps":
        files = {"interface.data": model.lammps_data, "in.production": model.lammps_input}
        for name, content in files.items():
            (directory / name).write_text(content, encoding="utf-8")
    else:
        files = generate_md_inputs(MDJobSpec("gromacs", "interface.data", temperature_k=temperature_c + 273.15, steps=steps), directory)
    if command is None:
        length = max(100, int(steps))
        return SimulationResult(
            np.arange(1, length + 1), np.zeros(length), np.zeros(length), _empty_positions(particles),
            0.0, 0.0, 0.0, engine=selected, status="input-generated", job_directory=str(directory),
            metadata={"files": tuple(files), "requires_external_run": True},
        )
    output = run_external_command(command, cwd=directory)
    if selected == "lammps":
        return run_interface_simulation(compatibility_index=compatibility_index, polar_fraction=polar_fraction, filler_ratio=filler_ratio, temperature_c=temperature_c, steps=steps, particles=particles, seed=seed, engine="lammps", thermo_output=output, coverage=coverage, final_positions=final_positions, area_nm2=area_nm2)
    return run_interface_simulation(compatibility_index=compatibility_index, polar_fraction=polar_fraction, filler_ratio=filler_ratio, temperature_c=temperature_c, steps=steps, particles=particles, seed=seed, engine="gromacs", energy_xvg=output, coverage=coverage, final_positions=final_positions, area_nm2=area_nm2)
