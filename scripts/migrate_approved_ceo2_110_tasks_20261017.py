"""Rebuild blocked CeO2(110) campaign inputs after neutral-facet approval."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import (
    _vasp_production_approved, get_campaign_run, resume_approved_vasp_tasks,
    vasp_approval_path_for_facet,
)
from adhesive_ai.vasp_production import (
    VaspBaseline, prepare_campaign_dft_task, validate_vasp_input_set,
)

RUN_ID = "CL5-00016-1b4ebc3d36"
RUN_ROOT = ROOT / "work" / "campaign_runs"
RUN_DIR = RUN_ROOT / RUN_ID
RUN_PATH = RUN_DIR / "run.json"
RESOURCES = ROOT / "work" / "vasp_resources.json"
FACET = "(110)"
EXPECTED_TASKS = {
    "dft-o-110-v00-h00", "dft-o-110-v00-h35", "dft-o-110-v08-h00",
    "dft-o-110-v08-h35", "dft-o-110-v15-h00", "dft-o-110-v15-h35",
    "dft-pda-binding-110",
}
OUTPUT_FILES = ("OUTCAR", "OSZICAR", "vasprun.xml", "CHGCAR", "WAVECAR", "CONTCAR")


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def selected_tasks(record: dict) -> dict[str, dict]:
    return {task["task_id"]: task for task in record["tasks"]
            if task.get("category") == "dft"
            and (task.get("conditions") or {}).get("facet") == FACET}


def approved_settings() -> VaspBaseline:
    approval = json.loads(vasp_approval_path_for_facet(FACET).read_text())
    if not _vasp_production_approved(vasp_approval_path_for_facet(FACET), expected_facet=FACET):
        raise RuntimeError("Neutral (110) convergence approval is not valid")
    settings = approval["settings"]
    if (settings.get("encut_ev") != 650 or settings.get("slab_layers") != 4
            or settings.get("vacuum_a") != 18.0
            or settings.get("ce_u_eff_ev") != 4.5):
        raise RuntimeError(f"Approved production settings changed: {settings}")
    return VaspBaseline(cutoff_ev=650, slab_layers=4, vacuum_a=18.0,
                        ce_u_eff_ev=4.5)


def preflight() -> tuple[dict, dict[str, dict]]:
    record = get_campaign_run(RUN_ID, root=RUN_ROOT)
    if (record.get("status") != "running" or record.get("candidate_id") != "CL5-00016"
            or record.get("max_parallel") != 2 or record.get("integrated_at")):
        raise RuntimeError("Campaign identity or state changed")
    tasks = selected_tasks(record)
    if set(tasks) != EXPECTED_TASKS:
        raise RuntimeError(f"Unexpected (110) tasks: {sorted(tasks)}")
    approved_settings()
    package = Path(record["package_directory"]).resolve()
    for name, task in tasks.items():
        directory = Path(task["workdir"]).resolve()
        if (directory != package / "tasks" / name
                or task.get("status") != "blocked" or task.get("job_id")
                or task.get("input_validation") != "static-valid; pending-convergence-approval"):
            raise RuntimeError(f"Task is not an untouched blocked input: {name}")
        if any((directory / filename).exists() for filename in OUTPUT_FILES):
            raise RuntimeError(f"Task already has VASP outputs: {name}")
        manifest = json.loads((directory / "input_manifest.json").read_text())
        if (manifest.get("surface_termination") != "legacy-v1"
                or manifest.get("encut_ev") != 520 or manifest.get("slab_layers") != 3):
            raise RuntimeError(f"Source input is not the known legacy model: {name}")
        if not (directory / "task.json").is_file():
            raise RuntimeError(f"Missing task specification: {name}")
    return record, tasks


def prepare() -> dict:
    record, tasks = preflight()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stage = RUN_DIR / f".neutral-110-staging-{stamp}"
    stage.mkdir(parents=True, exist_ok=False)
    settings = approved_settings()
    snapshots = {}
    for name, task in tasks.items():
        directory = stage / name
        directory.mkdir()
        source = Path(task["workdir"])
        shutil.copy2(source / "task.json", directory / "task.json")
        prepare_campaign_dft_task(task, directory, resources=RESOURCES, settings=settings)
        validation = validate_vasp_input_set(directory, resources=RESOURCES)
        manifest = json.loads((directory / "input_manifest.json").read_text())
        if (not validation["valid"] or manifest.get("surface_termination") != "neutral-CeO2-layers-v2"
                or manifest.get("encut_ev") != 650 or manifest.get("slab_layers") != 4
                or manifest.get("vacuum_a") != 18.0):
            raise RuntimeError(f"Regenerated input failed checks: {name}, {validation}, {manifest}")
        files = [p for p in directory.rglob("*") if p.is_file()]
        snapshots[name] = {str(p.relative_to(directory)): digest(p) for p in files}
    marker = {"run_id": RUN_ID, "facet": FACET, "prepared_at": datetime.now(timezone.utc).isoformat(),
              "stage": str(stage), "source_run_updated_at": record["updated_at"],
              "approved_settings": {"encut_ev": 650, "slab_layers": 4, "vacuum_a": 18.0},
              "hashes": snapshots}
    (stage / "prepared.json").write_text(json.dumps(marker, indent=2) + "\n")
    return {"prepared": True, "stage": str(stage), "task_count": len(tasks),
            "settings": marker["approved_settings"],
            "atom_counts": {name: json.loads((stage / name / "input_manifest.json").read_text())["atom_count"]
                            for name in tasks}}


def apply(stage: Path) -> dict:
    stage = stage.resolve()
    if stage.parent != RUN_DIR or not stage.name.startswith(".neutral-110-staging-"):
        raise RuntimeError("Stage is outside the expected campaign directory")
    marker = json.loads((stage / "prepared.json").read_text())
    if marker.get("run_id") != RUN_ID or marker.get("facet") != FACET or marker.get("stage") != str(stage):
        raise RuntimeError("Staging marker does not match this campaign")
    record, tasks = preflight()
    for name, expected in marker["hashes"].items():
        if name not in tasks:
            raise RuntimeError(f"Unexpected staged task: {name}")
        directory = stage / name
        actual = {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*") if p.is_file()}
        if actual != expected or not validate_vasp_input_set(directory, resources=RESOURCES)["valid"]:
            raise RuntimeError(f"Staged input changed: {name}")
    if set(marker["hashes"]) != set(tasks):
        raise RuntimeError("Staged task list is incomplete")
    archive = RUN_DIR / (".legacy-110-inputs-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    archive.mkdir(parents=True, exist_ok=False)
    shutil.copy2(RUN_PATH, archive / "run.json.before")
    swaps = []
    for name, task in tasks.items():
        source = Path(task["workdir"])
        old = archive / name
        source.rename(old)
        try:
            (stage / name).rename(source)
        except Exception:
            old.rename(source)
            raise
        swaps.append(name)
    after = resume_approved_vasp_tasks(RUN_ID, root=RUN_ROOT,
                                       vasp_resources=RESOURCES, launch_supervisor=False)
    time.sleep(3)
    after = get_campaign_run(RUN_ID, root=RUN_ROOT)
    statuses = {name: task["status"] for name, task in selected_tasks(after).items()}
    if any(status not in {"pending", "queued", "running"} for status in statuses.values()):
        after = resume_approved_vasp_tasks(RUN_ID, root=RUN_ROOT,
                                           vasp_resources=RESOURCES, launch_supervisor=False)
        statuses = {name: task["status"] for name, task in selected_tasks(after).items()}
    if any(status not in {"pending", "queued", "running"} for status in statuses.values()):
        raise RuntimeError(f"Inputs migrated but task unlock needs attention: {statuses}")
    return {"migrated": swaps, "archive": str(archive), "statuses": statuses,
            "campaign_status": after["status"], "integrated_at": after.get("integrated_at")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--apply", type=Path)
    args = parser.parse_args()
    if args.prepare == bool(args.apply):
        parser.error("Select exactly one of --prepare or --apply STAGING_DIRECTORY")
    print(json.dumps(prepare() if args.prepare else apply(args.apply), ensure_ascii=False))
