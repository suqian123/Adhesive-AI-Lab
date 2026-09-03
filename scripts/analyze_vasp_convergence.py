"""Analyze completed VASP convergence jobs and unlock production when justified."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re


ENERGY_PATTERN = re.compile(r"free\s+energy\s+TOTEN\s+=\s+([-+0-9.Ee]+)")


def _result(job: dict[str, object]) -> dict[str, object]:
    directory = Path(str(job["path"]))
    marker_path = directory / "run_status.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    outcar_path = directory / "OUTCAR"
    outcar = outcar_path.read_text(encoding="utf-8", errors="replace") if outcar_path.is_file() else ""
    energies = [float(value) for value in ENERGY_PATTERN.findall(outcar)]
    manifest = json.loads((directory / "input_manifest.json").read_text(encoding="utf-8"))
    atom_count = int(manifest["atom_count"])
    return {
        "path": str(directory),
        "axis": manifest["validation_axis"],
        "encut_ev": int(manifest["encut_ev"]),
        "kpoints": manifest["kpoints"],
        "slab_layers": int(manifest["slab_layers"]),
        "vacuum_a": float(manifest["vacuum_a"]),
        "atom_count": atom_count,
        "status": marker.get("status", "pending"),
        "complete": marker.get("complete") is True and "General timing and accounting" in outcar,
        "electronic_convergence": "aborting loop because EDIFF is reached" in outcar,
        "total_energy_ev": energies[-1] if energies else None,
        "energy_ev_per_atom": energies[-1] / atom_count if energies else None,
    }


def _last_delta_per_atom(results: list[dict[str, object]], key) -> float | None:
    ordered = sorted(results, key=key)
    if len(ordered) < 2 or any(item["total_energy_ev"] is None for item in ordered[-2:]):
        return None
    left, right = ordered[-2:]
    return abs(float(right["energy_ev_per_atom"]) - float(left["energy_ev_per_atom"]))


def analyze(plan_path: Path) -> dict[str, object]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    facet = str(plan.get("facet") or "")
    if facet not in {"(111)", "(110)", "(100)"}:
        raise ValueError(f"Convergence plan has an unsupported facet: {facet!r}")
    results = [_result(job) for job in plan["jobs"]]
    groups = {
        axis: [result for result in results if result["axis"] == axis]
        for axis in ("encut", "kpoints", "slab_layers", "vacuum")
    }
    threshold = float(plan["acceptance"]["total_energy_change_ev_per_atom_max"])
    deltas = {
        "encut_ev_per_atom": _last_delta_per_atom(groups["encut"], lambda item: item["encut_ev"]),
        # A convergence series is judged by its two finest meshes.  Requiring
        # the deliberately coarse starting mesh to agree would reject a
        # perfectly converged 2x2x1 -> 3x3x1 sequence.
        "kpoints_ev_per_atom": _last_delta_per_atom(groups["kpoints"], lambda item: np_product(item["kpoints"])),
        "vacuum_ev_per_atom": _last_delta_per_atom(groups["vacuum"], lambda item: item["vacuum_a"]),
    }
    layers = sorted(groups["slab_layers"], key=lambda item: item["slab_layers"])
    slab_second_difference = None
    if len(layers) >= 3 and all(item["total_energy_ev"] is not None for item in layers[-3:]):
        e2, e3, e4 = (float(item["total_energy_ev"]) for item in layers[-3:])
        slab_second_difference = abs(e4 - 2.0 * e3 + e2)
    checks = {
        "all_jobs_complete": all(result["complete"] for result in results),
        "all_electronic_steps_converged": all(result["electronic_convergence"] for result in results),
        "encut": deltas["encut_ev_per_atom"] is not None and deltas["encut_ev_per_atom"] <= threshold,
        "kpoints": deltas["kpoints_ev_per_atom"] is not None and deltas["kpoints_ev_per_atom"] <= threshold,
        "vacuum": deltas["vacuum_ev_per_atom"] is not None and deltas["vacuum_ev_per_atom"] <= threshold,
        "slab_layers": slab_second_difference is not None and slab_second_difference <= float(plan["acceptance"]["slab_second_difference_ev_max"]),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "facet": facet,
        "scientific_status": "convergence-approved" if all(checks.values()) else "pending-or-failed-convergence",
        "passed": all(checks.values()),
        "checks": checks,
        "deltas": {**deltas, "slab_second_difference_ev": slab_second_difference},
        "thresholds": plan["acceptance"],
        "results": results,
    }


def np_product(values: object) -> int:
    product = 1
    for value in list(values):
        product *= int(value)
    return product


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args()
    report = analyze(args.plan)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.approval and report["passed"]:
        approval = {
            "approved": True,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "model": "CeO2-fluorite/PDA-dopamine-tetramer-baseline-v1",
            "facet": report["facet"],
            "scope": "numerical VASP production baseline",
            "evidence": [str(args.report.resolve()), str(args.plan.resolve())],
            "settings": {
                "functional": "PBE-D3(BJ)",
                "ivdw": 12,
                "ce_u_eff_ev": 4.5,
                "encut_ev": 520,
                "reference_kpoints": [2, 2, 1],
                "slab_layers": 3,
                "vacuum_a": 18.0,
            },
            "limitations": [
                "Ce Ueff=4.5 eV and lattice a=5.411 A are user-confirmed baseline assumptions, not fitted experimental parameters",
                "PDA is represented by a covalently aryl-linked dopamine tetramer",
                "Each NEB endpoint still requires task-specific relaxation before barrier reporting",
                "The supplied vdW kernel is installed but unused because the local 8 GiB host cannot run the nonlocal optB86b grid",
            ],
        }
        args.approval.parent.mkdir(parents=True, exist_ok=True)
        args.approval.write_text(json.dumps(approval, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "deltas": report["deltas"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
