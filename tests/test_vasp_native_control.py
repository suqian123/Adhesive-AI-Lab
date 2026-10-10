"""Exercise native Linux process control using a disposable process group."""
import sys
import subprocess
import time
from pathlib import Path

import pytest

from adhesive_ai.campaign_runner import _send_vasp_runner_signal


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux process-state integration")
def test_native_vasp_runner_can_pause_and_resume_its_process_group():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    status_file = Path(f"/proc/{process.pid}/status")

    def wait_for_stopped(expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = next(line for line in status_file.read_text().splitlines() if line.startswith("State:"))
            if (state.split()[1] == "T") is expected:
                return
            time.sleep(0.05)
        pytest.fail(f"Unexpected runner state after signal: {state}")

    try:
        _send_vasp_runner_signal(process.pid, process.pid, "STOP")
        wait_for_stopped(True)
        _send_vasp_runner_signal(process.pid, process.pid, "CONT")
        wait_for_stopped(False)
        assert process.poll() is None
    finally:
        # Continue a stopped process before terminating it, including on failure.
        import os
        import signal
        if process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
            process.terminate()
            process.wait(timeout=5)
