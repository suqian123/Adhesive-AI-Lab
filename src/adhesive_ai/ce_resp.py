"""Explicit-unit ESP interchange for provisional CE molecular RESP references."""
from pathlib import Path
import numpy as np

BOHR_ANGSTROM = 0.52917721092
BONDI_RADII_A = {1: 1.20, 6: 1.70, 7: 1.55, 8: 1.52, 16: 1.80}


def surface_grid(numbers, coordinates_bohr, scales=(1.4, 1.6, 1.8, 2.0), density=1.0, phase=0.0):
    """Fibonacci points on exposed scaled Bondi spheres; not Gaussian's MK grid."""
    xyz = np.asarray(coordinates_bohr, dtype=float)
    radii = np.array([BONDI_RADII_A[int(z)] / BOHR_ANGSTROM for z in numbers])
    points = []
    for scale in scales:
        for center, radius in zip(xyz, radii * scale):
            count = max(24, int(np.ceil(4 * np.pi * (radius * BOHR_ANGSTROM)**2 * density)))
            index = np.arange(count, dtype=float)
            z = 1 - 2 * (index + .5) / count
            angle = index * np.pi * (3 - np.sqrt(5)) + phase
            r = np.sqrt(1 - z*z)
            shell = center + radius * np.column_stack((r*np.cos(angle), r*np.sin(angle), z))
            distances = np.linalg.norm(shell[:, None, :] - xyz[None, :, :], axis=2)
            points.extend(shell[(distances >= scale * radii[None, :] - 1e-8).all(axis=1)])
    result = np.asarray(points)
    if len(result) < 10 * len(numbers) or not np.isfinite(result).all():
        raise ValueError('Insufficient finite ESP surface points')
    return result


def point_charge_esp(coordinates_bohr, charges, points_bohr):
    distance = np.linalg.norm(np.asarray(points_bohr)[:, None, :] - np.asarray(coordinates_bohr)[None, :, :], axis=2)
    if (distance < 1e-8).any():
        raise ValueError('ESP point overlaps a nucleus')
    return (1 / distance) @ np.asarray(charges)


def molecular_esp(mol, density_matrix, points_bohr, block_size=64):
    """Vnuc - trace(P * int1e_grids), all in atomic units (PySCF MEP example)."""
    result = point_charge_esp(mol.atom_coords(), mol.atom_charges(), points_bohr)
    for start in range(0, len(points_bohr), block_size):
        end = min(start + block_size, len(points_bohr))
        integrals = mol.intor('int1e_grids', grids=points_bohr[start:end])
        result[start:end] -= np.einsum('pij,ji->p', integrals, density_matrix)
    if not np.isfinite(result).all():
        raise ValueError('Nonfinite quantum ESP')
    return result


def write_resp_esp(path, coordinates_bohr, points_bohr, potential_au):
    """Amber espgen's 3I5 / (16X,3E16.7) / 4E16.7 interchange layout."""
    xyz, points, values = map(np.asarray, (coordinates_bohr, points_bohr, potential_au))
    if xyz.ndim != 2 or xyz.shape[1] != 3 or points.shape != (len(values), 3):
        raise ValueError('Invalid ESP dimensions')
    if not all(np.isfinite(x).all() for x in (xyz, points, values)):
        raise ValueError('Nonfinite ESP data')
    if max(len(xyz), len(points)) > 99999:
        raise ValueError('ESP data exceeds I5 format')
    with Path(path).open('w', encoding='ascii', newline='\n') as out:
        out.write(f'{len(xyz):5d}{len(points):5d}{0:5d}\n')
        for x, y, z in xyz:
            out.write(f'{x:32.7E}{y:16.7E}{z:16.7E}\n')
        for value, (x, y, z) in zip(values, points):
            out.write(f'{value:16.7E}{x:16.7E}{y:16.7E}{z:16.7E}\n')


def read_resp_charges(path, count):
    charges = np.array([float(x.replace('D', 'E')) for x in Path(path).read_text().split()])
    if len(charges) != count or not np.isfinite(charges).all():
        raise ValueError('Invalid RESP output charge count/values')
    return charges


def esp_error(reference, predicted):
    residual = np.asarray(predicted) - reference
    rms = float(np.sqrt(np.mean(residual**2)))
    norm = float(np.sqrt(np.mean(np.asarray(reference)**2)))
    return {'rmse_au': rms, 'relative_rms': rms / norm if norm > 1e-12 else None}
