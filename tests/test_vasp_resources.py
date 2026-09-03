import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def test_configure_vasp_resources_builds_potcar_in_poscar_order(tmp_path):
    archive = tmp_path / "potpaw_PBE.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        for symbol in ("Ce", "O"):
            content = f"TITEL = PAW_PBE {symbol} test\nEnd of Dataset\n".encode()
            info = tarfile.TarInfo(f"{symbol}/POTCAR")
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))

    kernel = tmp_path / "vdw_kernel.bindat.gz"
    with gzip.open(kernel, "wb") as stream:
        stream.write(b"kernel-data\n")

    task = tmp_path / "task"
    task.mkdir()
    (task / "POSCAR").write_text(
        "test\n1\n1 0 0\n0 1 0\n0 0 1\nO Ce\n1 1\nCartesian\n0 0 0\n0 0 0\n",
        encoding="utf-8",
    )
    config = tmp_path / "resources.json"
    resources = tmp_path / "resources"
    script = Path(__file__).resolve().parents[1] / "scripts" / "configure_vasp_resources.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--potcar-archive",
            str(archive),
            "--vdw-kernel",
            str(kernel),
            "--resource-dir",
            str(resources),
            "--config",
            str(config),
            "--task-dir",
            str(task),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["installed"][0]["species"] == ["O", "Ce"]
    assert (task / "POTCAR").read_text().index(" O ") < (task / "POTCAR").read_text().index(" Ce ")
    assert (task / "vdw_kernel.bindat").read_bytes() == b"kernel-data\n"
    assert json.loads(config.read_text(encoding="utf-8"))["functional"] == "PBE"

    second_task = tmp_path / "second-task"
    second_task.mkdir()
    (second_task / "POSCAR").write_text(
        "test\n1\n1 0 0\n0 1 0\n0 0 1\nCe O\n1 1\nCartesian\n0 0 0\n0 0 0\n",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(script), "--from-config", str(config), "--task-dir", str(second_task)],
        check=True,
        text=True,
        capture_output=True,
    )
    assert (second_task / "POTCAR").read_text().index(" Ce ") < (second_task / "POTCAR").read_text().index(" O ")
