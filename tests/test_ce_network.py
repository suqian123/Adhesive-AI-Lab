from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from adhesive_ai.ce_network import trimerize,TRIAZINE,build_network
from adhesive_ai.ce_disulfide_baseline import CYANATE_PATTERN


def test_cyanate_cyclotrimerization_matches_triazine_product():
    reactant=Chem.MolFromSmiles('N#COc1ccccc1.N#COc1ccccc1.N#COc1ccccc1')
    product=trimerize(reactant,[list(reactant.GetSubstructMatches(CYANATE_PATTERN))])
    expected=Chem.MolFromSmiles('c1ccc(Oc2nc(Oc3ccccc3)nc(Oc3ccccc3)n2)cc1')
    assert Chem.MolToSmiles(product)==Chem.MolToSmiles(expected)
    assert rdMolDescriptors.CalcMolFormula(product)==rdMolDescriptors.CalcMolFormula(reactant)


def test_full_conversion_preserves_disulfides_and_forms_network():
    product,report=build_network(150,seed=20261031)
    assert all(report['checks'].values())
    assert report['conversion']==1
    assert report['largest_resin_component_fraction']>0.95
    assert report['production_approved'] is False
