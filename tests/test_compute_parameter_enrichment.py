from adhesive_ai.compute_parameter_enrichment import enrich
from adhesive_ai.ce_disulfide_baseline import composition_contract
import json
from pathlib import Path

SOURCES=json.loads((Path(__file__).parents[1]/'resources/compute_parameter_sources.json').read_text(encoding='utf-8'))


def test_family_reference_does_not_assign_another_materials_parameters():
    row={'candidate_id':'CL-00016','formulation_id':'other','candidate_library_version':'v4','resin':'PU','dynamic_unit':'Ionic','toughener_type':'rubber','tg_c':400}
    result=enrich(row,SOURCES,composition_contract())
    assert all(value is None for value in result['parameters'].values())
    assert len(result['reference_routes'])==2
    assert result['proposed_model'] is None
    assert result['production_approved'] is False


def test_specialization_is_bound_to_exact_id_and_formulation():
    specific=composition_contract()
    row={key:specific[key] for key in ('candidate_id','formulation_id','resin','dynamic_unit')}
    row['candidate_library_version']='candidate-library-v5'
    result=enrich(row,SOURCES,specific)
    assert result['parameters']['toughener_Mn_g_mol']==3150
    assert result['parameters']['forcefield_file'] is None
    row['formulation_id']='changed'
    assert enrich(row,SOURCES,specific)['parameters']['toughener_grade'] is None
