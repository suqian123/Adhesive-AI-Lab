"""Inventory provisional GAFF2 terms after RESP; never approve production MD."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1'
BIN = ROOT/'engines/md-parameterization-20261029/env/bin'
OUTPUT = BASE/'gaff2_audit'
NAMES = ('BADCy','DCDDS','triazine_reference')
ENV = dict(os.environ, AMBERHOME=str(BIN.parent), PATH=str(BIN)+os.pathsep+os.environ.get('PATH',''))


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    os.replace(temporary, path)


def run(directory, executable, arguments):
    with (directory/(executable+'.log')).open('w') as out:
        subprocess.run([str(BIN/executable),*arguments], cwd=directory, env=ENV,
                       stdout=out, stderr=subprocess.STDOUT, check=True, timeout=1800)


def audit(name):
    source = BASE/'resp_references'/name
    upstream = json.loads((source/'state.json').read_text())
    if upstream['state'] != 'resp-fit-complete-review-required':
        raise ValueError('RESP calculation is not complete')
    for filename, expected in upstream['output_hashes'].items():
        if hashlib.sha256((source/filename).read_bytes()).hexdigest() != expected:
            raise ValueError('RESP output changed: '+filename)
    directory = OUTPUT/name
    directory.mkdir(parents=True, exist_ok=True)
    report = {'state':'running', 'model_id':upstream['model_id'],
              'production_approved':False, 'forcefield_validated':False,
              'started_at':datetime.now(timezone.utc).isoformat(),
              'resp_state_sha256':hashlib.sha256((source/'state.json').read_bytes()).hexdigest()}
    write(directory/'state.json', report)
    try:
        run(directory, 'antechamber', ['-i',str(source/'molecule.ac'),'-fi','ac','-o','molecule.mol2',
            '-fo','mol2','-at','gaff2','-c','rc','-cf',str(source/'stage2.q'),'-nc','0','-dr','no','-s','2'])
        atom_section = (directory/'molecule.mol2').read_text().split('@<TRIPOS>ATOM')[1].split('@<TRIPOS>')[0]
        atoms = [line.split() for line in atom_section.strip().splitlines()]
        expected = upstream['charges_e']
        if len(atoms) != len(expected) or any(abs(float(atom[8])-q)>2e-6 for atom,q in zip(atoms,expected)):
            raise ValueError('Mol2 RESP charge transfer mismatch')
        run(directory, 'parmchk2', ['-i','molecule.mol2','-f','mol2','-o','estimated.frcmod','-s','gaff2'])
        terms = (directory/'estimated.frcmod').read_text()
        attention = [line for line in terms.splitlines() if any(word in line.upper() for word in ('ATTN','NEED','MISSING'))]
        report.update(atom_count=len(atoms), parameter_attention_lines=attention,
                      estimated_parameters=terms, atom_types=sorted({atom[5] for atom in atoms}))
        write(directory/'state.json', report)
        if attention:
            report.update(state='parameter-gaps-review-required', finished_at=datetime.now(timezone.utc).isoformat())
            write(directory/'state.json', report)
            return
        (directory/'tleap.in').write_text('source leaprc.gaff2\nloadamberparams estimated.frcmod\nMOL = loadmol2 molecule.mol2\ncheck MOL\nsaveamberparm MOL molecule.prmtop molecule.inpcrd\nquit\n')
        run(directory, 'tleap', ['-f','tleap.in'])
        leaplog = (directory/'tleap.log').read_text()
        if 'FATAL' in leaplog.upper() or any(not (directory/f).is_file() for f in ('molecule.prmtop','molecule.inpcrd')):
            raise ValueError('Provisional Amber topology construction failed')
        report.update(state='provisional-topology-built-review-required',
                      finished_at=datetime.now(timezone.utc).isoformat(),
                      required_validation=['independent QM torsion/geometry/energy comparisons',
                                           'CTBN and cured-site parameterization',
                                           'spatial curing and bulk stability/density checks'],
                      output_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in (directory/'molecule.mol2',directory/'estimated.frcmod',directory/'molecule.prmtop')})
        write(directory/'state.json', report)
    except Exception as exc:
        report.update(state='failed', error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())
        write(directory/'state.json', report)
        raise


def watch():
    deadline = time.monotonic()+72*3600
    while time.monotonic()<deadline:
        pending = []
        for name in NAMES:
            existing = OUTPUT/name/'state.json'
            if existing.is_file():
                result = json.loads(existing.read_text())
                if result['state'] not in ('parameter-gaps-review-required','provisional-topology-built-review-required'):
                    raise RuntimeError(name+': incomplete audit requires review')
                source = BASE/'resp_references'/name/'state.json'
                if hashlib.sha256(source.read_bytes()).hexdigest() != result['resp_state_sha256']:
                    raise RuntimeError(name+': audited RESP source changed')
                continue
            upstream = BASE/'resp_references'/name/'state.json'
            if upstream.is_file():
                state = json.loads(upstream.read_text())['state']
                if state == 'failed':
                    raise RuntimeError(name+': upstream RESP failed')
                if state == 'resp-fit-complete-review-required':
                    write(OUTPUT/'workflow_state.json',{'state':'auditing','molecule':name,'production_approved':False})
                    audit(name)
                    continue
            pending.append(name)
        if not pending:
            write(OUTPUT/'workflow_state.json',{'state':'audit-complete-review-required','production_approved':False})
            return
        write(OUTPUT/'workflow_state.json',{'state':'waiting-for-resp','molecules':pending,'production_approved':False})
        time.sleep(60)
    raise TimeoutError('GAFF2 audit queue exceeded 72 hours')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', choices=NAMES)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    try:
        if args.watch:
            watch()
        elif args.name:
            if (OUTPUT/args.name/'state.json').exists():
                raise ValueError('Existing audit requires review before rerun')
            audit(args.name)
        else:
            parser.error('Choose --name or --watch')
    except Exception as exc:
        write(OUTPUT/'workflow_state.json',{'state':'failed','error':str(exc),'production_approved':False})
        raise
