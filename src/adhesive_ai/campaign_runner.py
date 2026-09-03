"""Persistent one-click orchestration for multiscale external calculations.

External programs are never guessed. Each calculation family uses a command
profile from the environment, or a detected local LAMMPS executable for the
generated coarse-grained input. A command containing ``{task_file}`` is treated
as a validated task-wrapper contract; direct solver commands require their
normal input files to exist in the generated task directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .campaign import CalculationTask, MultiscaleCampaign, write_multiscale_campaign
from .jobs import cancel_job, get_job_status, parse_job_result, split_job_command, submit_job, update_job_metadata
from .result_integration import apply_external_results, to_jsonable


TERMINAL_TASK_STATUSES = {"completed", "failed", "blocked", "cancelled"}
ACTIVE_RUN_STATUSES = {"queued", "running"}
PROFILE_SPECS = {
    "dft": ("VASP", "OUTCAR"),
    "bulk_md": ("LAMMPS", "log.lammps"),
    "interface_md": ("LAMMPS", "log.lammps"),
    "coarse_grained": ("LAMMPS", "log.lammps"),
}
PROFILE_CONFIG_PATH = Path("work/multiscale_profiles.json")
VASP_RESOURCE_CONFIG_PATH = Path("work/vasp_resources.json")
VASP_VALIDATION_BASE_ROOT = Path("work/vasp_validation")
VASP_FACETS = ("(111)", "(110)", "(100)")
VASP_VALIDATION_ROOT = VASP_VALIDATION_BASE_ROOT / "ceo2-111-baseline-v1"
VASP_APPROVAL_PATH = VASP_VALIDATION_ROOT / "approved.json"
MD_APPROVAL_FILENAME = "md_approval.json"
VASP_RUNNER_CONTROL_FILENAME = "runner_control.json"
VASP_RUNNER_PID_FILENAME = "runner.pid"
VASP_RUNNER_PGID_FILENAME = "runner.pgid"
VASP_LOG_STALE_SECONDS = 15 * 60
VASP_PRECONVERGENCE_STAGES = (
    ("step1-fixed-charge", "固定电荷初始化"),
    ("step2-pbe", "PBE 桥接预收敛"),
    ("step3-dftu", "DFT+U 预收敛"),
)
VASP_ELECTRONIC_STEP_PATTERN = re.compile(r"^(?:DAV|RMM|CGA):\s+(\d+)", re.MULTILINE)
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


def _normalized_vasp_facet(facet: object) -> str:
    normalized = str(facet or "").strip()
    if normalized not in VASP_FACETS:
        raise ValueError(f"Unsupported CeO2 facet: {normalized!r}")
    return normalized


def vasp_validation_root_for_facet(
    facet: object,
    *,
    base_root: str | Path = VASP_VALIDATION_BASE_ROOT,
) -> Path:
    normalized = _normalized_vasp_facet(facet)
    return Path(base_root).expanduser() / f"ceo2-{normalized.strip('()')}-baseline-v1"


def vasp_approval_path_for_facet(
    facet: object,
    *,
    base_root: str | Path = VASP_VALIDATION_BASE_ROOT,
) -> Path:
    return vasp_validation_root_for_facet(facet, base_root=base_root) / "approved.json"


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
    contracts = {
        "dft": ("POSCAR、INCAR、KPOINTS、POTCAR", "需 VASP 收敛批准"),
        "bulk_md": ("system.data、forcefield.production、in.production", "需 md_approval.json"),
        "interface_md": ("interface.data、forcefield.production、in.production", "需 md_approval.json"),
        "coarse_grained": ("interface.data、in.cg", "依赖界面 MD 标定"),
    }
    return pd.DataFrame([{
        "计算类别": labels[category],
        "计算引擎": profile.get("engine"),
        "执行命令": profile.get("command") or "未配置",
        "结果文件": profile.get("result_file"),
        "配置状态": "命令已填写" if profile.get("command") else "未填写",
        "程序检查": _command_check(str(profile.get("command") or "")),
        "输入契约": contracts[category][0],
        "生产门槛": contracts[category][1],
    } for category, profile in selected.items()])


def available_engine_profiles(
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return configured calculation profiles whose launcher is executable."""
    selected = dict(profiles or engine_profiles_from_env())
    return {
        category: dict(profile)
        for category, profile in selected.items()
        if _command_check(str(profile.get("command") or "")) == "已找到可执行程序"
    }


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
        if normalized == "lammps":
            data_file = "system.data" if category == "bulk_md" else "interface.data"
            return (data_file, "forcefield.production", "in.production")
        return ("production.tpr",)
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
        missing = [
            name for name in _required_inputs(_category(task), str(profile.get("engine")))
            if not (task_dir / name).is_file() and not (name == "POSCAR" and (task_dir / "00" / "POSCAR").is_file())
        ]
        if missing:
            return command, "缺少经验证的输入文件：" + "、".join(missing)
    return command, None


def _prepare_direct_vasp_inputs(
    task: CalculationTask,
    profile: Mapping[str, Any],
    task_dir: Path,
    *,
    resources: str | Path,
) -> tuple[str | None, str | None]:
    """Generate and statically validate native VASP inputs before submission."""
    command_text = str(profile.get("command") or "")
    engine = str(profile.get("engine") or "").lower().replace(" ", "")
    if _category(task) != "dft" or engine != "vasp" or "{task_file}" in command_text:
        return None, None
    try:
        from .vasp_production import prepare_campaign_dft_task, validate_vasp_input_set

        prepare_campaign_dft_task(
            {
                "task_id": task.task_id,
                "calculation_kind": task.calculation_kind,
                "objective": task.objective,
                "conditions": task.conditions,
            },
            task_dir,
            resources=resources,
        )
        report = validate_vasp_input_set(task_dir, resources=resources)
    except Exception as exc:
        return "generation-failed", f"VASP 输入生成或静态验证失败：{exc}"
    if not report.get("valid"):
        detail = report.get("missing") or report.get("checks") or "未知校验错误"
        return "static-invalid", f"VASP 输入静态验证未通过：{detail}"
    return "static-valid; pending-convergence-approval", None


