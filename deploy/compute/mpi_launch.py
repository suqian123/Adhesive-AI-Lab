"""Server MPI policies and a shared rank budget across independent campaigns."""
from contextlib import contextmanager
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def positive(value, name):
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if number < 1:
        raise ValueError(f"{name} must be a positive integer")
    return number


def incar_tags(path):
    tags = {}
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            clean = re.split(r"[#!]", line, maxsplit=1)[0]
            for item in clean.split(";"):
                if "=" in item:
                    key, value = item.split("=", 1)
                    tags[key.strip().upper()] = value.strip()
    return tags


def launch_plan(engine, arguments, config, *, directory=None, ranks=None, executable=None, numa_nodes=None):
    directory = Path(directory or Path.cwd())
    fallback = config.get("ADHESIVE_MPI_RANKS") or "4"
    mode = engine
    if engine == "vasp":
        tags = incar_tags(directory / "INCAR")
        images = int(tags.get("IMAGES", "0"))
        if images < 0:
            raise ValueError("IMAGES must be nonnegative")
        if images:
            mode = "neb"
            selected = config.get("ADHESIVE_NEB_MPI_RANKS") or str(images * positive(config.get("ADHESIVE_VASP_MPI_RANKS") or fallback, "VASP ranks"))
        else:
            selected = config.get("ADHESIVE_VASP_MPI_RANKS") or fallback
        count = positive(ranks if ranks is not None else selected, "MPI ranks")
        kpar = positive(tags.get("KPAR", "1"), "KPAR")
        grouping = positive(tags.get("NPAR") or tags.get("NCORE", "1"), "NPAR/NCORE")
        quantum = max(1, images) * kpar * grouping
        if count % quantum:
            raise ValueError(f"{count} MPI ranks do not match IMAGES={images or 1}, KPAR={kpar}, NPAR/NCORE={grouping}; require a multiple of {quantum}")
        fft_group = count // (max(1, images) * kpar * grouping) if tags.get("NPAR") else grouping
        binary = executable or str(ROOT / "engines/vasp-6.4.2/bin/vasp_std")
    else:
        input_name = None
        for index, value in enumerate(arguments[:-1]):
            if value in {"-in", "-i"}:
                input_name = Path(arguments[index + 1]).name
        mode = "cg" if input_name == "in.cg" else "lammps"
        key = "ADHESIVE_CG_MPI_RANKS" if mode == "cg" else "ADHESIVE_LAMMPS_MPI_RANKS"
        selected = config.get(key) or config.get("ADHESIVE_LAMMPS_MPI_RANKS") or fallback
        count = positive(ranks if ranks is not None else selected, "MPI ranks")
        binary = executable or str(ROOT / "engines/lammps-30Sep2026/bin/lmp")
    binding = (config.get("ADHESIVE_VASP_MPI_BINDING") or "none") if engine == "vasp" else "none"
    mapping = ["--bind-to", "none"]
    if binding == "numa":
        nodes = numa_nodes if numa_nodes is not None else len(list(Path("/sys/devices/system/node").glob("node[0-9]*")))
        # Keep each FFT group within one NUMA domain; otherwise keep OS placement.
        if nodes and count >= nodes and count % nodes == 0 and (count // nodes) % fft_group == 0:
            mapping = ["--map-by", f"ppr:{count // nodes}:numa", "--bind-to", "numa"]
    elif binding != "none":
        raise ValueError("ADHESIVE_VASP_MPI_BINDING must be none or numa")
    return {"mode": mode, "mpi_ranks": count,
            "command": ["/usr/bin/mpirun", *mapping, "-np", str(count), binary, *arguments]}


def process_identity(pid):
    """Include Linux process start time so a recycled PID cannot hold a lease."""
    try:
        value = Path(f"/proc/{pid}/stat").read_text()
        fields = value.rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, IndexError):
        return None


class RankPool:
    def __init__(self, directory, budget):
        self.directory = Path(directory)
        self.budget = positive(budget, "MPI budget")

    @contextmanager
    def ledger(self):
        import fcntl
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / "ranks.lock").open("a+") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.directory / "ranks.json"
            leases = json.loads(path.read_text()) if path.exists() else {}
            for key, lease in list(leases.items()):
                parent_alive = lease.get("identity") is not None and process_identity(lease["pid"]) == lease["identity"]
                child_alive = lease.get("child_identity") is not None and process_identity(lease["child_pid"]) == lease["child_identity"]
                if not parent_alive and not child_alive:
                    del leases[key]
            try:
                yield leases
            finally:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(leases) + "\n"); temporary.chmod(0o600); temporary.replace(path)

    def acquire(self, ranks):
        ranks = positive(ranks, "MPI ranks")
        if ranks > self.budget:
            raise ValueError(f"Task needs {ranks} ranks, exceeding the shared budget {self.budget}")
        token = uuid.uuid4().hex
        waiting = False
        while True:
            with self.ledger() as leases:
                if sum(lease["ranks"] for lease in leases.values()) + ranks <= self.budget:
                    leases[token] = {"pid": os.getpid(), "identity": process_identity(os.getpid()),
                                     "ranks": ranks, "workdir": str(Path.cwd())}
                    return token
            if not waiting:
                print(f"Waiting for server MPI budget ({self.budget} ranks)", file=sys.stderr, flush=True)
                waiting = True
            time.sleep(1)

    def attach(self, token, child_pid):
        with self.ledger() as leases:
            leases[token].update(child_pid=child_pid, child_identity=process_identity(child_pid))

    def release(self, token):
        with self.ledger() as leases:
            leases.pop(token, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("vasp", "lammps"), required=True)
    parser.add_argument("--ranks", type=int)
    parser.add_argument("--executable")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    from dotenv import dotenv_values
    config = {**dotenv_values(ROOT / ".env"), **os.environ}
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    plan = launch_plan(args.engine, arguments, config, ranks=args.ranks, executable=args.executable)
    budget = int(config.get("ADHESIVE_MPI_BUDGET") or "0")
    if budget < 0:
        raise ValueError("ADHESIVE_MPI_BUDGET must be nonnegative")
    if budget and plan["mpi_ranks"] > budget:
        raise ValueError("Task MPI ranks exceed the server budget")
    if args.dry_run:
        print(json.dumps({**plan, "server_budget": budget})); return 0
    pool = RankPool(ROOT / "work/compute_resources", budget) if budget else None
    token = pool.acquire(plan["mpi_ranks"]) if pool else None
    environment = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OMP_STACKSIZE": "512m"}
    try:
        process = subprocess.Popen(plan["command"], env=environment, stdin=subprocess.DEVNULL)
        if pool: pool.attach(token, process.pid)
        return process.wait()
    finally:
        if pool: pool.release(token)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(2)
