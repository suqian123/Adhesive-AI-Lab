"""Create an explicitly provisional unreacted-mixture packing input."""
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from rdkit import Chem
from rdkit.Chem import AllChem
from adhesive_ai.ce_disulfide_baseline import representative_ctbn_smiles

base=Path('work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1')
directory=base/'packing'
directory.mkdir(exist_ok=True)
molecule=Chem.AddHs(Chem.MolFromSmiles(representative_ctbn_smiles()))
parameters=AllChem.ETKDGv3()
parameters.randomSeed=20261030
parameters.useRandomCoords=True
parameters.maxIterations=1000
if AllChem.EmbedMolecule(molecule,parameters)!=0:
    raise RuntimeError('Representative CTBN 3D embedding failed')
optimization=None
if AllChem.MMFFHasAllMoleculeParams(molecule):
    optimization=AllChem.MMFFOptimizeMolecule(molecule,maxIters=500)
Chem.MolToPDBFile(molecule,str(directory/'CTBN_representative.pdb'))
writer=Chem.SDWriter(str(directory/'CTBN_representative.sdf'))
writer.write(molecule);writer.close()
atoms=0
structures=[]
for name,count,path in [('BADCy',181,'../components/BADCy.pdb'),('DCDDS',44,'../components/DCDDS.pdb'),('CTBN',1,'CTBN_representative.pdb')]:
    mol=Chem.MolFromPDBFile(str(directory/path),removeHs=False)
    if mol is None: raise RuntimeError('Unreadable packing molecule '+name)
    atoms+=count*mol.GetNumAtoms()
    structures.append(f'structure {path}\n  number {count}\n  inside box 1. 1. 1. 84. 84. 84.\nend structure\n')
(directory/'initial.inp').write_text('tolerance 2.0\nfiletype pdb\noutput initial_mixture.pdb\nseed 20261030\nnloop 1000\n\n'+'\n'.join(structures),encoding='utf-8')
report={'expected_atoms':atoms,'CTBN_atoms':molecule.GetNumAtoms(),'CTBN_MMFF_optimizer_code':optimization,
        'box_A':[85,85,85],'packing_bounds_A':[1,84],'intermolecular_tolerance_A':2.0,'scope':'dilute unreacted starting packing; box is a construction choice, not validated density',
        'production_approved':False,'remaining':'distance-based curing, complete force field, compression and equilibration'}
(directory/'packing_plan.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
