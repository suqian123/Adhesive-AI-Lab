"""Compute bounded molecular HF references; never approve MD production."""
from datetime import datetime,timezone
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from pyscf import gto,scf,lib
from pyscf.geomopt.geometric_solver import kernel

ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/qm_inputs'
OUTPUT=ROOT/'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/qm_references'
OUTPUT.mkdir(exist_ok=True)
lib.num_threads(4)

packing=subprocess.run([sys.executable,str(ROOT/'scripts/run_ce_packing.py')],check=False)
print('Initial mixture packing return code:',packing.returncode,flush=True)

def write(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    os.replace(temporary,path)

smoke=scf.RHF(gto.M(atom='H 0 0 0; H 0 0 0.74',basis='6-31g*',verbose=0,max_memory=1000))
smoke_energy=smoke.kernel()
if not smoke.converged or not -1.2<float(smoke_energy)<-1.0:
    raise RuntimeError('Molecular HF smoke calculation failed')
write(OUTPUT/'toolchain_smoke.json',{'molecule':'H2','method':'RHF/6-31G*',
      'energy_hartree':float(smoke_energy),'scf_converged':True,'production_approved':False})

for job in json.loads((INPUT/'jobs.json').read_text())['jobs']:
    directory=OUTPUT/job['name']
    directory.mkdir(exist_ok=True)
    state=directory/'state.json'
    xyz=INPUT/job['xyz']
    if hashlib.sha256(xyz.read_bytes()).hexdigest()!=job['sha256']:
        raise RuntimeError('QM input hash changed')
    if state.exists():
        previous=json.loads(state.read_text())
        if previous.get('state')=='qm-geometry-converged' and previous.get('input')==job:
            continue
        raise RuntimeError('Existing unfinished reference needs review: '+str(directory))
    report={'state':'running','started_at':datetime.now(timezone.utc).isoformat(),'input':job,
            'method':'RHF','basis':'6-31g*','threads':4,'production_approved':False}
    write(state,report)
    try:
        mol=gto.M(atom='\n'.join(xyz.read_text().splitlines()[2:]),basis='6-31g*',unit='Angstrom',
                  charge=job['charge'],spin=job['spin'],max_memory=8000,verbose=4,output=str(directory/'quantum.log'))
        mf=scf.RHF(mol)
        mf.conv_tol=1e-9
        mf.max_cycle=150
        mf.chkfile=str(directory/'initial.chk')
        mf.kernel()
        if not mf.converged: raise RuntimeError('Initial molecular SCF did not converge')
        converged,optimized=kernel(mf,maxsteps=80)
        if not converged: raise RuntimeError('Molecular geometry optimization did not converge')
        final=scf.RHF(optimized)
        final.conv_tol=1e-9
        final.max_cycle=150
        final.chkfile=str(directory/'optimized.chk')
        energy=final.kernel()
        if not final.converged: raise RuntimeError('Final molecular SCF did not converge')
        (directory/'optimized.xyz').write_text(optimized.tostring(format='xyz'),encoding='utf-8')
        gradient=final.nuc_grad_method().kernel()
        report.update(state='qm-geometry-converged',finished_at=datetime.now(timezone.utc).isoformat(),
                      energy_hartree=float(energy),gradient_max_abs_hartree_bohr=float(abs(gradient).max()),
                      wavefunction_stability='not-yet-tested',
                      next_step='ESP/RESP charge fitting and independent force-field validation')
        write(state,report)
    except Exception as exc:
        report.update(state='failed',error=str(exc),finished_at=datetime.now(timezone.utc).isoformat())
        write(state,report)
        raise
