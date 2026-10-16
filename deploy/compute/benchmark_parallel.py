"""Measure MPI scaling in isolated directories; never approve or import results."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def run_case(command, directory, timeout=900):
    env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    start = time.monotonic()
    with (directory / "stdout.log").open("w") as output:
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    return code, time.monotonic() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vasp-source", type=Path, required=True)
    parser.add_argument("--lammps-source", type=Path)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.vasp_source.resolve()
    if not 6 <= args.iterations <= 12:
        raise ValueError("Use 6 to 12 electronic steps for this short performance-only scan")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report_path = args.output / "report.json"
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ("POSCAR", "POTCAR", "KPOINTS", "INCAR")}
    report = {"scope": "performance-only; incomplete VASP SCF; no scientific approval or database write",
              "source": str(source), "source_hashes": hashes, "created_at": datetime.now(timezone.utc).isoformat(),
              "vasp": [], "lammps": [], "state": "running"}

    def save():
        temp = report_path.with_suffix(".tmp")
        temp.write_text(json.dumps(report, indent=2) + "\n"); temp.chmod(0o600); temp.replace(report_path)
    save()
    configs = [(4, 2, "none"), (8, 2, "none"), (8, 4, "none"),
               (8, 2, "numa"), (16, 4, "numa"), (32, 8, "numa")]
    base = (source / "INCAR").read_text()
    nodes = len(list(Path('/sys/devices/system/node').glob('node[0-9]*')))
    if nodes != 4:
        raise RuntimeError("This scan requires the inspected four-NUMA-node server")
    for ranks, ncore, binding in configs:
        label = f"mpi{ranks}-ncore{ncore}-{binding}"
        report['current_case'] = label; save()
        directory = args.output / label; directory.mkdir(mode=0o700)
        for name in ('POSCAR', 'POTCAR', 'KPOINTS'):
            shutil.copyfile(source/name, directory/name); (directory/name).chmod(0o600)
        settings = {'NCORE': str(ncore), 'KPAR':'1', 'NBANDS':'176', 'NSW':'0', 'IBRION':'-1',
                    'ISTART':'0', 'ICHARG':'2', 'ALGO':'All', 'TIME':'0.1', 'NELM':str(args.iterations),
                    'LWAVE':'.FALSE.', 'LCHARG':'.FALSE.'}
        lines = [line for line in base.splitlines() if line.split('=',1)[0].strip().upper() not in set(settings)|{'NPAR','IMAGES'}]
        (directory/'INCAR').write_text('\n'.join(lines + [f'{key} = {value}' for key,value in settings.items()])+'\n')
        mapping = ['--bind-to','none'] if binding=='none' else ['--map-by',f'ppr:{ranks//nodes}:numa','--bind-to','numa']
        command = ['/usr/bin/mpirun', *mapping, '-np',str(ranks),str(ROOT/'engines/vasp-6.4.2/bin/vasp_std')]
        code, wall = run_case(command, directory)
        text = (directory/'OUTCAR').read_text(errors='replace') if (directory/'OUTCAR').exists() else ''
        loops = [float(value) for value in re.findall(r'LOOP:\s+cpu time\s+\S+:\s+real time\s+([-+0-9.Ee]+)', text)]
        energies = re.findall(r'free\s+energy\s+TOTEN\s+=\s+([-+0-9.Ee]+)', text)
        energy = float(energies[-1]) if energies else None
        row = {'mpi_ranks':ranks,'ncore':ncore,'binding':binding,'exit_code':code,'wall_seconds':wall,
               'electronic_steps':len(loops),'last_three_step_mean_seconds':sum(loops[-3:])/3 if len(loops)>=3 else None,
               'finite_energy':energy is not None and math.isfinite(energy),'directory':str(directory)}
        report['vasp'].append(row); save(); print(json.dumps(row),flush=True)
    if args.lammps_source:
        md_source = args.lammps_source.resolve()
        script = (md_source/'in.cg').read_text()
        shortened, replacements = re.subn(r'(?m)^run\s+100000\s*$', 'run 2000', script)
        if replacements != 1: raise ValueError('Unexpected CG protocol; refusing automatic benchmark rewrite')
        for ranks in (1,2,4,8,16):
            label=f'cg-mpi{ranks}'; report['current_case']=label; save()
            directory=args.output/label; directory.mkdir(mode=0o700)
            shutil.copyfile(md_source/'interface.data',directory/'interface.data')
            (directory/'in.cg').write_text(shortened)
            command=['/usr/bin/mpirun','--bind-to','none','-np',str(ranks),str(ROOT/'engines/lammps-30Sep2026/bin/lmp'),'-in','in.cg']
            code,wall=run_case(command,directory,timeout=180)
            text=(directory/'stdout.log').read_text(errors='replace')
            loop=re.search(r'Loop time of\s+([0-9.eE+-]+)\s+on\s+\d+\s+procs\s+for\s+(\d+)\s+steps\s+with\s+(\d+)\s+atoms',text)
            row={'mpi_ranks':ranks,'exit_code':code,'wall_seconds':wall,
                 'loop_seconds':float(loop[1]) if loop else None,'steps':int(loop[2]) if loop else None,
                 'atoms':int(loop[3]) if loop else None,'directory':str(directory)}
            report['lammps'].append(row); save(); print(json.dumps(row),flush=True)
    assert hashes == {name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in hashes}
    report.update(state='finished',current_case=None,source_unchanged=True)
    save(); print('REPORT',str(report_path),flush=True)


if __name__=='__main__':
    main()
