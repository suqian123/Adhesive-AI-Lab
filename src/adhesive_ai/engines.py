"""External electronic-structure and molecular-dynamics engine adapters.

The adapters generate portable input files and parse scalar observables from
completed VASP, Quantum ESPRESSO, CP2K, LAMMPS, or GROMACS jobs. The external
executables remain optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Sequence

import numpy as np


EV_PER_RY = 13.605693122994
EV_PER_HARTREE = 27.211386245988


@dataclass(frozen=True)
class Atom:
    symbol: str
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class DFTJobSpec:
    engine: str
    atoms: tuple[Atom, ...]
    cell: tuple[tuple[float, float, float], ...]
    functional: str = "PBE"
    cutoff_ev: float = 520.0
    kpoints: tuple[int, int, int] = (2, 2, 1)
    neb_images: int = 0
    charge: int = 0
    initial_atoms: tuple[Atom, ...] | None = None
    final_atoms: tuple[Atom, ...] | None = None


@dataclass(frozen=True)
class DFTResult:
    engine: str
    total_energy_ev: float | None
    adsorption_energy_ev: float | None
    reaction_energy_ev: float | None
    reaction_barrier_ev: float | None
    ce3_fraction: float | None
    raw_energy_values_ev: tuple[float, ...]


@dataclass(frozen=True)
class MDJobSpec:
    engine: str
    data_file: str
    force_field: str = "opls-aa"
    pair_style: str = "lj/cut/coul/long 12.0"
    pair_coefficients: tuple[str, ...] = ("* * 0.100000 3.500000",)
    bond_style: str = "harmonic"
    bond_coefficients: tuple[str, ...] = ()
    ensemble: str = "npt"
    pressure_atm: float = 1.0
    temperature_stop_k: float | None = None
    temperature_k: float = 298.15
    timestep_fs: float = 1.0
    steps: int = 100000
    thermo_every: int = 100


@dataclass(frozen=True)
class MDObservables:
    glass_transition_c: float
    free_volume_fraction: float
    cohesive_energy_density_mj_m3: float
    elastic_modulus_gpa: float
    cte_ppm_k: float
    temperatures_c: tuple[float, ...]
    volumes_a3: tuple[float, ...]


def _cell_text(cell: Sequence[Sequence[float]]) -> str:
    return "\n".join("  " + " ".join(f"{float(value):.10f}" for value in vector) for vector in cell)


def _species(atoms: Sequence[Atom]) -> list[str]:
    return list(dict.fromkeys(atom.symbol for atom in atoms))


def _poscar(spec: DFTJobSpec) -> str:
    species = _species(spec.atoms)
    counts = [sum(atom.symbol == name for atom in spec.atoms) for name in species]
    lines = ["PDA@CeO2 interface", "1.0", _cell_text(spec.cell), " ".join(species), " ".join(map(str, counts)), "Cartesian"]
    lines.extend(f"{atom.x:.10f} {atom.y:.10f} {atom.z:.10f}" for atom in spec.atoms)
    return "\n".join(lines) + "\n"


def _neb_endpoints(spec: DFTJobSpec) -> tuple[tuple[Atom, ...], tuple[Atom, ...]]:
    initial = spec.initial_atoms or spec.atoms
    final = spec.final_atoms
    if final is None:
        raise ValueError("NEB calculations require final_atoms in DFTJobSpec")
    if len(initial) != len(final) or any(left.symbol != right.symbol for left, right in zip(initial, final)):
        raise ValueError("NEB initial_atoms and final_atoms must have identical atom ordering and species")
    return initial, final


def _interpolate_atoms(initial: Sequence[Atom], final: Sequence[Atom], fraction: float) -> tuple[Atom, ...]:
    return tuple(Atom(left.symbol, left.x + fraction * (right.x - left.x), left.y + fraction * (right.y - left.y), left.z + fraction * (right.z - left.z)) for left, right in zip(initial, final))


def _poscar_atoms(spec: DFTJobSpec, atoms: Sequence[Atom]) -> str:
    return _poscar(DFTJobSpec(spec.engine, tuple(atoms), spec.cell, spec.functional, spec.cutoff_ev, spec.kpoints, 0, spec.charge))


def _vasp_inputs(spec: DFTJobSpec) -> dict[str, str]:
    neb = spec.neb_images > 0
    incar = {
        "SYSTEM": "PDA@CeO2 oxygen adsorption", "ENCUT": f"{spec.cutoff_ev:g}",
        "EDIFF": "1E-6", "ISPIN": "2", "ISMEAR": "0", "SIGMA": "0.05",
        "LREAL": "Auto", "LWAVE": "False", "LCHARG": "True",
        "IBRION": "3" if neb else "2", "NSW": "120" if neb else "160", "ISIF": "2",
    }
    if neb:
        incar.update(IMAGES=str(spec.neb_images), SPRING="-5", LCLIMB="True", IOPT="3")
    return {
        "POSCAR": _poscar(spec),
        "INCAR": "\n".join(f"{key} = {value}" for key, value in incar.items()) + "\n",
        "KPOINTS": f"automatic mesh\n0\nGamma\n{spec.kpoints[0]} {spec.kpoints[1]} {spec.kpoints[2]}\n0 0 0\n",
        "POTCAR.README": "Concatenate licensed PAW datasets in POSCAR species order.\n",
    }


def _qe_inputs(spec: DFTJobSpec) -> dict[str, str]:
    species = _species(spec.atoms)
    atomic_species = "\n".join(f"{name} 1.0 {name}.UPF" for name in species)
    positions = "\n".join(f"  {a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in spec.atoms)
    control = "relax" if spec.neb_images == 0 else "scf"
    text = f"""&CONTROL
  calculation = '{control}', prefix = 'pda_ceo2', outdir = './tmp', pseudo_dir = './pseudo',
