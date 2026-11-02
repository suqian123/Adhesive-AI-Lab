"""Run and check an unreacted starting mixture; no MD production approval."""
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
from scipy.spatial import cKDTree

ROOT=Path(__file__).resolve().parents[1]
directory=ROOT/'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1/packing'
report={'passed':False,'production_approved':False,'scope':'initial unreacted mixture packing only'}

def records(path):
    return [line for line in path.read_text().splitlines() if line.startswith(('ATOM  ','HETATM'))]

try:
    if (directory/'initial_mixture.pdb').exists():
        raise RuntimeError('Packing output already exists; inspect before rerun')
    with (directory/'initial.inp').open('rb') as source, (directory/'packmol.log').open('wb') as log:
        process=subprocess.run([str(Path(sys.executable).parent/'packmol')],stdin=source,stdout=log,stderr=subprocess.STDOUT,cwd=directory,timeout=3600)
    if process.returncode!=0: raise RuntimeError('Packmol failed: '+str(process.returncode))
    plan=json.loads((directory/'packing_plan.json').read_text())
    output=records(directory/'initial_mixture.pdb')
    if len(output)!=plan['expected_atoms']: raise RuntimeError('Packed atom count mismatch')
    xyz=np.array([[float(line[30:38]),float(line[38:46]),float(line[46:54])] for line in output])
    if not np.isfinite(xyz).all() or xyz.min()<-0.01 or xyz.max()>85.01:
        raise RuntimeError('Packed coordinates outside box or nonfinite')
    counts=[len(records(directory/'../components/BADCy.pdb'))]*181
    counts += [len(records(directory/'../components/DCDDS.pdb'))]*44
    counts += [len(records(directory/'CTBN_representative.pdb'))]
    labels=np.repeat(np.arange(len(counts)),counts)
    contacts=cKDTree(xyz).query_pairs(1.98,output_type='ndarray')
    bad=sum(labels[i]!=labels[j] for i,j in contacts)
    if bad: raise RuntimeError('Intermolecular contacts below 1.98 A: '+str(bad))
    report.update(passed=True,atoms=len(output),molecules=len(counts),intermolecular_contacts_below_1_98_A=int(bad))
except Exception as exc:
    report['error']=str(exc)
    raise
finally:
    (directory/'packing_validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)
