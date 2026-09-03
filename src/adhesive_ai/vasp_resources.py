"""Local VASP resource handling without redistributing licensed POTCAR data."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
from typing import Mapping, Sequence


DEFAULT_POTCAR_VARIANTS = {
    "H": "H", "B": "B", "C": "C", "N": "N", "O": "O", "Ce": "Ce",
}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def persist_vdw_kernel(source: str | Path, destination: str | Path) -> Path:
    source_path, destination_path = Path(source), Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if source_path.suffix.lower() == ".gz" else Path.open
    with opener(source_path, "rb") as input_stream, destination_path.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
    if destination_path.stat().st_size == 0:
        raise ValueError(f"vdW kernel is empty: {destination_path}")
    return destination_path.resolve()


def find_poscar(task_dir: str | Path) -> Path:
    directory = Path(task_dir)
    for relative in ("POSCAR", "00/POSCAR"):
        candidate = directory / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"No POSCAR or 00/POSCAR found in {directory}")


def poscar_species(path: str | Path) -> list[str]:
    poscar = Path(path)
    lines = [line.strip() for line in poscar.read_text(encoding="utf-8").splitlines()]
    if len(lines) < 7:
        raise ValueError(f"POSCAR is too short: {poscar}")
    species, counts = lines[5].split(), lines[6].split()
    if not species or len(species) != len(counts):
        raise ValueError(f"POSCAR must use the VASP 5 element-name line: {poscar}")
    if not all(re.fullmatch(r"[A-Z][a-z]?", symbol) for symbol in species):
        raise ValueError(f"Invalid POSCAR species line in {poscar}: {' '.join(species)}")
    if not all(value.isdigit() and int(value) > 0 for value in counts):
        raise ValueError(f"Invalid POSCAR atom counts in {poscar}: {' '.join(counts)}")
    return species


def build_potcar(
    archive: str | Path,
    species: Sequence[str],
    variants: Mapping[str, str] | None = None,
) -> bytes:
    archive_path = Path(archive)
    selected = {**DEFAULT_POTCAR_VARIANTS, **dict(variants or {})}
    chunks: list[bytes] = []
    with tarfile.open(archive_path, "r:gz") as bundle:
        for symbol in species:
            member_name = f"{selected.get(symbol, symbol)}/POTCAR"
            try:
                member = bundle.getmember(member_name)
            except KeyError as exc:
                raise FileNotFoundError(
                    f"POTCAR entry {member_name!r} is missing from {archive_path}"
                ) from exc
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError(f"POTCAR entry is not a regular file: {member_name}")
            content = stream.read()
            title = re.search(rb"TITEL\s*=\s*([^\r\n]+)", content)
            if not title or symbol.encode("ascii") not in title.group(1).split():
                raise ValueError(f"POTCAR entry failed element validation: {member_name}")
            chunks.append(content if content.endswith(b"\n") else content + b"\n")
    return b"".join(chunks)


def load_vasp_resource_config(path: str | Path = "work/vasp_resources.json") -> dict[str, object]:
    config_path = Path(path).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    archive, kernel = Path(str(config["potcar_archive"])), Path(str(config["vdw_kernel"]))
    if not archive.is_file() or not kernel.is_file():
        raise FileNotFoundError("Configured POTCAR archive or vdW kernel is unavailable")
    if config.get("potcar_archive_sha256") and sha256_file(archive) != config["potcar_archive_sha256"]:
        raise ValueError("Configured POTCAR archive checksum changed")
    if config.get("vdw_kernel_sha256") and sha256_file(kernel) != config["vdw_kernel_sha256"]:
        raise ValueError("Configured vdW kernel checksum changed")
    return config


def install_vasp_resources(
    task_dir: str | Path,
    config: Mapping[str, object] | str | Path = "work/vasp_resources.json",
) -> dict[str, object]:
    selected = load_vasp_resource_config(config) if isinstance(config, (str, Path)) else dict(config)
    directory = Path(task_dir).expanduser().resolve()
    poscar = find_poscar(directory)
    species = poscar_species(poscar)
    potcar = directory / "POTCAR"
    potcar.write_bytes(build_potcar(
        str(selected["potcar_archive"]), species,
        {str(key): str(value) for key, value in dict(selected.get("potcar_variants", {})).items()},
    ))
    kernel = directory / "vdw_kernel.bindat"
    shutil.copyfile(str(selected["vdw_kernel"]), kernel)
    return {
        "task_dir": str(directory), "poscar": str(poscar), "species": species,
        "potcar": str(potcar), "potcar_sha256": sha256_file(potcar),
        "vdw_kernel": str(kernel), "vdw_kernel_sha256": sha256_file(kernel),
    }
