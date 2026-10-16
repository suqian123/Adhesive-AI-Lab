"""Run isolated installation checks; outputs never enter the candidate database."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from adhesive_ai.campaign_runner import available_engine_profiles, load_engine_profiles
from adhesive_ai.engines import parse_dft_output, parse_lammps_thermo
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_resources import install_vasp_resources, load_vasp_resource_config
from mpi_launch import launch_plan


def run_engine(command, directory, logfile, timeout=180):
    with logfile.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            raise RuntimeError(f"Engine timed out; inspect {logfile}")
    if exit_code:
        raise RuntimeError(f"Engine exited with {exit_code}; inspect {logfile}")


def main():
    profiles = load_engine_profiles(ROOT / "work/multiscale_profiles.json")
    if set(available_engine_profiles(profiles)) != {"dft", "bulk_md", "interface_md", "coarse_grained"}:
        raise RuntimeError("One or more engine launchers are unavailable")
    resources = ROOT / "work/vasp_resources.json"
    load_vasp_resource_config(resources)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "work/engine_checks" / stamp
    directory.mkdir(parents=True, mode=0o700)
    lammps = directory / "lammps"
    lammps.mkdir()
    (lammps / "in.smoke").write_text((ROOT / "deploy/compute/in.smoke-lammps").read_text())
    run_engine([str(ROOT / "deploy/compute/run-lammps.sh"), "-in", "in.smoke"],
               lammps, lammps / "stdout.log")
    thermo = parse_lammps_thermo((lammps / "log.lammps").read_text())
    if "Step" not in thermo or int(thermo["Step"][-1]) != 100:
        raise RuntimeError("LAMMPS did not finish 100 installation-check steps")
    if not all(math.isfinite(float(value)) for values in thermo.values() for value in values):
        raise RuntimeError("LAMMPS produced non-finite thermo data")
    styles = subprocess.check_output([str(ROOT / "engines/lammps-30Sep2026/bin/lmp"), "-h"], text=True)
    required = ["MOLECULE", "KSPACE", "EXTRA-MOLECULE", "REACTION", "lj/cut/coul/long", "fourier", "cvff", "pppm", "bond/react"]
    missing = [item for item in required if item not in styles]
    if missing:
        raise RuntimeError(f"LAMMPS required features missing: {missing}")
    vasp = directory / "vasp-h2"
    vasp.mkdir()
    (vasp / "POSCAR").write_text("H2 installation check\n1.0\n8 0 0\n0 8 0\n0 0 8\nH\n2\nCartesian\n3.63 4 4\n4.37 4 4\n")
    (vasp / "KPOINTS").write_text("Gamma installation check\n0\nGamma\n1 1 1\n0 0 0\n")
    (vasp / "INCAR").write_text("SYSTEM = H2 engine installation check\nENCUT = 300\nPREC = Normal\nEDIFF = 1E-6\nNELM = 60\nALGO = Normal\nISMEAR = 0\nSIGMA = 0.05\nNSW = 0\nIBRION = -1\nLREAL = .FALSE.\nLWAVE = .FALSE.\nLCHARG = .FALSE.\nNBANDS = 8\n")
    install_vasp_resources(vasp, resources)
    run_engine([str(ROOT / "deploy/compute/run-vasp.sh")], vasp, vasp / "stdout.log")
    result = parse_dft_output("VASP", (vasp / "OUTCAR").read_text())
    energy = result.total_energy_ev
    if not electronic_converged(vasp) or energy is None or not math.isfinite(energy):
        raise RuntimeError("VASP H2 check did not reach electronic convergence and normal termination")
    report = {"status": "installation-check-passed",
              "mpi_ranks_by_engine": {
                  "vasp": launch_plan("vasp", [], os.environ, directory=vasp)["mpi_ranks"],
                  "lammps": launch_plan("lammps", ["-in", "in.smoke"], os.environ)["mpi_ranks"],
              },
              "server_mpi_budget": int(os.getenv("ADHESIVE_MPI_BUDGET", "0")),
              "lammps_steps": 100, "lammps_required_features": required,
              "vasp_h2_energy_ev": energy, "vasp_h2_electronic_converged": True,
              "scientific_scope": "Software installation checks; no material-production approval",
              "directory": str(directory)}
    (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