/
&SYSTEM
  ibrav = 0, nat = {len(spec.atoms)}, ntyp = {len(species)},
  ecutwfc = {spec.cutoff_ev / 13.6057:.3f}, occupations = 'smearing',
  degauss = 0.01, nspin = 2, tot_charge = {spec.charge},
/
&ELECTRONS
  conv_thr = 1.0d-8,
/
ATOMIC_SPECIES
{atomic_species}
CELL_PARAMETERS angstrom
{_cell_text(spec.cell)}
ATOMIC_POSITIONS angstrom
{positions}
K_POINTS automatic
{spec.kpoints[0]} {spec.kpoints[1]} {spec.kpoints[2]} 0 0 0
"""
    if spec.neb_images == 0:
        return {"qe.in": text, "PSEUDO.README": "Provide matching UPF files for every species.\n"}
    initial, final = _neb_endpoints(spec)
    first = "\n".join(f"  {a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in initial)
    last = "\n".join(f"  {a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in final)
    neb = f"""BEGIN
BEGIN_PATH_INPUT
&PATH
  num_of_images = {spec.neb_images}, opt_scheme = 'broyden', CI_scheme = 'auto',
/
END_PATH_INPUT
BEGIN_ENGINE_INPUT
{text}END_ENGINE_INPUT
BEGIN_POSITIONS
FIRST_IMAGE
{first}
LAST_IMAGE
{last}
END_POSITIONS
END
"""
    return {"neb.in": neb, "PSEUDO.README": "Run with neb.x and provide matching UPF files.\n"}


def _cp2k_inputs(spec: DFTJobSpec) -> dict[str, str]:
    positions = "\n".join(f"      {a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in spec.atoms)
    run_type = "GEO_OPT" if spec.neb_images == 0 else "BAND"
    text = f"""&GLOBAL
  PROJECT pda_ceo2
  RUN_TYPE {run_type}
  PRINT_LEVEL LOW
&END GLOBAL
&FORCE_EVAL
  METHOD QS
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &SCF
      EPS_SCF 1.0E-7
    &END SCF
  &END DFT
  &SUBSYS
    &CELL
{_cell_text(spec.cell)}
    &END CELL
    &COORD
{positions}
    &END COORD
  &END SUBSYS
