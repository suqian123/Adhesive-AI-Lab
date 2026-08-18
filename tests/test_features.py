from adhesive_ai.features import (
    FORMULATION_FEATURE_LABELS,
    feature_names,
    formulation_features,
    molecule_features,
)


def test_molecule_features():
    features = molecule_features("c1ccccc1O")
    assert features.heavy_atom_count > 0
    assert features.ring_count == 1
    assert 0 <= features.polar_atom_fraction <= 1


def test_formulation_ratios_normalize():
    features = formulation_features("CCO", "c1ccccc1O", "C", 60, 30, 10, 25, 40)
    assert abs(features["resin_ratio"] + features["tackifier_ratio"] + features["filler_ratio"] - 1) < 1e-9
    assert 0 <= features["compatibility_index"] <= 1


def test_all_formulation_features_have_chinese_labels():
    assert set(FORMULATION_FEATURE_LABELS) == set(feature_names())
