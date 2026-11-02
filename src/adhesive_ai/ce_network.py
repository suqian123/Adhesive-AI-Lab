"""Cyanate cyclotrimerization connectivity construction, before 3D realization."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from .ce_disulfide_baseline import (
    BADCY_SMILES, DCDDS_SMILES, CYANATE_PATTERN, DISULFIDE_PATTERN,
    composition_contract, representative_ctbn_smiles,
)

TRIAZINE = Chem.MolFromSmarts('c1ncncn1')


def trimerize(molecule: Chem.Mol, groups: list[list[tuple[int, int, int]]]) -> Chem.Mol:
    """Apply O-C#N -> O-substituted C/N six-ring stoichiometric graph edits."""
    used = set()
    editable = Chem.RWMol(molecule)
    for group in groups:
        if len(group) != 3:
            raise ValueError('A triazine ring consumes three cyanate groups')
        for oxygen, carbon, nitrogen in group:
            if carbon in used:
                raise ValueError('A cyanate site cannot react twice')
            used.add(carbon)
            bond = editable.GetBondBetweenAtoms(carbon, nitrogen)
            if bond is None or bond.GetBondType() != Chem.BondType.TRIPLE:
                raise ValueError('Expected cyanate C#N bond')
            if editable.GetBondBetweenAtoms(oxygen, carbon) is None:
                raise ValueError('Expected O-C bond')
            bond.SetBondType(Chem.BondType.DOUBLE)
        for index, (_, _, nitrogen) in enumerate(group):
            next_carbon = group[(index + 1) % 3][1]
            editable.AddBond(nitrogen, next_carbon, Chem.BondType.SINGLE)
    product = editable.GetMol()
    Chem.SanitizeMol(product)
    return product


def build_network(triazine_rings: int, *, seed: int) -> tuple[Chem.Mol, dict]:
    contract = composition_contract()
    proposal = contract['finite_cell_proposal']
    combined = Chem.Mol()
    sites = []
    monomer_count = 0
    for name, smiles, count in (
        ('BADCy', BADCY_SMILES, proposal['BADCy_molecules']),
        ('DCDDS', DCDDS_SMILES, proposal['DCDDS_molecules']),
    ):
        for _ in range(count):
            monomer = Chem.MolFromSmiles(smiles)
            offset = combined.GetNumAtoms()
            for atom in monomer.GetAtoms():
                atom.SetIntProp('monomer_id', monomer_count)
                atom.SetProp('component', name)
            sites.extend((monomer_count, tuple(offset + i for i in match))
                         for match in monomer.GetSubstructMatches(CYANATE_PATTERN))
            combined = Chem.CombineMols(combined, monomer)
            monomer_count += 1
    ctbn = Chem.MolFromSmiles(representative_ctbn_smiles())
    for atom in ctbn.GetAtoms():
        atom.SetIntProp('monomer_id', -1)
        atom.SetProp('component', 'CTBN')
    combined = Chem.CombineMols(combined, ctbn)
    Chem.SanitizeMol(combined)
    formula = rdMolDescriptors.CalcMolFormula(combined)
    if not 0 <= triazine_rings <= len(sites) // 3:
        raise ValueError('Invalid triazine ring count')
    rng = random.Random(seed)
    selected = rng.sample(sites, triazine_rings * 3)
    for _ in range(10000):
        rng.shuffle(selected)
        if all(len({item[0] for item in selected[i:i+3]}) == 3
               for i in range(0, len(selected), 3)):
            break
    else:
        raise RuntimeError('Unable to group distinct monomers')
    groups = [[item[1] for item in selected[i:i+3]] for i in range(0, len(selected), 3)]
    product = trimerize(combined, groups)
    fragments = Chem.GetMolFrags(product)
    resin_components = [set(product.GetAtomWithIdx(i).GetIntProp('monomer_id') for i in fragment)
                        for fragment in fragments]
    resin_components = [ids for ids in resin_components if -1 not in ids]
    checks = {
        'formula_conserved': rdMolDescriptors.CalcMolFormula(product) == formula,
        'atom_count_conserved': product.GetNumAtoms() == combined.GetNumAtoms(),
        'neutral': Chem.GetFormalCharge(product) == 0,
        'remaining_cyanate_count': len(product.GetSubstructMatches(CYANATE_PATTERN)) == len(sites)-3*triazine_rings,
        'triazine_ring_count': len(product.GetSubstructMatches(TRIAZINE)) == triazine_rings,
        'disulfides_preserved': len(product.GetSubstructMatches(DISULFIDE_PATTERN)) == proposal['DCDDS_molecules'],
    }
    if not all(checks.values()):
        raise RuntimeError(f'Connectivity checks failed: {checks}')
    report = {
        'candidate_id': contract['candidate_id'],
        'source_formulation_id': contract['formulation_id'],
        'model_id': 'MDM-'+hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()[:16].upper(),
        'identity_scope': 'candidate-specific computational model; production formulation binding pending',
        'seed': seed, 'formula': formula, 'heavy_atoms': product.GetNumAtoms(),
        'triazine_rings': triazine_rings, 'reacted_cyanate_sites': 3*triazine_rings,
        'conversion': 3*triazine_rings / len(sites), 'checks': checks,
        'resin_connected_components': len(resin_components),
        'largest_resin_component_monomers': max(map(len, resin_components)),
        'largest_resin_component_fraction': max(map(len, resin_components))/monomer_count,
        'CTBN_covalently_coupled': False,
        'construction_scope': 'seeded random connectivity; spatial feasibility and CTBN coupling remain unvalidated',
        'validation_scope': 'chemical graph only; 3D packing, force field and dynamics pending',
        'production_approved': False,
        'reaction_atom_mapping': groups,
    }
    return product, report


def prepare_networks(output_dir: str | Path) -> list[dict]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, target in enumerate(composition_contract()['finite_cell_proposal']['conversion_targets']):
        molecule, report = build_network(target['triazine_rings'], seed=20261029 + index)
        report.update(task_id=target['task_id'], target_conversion=target['target_conversion'])
        directory = root / target['task_id']
        directory.mkdir(exist_ok=True)
        atoms = [{'id':a.GetIdx(), 'element':a.GetSymbol(), 'formal_charge':a.GetFormalCharge(),
                  'hydrogens':a.GetTotalNumHs(), 'aromatic':a.GetIsAromatic(),
                  'monomer_id':a.GetIntProp('monomer_id'), 'component':a.GetProp('component')}
                 for a in molecule.GetAtoms()]
        bonds = [{'atoms':[b.GetBeginAtomIdx(), b.GetEndAtomIdx()], 'order':b.GetBondTypeAsDouble()}
                 for b in molecule.GetBonds()]
        topology = directory/'connectivity.json'
        topology.write_text(json.dumps({'coordinates':None, 'atoms':atoms, 'bonds':bonds},indent=2),encoding='utf-8')
        smiles = directory/'network.smiles'
        smiles.write_text(Chem.MolToSmiles(molecule)+'\n',encoding='utf-8')
        report['artifact_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (topology,smiles)}
        (directory/'connectivity_validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        reports.append(report)
    return reports