def _lammps_production_template(task: CalculationTask | Mapping[str, Any], data_file: str) -> str:
    conditions = task.conditions if isinstance(task, CalculationTask) else dict(task.get("conditions") or {})
    temperatures_c = conditions.get("temperatures_c") or (conditions.get("temperature_c", 25.0),)
    temperatures_k = " ".join(f"{float(value) + 273.15:.2f}" for value in temperatures_c)
    return f"""# Generated production protocol. Supply validated topology and force-field files before running.
clear
units real
atom_style full
boundary p p p
include forcefield.production
read_data {data_file}

neighbor 2.0 bin
neigh_modify delay 0 every 1 check yes
thermo 1000
thermo_style custom step temp pe ke etotal vol press density
timestep 1.0

variable target_temperature index {temperatures_k}
label temperature_loop
velocity all create ${{target_temperature}} 4928459 mom yes rot yes dist gaussian
fix production all npt temp ${{target_temperature}} ${{target_temperature}} 100.0 iso 1.0 1.0 1000.0
run 100000
write_restart restart.${{target_temperature}}K
unfix production
next target_temperature
jump SELF temperature_loop
write_data final.data
"""


def _prepare_direct_lammps_inputs(
    task: CalculationTask | Mapping[str, Any],
    profile: Mapping[str, Any],
    task_dir: Path,
) -> tuple[str | None, str | None]:
    """Create an auditable MD protocol without inventing topology or force-field data."""
    category = _category(task)
    command_text = str(profile.get("command") or "")
    engine = str(profile.get("engine") or "").lower().replace(" ", "")
    if category not in {"bulk_md", "interface_md"} or engine != "lammps" or "{task_file}" in command_text:
        return None, None

    data_file = "system.data" if category == "bulk_md" else "interface.data"
    task_dir.mkdir(parents=True, exist_ok=True)
    if category == "bulk_md":
        from .md_production import provision_bulk_md_inputs

        provision_bulk_md_inputs(task, task_dir)
    elif category == "interface_md":
        from .md_production import provision_interface_md_inputs

        provision_interface_md_inputs(task, task_dir)
    input_path = task_dir / "in.production"
    requirements_path = task_dir / "MD_INPUT_REQUIREMENTS.md"
    try:
        if not input_path.exists():
            input_path.write_text(_lammps_production_template(task, data_file), encoding="utf-8")
        if not requirements_path.exists():
            requirements_path.write_text(
                "# Required validated MD inputs\n\n"
                f"- `{data_file}`: equilibrated topology with masses, charges, bonds, and box dimensions.\n"
                "- `forcefield.production`: validated LAMMPS styles and all coefficients for that topology.\n"
                "- `in.production`: generated temperature protocol; review run length and ensemble before approval.\n\n"
                "- `md_approval.json`: approval record pointing to evidence for the exact hashed topology and force field.\n\n"
                "The campaign will not submit this task until all files are present and the MD production evidence is approved.\n",
                encoding="utf-8",
            )
    except OSError as exc:
        return "generation-failed", f"LAMMPS 生产输入模板生成失败：{exc}"

    missing = [name for name in (data_file, "forcefield.production") if not (task_dir / name).is_file()]
    if missing:
        return "template-valid; pending-topology-forcefield", None
    if not _md_production_approved(task_dir, data_file=data_file):
        return (
            "static-valid; pending-md-production-approval",
            "输入已生成并通过静态验证；等待 MD 力场、交联拓扑与物性验证完成并批准生产计算",
        )
    return "static-valid; md-production-approved", None


