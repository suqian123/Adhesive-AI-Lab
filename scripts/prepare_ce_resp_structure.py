"""Preserve atom identities/bonds while inserting optimized QM coordinates."""
import argparse
import json
from pathlib import Path
from rdkit import Chem
from rdkit.Geometry import Point3D

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('name', choices=['BADCy', 'DCDDS', 'triazine_reference'])
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = BASE / ('qm_inputs/triazine_reference.sdf' if args.name == 'triazine_reference' else f'components/{args.name}.sdf')
    mol = Chem.SDMolSupplier(str(source), removeHs=False)[0]
    lines = (BASE / 'qm_references' / args.name / 'optimized.xyz').read_text().splitlines()
    if mol is None or int(lines[0]) != mol.GetNumAtoms():
        raise ValueError('Molecular atom count mismatch')
    atom_map = []
    for atom, line in zip(mol.GetAtoms(), lines[2:]):
        symbol, x, y, z = line.split()
        if symbol != atom.GetSymbol():
            raise ValueError('QM atom order mismatch')
        mol.GetConformer().SetAtomPosition(atom.GetIdx(), Point3D(float(x), float(y), float(z)))
        atom_map.append({'index_1based': atom.GetIdx()+1, 'element': symbol,
                         'atomic_number': atom.GetAtomicNum()})
    args.output.mkdir(parents=True, exist_ok=True)
    with Chem.SDWriter(str(args.output / 'optimized.sdf')) as writer:
        writer.write(mol)
    (args.output / 'atom_map.json').write_text(json.dumps(atom_map, indent=2))


if __name__ == '__main__':
    main()
