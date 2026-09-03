"""Prepare and run one approved campaign DFT task through WSL VASP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from adhesive_ai.vasp_production import prepare_campaign_dft_task, validate_vasp_input_set


def _approved(path: Path, *, expected_facet: str) -> bool:
    if not path.is_file():
        return False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("approved") is not True or not payload.get("evidence"):
        return False
    report_path = Path(str(payload["evidence"][0]))
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        payload.get("facet") == expected_facet
        and report.get("facet") == expected_facet
        and report.get("passed") is True
        and report.get("scientific_status") == "convergence-approved"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_file", type=Path)
    parser.add_argument("--resources", type=Path, default=PROJECT_ROOT / "work" / "vasp_resources.json")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--allow-unvalidated", action="store_true")
    parser.add_argument("--distro", default="Ubuntu-24.04")
    parser.add_argument("--user", default="vasp")
    parser.add_argument("--np", type=int, default=4)
    parser.add_argument("--executable", default="/usr/local/bin/vasp_std")
    args = parser.parse_args()

    task_file = args.task_file.expanduser().resolve()
    task = json.loads(task_file.read_text(encoding="utf-8"))
    facet = str((task.get("conditions") or {}).get("facet") or "(111)")
    if facet not in {"(111)", "(110)", "(100)"}:
        raise SystemExit(f"Unsupported CeO2 facet: {facet}")
    approval = args.approval or (
        PROJECT_ROOT / "work" / "vasp_validation" / f"ceo2-{facet.strip('()')}-baseline-v1" / "approved.json"
    )
    prepare_campaign_dft_task(task, task_file.parent, resources=args.resources)
    report = validate_vasp_input_set(task_file.parent, resources=args.resources)
    if not report["valid"]:
        raise SystemExit(f"Static VASP input validation failed: {report}")
    if args.prepare_only:
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if not args.allow_unvalidated and not _approved(approval, expected_facet=facet):
        raise SystemExit(
            f"Production run is locked until {facet} convergence evidence is approved in {approval}"
        )

    translated = subprocess.run(
        ["wsl.exe", "-d", args.distro, "-u", args.user, "--", "wslpath", "-a", str(task_file.parent)],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    command = [
        "wsl.exe", "-d", args.distro, "-u", args.user, "--cd", translated, "--",
        "env", "OMP_NUM_THREADS=1", "OMP_STACKSIZE=512m", "mpirun", "-np", str(args.np), args.executable,
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
