"""Run with WSL python3 (stdlib only); no VASP or MPI calculation is launched."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipIf(os.name == "nt", "Bash integration test; run in WSL")
class RunnerChecks(unittest.TestCase):
    def run_scenario(self, first_converged):
        with tempfile.TemporaryDirectory(prefix="vasp-runner-test-") as temp:
            root = Path(temp)
            binary = root / "bin"
            binary.mkdir()
            fake = binary / "mpirun"
            fake.write_text("""#!/bin/sh
echo 'DAV: 1 -123.0'
if [ "$FAKE_FIRST_CONVERGED" = 1 ] && [ "${PWD##*/}" = 450 ]; then
  echo 'Iteration 1(12)' > OUTCAR
  echo 'aborting loop because EDIFF is reached' >> OUTCAR
else
  echo 'Iteration 1(160)' > OUTCAR
fi
echo 'free energy TOTEN = -123.0' >> OUTCAR
echo 'General timing and accounting' >> OUTCAR
echo charge > CHGCAR
echo wave > WAVECAR
exit 0
""")
            fake.chmod(0o755)
            for job in ("encut/450", "encut/520", "encut/600"):
                directory = root / job
                directory.mkdir(parents=True)
                for filename in ("POSCAR", "POTCAR", "KPOINTS"):
                    (directory / filename).write_text("identical input\n")
                (directory / "INCAR").write_text("ICHARG = 2\nISTART = 0\nNELMDL = -5\n")
            (root / "clean_baseline.json").write_text("{}")
            env = {**os.environ, "PATH": f"{binary}:{os.environ['PATH']}",
                   "ADHESIVE_VASP_VALIDATION_ROOT": str(root),
                   "FAKE_FIRST_CONVERGED": "1" if first_converged else "0"}
            script = Path(__file__).resolve().parents[1] / "scripts/run_vasp_convergence.sh"
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1, result.stderr)
            first = json.loads((root / "encut/450/run_status.json").read_text())
            self.assertEqual(first["complete"], first_converged)
            self.assertEqual(first["exit_code"], 0)  # Zero solver exit is not sufficient.
            self.assertFalse((root / "encut/600/run_status.json").exists())
            if first_converged:
                second = json.loads((root / "encut/520/run_status.json").read_text())
                self.assertFalse(second["complete"])
                self.assertIn("ICHARG = 1", (root / "encut/520/INCAR").read_text())
            else:
                self.assertFalse((root / "encut/520/run_status.json").exists())
            self.assertFalse((root / "runner.pid").exists())
            self.assertEqual(json.loads((root / "runner_control.json").read_text())["state"], "failed")
            before = (root / "status.tsv").read_text()
            repeated = subprocess.run(["bash", str(script)], env=env, capture_output=True, timeout=20)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual((root / "status.tsv").read_text(), before)

    def test_fresh_recovery_does_not_retain_an_unconverged_checkpoint(self):
        with tempfile.TemporaryDirectory(prefix="vasp-fresh-recovery-test-") as temp:
            root = Path(temp)
            for job in ("encut/450", "encut/520", "encut/600"):
                directory = root / job
                directory.mkdir(parents=True)
                for filename in ("POSCAR", "POTCAR", "KPOINTS"):
                    (directory / filename).write_text("identical input\n")
                (directory / "INCAR").write_text("ICHARG = 2\nISTART = 0\n")
            (root / "clean_baseline.json").write_text("{}")
            (root / "encut/450/scf_recovery.json").write_text('{"state":"prepared-fresh-atomic"}')
            (root / "encut/450/CHGCAR").write_text("unconverged")
            script = Path(__file__).resolve().parents[1] / "scripts/run_vasp_convergence.sh"
            result = subprocess.run(["bash", str(script)], env={**os.environ, "ADHESIVE_VASP_VALIDATION_ROOT": str(root)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not retain CHGCAR", result.stderr)

    def test_nelm_exhaustion_stops_matrix_despite_zero_exit(self):
        self.run_scenario(False)

    def test_only_converged_seed_is_reused_and_failure_blocks_retry(self):
        self.run_scenario(True)


if __name__ == "__main__":
    unittest.main()
