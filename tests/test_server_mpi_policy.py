"""MPI image compatibility and cross-process server resource accounting."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/compute/mpi_launch.py"
spec = importlib.util.spec_from_file_location("server_mpi_policy", SCRIPT)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def test_neb_allocates_per_image_ranks_without_changing_inputs(tmp_path):
    incar = tmp_path / "INCAR"
    text = "IMAGES = 5 ; NCORE = 2\nKPAR = 1 # comment\nENCUT = 600\n"
    incar.write_text(text)
    plan = policy.launch_plan("vasp", [], {"ADHESIVE_MPI_RANKS": "8"}, directory=tmp_path)
    assert plan["mode"] == "neb" and plan["mpi_ranks"] == 40
    assert incar.read_text() == text
    with pytest.raises(ValueError, match="multiple of 10"):
        policy.launch_plan("vasp", [], {}, directory=tmp_path, ranks=8)


def test_engine_specific_ranks_preserve_legacy_fallback(tmp_path):
    config = {"ADHESIVE_MPI_RANKS": "8", "ADHESIVE_VASP_MPI_RANKS": "16",
              "ADHESIVE_LAMMPS_MPI_RANKS": "4", "ADHESIVE_CG_MPI_RANKS": "1"}
    assert policy.launch_plan("vasp", [], config, directory=tmp_path)["mpi_ranks"] == 16
    assert policy.launch_plan("lammps", ["-in", "in.production"], config)["mpi_ranks"] == 4
    assert policy.launch_plan("lammps", ["-in", "/job/in.cg"], config)["mpi_ranks"] == 1
    assert policy.launch_plan("lammps", ["-in", "in.cg"], {"ADHESIVE_MPI_RANKS": "8"})["mpi_ranks"] == 8


def test_npar_takes_precedence_over_ncore(tmp_path):
    (tmp_path / "INCAR").write_text("NCORE = 3\nNPAR = 2\nKPAR = 1\n")
    assert policy.launch_plan("vasp", [], {}, directory=tmp_path, ranks=8)["mpi_ranks"] == 8


def test_numa_mapping_keeps_fft_groups_together(tmp_path):
    config = {"ADHESIVE_VASP_MPI_RANKS": "8", "ADHESIVE_VASP_MPI_BINDING": "numa"}
    (tmp_path / "INCAR").write_text("NCORE = 2\n")
    plan = policy.launch_plan("vasp", [], config, directory=tmp_path, numa_nodes=4)
    assert plan["command"][1:5] == ["--map-by", "ppr:2:numa", "--bind-to", "numa"]
    (tmp_path / "INCAR").write_text("NCORE = 4\n")
    plan = policy.launch_plan("vasp", [], config, directory=tmp_path, numa_nodes=4)
    assert plan["command"][1:3] == ["--bind-to", "none"]


@pytest.mark.parametrize("value", ["0", "-8", "eight", None])
def test_invalid_rank_count_is_rejected(value):
    with pytest.raises(ValueError):
        policy.positive(value, "MPI ranks")


@pytest.mark.skipif(os.name != "posix", reason="Linux server rank pool uses flock and /proc")
def test_rank_budget_is_shared_across_concurrent_workers(tmp_path):
    code = """
import importlib.util,json,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('policy',sys.argv[1]); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
pool=module.RankPool(sys.argv[2],8); token=pool.acquire(int(sys.argv[3]))
try:
    with pool.ledger() as leases:
        total=sum(lease['ranks'] for lease in leases.values())
        assert total<=8
        Path(sys.argv[4]).write_text(json.dumps({'total':total}))
    time.sleep(.3)
finally: pool.release(token)
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPT), str(tmp_path / "pool"), str(ranks), str(tmp_path / f"worker{index}.json")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for index, ranks in enumerate((6, 4, 2))]
    try:
        for process in processes:
            out, err = process.communicate(timeout=15)
            assert process.returncode == 0, err.decode()
        assert all(json.loads((tmp_path / f"worker{index}.json").read_text())["total"] <= 8 for index in range(3))
        assert json.loads((tmp_path / "pool/ranks.json").read_text()) == {}
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill(); process.wait()


@pytest.mark.skipif(os.name != "posix", reason="Linux process identity")
def test_stale_child_identity_does_not_hold_resources(tmp_path):
    pool = policy.RankPool(tmp_path, 8)
    with pool.ledger() as leases:
        leases['stale'] = {'pid':999999999,'identity':'old-start-time','ranks':8,
                           'child_pid':999999998,'child_identity':None}
    token = pool.acquire(8)
    with pool.ledger() as leases:
        assert 'stale' not in leases and leases[token]['ranks'] == 8
    pool.release(token)


@pytest.mark.skipif(os.name != "posix", reason="Linux process identity")
def test_orphaned_live_mpi_parent_keeps_its_rank_lease(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        pool = policy.RankPool(tmp_path, 8)
        with pool.ledger() as leases:
            leases['orphan'] = {'pid':999999999,'identity':'old-start-time','ranks':6,
                                'child_pid':child.pid,'child_identity':policy.process_identity(child.pid)}
        token = pool.acquire(2)
        with pool.ledger() as leases:
            assert leases['orphan']['ranks'] == 6
            assert sum(lease['ranks'] for lease in leases.values()) == 8
        pool.release(token)
    finally:
        child.terminate(); child.wait(timeout=5)