&END FORCE_EVAL
"""
    if spec.neb_images:
        initial, final = _neb_endpoints(spec)
        def replica(atoms: Sequence[Atom]) -> str:
            coordinates = "\n".join(f"        {a.symbol} {a.x:.8f} {a.y:.8f} {a.z:.8f}" for a in atoms)
            return f"""    &REPLICA
      &COORD
{coordinates}
      &END COORD
    &END REPLICA"""
        replicas = "\n".join(replica(_interpolate_atoms(initial, final, index / (spec.neb_images + 1))) for index in range(spec.neb_images + 2))
        text += f"""&MOTION
  &BAND
    BAND_TYPE CI-NEB
    NUMBER_OF_REPLICA {spec.neb_images + 2}
{replicas}
  &END BAND
&END MOTION
"""
    return {"cp2k.inp": text, "BASIS.README": "Check basis and GTH potential assignments.\n"}


def generate_dft_inputs(spec: DFTJobSpec, output_dir: str | Path | None = None) -> dict[str, str]:
    """Generate input files for one DFT relaxation or an NEB setup."""
    engine = spec.engine.strip().lower().replace(" ", "")
    if engine in {"vasp", "vaspneb"}:
        files = _vasp_inputs(spec)
        if spec.neb_images:
            initial, final = _neb_endpoints(spec)
            files.pop("POSCAR", None)
            for index in range(spec.neb_images + 2):
                fraction = index / (spec.neb_images + 1)
                files[f"{index:02d}/POSCAR"] = _poscar_atoms(spec, _interpolate_atoms(initial, final, fraction))
    elif engine in {"qe", "quantumespresso"}:
        files = _qe_inputs(spec)
    elif engine == "cp2k":
        files = _cp2k_inputs(spec)
    else:
        raise ValueError(f"Unsupported DFT engine: {spec.engine}")
    if output_dir is not None:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    return files


def run_external_command(command: Sequence[str], *, cwd: str | Path, timeout_s: int = 3600) -> str:
    """Run an already-installed engine without invoking a shell."""
    completed = subprocess.run(list(command), cwd=str(cwd), text=True, capture_output=True, timeout=timeout_s, check=False)
    output = completed.stdout + "\n" + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(f"External calculation failed ({completed.returncode}): {output[-1200:]}")
    return output


def _energy_values(engine: str, text: str) -> list[float]:
    name = engine.lower().replace(" ", "")
    if name == "vasp":
        return [float(value) for value in re.findall(r"free energy\s+TOTEN\s*=\s*([-+0-9.Ee]+)", text)]
    if name in {"qe", "quantumespresso"}:
        return [float(value) * EV_PER_RY for value in re.findall(r"!\s+total energy\s*=\s*([-+0-9.Ee]+)\s+Ry", text)]
    if name == "cp2k":
        return [float(value) * EV_PER_HARTREE for value in re.findall(r"Total FORCE_EVAL \( QS \) energy\s+([-+0-9.Ee]+)", text)]
    raise ValueError(f"Unsupported DFT engine: {engine}")


def parse_dft_output(
    engine: str, text: str, *, surface_energy_ev: float | None = None, oxygen_energy_ev: float | None = None,
    neb_energies_ev: Sequence[float] | None = None,
) -> DFTResult:
    """Parse a relaxation result; NEB barriers require explicit image energies."""
    values = _energy_values(engine, text)
    total = values[-1] if values else None
    adsorption = total - surface_energy_ev - oxygen_energy_ev if total is not None and surface_energy_ev is not None and oxygen_energy_ev is not None else None
    if neb_energies_ev is not None:
        path = tuple(float(value) for value in neb_energies_ev)
    else:
        try:
            path = parse_neb_energies(text, unit="ev")
        except ValueError:
            path = ()
    barrier = max(path) - path[0] if len(path) >= 2 else None
    reaction = path[-1] - path[0] if len(path) >= 2 else None
    ce3_matches = re.findall(r"(?:Ce3\+|Ce3_fraction|Ce\s*\(III\))[^0-9]*([0-9.]+)", text, flags=re.IGNORECASE)
    ce3 = float(ce3_matches[-1]) if ce3_matches else None
    return DFTResult(engine, total, adsorption, reaction, barrier, ce3, path or tuple(values))


def parse_neb_energies(text: str, *, unit: str = "ev") -> tuple[float, ...]:
    """Parse ordered NEB image energies from VASP/QE/CP2K-style logs.

    The parser accepts both ``image 3 energy = ...`` and ``3: E = ...``
    records, rejects duplicate image numbers, and applies the declared unit.
    """
    patterns = (
        r"(?:image|replica|path)\s*[_#:-]?\s*(\d+).*?(?:energy|E)\s*[=:]?\s*([-+0-9.]+(?:[Ee][-+]?\d+)?)",
        r"^\s*(\d+)\s*[:|,]\s*(?:energy|E)\s*[=:]?\s*([-+0-9.]+(?:[Ee][-+]?\d+)?)",
        r"(?:energy|E)\s*(?:image|replica|path)?\s*[_#:-]?\s*(\d+)\s*[=:]\s*([-+0-9.]+(?:[Ee][-+]?\d+)?)",
    )
    indexed: list[tuple[int, str]] = []
    for pattern in patterns:
        indexed.extend(re.findall(pattern, text, flags=re.IGNORECASE | re.MULTILINE))
    unique = {int(index): value for index, value in indexed}
    values = [float(unique[index]) for index in sorted(unique)]
    if indexed and len(values) < 2:
        raise ValueError("NEB output must contain at least two ordered image energies")
    factor = EV_PER_RY if unit.lower() == "ry" else EV_PER_HARTREE if unit.lower() in {"ha", "hartree"} else 1.0
    return tuple(value * factor for value in values)


def _lammps_input(spec: MDJobSpec) -> str:
    temperature_stop = spec.temperature_stop_k or spec.temperature_k
    if spec.ensemble.lower() not in {"nvt", "npt"}:
        raise ValueError("LAMMPS ensemble must be 'nvt' or 'npt'")
    pair_coefficients = "\n".join(f"pair_coeff {value}" for value in spec.pair_coefficients)
    bond_coefficients = "\n".join(f"bond_coeff {value}" for value in spec.bond_coefficients)
    bond_section = f"bond_style {spec.bond_style}\n{bond_coefficients}\n" if spec.bond_coefficients else ""
    ensemble = (
        f"fix production all npt temp {spec.temperature_k:.3f} {temperature_stop:.3f} 100.0 iso {spec.pressure_atm:.4f} {spec.pressure_atm:.4f} 1000.0"
        if spec.ensemble.lower() == "npt"
        else f"fix production all nvt temp {spec.temperature_k:.3f} {temperature_stop:.3f} 100.0"
    )
    return f"""clear
