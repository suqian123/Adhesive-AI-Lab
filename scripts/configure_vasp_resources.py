"""Configure licensed VASP resources and install them into task directories.

The script never stores POTCAR data in Git.  It keeps only a local JSON
configuration, persists the vdW kernel outside temporary directories, and
builds each task's POTCAR in the species order declared by its POSCAR.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adhesive_ai.vasp_resources import (
    DEFAULT_POTCAR_VARIANTS,
    install_vasp_resources,
    persist_vdw_kernel,
    sha256_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--potcar-archive", type=Path)
    parser.add_argument("--vdw-kernel", type=Path)
    parser.add_argument(
        "--from-config",
        type=Path,
        help="Reuse a configuration previously written by this script",
    )
    parser.add_argument("--resource-dir", type=Path, default=Path("work/vasp_resources"))
    parser.add_argument("--config", type=Path, default=Path("work/vasp_resources.json"))
    parser.add_argument("--task-dir", type=Path, action="append", default=[])
    parser.add_argument(
        "--variant",
        action="append",
        default=[],
        metavar="ELEMENT=ARCHIVE_ENTRY",
        help="Override a POTCAR archive entry, for example Ce=Ce_3",
    )
    args = parser.parse_args()

    saved_config: dict[str, object] = {}
    if args.from_config:
        saved_config = json.loads(args.from_config.expanduser().read_text(encoding="utf-8"))
    archive_value = args.potcar_archive or saved_config.get("potcar_archive")
    kernel_value = args.vdw_kernel or saved_config.get("vdw_kernel")
    if not archive_value or not kernel_value:
        parser.error("provide --potcar-archive and --vdw-kernel, or use --from-config")
    archive = Path(str(archive_value)).expanduser().resolve()
    source_kernel = Path(str(kernel_value)).expanduser().resolve()
    if not archive.is_file():
        parser.error(f"POTCAR archive does not exist: {archive}")
    if not source_kernel.is_file():
        parser.error(f"vdW kernel does not exist: {source_kernel}")

    variants = dict(DEFAULT_POTCAR_VARIANTS)
    variants.update({str(key): str(value) for key, value in dict(saved_config.get("potcar_variants", {})).items()})
    for value in args.variant:
        if "=" not in value:
            parser.error(f"invalid --variant value: {value!r}")
        element, entry = value.split("=", 1)
        variants[element.strip()] = entry.strip()

    resource_dir = args.resource_dir.expanduser().resolve()
    kernel = source_kernel if args.from_config and source_kernel.suffix.lower() != ".gz" else persist_vdw_kernel(source_kernel, resource_dir / "vdw_kernel.bindat")
    config = {
        "potcar_archive": str(archive),
        "potcar_archive_sha256": sha256_file(archive),
        "functional": "PBE",
        "potcar_variants": variants,
        "vdw_kernel": str(kernel),
        "vdw_kernel_sha256": sha256_file(kernel),
    }
    config_path = (args.from_config or args.config).expanduser().resolve()
    if not args.from_config:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    installed = [install_vasp_resources(path.expanduser(), config) for path in args.task_dir]
    print(json.dumps({"config": str(config_path), "resources": config, "installed": installed}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
