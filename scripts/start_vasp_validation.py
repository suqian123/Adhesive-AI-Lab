"""Start the shared (111) validation with persistent logs and overlap protection."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adhesive_ai.campaign_runner import restart_vasp_convergence, _windows_path_to_wsl


if __name__ == "__main__":
    runner = Path(__file__).with_name("run_vasp_convergence.sh")
    subprocess.run(
        ["wsl.exe", "-d", "Ubuntu-24.04", "-u", "vasp", "--", "bash", "-n", _windows_path_to_wsl(runner)],
        check=True, timeout=30,
    )
    print(json.dumps(restart_vasp_convergence(), default=str, ensure_ascii=True))