def prepare_standalone_external_task(
    *,
    candidate_id: str,
    calculation_kind: str,
    engine: str,
    workdir: str | Path,
    conditions: Mapping[str, Any],
    resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
) -> dict[str, Any]:
    """Create a standalone task directory using the same validated generators as campaigns."""
    directory = Path(workdir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    normalized_engine = engine.lower().replace(" ", "")
    task_id = f"manual-{calculation_kind}-{uuid.uuid4().hex[:8]}"
    profile = {"engine": engine, "command": ""}

    if calculation_kind == "dft" and normalized_engine == "vasp":
        task = CalculationTask(
            task_id=task_id,
            candidate_id=candidate_id,
            scale="quantum",
            calculation_kind="dft",
            objective="atomic-oxygen-surface-chemistry",
            engine_options=(engine,),
            conditions=dict(conditions),
            expected_outputs=(),
            readiness="manual-input-generation",
        )
        input_status, blocker = _prepare_direct_vasp_inputs(task, profile, directory, resources=resources)
    elif calculation_kind in {"bulk_md", "interface_md"} and normalized_engine == "lammps":
        task = {
            "scale": "atomistic-md",
            "calculation_kind": calculation_kind,
            "conditions": dict(conditions),
        }
        input_status, blocker = _prepare_direct_lammps_inputs(task, profile, directory)
    else:
        required = _required_inputs(calculation_kind, engine)
        requirements_path = directory / "MANUAL_INPUT_REQUIREMENTS.md"
        requirements_path.write_text(
            "# Manual external-task inputs\n\n"
            f"Engine: {engine}\n\n"
            "Provide and validate these files before submitting the task:\n\n"
            + "".join(f"- `{name}`\n" for name in required),
            encoding="utf-8",
        )
        input_status = "manual-input-required"
        blocker = "该引擎尚无自动输入生成器；已创建工作目录和输入要求说明。"

    required = _required_inputs(calculation_kind, engine)
    return {
        "workdir": str(directory),
        "input_status": input_status,
        "blocker": blocker,
        "required": list(required),
        "present": [name for name in required if (directory / name).is_file()],
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md_production_approved(task_dir: str | Path, *, data_file: str | None = None) -> bool:
    """Verify approval evidence and hashes for an exact all-atom MD input set."""
    directory = Path(task_dir).expanduser().resolve()
    approval_path = directory / MD_APPROVAL_FILENAME
    data_name = data_file or ("interface.data" if (directory / "interface.data").is_file() else "system.data")
    required_files = (data_name, "forcefield.production")
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        evidence = list(approval.get("evidence") or [])
        if approval.get("approved") is not True or not evidence:
            return False
        report_path = Path(str(evidence[0])).expanduser()
        if not report_path.is_absolute():
            report_path = (approval_path.parent / report_path).resolve()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        validated_files = dict(report.get("validated_files") or {})
        if not all((directory / name).is_file() for name in required_files):
            return False
        hashes_match = all(
            str(validated_files.get(name) or "").lower() == _file_sha256(directory / name)
            for name in required_files
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        hashes_match
        and report.get("passed") is True
        and report.get("scientific_status") == "md-production-approved"
        and report.get("resolution") == "all-atom"
        and str(report.get("charge_model") or "").upper() == "RESP"
        and bool(report.get("baseline_id"))
    )


def _input_artifact_manifest(task_dir: str | Path, category: str, engine: str) -> dict[str, Any]:
    """Snapshot exact solver inputs so parsed results retain reproducible provenance."""
    directory = Path(task_dir).expanduser().resolve()
    required = _required_inputs(category, engine)
    artifacts = {}
    for name in required:
        path = directory / name
        if path.is_file():
            artifacts[name] = {
                "path": str(path),
                "sha256": _file_sha256(path),
                "bytes": path.stat().st_size,
            }
    is_md = category in {"bulk_md", "interface_md"}
    return {
        "required": list(required),
        "present": sorted(artifacts),
        "complete": len(artifacts) == len(required),
        "production_approved": _md_production_approved(directory) if is_md else None,
        "artifacts": artifacts,
    }


def _vasp_production_approved(path: str | Path, *, expected_facet: object | None = None) -> bool:
    """Accept production runs only with matching-facet convergence evidence."""
    approval_path = Path(path).expanduser().resolve()
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        evidence = list(approval.get("evidence") or [])
        if approval.get("approved") is not True or not evidence:
            return False
        if expected_facet is not None and approval.get("facet") != _normalized_vasp_facet(expected_facet):
            return False
        report_path = Path(str(evidence[0])).expanduser()
        if not report_path.is_absolute():
            report_path = (approval_path.parent / report_path).resolve()
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        report.get("passed") is True
        and report.get("scientific_status") == "convergence-approved"
        and (expected_facet is None or report.get("facet") == _normalized_vasp_facet(expected_facet))
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _runner_identifiers(validation_root: Path) -> tuple[int | None, int | None]:
    def read_identifier(filename: str) -> int | None:
        try:
            value = int((validation_root / filename).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return value if value > 0 else None

    return read_identifier(VASP_RUNNER_PID_FILENAME), read_identifier(VASP_RUNNER_PGID_FILENAME)


def _windows_path_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    if os.name != "nt":
        return resolved.as_posix()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1:
        raise ValueError(f"A drive-letter path is required for WSL: {resolved}")
    suffix = resolved.as_posix().split(":", 1)[-1].lstrip("/")
    return f"/mnt/{drive}/{suffix}"


def _send_vasp_runner_signal(pid: int, pgid: int, signal_name: str) -> None:
    if signal_name not in {"STOP", "CONT"}:
        raise ValueError(f"Unsupported VASP runner signal: {signal_name}")
    distribution = os.environ.get("ADHESIVE_VASP_WSL_DISTRIBUTION", "Ubuntu-24.04")
    user = os.environ.get("ADHESIVE_VASP_WSL_USER", "vasp")
    command = f"kill -0 {pid} && kill -{signal_name} -- -{pgid}"
    completed = subprocess.run(
        ["wsl.exe", "-d", distribution, "-u", user, "--", "bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"VASP 收敛运行器不可控制：{_wsl_failure_detail(completed)}")


def _wsl_failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip()
    if "E_ACCESSDENIED" in detail or "ACCESSDENIED" in detail:
        return "当前页面服务没有访问 WSL 的权限。请重启页面服务后重试。"
    return detail or "未找到运行器进程。"


def _ensure_no_vasp_process_is_running() -> None:
    """Prevent a restart from overlapping an untracked VASP calculation."""
    distribution = os.environ.get("ADHESIVE_VASP_WSL_DISTRIBUTION", "Ubuntu-24.04")
    user = os.environ.get("ADHESIVE_VASP_WSL_USER", "vasp")
    completed = subprocess.run(
        ["wsl.exe", "-d", distribution, "-u", user, "--", "bash", "-lc", "pgrep -x vasp_std"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        process_ids = ", ".join(completed.stdout.split()) or "unknown"
        raise RuntimeError(f"检测到仍在运行的 VASP 进程（PID: {process_ids}），不能重复启动。")
    if completed.returncode != 1:
        raise RuntimeError(f"无法确认 VASP 进程状态：{_wsl_failure_detail(completed)}")


def _set_vasp_runner_state(validation_root: Path, state: str, pid: int, pgid: int) -> None:
    _write_json_file(
        validation_root / VASP_RUNNER_CONTROL_FILENAME,
        {"state": state, "pid": pid, "pgid": pgid, "updated_at": _now()},
    )


def pause_vasp_convergence(root: str | Path = VASP_VALIDATION_ROOT) -> dict[str, Any]:
    """Pause the dedicated VASP validation session without touching other jobs."""
    validation_root = Path(root).expanduser().resolve()
    progress = vasp_convergence_progress(validation_root)
    if progress.get("stalled"):
        raise RuntimeError("VASP 验证日志已停滞，无法暂停；请改用断点重新启动。")
    if not progress.get("active"):
        raise RuntimeError("当前没有可暂停的 VASP 收敛验证。")
    pid, pgid = _runner_identifiers(validation_root)
    if pid is None or pgid is None:
        raise RuntimeError("未找到 VASP 运行器 PID，无法安全暂停。")
    _send_vasp_runner_signal(pid, pgid, "STOP")
    _set_vasp_runner_state(validation_root, "paused", pid, pgid)
    return vasp_convergence_progress(validation_root)


def resume_vasp_convergence(root: str | Path = VASP_VALIDATION_ROOT) -> dict[str, Any]:
    """Continue a VASP validation session that was paused from this application."""
    validation_root = Path(root).expanduser().resolve()
    control = _read_json_file(validation_root / VASP_RUNNER_CONTROL_FILENAME)
    if control.get("state") != "paused":
        raise RuntimeError("当前 VASP 收敛验证并未处于已暂停状态。")
    pid, pgid = _runner_identifiers(validation_root)
    if pid is None or pgid is None:
        raise RuntimeError("未找到 VASP 运行器 PID，无法继续。")
    _send_vasp_runner_signal(pid, pgid, "CONT")
    _set_vasp_runner_state(validation_root, "running", pid, pgid)
    return vasp_convergence_progress(validation_root)


def cancel_vasp_convergence(root: str | Path = VASP_VALIDATION_ROOT) -> dict[str, Any]:
    """Terminate the dedicated VASP validation session and preserve its checkpoints."""
    validation_root = Path(root).expanduser().resolve()
    progress = vasp_convergence_progress(validation_root)
    if not (progress.get("active") or progress.get("paused")):
        raise RuntimeError("当前没有可取消的 VASP 收敛验证。")
    pid, pgid = _runner_identifiers(validation_root)
    if pid is None or pgid is None:
        raise RuntimeError("未找到 VASP 运行器 PID，无法安全取消。")
    _set_vasp_runner_state(validation_root, "cancelling", pid, pgid)
    distribution = os.environ.get("ADHESIVE_VASP_WSL_DISTRIBUTION", "Ubuntu-24.04")
    user = os.environ.get("ADHESIVE_VASP_WSL_USER", "vasp")
    command = f"kill -0 {pid} && kill -CONT -- -{pgid} && kill -TERM -- -{pgid}"
    completed = subprocess.run(
        ["wsl.exe", "-d", distribution, "-u", user, "--", "bash", "-lc", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"VASP 收敛运行器不可取消：{_wsl_failure_detail(completed)}")
    _set_vasp_runner_state(validation_root, "cancelled", pid, pgid)
    return vasp_convergence_progress(validation_root)


def restart_vasp_convergence(
    root: str | Path = VASP_VALIDATION_ROOT,
    *,
    facet: object = "(111)",
    resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
) -> dict[str, Any]:
    """Launch the validation runner again after an interrupted or stalled session."""
    validation_root = Path(root).expanduser().resolve()
    normalized_facet = _normalized_vasp_facet(facet)
    plan = _read_json_file(validation_root / "validation_plan.json")
    if plan and plan.get("facet") != normalized_facet:
        raise ValueError(f"Validation root belongs to {plan.get('facet')}, not {normalized_facet}")
    if not plan:
        from .vasp_production import write_convergence_suite

        write_convergence_suite(validation_root, facet=normalized_facet, resources=resources)
    approval = validation_root / "approved.json"
    progress = vasp_convergence_progress(validation_root, approval=approval)
    if progress.get("active") or progress.get("paused"):
        raise RuntimeError("VASP 收敛验证仍在运行或已暂停，不能重复启动。")
    _ensure_no_vasp_process_is_running()
    runner = Path(__file__).resolve().parents[2] / "scripts" / "run_vasp_convergence.sh"
    if not runner.is_file():
        raise FileNotFoundError(f"未找到 VASP 收敛运行脚本：{runner}")
    distribution = os.environ.get("ADHESIVE_VASP_WSL_DISTRIBUTION", "Ubuntu-24.04")
    user = os.environ.get("ADHESIVE_VASP_WSL_USER", "vasp")
    command = [
        "wsl.exe", "-d", distribution, "-u", user, "--", "/usr/bin/env",
        f"ADHESIVE_VASP_VALIDATION_ROOT={_windows_path_to_wsl(validation_root)}",
        "setsid", "bash", _windows_path_to_wsl(runner),
    ]
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)
    return {"started": True, **vasp_convergence_progress(validation_root, approval=approval)}


def ensure_vasp_convergence_running(
    root: str | Path = VASP_VALIDATION_ROOT,
    *,
    approval: str | Path | None = None,
    facet: object = "(111)",
    resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
) -> dict[str, Any]:
    """Start the shared convergence matrix only when it is needed and inactive."""
    validation_root = Path(root).expanduser().resolve()
    normalized_facet = _normalized_vasp_facet(facet)
    approval_path = Path(approval).expanduser() if approval is not None else validation_root / "approved.json"
    progress = vasp_convergence_progress(validation_root, approval=approval_path)
    if progress.get("approved"):
        return {"started": False, "reason": "approved", **progress}
    if progress.get("active"):
        return {"started": False, "reason": "running", **progress}
    if progress.get("paused"):
        resumed = resume_vasp_convergence(validation_root)
        return {"started": False, "resumed": True, "reason": "resumed", **resumed}
    return restart_vasp_convergence(validation_root, facet=normalized_facet, resources=resources)


def ensure_next_vasp_facet_convergence(
    facets: object,
    *,
    base_root: str | Path = VASP_VALIDATION_BASE_ROOT,
    resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
) -> dict[str, Any]:
    """Run at most one required facet matrix at a time on the shared VASP host."""
    requested = {_normalized_vasp_facet(facet) for facet in facets}
    ordered = [facet for facet in VASP_FACETS if facet in requested]
    progress_by_facet = {
        facet: vasp_convergence_progress(
            vasp_validation_root_for_facet(facet, base_root=base_root),
            approval=vasp_approval_path_for_facet(facet, base_root=base_root),
        )
        for facet in ordered
    }
    for facet, progress in progress_by_facet.items():
        progress.setdefault("facet", facet)
    active = next(
        (
            facet for facet, progress in progress_by_facet.items()
            if progress.get("active") or progress.get("paused")
        ),
        None,
    )
    if active is not None:
        return {"facet": active, "reason": "running", "progress": progress_by_facet, "started": False}
    pending = next((facet for facet in ordered if not progress_by_facet[facet].get("approved")), None)
    if pending is None:
        return {"facet": None, "reason": "approved", "progress": progress_by_facet, "started": False}
    pending_progress = progress_by_facet[pending]
    if pending_progress.get("available") and (
        pending_progress.get("cancelled")
        or pending_progress.get("stalled")
        or pending_progress.get("uncontrolled")
    ):
        return {"facet": pending, "reason": "interrupted", "progress": progress_by_facet, "started": False}
    root = vasp_validation_root_for_facet(pending, base_root=base_root)
    result = ensure_vasp_convergence_running(
        root,
        approval=vasp_approval_path_for_facet(pending, base_root=base_root),
        facet=pending,
        resources=resources,
    )
    return {"facet": pending, "progress": progress_by_facet, **result}


def vasp_convergence_progress(
    root: str | Path = VASP_VALIDATION_ROOT,
    *,
    approval: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize nested VASP convergence checkpoints for live UI status."""
    validation_root = Path(root).expanduser().resolve()
    approval_path = Path(approval).expanduser() if approval is not None else validation_root / "approved.json"
    runner_control = _read_json_file(validation_root / VASP_RUNNER_CONTROL_FILENAME)
    plan_path = validation_root / "validation_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "available": False,
            "approved": _vasp_production_approved(approval_path),
            "completed": 0,
            "total": 0,
            "active": False,
        }

    facet = str(plan.get("facet") or "")
    legacy_approval = validation_root.parent / "approved.json"
    if (
        facet in VASP_FACETS
        and not approval_path.is_file()
        and legacy_approval != approval_path
        and _vasp_production_approved(legacy_approval, expected_facet=facet)
    ):
        migrated = _read_json_file(legacy_approval)
        migrated["migrated_from"] = str(legacy_approval)
        _write_json_file(approval_path, migrated)
    jobs = list(plan.get("jobs") or [])
    total = int(plan.get("job_count") or len(jobs))
    completed = 0
    failed = 0
    active_detail: dict[str, Any] | None = None

    for job in jobs:
        job_path = Path(str(job.get("path") or "")).expanduser()
        if not job_path.is_absolute():
            job_path = validation_root / job_path
        try:
            job_name = job_path.resolve().relative_to(validation_root).as_posix()
        except (OSError, ValueError):
            job_name = job_path.name

        try:
            job_status = json.loads((job_path / "run_status.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            job_status = {}
        if job_status.get("complete") is True or job_status.get("status") == "complete":
            completed += 1
            continue
        if job_status.get("status") == "failed":
            failed += 1

        for stage_name, phase in VASP_PRECONVERGENCE_STAGES:
            stage_path = job_path / ".model-preconverge" / stage_name
            try:
                stage_status = json.loads((stage_path / "run_status.json").read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if stage_status.get("status") != "running":
                continue
            active_detail = {
                "job": job_name,
                "phase": phase,
                "attempt": stage_status.get("attempt"),
                "log_path": stage_path / "stage.stdout.log",
            }
            break

        if active_detail is None and job_status.get("status") == "running":
            active_detail = {
                "job": job_name,
                "phase": "正式收敛计算",
                "attempt": job_status.get("attempt"),
                "log_path": job_path / "vasp.stdout.log",
            }
        if active_detail is not None:
            break

    electronic_step = None
    updated_at = None
    stale = False
    if active_detail is not None:
        log_path = Path(active_detail.pop("log_path"))
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            matches = list(VASP_ELECTRONIC_STEP_PATTERN.finditer(log_text))
            if matches:
                electronic_step = int(matches[-1].group(1))
            modified_at = log_path.stat().st_mtime
            updated_at = datetime.fromtimestamp(modified_at, timezone.utc).isoformat()
            stale = (time.time() - modified_at) > VASP_LOG_STALE_SECONDS
        except OSError:
            stale = True

    runner_pid, runner_pgid = _runner_identifiers(validation_root)
    runner_controlled = runner_pid is not None and runner_pgid is not None
    paused = runner_control.get("state") == "paused" and runner_controlled
    cancelled = runner_control.get("state") in {"cancelling", "cancelled"}
    uncontrolled = active_detail is not None and not runner_controlled and not cancelled
    active = active_detail is not None and not stale and not paused and not cancelled and runner_controlled

    return {
        "available": True,
        "approved": _vasp_production_approved(approval_path, expected_facet=facet) if facet else False,
        "facet": facet,
        "completed": completed,
        "failed": failed,
        "total": total,
        "active": active,
        "paused": paused,
        "cancelled": cancelled,
        "uncontrolled": uncontrolled,
        "runner_pid": runner_pid,
        "runner_pgid": runner_pgid,
        "stalled": active_detail is not None and (stale or cancelled),
        "runner_state": runner_control.get("state"),
        "stale_after_seconds": VASP_LOG_STALE_SECONDS,
        "electronic_step": electronic_step,
        "updated_at": updated_at,
        **(active_detail or {}),
    }


def _job_metadata(
    task: CalculationTask,
    profile: Mapping[str, Any],
    campaign: MultiscaleCampaign,
    run_id: str,
    *,
    task_dir: str | Path | None = None,
) -> dict[str, Any]:
    category = _category(task)
    snapshot = campaign.candidate_snapshot
    metadata: dict[str, Any] = {
        "candidate_id": campaign.candidate_id,
        "formulation_id": snapshot.get("formulation_id"),
        "candidate_library_version": snapshot.get("candidate_library_version"),
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
    if task_dir is not None:
        metadata["input_artifacts"] = _input_artifact_manifest(task_dir, category, str(profile.get("engine") or ""))
    if category == "dft":
        for name in ("surface_energy_ev", "oxygen_energy_ev"):
            if profile.get(name) not in (None, ""):
                metadata[name] = float(profile[name])
    return to_jsonable(metadata)


def _run_status(tasks: list[dict[str, Any]]) -> str:
    statuses = {str(task.get("status")) for task in tasks}
    if statuses and statuses <= {"external_pending", "imported", "cancelled"}:
        if "external_pending" in statuses:
            return "external_pending"
        if statuses == {"cancelled"}:
            return "cancelled"
        if "imported" in statuses:
            return "completed"
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


def _write_external_execution_manifest(
    package_directory: Path,
    *,
    run_id: str,
    campaign: MultiscaleCampaign,
    task_states: list[dict[str, Any]],
) -> Path:
    """Record the identity and expected result path for off-platform execution."""
    payload = {
        "run_id": run_id,
        "execution_mode": "external",
        "candidate_id": campaign.candidate_id,
        "formulation_id": campaign.candidate_snapshot.get("formulation_id"),
        "candidate_library_version": campaign.candidate_snapshot.get("candidate_library_version"),
        "tasks": [
            {
                "task_id": task["task_id"],
                "engine": task.get("engine"),
                "command": task.get("command"),
                "result_file": task.get("result_file"),
                "workdir": task.get("workdir"),
                "input_validation": task.get("input_validation"),
                "expected_outputs": task.get("expected_outputs"),
            }
            for task in task_states
        ],
    }
    manifest = package_directory / "external_execution.json"
    manifest.write_text(json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def register_external_campaign_package(
    campaign: MultiscaleCampaign,
    *,
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
    root: str | Path = "work/campaign_runs",
    job_root: str | Path = "work/jobs",
    vasp_resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
) -> dict[str, Any]:
    """Create a tracked campaign package for execution on an external machine.

    This deliberately performs no preflight executable check and never submits a
    child job. It records enough identity and result-path metadata for the
    uploaded external output to be safely matched and written back later.
    """
    selected_profiles = {key: dict(value) for key, value in (profiles or engine_profiles_from_env()).items()}
    run_id = f"{campaign.candidate_id}-{uuid.uuid4().hex[:10]}"
    run_directory = _run_path(root, run_id).parent
    package_paths = write_multiscale_campaign(campaign, run_directory / "package")
    package_directory = package_paths["campaign"].parent
    task_states: list[dict[str, Any]] = []
    for task in campaign.tasks:
        category = _category(task)
        profile = selected_profiles.get(category, {})
        task_dir = package_directory / "tasks" / task.task_id
        input_validation, input_note = _prepare_direct_vasp_inputs(
            task, profile, task_dir, resources=vasp_resources,
        )
        md_input_validation, md_input_note = _prepare_direct_lammps_inputs(task, profile, task_dir)
        input_validation = input_validation or md_input_validation
        input_note = input_note or md_input_note
        if input_validation is None:
            required = _required_inputs(category, str(profile.get("engine") or ""))
            present = [name for name in required if (task_dir / name).is_file()]
            input_validation = "external-inputs-ready" if len(present) == len(required) else "external-inputs-required"
        command: tuple[str, ...] = ()
        command_text = str(profile.get("command") or "").strip()
        if command_text:
            try:
                command = _expand_command(
                    command_text, task_dir=task_dir, task_file=task_dir / "task.json", campaign=campaign,
                )
            except (ValueError, KeyError):
                # The remote environment may use a different launcher; retain the task package regardless.
                command = ()
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
            "metadata": _job_metadata(task, profile, campaign, run_id, task_dir=task_dir),
            "input_artifacts": _input_artifact_manifest(task_dir, category, str(profile.get("engine") or "")),
            "workdir": str(task_dir),
            "input_validation": input_validation,
            "blocker": input_note,
            "status": "external_pending",
            "job_id": None,
            "imported_job_id": None,
        })
    external_manifest = _write_external_execution_manifest(
        package_directory, run_id=run_id, campaign=campaign, task_states=task_states,
    )
    archive = Path(shutil.make_archive(
        str(run_directory / "external-task-package"),
        "zip",
        root_dir=package_directory,
    ))
    return _write_run({
        "run_id": run_id,
        "campaign_id": campaign.campaign_id,
        "candidate_id": campaign.candidate_id,
        "created_at": _now(),
        "status": "external_pending",
        "execution_mode": "external",
        "integrated_at": None,
        "integration_error": None,
        "package_directory": str(package_directory),
        "external_execution_manifest": str(external_manifest),
        "external_package_archive": str(archive),
        "job_root": str(Path(job_root).expanduser().resolve()),
        "max_parallel": None,
        "candidate_snapshot": campaign.candidate_snapshot,
        "tasks": task_states,
        "result_payload": {},
    }, root)


def mark_external_campaign_task_imported(
    run_id: str,
    task_id: str,
    job_id: str,
    *,
    root: str | Path = "work/campaign_runs",
) -> dict[str, Any]:
    """Mark an externally executed task after its uploaded output was integrated."""
    record = get_campaign_run(run_id, root=root)
    if record.get("execution_mode") != "external":
        return record
    tasks = [dict(task) for task in record.get("tasks", [])]
    matched = False
    for task in tasks:
        if str(task.get("task_id")) != task_id:
            continue
        task.update(
            status="imported",
            imported_job_id=job_id,
            imported_at=_now(),
            blocker=None,
            error=None,
        )
        matched = True
        break
    if not matched:
        raise ValueError(f"Campaign task not found: {task_id}")
    record["tasks"] = tasks
    record["status"] = _run_status(tasks)
    if record["status"] == "completed":
        # Each upload is already integrated independently; skip campaign aggregation.
        record["integrated_at"] = _now()
        record["integrated_model_version"] = "individual-external-imports"
    return _write_run(record, root)


def match_external_result_archive(
    record: Mapping[str, Any], archive_content: bytes,
) -> dict[str, Any]:
    """Match an external-result ZIP to pending task result paths without extracting it."""
    if record.get("execution_mode") != "external":
        raise ValueError("Only externally registered campaign packages support ZIP result import")
    if not archive_content:
        raise ValueError("External result ZIP is empty")
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_content))
    except zipfile.BadZipFile as exc:
        raise ValueError("Uploaded file is not a valid ZIP archive") from exc
    with archive:
        entries = [
            entry for entry in archive.infolist()
            if not entry.is_dir() and entry.file_size > 0
        ]
        if sum(entry.file_size for entry in entries) > 512 * 1024 * 1024:
            raise ValueError("External result ZIP exceeds the 512 MB import limit")
        normalized_entries = {
            entry.filename.replace("\\", "/").strip("/").lower(): entry
            for entry in entries
        }
        matches: list[dict[str, Any]] = []
        used_paths: set[str] = set()
        pending_task_ids: list[str] = []
        expected_result_names: set[str] = set()
        for task in record.get("tasks", []):
            if not isinstance(task, Mapping) or task.get("status") != "external_pending":
                continue
            task_id = str(task.get("task_id") or "")
            result_file = str(task.get("result_file") or (task.get("metadata") or {}).get("result_file") or "").strip()
            if not task_id or not result_file:
                continue
            result_path = Path(result_file)
            if result_path.is_absolute() or ".." in result_path.parts or not result_path.name:
                continue
            expected = result_path.as_posix().lower()
            expected_result_names.add(result_path.name.lower())
            candidate_paths = (
                f"tasks/{task_id}/{expected}",
                f"{task_id}/{expected}",
            )
            entry_path = next(
                (
                    path for path in normalized_entries
                    if any(path == suffix or path.endswith(f"/{suffix}") for suffix in candidate_paths)
                ),
                None,
            )
            if entry_path is None:
                pending_task_ids.append(task_id)
                continue
            entry = normalized_entries[entry_path]
            matches.append({
                "task_id": task_id,
                "result_file": result_file,
                "engine": task.get("engine"),
                "source_filename": entry.filename,
                "result_content": archive.read(entry),
            })
            used_paths.add(entry_path)
        return {
            "matches": matches,
            "pending_task_ids": pending_task_ids,
            "unmatched_files": [
                entry.filename
                for path, entry in normalized_entries.items()
                if path not in used_paths and Path(path).name.lower() in expected_result_names
            ],
        }


def _vasp_approval_for_task(task: Mapping[str, Any], override: str | Path | None = None) -> tuple[str, Path]:
    facet = _normalized_vasp_facet((task.get("conditions") or {}).get("facet", "(111)"))
    if override is not None:
        return facet, Path(override).expanduser()
    return facet, vasp_approval_path_for_facet(facet)


def start_campaign_run(
    campaign: MultiscaleCampaign,
    *,
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
    root: str | Path = "work/campaign_runs",
    job_root: str | Path = "work/jobs",
    max_parallel: int | None = None,
    launch_supervisor: bool = True,
    vasp_resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
    vasp_approval: str | Path | None = None,
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
        input_validation, input_blocker = _prepare_direct_vasp_inputs(
            task, profile, task_dir, resources=vasp_resources,
        )
        md_input_validation, md_input_blocker = _prepare_direct_lammps_inputs(task, profile, task_dir)
        input_validation = input_validation or md_input_validation
        command, blocker = _preflight(task, profile, task_dir, campaign)
        blocker = input_blocker or md_input_blocker or blocker
        direct_vasp = (
            category == "dft"
            and str(profile.get("engine") or "").lower().replace(" ", "") == "vasp"
            and "{task_file}" not in str(profile.get("command") or "")
        )
        facet, approval_path = _vasp_approval_for_task({"conditions": task.conditions}, vasp_approval)
        if not blocker and direct_vasp and not _vasp_production_approved(approval_path, expected_facet=facet):
            blocker = "输入已生成并通过静态验证；等待 VASP 收敛验证完成并批准生产计算"
        task_states.append({
            "task_id": task.task_id,
            "scale": task.scale,
            "calculation_kind": task.calculation_kind,
            "category": category,
            "objective": task.objective,
            "conditions": to_jsonable(task.conditions),
            "vasp_validation_facet": facet if category == "dft" else None,
            "vasp_approval_path": str(approval_path) if category == "dft" else None,
            "expected_outputs": list(task.expected_outputs),
            "dependencies": _dependencies(task, campaign.tasks),
            "engine": profile.get("engine"),
            "command": list(command),
            "result_file": profile.get("result_file"),
            "metadata": _job_metadata(task, profile, campaign, run_id, task_dir=task_dir),
            "input_artifacts": _input_artifact_manifest(task_dir, category, str(profile.get("engine") or "")),
            "workdir": str(task_dir),
            "input_validation": input_validation,
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
        "max_parallel": max(1, int(max_parallel or os.getenv("ADHESIVE_CAMPAIGN_MAX_PARALLEL", "16"))),
        "candidate_snapshot": campaign.candidate_snapshot,
        "tasks": task_states,
        "result_payload": {},
    }, root)
    record = advance_campaign_run(run_id, root=root)
    if launch_supervisor and record["status"] in ACTIVE_RUN_STATUSES:
        _launch_supervisor(run_id, root=root)
    return get_campaign_run(run_id, root=root)


def terminate_campaign_run(
    run_id: str,
    *,
    root: str | Path = "work/campaign_runs",
) -> dict[str, Any]:
    """Terminate one campaign without changing the shared VASP validation baseline."""
    record = get_campaign_run(run_id, root=root)
    if record.get("status") in {"completed", "partial", "cancelled"}:
        return record

    tasks = [dict(task) for task in record.get("tasks", [])]
    errors: list[str] = []
    for task in tasks:
        if task.get("status") == "completed":
            continue
        job_id = task.get("job_id")
        if task.get("status") in {"queued", "running"} and job_id:
            try:
                cancel_job(str(job_id), root=record["job_root"])
            except Exception as exc:
                errors.append(f"{task.get('task_id')}: {exc}")
                continue
        task.update(
            status="cancelled",
            blocker="用户终止当前多尺度运行",
            error=None,
        )

    record["tasks"] = tasks
    record["terminated_at"] = _now()
    if errors:
        record["status"] = "termination_failed"
        record["termination_error"] = "；".join(errors)
    else:
        record["status"] = "cancelled"
        record["termination_error"] = None
    return _write_run(record, root)


def resume_approved_vasp_tasks(
    run_id: str,
    *,
    root: str | Path = "work/campaign_runs",
    vasp_resources: str | Path = VASP_RESOURCE_CONFIG_PATH,
    vasp_approval: str | Path | None = None,
    launch_supervisor: bool = True,
) -> dict[str, Any]:
    """Resume statically valid DFT tasks once convergence evidence is approved."""
    record = get_campaign_run(run_id, root=root)
    if record.get("status") in {"completed", "partial", "cancelled", "termination_failed"}:
        return record

    from .vasp_production import validate_vasp_input_set

    tasks = [dict(task) for task in record.get("tasks", [])]
    resumed = False
    for task in tasks:
        if (
            task.get("category") != "dft"
            or task.get("status") != "blocked"
            or not str(task.get("input_validation") or "").startswith("static-valid")
            or "收敛" not in str(task.get("blocker") or "")
        ):
            continue
        facet, approval_path = _vasp_approval_for_task(task, vasp_approval)
        if not _vasp_production_approved(approval_path, expected_facet=facet):
            continue
        report = validate_vasp_input_set(task["workdir"], resources=vasp_resources)
        if not report.get("valid"):
            task.update(
                input_validation="static-invalid",
                blocker=f"批准后复核 VASP 输入失败：{report.get('missing') or report.get('checks')}",
            )
            continue
        task.update(
            status="pending",
            blocker=None,
            input_validation="static-valid; convergence-approved",
        )
        resumed = True
    if not resumed:
        return record

    record["tasks"] = tasks
    record["status"] = _run_status(tasks)
    record = _write_run(record, root)
    record = advance_campaign_run(run_id, root=root)
    if launch_supervisor and record["status"] in ACTIVE_RUN_STATUSES:
        _launch_supervisor(run_id, root=root)
    return get_campaign_run(run_id, root=root)


def resume_prepared_md_tasks(
    run_id: str,
    *,
    root: str | Path = "work/campaign_runs",
    launch_supervisor: bool = True,
) -> dict[str, Any]:
    """Generate MD protocols and resume tasks after validated external inputs arrive."""
    record = get_campaign_run(run_id, root=root)
    tasks = [dict(task) for task in record.get("tasks", [])]
    by_id = {str(task["task_id"]): task for task in tasks}
    changed = False
    ready = False

    for task in tasks:
        if (
            task.get("category") in {"bulk_md", "interface_md"}
            and task.get("input_validation") == "static-valid; topology-forcefield-present"
            and not _md_production_approved(task["workdir"])
        ):
            task.update(
                status="blocked",
                job_id=None,
                input_validation="static-valid; pending-md-production-approval",
                blocker="输入已生成并通过静态验证；等待 MD 力场、交联拓扑与物性验证完成并批准生产计算",
            )
            task.pop("error", None)
            task.pop("return_code", None)
            changed = True

    for task in tasks:
        if task.get("category") not in {"bulk_md", "interface_md"} or task.get("status") != "blocked":
            continue
        profile = {"engine": task.get("engine"), "command": " ".join(task.get("command") or [])}
        validation, generation_blocker = _prepare_direct_lammps_inputs(
            task, profile, Path(str(task["workdir"])),
        )
        required = _required_inputs(str(task["category"]), str(task.get("engine") or ""))
        missing = [name for name in required if not (Path(str(task["workdir"])) / name).is_file()]
        dependencies = [by_id[name] for name in task.get("dependencies", []) if name in by_id]
        failed_dependencies = [str(item["task_id"]) for item in dependencies if item.get("status") == "failed"]
        waiting_dependencies = [str(item["task_id"]) for item in dependencies if item.get("status") != "completed"]

        if generation_blocker:
            blocker = generation_blocker
        elif missing:
            blocker = "in.production 已生成并通过模板校验；缺少经验证的拓扑或力场文件：" + "、".join(missing)
        elif failed_dependencies:
            blocker = "依赖任务失败：" + "、".join(failed_dependencies)
        elif waiting_dependencies:
            blocker = "输入已就绪；等待依赖任务完成：" + "、".join(waiting_dependencies)
        else:
            blocker = None

        updates: dict[str, Any] = {"input_validation": validation, "blocker": blocker}
        updates["input_artifacts"] = _input_artifact_manifest(
            task["workdir"], str(task["category"]), str(task.get("engine") or ""),
        )
        if blocker is None:
            updates["status"] = "pending"
            ready = True
        if any(task.get(name) != value for name, value in updates.items()):
            task.update(updates)
            changed = True

    for task in tasks:
        if task.get("category") != "coarse_grained" or task.get("status") != "blocked":
            continue
        dependencies = [by_id[name] for name in task.get("dependencies", []) if name in by_id]
        failed_dependencies = [str(item["task_id"]) for item in dependencies if item.get("status") == "failed"]
        waiting_dependencies = [str(item["task_id"]) for item in dependencies if item.get("status") != "completed"]
        blocker = (
            "依赖任务失败：" + "、".join(failed_dependencies)
            if failed_dependencies
            else "等待界面 MD 结果完成校准：" + "、".join(waiting_dependencies)
            if waiting_dependencies
            else None
        )
        updates = {"blocker": blocker}
        if blocker is None:
            updates["status"] = "pending"
            ready = True
        if any(task.get(name) != value for name, value in updates.items()):
            task.update(updates)
            changed = True

    if not changed:
        return record
    record["tasks"] = tasks
    record["status"] = _run_status(tasks)
    record = _write_run(record, root)
    if ready:
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
        if task.get("category") in {"bulk_md", "interface_md"} and not _md_production_approved(task["workdir"]):
            task.update(
                status="blocked",
                input_validation="static-valid; pending-md-production-approval",
                blocker="输入已生成并通过静态验证；等待 MD 力场、交联拓扑与物性验证完成并批准生产计算",
            )
            continue
        dependency_states = [by_id[name]["status"] for name in task.get("dependencies", [])]
        failed_dependencies = [
            name for name in task.get("dependencies", []) if by_id[name]["status"] == "failed"
        ]
        blocked_dependencies = [
            name for name in task.get("dependencies", []) if by_id[name]["status"] == "blocked"
        ]
        if failed_dependencies:
            task.update(status="blocked", blocker="依赖任务失败：" + "、".join(failed_dependencies))
            continue
        if blocked_dependencies:
            task.update(status="blocked", blocker="等待依赖任务解除阻塞：" + "、".join(blocked_dependencies))
            continue
        if any(status != "completed" for status in dependency_states):
            continue
        metadata = {
            **task.get("metadata", {}),
            "candidate_id": record["candidate_id"],
            "formulation_id": record["candidate_snapshot"].get("formulation_id"),
            "candidate_library_version": record["candidate_snapshot"].get("candidate_library_version"),
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
            "submission_source": "campaign",
            "input_validation": task.get("input_validation"),
            "production_approved": (
                "approved" in str(task.get("input_validation") or "").lower()
                or bool((task.get("input_artifacts") or {}).get("production_approved"))
            ),
            **task.get("conditions", {}),
        }
        metadata["input_artifacts"] = _input_artifact_manifest(
            task["workdir"], str(task["category"]), str(task.get("engine") or ""),
        )
        task["input_artifacts"] = metadata["input_artifacts"]
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
            result["input_artifacts"] = task.get("input_artifacts") or (job.metadata or {}).get("input_artifacts", {})
            dft_results.append(result)
        if payload.get("md"):
            result = dict(payload["md"])
            result["input_artifacts"] = task.get("input_artifacts") or (job.metadata or {}).get("input_artifacts", {})
            md_results.append((task, result))
        if payload.get("interface"):
            result = dict(payload["interface"])
            result["input_artifacts"] = task.get("input_artifacts") or (job.metadata or {}).get("input_artifacts", {})
            interface_results.append((task, result))
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
    from .database import load_latest_simulation_results, save_model_version, save_simulation
    from .result_arbitration import annotate_payload, build_provenance, merge_external_payloads
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
    current_candidate = candidates.loc[candidates["candidate_id"].astype(str) == candidate_id]
    if len(current_candidate) != 1:
        raise ValueError(f"候选编号不在当前候选库：{candidate_id}")
    expected_formulation_id = str(current_candidate.iloc[0].get("formulation_id") or "").strip()
    recorded_formulation_id = str(record.get("candidate_snapshot", {}).get("formulation_id") or "").strip()
    if not recorded_formulation_id:
        raise ValueError(f"多尺度运行 {run_id} 缺少配方指纹，属于旧记录，不能自动回写。")
    if recorded_formulation_id != expected_formulation_id:
        raise ValueError(f"多尺度运行的配方指纹与当前候选不匹配：{candidate_id}。为避免错配，已拒绝自动回写。")
    component_tasks = {
        "dft": [task for task in record["tasks"] if task.get("category") == "dft" and task.get("status") == "completed"],
        "md": [task for task in record["tasks"] if task.get("category") == "bulk_md" and task.get("status") == "completed"],
        "interface": [
            task for task in record["tasks"]
            if task.get("category") in {"interface_md", "coarse_grained"} and task.get("status") == "completed"
        ],
    }
    annotated_payload: dict[str, dict[str, Any]] = {}
    for component, value in payload.items():
        if not value:
            annotated_payload[component] = {}
            continue
        tasks = component_tasks.get(component, [])
        task = tasks[0] if tasks else {}
        validation = " ".join(str(item.get("input_validation") or "") for item in tasks)
        approved = any(
            "approved" in str(item.get("input_validation") or "").lower()
            or bool((item.get("input_artifacts") or {}).get("production_approved"))
            for item in tasks
        )
        metadata = {
            **dict(task.get("conditions") or {}),
            "input_validation": validation,
            "production_approved": approved,
        }
        annotated = annotate_payload(
            {component: value},
            build_provenance(
                metadata,
                source="campaign",
                result_id=f"{run_id}:{component}",
                completed_at=str(record.get("updated_at") or record.get("created_at") or _now()),
            ),
        )
        annotated_payload[component] = annotated[component]
    latest = load_latest_simulation_results(
        [candidate_id], formulation_ids={candidate_id: expected_formulation_id},
    ).get(candidate_id, {})
    payload = merge_external_payloads(latest, annotated_payload)
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
        "候选编号": record.get("candidate_id") or "—",
        "运行编号": record.get("run_id") or "—",
        "任务编号": task.get("task_id"),
        "计算尺度": task.get("scale"),
        "计算类型": task.get("category"),
        "计算引擎": task.get("engine") or "未配置",
        "状态": task.get("status"),
        "阻塞或错误原因": task.get("blocker") or task.get("error") or task.get("parse_error") or "—",
        "外部任务编号": task.get("job_id") or "—",
        "输入可追溯性": "已记录输入哈希" if task.get("input_artifacts", {}).get("complete") else "待生成或补齐输入",
    } for task in record.get("tasks", [])])


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--supervise":
        raise SystemExit(_supervise(sys.argv[2], sys.argv[3]))
    raise SystemExit("Usage: campaign_runner.py --supervise ROOT RUN_ID")
