"""Persistent one-click orchestration for multiscale external calculations.

External programs are never guessed. Each calculation family uses a command
profile from the environment, or a detected local LAMMPS executable for the
generated coarse-grained input. A command containing ``{task_file}`` is treated
as a validated task-wrapper contract; direct solver commands require their
normal input files to exist in the generated task directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .campaign import CalculationTask, MultiscaleCampaign, write_multiscale_campaign
from .jobs import get_job_status, parse_job_result, split_job_command, submit_job, update_job_metadata
from .result_integration import apply_external_results, to_jsonable


TERMINAL_TASK_STATUSES = {"completed", "failed", "blocked"}
ACTIVE_RUN_STATUSES = {"queued", "running"}
PROFILE_SPECS = {
    "dft": ("VASP", "OUTCAR"),
    "bulk_md": ("LAMMPS", "log.lammps"),
    "interface_md": ("LAMMPS", "log.lammps"),
    "coarse_grained": ("LAMMPS", "log.lammps"),
}
PROFILE_CONFIG_PATH = Path("work/multiscale_profiles.json")
ENV_PREFIXES = {
    "dft": "ADHESIVE_DFT",
    "bulk_md": "ADHESIVE_BULK_MD",
    "interface_md": "ADHESIVE_INTERFACE_MD",
    "coarse_grained": "ADHESIVE_CG",
}
AUTO_ENGINE_COMMANDS = {
    "dft": (
        ("VASP", ("vasp_std", "vasp_gam"), "", "OUTCAR"),
        ("Quantum ESPRESSO", ("pw.x",), "-in scf.in", "scf.out"),
        ("CP2K", ("cp2k.psmp", "cp2k"), "-i cp2k.inp -o cp2k.out", "cp2k.out"),
    ),
    "bulk_md": (
        ("LAMMPS", ("lmp", "lmp_serial"), "-in in.production", "log.lammps"),
        ("GROMACS", ("gmx",), "mdrun -deffnm production", "potential.xvg"),
    ),
    "interface_md": (
        ("LAMMPS", ("lmp", "lmp_serial"), "-in in.production", "log.lammps"),
        ("GROMACS", ("gmx",), "mdrun -deffnm production", "potential.xvg"),
    ),
    "coarse_grained": (
        ("LAMMPS", ("lmp", "lmp_serial"), "-in in.cg", "log.lammps"),
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_path(root: str | Path, run_id: str) -> Path:
    if not run_id or any(value in run_id for value in ("/", "\\", "..")):
        raise ValueError(f"Invalid campaign run ID: {run_id!r}")
    return Path(root).expanduser().resolve() / run_id / "run.json"


def _write_run(record: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    payload = dict(to_jsonable(record))
    payload["updated_at"] = _now()
    target = _run_path(root, str(payload["run_id"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return payload


def get_campaign_run(run_id: str, *, root: str | Path = "work/campaign_runs") -> dict[str, Any]:
    return json.loads(_run_path(root, run_id).read_text(encoding="utf-8"))


def list_campaign_runs(
    *, root: str | Path = "work/campaign_runs", candidate_id: str | None = None,
) -> list[dict[str, Any]]:
    directory = Path(root)
    if not directory.exists():
        return []
    records = []
    for path in directory.glob("*/run.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if candidate_id is None or str(record.get("candidate_id")) == str(candidate_id):
            records.append(record)
    return sorted(records, key=lambda item: str(item.get("created_at", "")), reverse=True)


def engine_profiles_from_env(environ: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Load external command profiles without assuming installed licensed tools."""
    env = os.environ if environ is None else environ
    profiles: dict[str, dict[str, Any]] = {}
    for category, (default_engine, default_result) in PROFILE_SPECS.items():
        prefix = ENV_PREFIXES[category]
        command = str(env.get(f"{prefix}_COMMAND", "")).strip()
        engine = str(env.get(f"{prefix}_ENGINE", default_engine)).strip() or default_engine
        result_file = str(env.get(f"{prefix}_RESULT_FILE", default_result)).strip() or default_result
        profiles[category] = {
            "category": category,
            "engine": engine,
            "command": command,
            "result_file": result_file,
            "surface_energy_ev": env.get("ADHESIVE_DFT_SURFACE_ENERGY_EV") if category == "dft" else None,
            "oxygen_energy_ev": env.get("ADHESIVE_DFT_OXYGEN_ENERGY_EV") if category == "dft" else None,
        }
    for category, options in AUTO_ENGINE_COMMANDS.items():
        if profiles[category]["command"]:
            continue
        for engine, executable_names, arguments, result_file in options:
            executable = next((path for name in executable_names if (path := shutil.which(name))), None)
            if not executable:
                continue
            command = f'"{executable}" {arguments}'.strip()
            profiles[category].update(command=command, engine=engine, result_file=result_file)
            break
    return profiles