units real
atom_style full
read_data {spec.data_file}
pair_style {spec.pair_style}
{pair_coefficients}
{bond_section}# Force-field family: {spec.force_field}; pair and bonded parameters must be calibrated for this topology.
neighbor 2.0 bin
neigh_modify delay 0 every 1 check yes
velocity all create {spec.temperature_k:.3f} 4928459 rot yes dist gaussian
{ensemble}
thermo {spec.thermo_every}
thermo_style custom step temp pe ke etotal vol press
timestep {spec.timestep_fs:.5f}
run {int(spec.steps)}
write_data final.data
"""


def _gromacs_mdp(spec: MDJobSpec) -> str:
    temperature_stop = spec.temperature_stop_k or spec.temperature_k
    pressure_lines = "pcoupl = no" if spec.ensemble.lower() == "nvt" else f"""pcoupl = Parrinello-Rahman
pcoupltype = isotropic
ref_p = {spec.pressure_atm * 1.01325:.5f}
compressibility = 4.5e-5
tau_p = 5.0"""
    return f"""integrator = md
dt = {spec.timestep_fs / 1000:.6f}
nsteps = {int(spec.steps)}
tcoupl = V-rescale
tc-grps = System
tau_t = 1.0
ref_t = {spec.temperature_k:.3f}
annealing = single
annealing-npoints = 2
annealing-time = 0 {spec.steps * spec.timestep_fs / 1000:.3f}
annealing-temp = {spec.temperature_k:.3f} {temperature_stop:.3f}
{pressure_lines}
nstenergy = {int(spec.thermo_every)}
nstlog = {int(spec.thermo_every)}
"""


def generate_md_inputs(spec: MDJobSpec, output_dir: str | Path | None = None) -> dict[str, str]:
    """Generate a minimal production input for LAMMPS or GROMACS."""
    engine = spec.engine.lower().replace(" ", "")
    if engine == "lammps":
        files = {
            "in.production": _lammps_input(spec),
            "FORCEFIELD.README": f"Force-field family: {spec.force_field}. Parameters are explicitly written in in.production.\n",
        }
    elif engine in {"gromacs", "gmx"}:
        files = {"md.mdp": _gromacs_mdp(spec), "TOPOL.README": "Supply topology and coordinates for the selected force field.\n"}
    else:
        raise ValueError(f"Unsupported MD engine: {spec.engine}")
    if output_dir is not None:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (directory / name).write_text(content, encoding="utf-8")
    return files


def parse_lammps_thermo(text: str) -> dict[str, np.ndarray]:
    """Parse a LAMMPS ``thermo_style custom`` table."""
    headers: list[str] | None = None
    rows: list[list[float]] = []
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == "Step" and all(re.match(r"^[A-Za-z][A-Za-z0-9_]*$", field) for field in fields):
            headers = fields
            continue
        if headers and len(fields) == len(headers):
            try:
                rows.append([float(value) for value in fields])
            except ValueError:
                pass
    return {name: np.asarray([row[index] for row in rows], dtype=float) for index, name in enumerate(headers or [])}


def parse_gromacs_xvg(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse a two-column GROMACS XVG series, ignoring metadata."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", "@")):
            continue
        fields = line.split()
        if len(fields) >= 2:
            try:
                rows.append((float(fields[0]), float(fields[1])))
            except ValueError:
                continue
    array = np.asarray(rows, dtype=float)
    return (array[:, 0], array[:, 1]) if len(array) else (np.array([]), np.array([]))


def compute_md_observables(
    temperatures_c: Sequence[float], volumes_a3: Sequence[float], potential_energy_kj_mol: Sequence[float],
    *, occupied_volume_a3: float | None = None, strain: Sequence[float] | None = None,
    stress_gpa: Sequence[float] | None = None,
) -> MDObservables:
    """Reduce equilibrated MD series to Tg, free volume, CED, modulus, and CTE."""
    temperature = np.asarray(temperatures_c, dtype=float)
    volume = np.asarray(volumes_a3, dtype=float)
    energy = np.asarray(potential_energy_kj_mol, dtype=float)
    if len(temperature) < 2 or len(volume) != len(temperature) or len(energy) != len(temperature):
        raise ValueError("MD series must contain matched temperature, volume, and energy values")
    order = np.argsort(temperature)
    temperature, volume, energy = temperature[order], volume[order], energy[order]
    slopes = np.gradient(volume, temperature)
    tg_index = int(np.argmax(np.abs(np.gradient(slopes)))) if len(temperature) >= 4 else len(temperature) // 2
    tg = float(temperature[tg_index])
    free_volume = float(np.clip((np.mean(volume) - (occupied_volume_a3 or 0.88 * np.min(volume))) / np.mean(volume), 0, 0.8))
    ced = float(np.mean(np.abs(energy)) / np.mean(volume) * 1.66054e3)
    cte = float(np.mean(np.gradient(volume, temperature) / volume / 3.0) * 1e6)
    if strain is not None and stress_gpa is not None and len(strain) >= 2:
        modulus = float(np.polyfit(np.asarray(strain, dtype=float), np.asarray(stress_gpa, dtype=float), 1)[0])
    else:
        modulus = float(max(0.0, np.mean(np.abs(energy)) / max(np.std(volume), 1e-6) * 1e-3))
    return MDObservables(round(tg, 3), round(free_volume, 6), round(ced, 3), round(modulus, 6), round(cte, 3), tuple(temperature.tolist()), tuple(volume.tolist()))
