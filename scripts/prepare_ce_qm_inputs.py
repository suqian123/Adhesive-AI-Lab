"""Prepare isolated molecular references for CE force-field development."""
from pathlib import Path
import hashlib
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from adhesive_ai.ce_disulfide_baseline import _write_3d

root=Path('work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1')
directory=root/'qm_inputs'
directory.mkdir(exist_ok=True)
triazine=Chem.MolFromSmiles('c1ccc(Oc2nc(Oc3ccccc3)nc(Oc3ccccc3)n2)cc1')
_write_3d(triazine,directory/'triazine_reference.sdf',directory/'triazine_reference.pdb',20261030)
jobs=[]
for name,path in [('BADCy',root/'components/BADCy.sdf'),('DCDDS',root/'components/DCDDS.sdf'),('triazine_reference',directory/'triazine_reference.sdf')]:
    molecule=Chem.SDMolSupplier(str(path),removeHs=False)[0]
    if molecule is None or Chem.GetFormalCharge(molecule)!=0:
        raise ValueError('Invalid neutral reference '+name)
    output=directory/(name+'.xyz')
    output.write_text(Chem.MolToXYZBlock(molecule),encoding='utf-8')
    jobs.append({'name':name,'xyz':output.name,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
                 'charge':0,'spin':0,'formula':rdMolDescriptors.CalcMolFormula(molecule)})
manifest={'scope':'isolated HF/6-31G* geometry references; RESP, bonded fitting and force-field validation pending',
          'production_approved':False,'jobs':jobs}
(directory/'jobs.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest,indent=2))
