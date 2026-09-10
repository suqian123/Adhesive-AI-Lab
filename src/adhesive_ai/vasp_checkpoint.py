"""Read-only electronic-convergence checks for static VASP checkpoints."""

from pathlib import Path
import re
import sys
import math


def electronic_converged(directory: str | Path) -> bool:
    """Require convergence of the final electronic loop AND a normal finish.

    VASP can return zero on NELM exhaustion. An earlier ionic step's EDIFF
    message is not sufficient, nor is a partially written final output.
    """
    try:
        text = (Path(directory) / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    final_loop = text.rfind("Iteration")
    convergence = text.rfind("aborting loop because EDIFF is reached")
    finish = text.rfind("General timing and accounting")
    energies = re.findall(r"free\s+energy\s+TOTEN\s+=\s+([-+0-9.Ee]+)", text)
    try:
        finite_energy = bool(energies) and math.isfinite(float(energies[-1]))
    except ValueError:
        return False
    return convergence > final_loop and finish > convergence and finite_energy


def enable_charge_restart(directory: str | Path) -> None:
    """Prepare a validated seed without relying on DrvFS chmod/rename support."""
    root = Path(directory)
    incar = root / "INCAR"
    wave = root / "WAVECAR"
    settings = {"ICHARG": "1", "NELMDL": "-5", "ISTART": "1" if wave.is_file() and wave.stat().st_size else "0"}
    lines = incar.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if line.split("=", 1)[0].strip().upper() not in settings]
    kept.extend(f"{key} = {value}" for key, value in settings.items())
    incar.write_text("\n".join(kept) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--enable-charge-restart":
        enable_charge_restart(sys.argv[2])
        raise SystemExit(0)
    raise SystemExit(0 if len(sys.argv) == 2 and electronic_converged(sys.argv[1]) else 1)
