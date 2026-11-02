import numpy as np
import pytest
from adhesive_ai.ce_resp import (BOHR_ANGSTROM, BONDI_RADII_A, surface_grid,
                                point_charge_esp, write_resp_esp, read_resp_charges)


def test_surface_exclusion_and_translation_invariance():
    xyz = np.array([[0., 0., 0.], [1.4, 0., 1.1], [-1.4, 0., 1.1]])
    grid = surface_grid([8, 1, 1], xyz, scales=(1.4,))
    distances = np.linalg.norm(grid[:, None]-xyz[None], axis=2)
    assert np.all(distances >= 1.4*np.array([BONDI_RADII_A[z] for z in [8,1,1]])/BOHR_ANGSTROM - 1e-8)
    shift = np.array([4., -3., 1.])
    translated = surface_grid([8,1,1], xyz+shift, scales=(1.4,))
    np.testing.assert_allclose(translated-shift, grid, atol=1e-12)
    np.testing.assert_allclose(point_charge_esp(xyz, [-.8,.4,.4], grid),
                               point_charge_esp(xyz+shift, [-.8,.4,.4], translated), atol=1e-12)


def test_resp_fixed_width_units_and_sign(tmp_path):
    path = tmp_path/'molecule.esp'
    xyz = np.array([[0., 0., 0.]])
    points = np.array([[2., 0., 0.], [0., 0., 4.]])
    values = point_charge_esp(xyz, [-1.], points)
    write_resp_esp(path, xyz, points, values)
    lines = path.read_text().splitlines()
    assert [int(lines[0][i:i+5]) for i in (0,5,10)] == [1,2,0]
    assert float(lines[1][16:32]) == 0
    np.testing.assert_allclose([float(lines[2][i:i+16]) for i in (0,16,32,48)], [-.5,2,0,0])
    np.testing.assert_allclose([float(lines[3][i:i+16]) for i in (0,16,32,48)], [-.25,0,0,4])
    with pytest.raises(ValueError):
        write_resp_esp(path, xyz, points, [np.nan,0])


def test_resp_rejects_incomplete_charge_output(tmp_path):
    path = tmp_path/'charges'
    path.write_text('-.8 .4')
    with pytest.raises(ValueError):
        read_resp_charges(path, 3)