def load_engine_profiles(
    path: str | Path = PROFILE_CONFIG_PATH,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load page-saved profiles over environment defaults."""
    profiles = engine_profiles_from_env(environ)
    target = Path(path)
    if not target.is_file():
        return profiles
    try:
        saved = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return profiles
    if not isinstance(saved, Mapping):
        return profiles
    for category in PROFILE_SPECS:
        profile = saved.get(category)
        if not isinstance(profile, Mapping):
            continue
        profiles[category].update({
            "category": category,
            "engine": str(profile.get("engine") or profiles[category]["engine"]),
            "command": str(profile.get("command") or "").strip(),
            "result_file": str(profile.get("result_file") or profiles[category]["result_file"]),
        })
        for name in ("surface_energy_ev", "oxygen_energy_ev"):
            if category == "dft" and profile.get(name) not in (None, ""):
                profiles[category][name] = profile[name]
    return profiles


def save_engine_profiles(
    profiles: Mapping[str, Mapping[str, Any]],
    path: str | Path = PROFILE_CONFIG_PATH,
) -> Path:
    """Persist the four non-shell external command profiles selected in the UI."""
    payload: dict[str, dict[str, Any]] = {}
    for category, (default_engine, default_result) in PROFILE_SPECS.items():
        profile = profiles.get(category, {})
        payload[category] = {
            "engine": str(profile.get("engine") or default_engine),
            "command": str(profile.get("command") or "").strip(),
            "result_file": str(profile.get("result_file") or default_result),
        }
        for name in ("surface_energy_ev", "oxygen_energy_ev"):
            if category == "dft" and profile.get(name) not in (None, ""):
                payload[category][name] = profile[name]
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def _command_check(command_text: str) -> str:
    if not command_text.strip():
        return "未填写"
    try:
        command = split_job_command(command_text)
    except ValueError:
        return "命令格式无效"
    executable = command[0] if command else ""
    explicit = Path(executable).expanduser()
    found = (explicit.is_file() if explicit.is_absolute() else False) or bool(shutil.which(executable))
    return "已找到可执行程序" if found else f"未找到：{executable}"


def campaign_environment_frame(profiles: Mapping[str, Mapping[str, Any]] | None = None) -> pd.DataFrame:
    selected = dict(profiles or engine_profiles_from_env())
    labels = {"dft": "量子化学 DFT", "bulk_md": "树脂体相 MD", "interface_md": "界面 MD", "coarse_grained": "粗粒化动力学"}
    return pd.DataFrame([{
        "计算类别": labels[category],
        "计算引擎": profile.get("engine"),
        "执行命令": profile.get("command") or "未配置",
        "结果文件": profile.get("result_file"),
        "配置状态": "命令已填写" if profile.get("command") else "未填写",
        "程序检查": _command_check(str(profile.get("command") or "")),
    } for category, profile in selected.items()])


def _category(task: CalculationTask | Mapping[str, Any]) -> str:
    scale = task.scale if isinstance(task, CalculationTask) else str(task.get("scale"))
    kind = task.calculation_kind if isinstance(task, CalculationTask) else str(task.get("calculation_kind"))
    return "coarse_grained" if scale == "coarse-grained" else kind


def _dependencies(task: CalculationTask, tasks: tuple[CalculationTask, ...]) -> list[str]:
    if task.scale == "coarse-grained":
        return [item.task_id for item in tasks if item.calculation_kind == "interface_md" and item.scale != "coarse-grained"]
    if task.calculation_kind == "interface_md":
        return [item.task_id for item in tasks if item.calculation_kind in {"dft", "bulk_md"}]
    return []


def _required_inputs(category: str, engine: str) -> tuple[str, ...]:
    normalized = engine.lower().replace(" ", "")
    if category == "dft":
        if normalized == "vasp":
            return ("POSCAR", "INCAR", "KPOINTS", "POTCAR")
        if normalized in {"quantumespresso", "qe"}:
            return ("scf.in",)
        if normalized == "cp2k":
            return ("cp2k.inp",)
    if category in {"bulk_md", "interface_md"}:
        return ("in.production",) if normalized == "lammps" else ("production.tpr",)
    if category == "coarse_grained":
        return ("interface.data", "in.cg")
    return ()


def _expand_command(command: str, *, task_dir: Path, task_file: Path, campaign: MultiscaleCampaign) -> tuple[str, ...]:
    values = {
        "task_dir": str(task_dir), "task_file": str(task_file),
        "candidate_id": campaign.candidate_id, "campaign_id": campaign.campaign_id,
    }
    return tuple(token.format(**values) for token in split_job_command(command))


def _preflight(
    task: CalculationTask, profile: Mapping[str, Any], task_dir: Path, campaign: MultiscaleCampaign,
) -> tuple[tuple[str, ...], str | None]:
    command_text = str(profile.get("command") or "").strip()
    if not command_text:
        return (), f"未配置 {ENV_PREFIXES[_category(task)]}_COMMAND"
    try:
        command = _expand_command(command_text, task_dir=task_dir, task_file=task_dir / "task.json", campaign=campaign)
    except (ValueError, KeyError) as exc:
        return (), f"命令模板无效：{exc}"
    executable = command[0] if command else ""
    explicit = Path(executable).expanduser()
    if not ((explicit.is_file() if explicit.is_absolute() else False) or shutil.which(executable)):
        return command, f"找不到外部程序：{executable}"
    # Wrapper profiles receive task.json and own validated input generation.
    if "{task_file}" not in command_text:
        missing = [name for name in _required_inputs(_category(task), str(profile.get("engine"))) if not (task_dir / name).is_file()]
        if missing:
            return command, "缺少经验证的输入文件：" + "、".join(missing)
    return command, None


def _job_metadata(task: CalculationTask, profile: Mapping[str, Any], campaign: MultiscaleCampaign, run_id: str) -> dict[str, Any]:
    category = _category(task)
    snapshot = campaign.candidate_snapshot
    metadata: dict[str, Any] = {
        "candidate_id": campaign.candidate_id,
        "campaign_run_id": run_id,
        "campaign_task_id": task.task_id,
        "calculation_kind": "interface_md" if category == "coarse_grained" else task.calculation_kind,
        "result_file": profile.get("result_file"),
        "area_nm2": float(task.conditions.get("area_nm2", 100.0)),
        "filler_ratio": float(snapshot.get("filler_pct", 0.0)) / 100.0,
        "polar_fraction": float(snapshot.get("resin_polarity", 0.5)),
        "compatibility_index": float(snapshot.get("low_temp_toughness_index", 0.5)),
        "temperature_c": float(task.conditions.get("temperature_c", 25.0)),
        "temperature_unit": "K",
        "energy_unit": "kcal/mol" if str(profile.get("engine", "")).lower() == "lammps" else "kJ/mol",
        **dict(task.conditions),
    }
    if category == "dft":
        for name in ("surface_energy_ev", "oxygen_energy_ev"):
            if profile.get(name) not in (None, ""):
                metadata[name] = float(profile[name])
    return to_jsonable(metadata)


def _run_status(tasks: list[dict[str, Any]]) -> str:
    statuses = {str(task.get("status")) for task in tasks}
    if statuses & {"pending", "queued", "running"}:
        return "running"
    completed = sum(task.get("status") == "completed" for task in tasks)
    if completed == len(tasks):
        return "completed"
    if completed:
        return "partial"
    if "failed" in statuses:
        return "failed"
    return "blocked"


def start_campaign_run(
    campaign: MultiscaleCampaign,
    *,
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
    root: str | Path = "work/campaign_runs",
    job_root: str | Path = "work/jobs",
    max_parallel: int | None = None,
    launch_supervisor: bool = True,
) -> dict[str, Any]:
    """Generate a campaign package, preflight it, and start ready tasks."""
    selected_profiles = {key: dict(value) for key, value in (profiles or engine_profiles_from_env()).items()}
    run_id = f"{campaign.candidate_id}-{uuid.uuid4().hex[:10]}"
    run_directory = _run_path(root, run_id).parent
    package_paths = write_multiscale_campaign(campaign, run_directory / "package")
    package_directory = package_paths["campaign"].parent
    task_states = []
    for task in campaign.tasks:
        category = _category(task)
        profile = selected_profiles.get(category, {})
        task_dir = package_directory / "tasks" / task.task_id
        command, blocker = _preflight(task, profile, task_dir, campaign)
        task_states.append({
            "task_id": task.task_id,
            "scale": task.scale,
            "calculation_kind": task.calculation_kind,
            "category": category,
            "objective": task.objective,
            "conditions": to_jsonable(task.conditions),
            "expected_outputs": list(task.expected_outputs),
            "dependencies": _dependencies(task, campaign.tasks),
            "engine": profile.get("engine"),
            "command": list(command),
            "result_file": profile.get("result_file"),
            "metadata": _job_metadata(task, profile, campaign, run_id),
            "workdir": str(task_dir),
            "status": "blocked" if blocker else "pending",
            "blocker": blocker,
            "job_id": None,
        })
    record = _write_run({
        "run_id": run_id,
        "campaign_id": campaign.campaign_id,
        "candidate_id": campaign.candidate_id,
        "created_at": _now(),
        "status": "queued",
        "integrated_at": None,
        "integration_error": None,
        "package_directory": str(package_directory),
        "job_root": str(Path(job_root).expanduser().resolve()),
        "max_parallel": max(1, int(max_parallel or os.getenv("ADHESIVE_CAMPAIGN_MAX_PARALLEL", "2"))),
        "candidate_snapshot": campaign.candidate_snapshot,
        "tasks": task_states,
        "result_payload": {},
    }, root)
    record = advance_campaign_run(run_id, root=root)
    if launch_supervisor and record["status"] in ACTIVE_RUN_STATUSES:
        _launch_supervisor(run_id, root=root)
    return get_campaign_run(run_id, root=root)


def advance_campaign_run(run_id: str, *, root: str | Path = "work/campaign_runs") -> dict[str, Any]:
    """Refresh child jobs and submit dependency-ready tasks up to the limit."""
    record = get_campaign_run(run_id, root=root)
    job_root = record["job_root"]
    tasks = [dict(task) for task in record["tasks"]]
    by_id = {task["task_id"]: task for task in tasks}
    for task in tasks:
        if task["status"] not in {"queued", "running"} or not task.get("job_id"):
            continue
        try:
            job = get_job_status(task["job_id"], root=job_root)
            task["status"] = job.status
            task["return_code"] = job.return_code
            task["error"] = job.error
        except Exception as exc:
            task.update(status="failed", error=str(exc))

    running = sum(task["status"] in {"queued", "running"} for task in tasks)
    capacity = max(0, int(record["max_parallel"]) - running)
    for task in tasks:
        if task["status"] != "pending" or capacity <= 0:
            continue
        dependency_states = [by_id[name]["status"] for name in task.get("dependencies", [])]
        if any(status in {"failed", "blocked"} for status in dependency_states):
            task.update(status="blocked", blocker="依赖任务失败或被阻塞")
            continue
        if any(status != "completed" for status in dependency_states):
            continue
        metadata = {
            **task.get("metadata", {}),
            "candidate_id": record["candidate_id"],
            "campaign_run_id": run_id,
            "campaign_task_id": task["task_id"],
            "calculation_kind": "interface_md" if task["category"] == "coarse_grained" else task["calculation_kind"],
            "result_file": task.get("result_file"),
            "area_nm2": float(task.get("conditions", {}).get("area_nm2", 100.0)),
            "filler_ratio": float(record["candidate_snapshot"].get("filler_pct", 0.0)) / 100.0,
            "polar_fraction": float(record["candidate_snapshot"].get("resin_polarity", 0.5)),
            "compatibility_index": float(record["candidate_snapshot"].get("low_temp_toughness_index", 0.5)),
            "temperature_c": float(task.get("conditions", {}).get("temperature_c", 25.0)),
            "temperature_unit": "K",
            "energy_unit": "kcal/mol" if str(task.get("engine", "")).lower() == "lammps" else "kJ/mol",
            **task.get("conditions", {}),
        }
        try:
            job = submit_job(task["engine"], task["command"], workdir=task["workdir"], root=job_root, metadata=metadata)
            task.update(status=job.status, job_id=job.job_id, blocker=None)
            capacity -= 1
        except Exception as exc:
            task.update(status="failed", error=str(exc))
    record["tasks"] = tasks
    record["status"] = _run_status(tasks)
    if record["status"] not in ACTIVE_RUN_STATUSES and not record.get("result_payload"):
        record["result_payload"] = collect_campaign_payload(record)
    return _write_run(record, root)


def _launch_supervisor(run_id: str, *, root: str | Path) -> None:
    source_root = str(Path(__file__).resolve().parents[1])
    bootstrap = (
        "import sys; "
        f"sys.path.insert(0, {source_root!r}); "
        "from adhesive_ai.campaign_runner import _supervise; "
        f"raise SystemExit(_supervise({str(Path(root).resolve())!r}, {run_id!r}))"
    )
    launcher = [sys.executable, "-c", bootstrap]
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    subprocess.Popen(launcher, **options)


def _supervise(root: str | Path, run_id: str) -> int:
    while True:
        record = advance_campaign_run(run_id, root=root)
        if record["status"] not in ACTIVE_RUN_STATUSES:
            return 0 if record["status"] in {"completed", "partial", "blocked"} else 1
        time.sleep(2.0)


def _aggregate_dft(results: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {"task_results": results, "observables_available": bool(results)}
    rules = {
        "adsorption_energy_ev": min, "reaction_energy_ev": min,
        "reaction_barrier_ev": min, "ce3_fraction": max,
        "reactive_oxygen_capture_index": max,
    }
    for name, reducer in rules.items():
        values = [float(item[name]) for item in results if item.get(name) is not None and np.isfinite(float(item[name]))]
        if values:
            output[name] = reducer(values)
    output["job_id"] = "campaign-aggregate"
    return output


def collect_campaign_payload(record_or_id: Mapping[str, Any] | str, *, root: str | Path = "work/campaign_runs") -> dict[str, Any]:
    """Parse completed child jobs and aggregate them into the database schema."""
    from .workflow import calculation_payload

    record = get_campaign_run(record_or_id, root=root) if isinstance(record_or_id, str) else dict(record_or_id)
    dft_results: list[dict[str, Any]] = []
    md_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    interface_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for task in record["tasks"]:
        if task.get("status") != "completed" or not task.get("job_id"):
            continue
        try:
            job = get_job_status(task["job_id"], root=record["job_root"])
            parsed = parse_job_result(task["job_id"], root=record["job_root"])
            payload = calculation_payload(job, parsed, root=record["job_root"])
        except Exception as exc:
            task["parse_error"] = str(exc)
            continue
        if payload.get("dft"):
            result = dict(payload["dft"])
            result["task_id"] = task["task_id"]
            dft_results.append(result)
        if payload.get("md"):
            md_results.append((task, dict(payload["md"])))
        if payload.get("interface"):
            interface_results.append((task, dict(payload["interface"])))
    aggregate: dict[str, Any] = {"dft": {}, "md": {}, "interface": {}}
    if dft_results:
        aggregate["dft"] = _aggregate_dft(dft_results)
    usable_md = [(task, result) for task, result in md_results if result.get("observables_available")]
    if usable_md:
        target = float(record["candidate_snapshot"].get("crosslink_density", 0.65))
        selected_task, selected_md = min(usable_md, key=lambda item: abs(float(item[0].get("conditions", {}).get("crosslink_density", target)) - target))
        aggregate["md"] = {**selected_md, "task_id": selected_task["task_id"], "task_results": [item for _, item in usable_md]}
    if interface_results:
        # Prefer atomistic interface MD; retain CG as supporting task results.
        selected_task, selected_interface = next(
            ((task, result) for task, result in interface_results if task.get("category") == "interface_md"),
            interface_results[0],
        )
        aggregate["interface"] = {
            **selected_interface,
            "task_id": selected_task["task_id"],
            "task_results": [result for _, result in interface_results],
        }
    return to_jsonable(aggregate)


def integrate_campaign_run(
    run_id: str,
    candidates: pd.DataFrame,
    *,
    experiments: pd.DataFrame | None = None,
    root: str | Path = "work/campaign_runs",
    model_root: str | Path = "work/models",
    top_n: int = 12,
) -> Any | None:
    """Write one terminal campaign aggregate, retrain, and mark it integrated."""
    from .database import save_model_version, save_simulation
    from .screening import predict_screening, save_model, screen_candidates
    from .workflow import IntegrationResult

    record = get_campaign_run(run_id, root=root)
    if record.get("integrated_at"):
        return None
    if record["status"] not in {"completed", "partial"}:
        raise RuntimeError(f"Campaign {run_id} is {record['status']}, not ready for integration")
    payload = dict(record.get("result_payload") or collect_campaign_payload(record))
    if not any(payload.get(name) for name in ("dft", "md", "interface")):
        raise ValueError("Campaign completed without parseable scientific observables")
    candidate_id = str(record["candidate_id"])
    updated = apply_external_results(candidates, {candidate_id: payload})
    version = f"campaign-{run_id[-8:]}"
    shortlist, model = screen_candidates(
        updated,
        experiments=experiments if experiments is not None and not experiments.empty else None,
        top_n=top_n,
        minimum_class="C",
        version=version,
    )
    predictions = predict_screening(model, updated)
    row = predictions.loc[predictions["candidate_id"].astype(str) == candidate_id].iloc[0].to_dict()
    save_simulation(row, payload.get("dft", {}), payload.get("md", {}), payload.get("interface", {}), model.version)
    artifact = save_model(model, Path(model_root) / f"{model.version}.npz")
    save_model_version(model, str(artifact))
    integrated_at = _now()
    record.update(integrated_at=integrated_at, integrated_model_version=model.version, integration_error=None)
    _write_run(record, root)
    for task in record["tasks"]:
        if task.get("job_id"):
            update_job_metadata(task["job_id"], {"integrated_at": integrated_at, "integrated_model_version": model.version}, root=record["job_root"])
    return IntegrationResult(run_id, candidate_id, updated, shortlist, model, payload)


def campaign_run_frame(record: Mapping[str, Any]) -> pd.DataFrame:
    return pd.DataFrame([{
        "任务编号": task.get("task_id"),
        "计算尺度": task.get("scale"),
        "计算类型": task.get("category"),
        "计算引擎": task.get("engine") or "未配置",
        "状态": task.get("status"),
        "阻塞或错误原因": task.get("blocker") or task.get("error") or task.get("parse_error") or "—",
        "外部任务编号": task.get("job_id") or "—",
    } for task in record.get("tasks", [])])


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--supervise":
        raise SystemExit(_supervise(sys.argv[2], sys.argv[3]))
    raise SystemExit("Usage: campaign_runner.py --supervise ROOT RUN_ID")
