"""Fit exploratory molecular RESP charges with explicit checks; no MD approval."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
from pyscf import gto, lib, scf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/adhesive_ai'))
from ce_resp import (surface_grid, molecular_esp, point_charge_esp,
                                write_resp_esp, read_resp_charges, esp_error)
BASE = ROOT / 'work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1'
BIN = Path(sys.executable).parent
os.environ['AMBERHOME'] = str(BIN.parent)
os.environ['PATH'] = str(BIN) + os.pathsep + os.environ.get('PATH', '')
lib.num_threads(4)


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def run(directory, name, arguments):
    with (directory / (name + '.log')).open('w') as out:
        subprocess.run([str(BIN / arguments[0]), *arguments[1:]], cwd=directory,
                       stdout=out, stderr=subprocess.STDOUT, check=True, timeout=1800)


def smoke():
    directory = BASE / 'resp_references' / 'adapter_smoke'
    directory.mkdir(parents=True, exist_ok=True)
    xyz = np.array([[0., 0., 0.], [1.43, 0., 1.1], [-1.43, 0., 1.1]])
    known = np.array([-.82, .41, .41])
    points = surface_grid([8, 1, 1], xyz)
    write_resp_esp(directory/'synthetic.esp', xyz, points, point_charge_esp(xyz, known, points))
    control = 'Synthetic water point charges\n\n &cntrl\n nmol=1, ihfree=1, ioutopt=1, qwt=0.0,\n &end\n 1.0\n synthetic\n'
    control += f'{0:5d}{3:5d}\n'
    control += ''.join(f'{number:5d}{0:5d}\n' for number in (8,1,1)) + '\n\n'
    (directory/'synthetic.in').write_text(control)
    run(directory, 'resp', ['resp', '-O', '-i', 'synthetic.in', '-o', 'synthetic.out',
                          '-e', 'synthetic.esp', '-t', 'synthetic.q', '-p', 'synthetic.punch'])
    recovered = read_resp_charges(directory/'synthetic.q', 3)
    if np.max(abs(recovered-known)) > 2e-5:
        raise ValueError('RESP interchange does not recover known charges')
    mol = gto.M(atom='H 0 0 0; H 0 0 .74', basis='6-31g*', verbose=0)
    mf = scf.RHF(mol).run(conv_tol=1e-11)
    if not mf.converged:
        raise ValueError('H2 adapter smoke SCF did not converge')
    probe = np.array([[4., 0., 0.], [0., 4., 0.], [0., 0., 5.]])
    batched = molecular_esp(mol, mf.make_rdm1(), probe)
    direct = []
    for point in probe:
        with mol.with_rinv_origin(point):
            direct.append(sum(mol.atom_charges()/np.linalg.norm(mol.atom_coords()-point, axis=1))
                          - np.einsum('ij,ji', mol.intor('int1e_rinv'), mf.make_rdm1()))
    error = float(np.max(abs(batched-direct)))
    if error > 1e-9:
        raise ValueError('Batched quantum ESP disagrees with independent integral path')
    write(directory/'validation.json', {'passed': True, 'known_charges': known.tolist(),
          'recovered_charges': recovered.tolist(), 'pyscf_integral_max_error_au': error,
          'production_approved': False})


def calculate(name):
    reference = BASE / 'qm_references' / name
    previous = json.loads((reference/'state.json').read_text())
    if previous['state'] != 'qm-geometry-converged':
        raise ValueError('Geometry is not converged')
    original_input = BASE/'qm_inputs'/previous['input']['xyz']
    if hashlib.sha256(original_input.read_bytes()).hexdigest() != previous['input']['sha256']:
        raise ValueError('Original QM input changed')
    directory = BASE/'resp_references'/name
    directory.mkdir(parents=True, exist_ok=True)
    state = directory/'state.json'
    if state.exists():
        raise ValueError('Existing RESP result requires review before rerun')
    tracked = {str(p.relative_to(BASE)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (reference/'optimized.xyz', reference/'optimized.chk', reference/'state.json')}
    report = {'state': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'model_id': 'MDM-00203D56BC3B1B90', 'name': name, 'input_hashes': tracked,
              'method': 'RHF/6-31G*; AmberTools two-stage RESP', 'threads': 4,
              'production_approved': False, 'forcefield_validated': False}
    write(state, report)
    try:
        mol, record = scf.chkfile.load_scf(str(reference/'optimized.chk'))
        symbols = [row.split()[0] for row in (reference/'optimized.xyz').read_text().splitlines()[2:]]
        if symbols != [mol.atom_symbol(i) for i in range(mol.natm)]:
            raise ValueError('Checkpoint/XYZ atom order mismatch')
        xyz_angstrom = np.array([[float(v) for v in row.split()[1:]] for row in (reference/'optimized.xyz').read_text().splitlines()[2:]])
        if not np.allclose(xyz_angstrom, mol.atom_coords(unit='Angstrom'), atol=2e-6, rtol=0):
            raise ValueError('Checkpoint/XYZ coordinate mismatch')
        mol.output = str(directory/'quantum.log')
        mol.stdout = open(mol.output, 'w')
        mol.max_memory = 8000
        mf = scf.RHF(mol)
        mf.conv_tol = 1e-10
        mf.max_cycle = 150
        mf.chkfile = str(directory/'stable.chk')
        density = mf.make_rdm1(record['mo_coeff'], record['mo_occ'])
        energy = mf.kernel(dm0=density)
        if not mf.converged or abs(energy-previous['energy_hartree']) > 1e-6:
            raise ValueError('Checkpoint SCF reconstruction failed')
        _, _, internal, external = mf.stability(internal=True, external=True, return_status=True)
        report['wavefunction_stability'] = {'internal': bool(internal), 'external': bool(external)}
        write(state, report)
        if not internal or not external:
            raise ValueError('RHF wavefunction stability failed; review required')
        xyz, numbers = mol.atom_coords(), mol.atom_charges()
        train = surface_grid(numbers, xyz)
        holdout = surface_grid(numbers, xyz, scales=(1.5, 1.7, 1.9), density=.8, phase=.37)
        train_esp = molecular_esp(mol, mf.make_rdm1(), train)
        test_esp = molecular_esp(mol, mf.make_rdm1(), holdout)
        np.savez_compressed(directory/'esp_arrays.npz', coordinates_bohr=xyz, numbers=numbers,
                            train_points_bohr=train, train_esp_au=train_esp,
                            holdout_points_bohr=holdout, holdout_esp_au=test_esp)
        write_resp_esp(directory/'molecule.esp', xyz, train, train_esp)
        subprocess.run([str(ROOT/'.venv/bin/python'), str(ROOT/'scripts/prepare_ce_resp_structure.py'), name, str(directory)], check=True)
        (directory/'zero.q').write_text(' '.join('0.0' for _ in numbers)+'\n')
        run(directory, 'antechamber', ['antechamber','-i','optimized.sdf','-fi','sdf','-o','molecule.ac',
            '-fo','ac','-at','gaff2','-c','rc','-cf','zero.q','-nc',str(mol.charge),'-dr','no','-s','2'])
        # Check antechamber did not reorder atoms before respgen establishes equivalences.
        atom_lines = [line for line in (directory/'molecule.ac').read_text().splitlines() if line.startswith('ATOM')]
        ac_xyz = np.array([[float(v) for v in row.split()[5:8]] for row in atom_lines])
        if ac_xyz.shape != xyz.shape or not np.allclose(ac_xyz, xyz_angstrom, atol=.002, rtol=0):
            raise ValueError('Antechamber atom coordinate order changed')
        for stage in (1, 2):
            run(directory, f'respgen{stage}', ['respgen','-i','molecule.ac','-o',f'stage{stage}.in','-f',f'resp{stage}'])
            arguments = ['resp','-O','-i',f'stage{stage}.in','-o',f'stage{stage}.out','-e','molecule.esp',
                         '-t',f'stage{stage}.q','-p',f'stage{stage}.punch']
            if stage == 2:
                arguments += ['-q','stage1.q']
            run(directory, f'resp{stage}', arguments)
            if 'does not converge' in (directory/f'stage{stage}.out').read_text().lower():
                raise ValueError('RESP iteration did not converge')
        charges = read_resp_charges(directory/'stage2.q', mol.natm)
        if abs(charges.sum()-mol.charge) > 1e-4 or np.max(abs(charges)) > 2:
            raise ValueError('RESP total charge or charge range check failed')
        report.update(state='resp-fit-complete-review-required', finished_at=datetime.now(timezone.utc).isoformat(),
            charges_e=charges.tolist(), charge_sum_e=float(charges.sum()),
            grid={'type':'exposed Fibonacci/Bondi spheres, exploratory', 'train_scales':[1.4,1.6,1.8,2.0],
                  'training_points':len(train), 'holdout_points':len(holdout), 'coordinate_unit':'Bohr', 'potential_unit':'atomic'},
            training=esp_error(train_esp, point_charge_esp(xyz, charges, train)),
            holdout=esp_error(test_esp, point_charge_esp(xyz, charges, holdout)),
            limitations=['single conformer', 'grid sensitivity and independent force-field validation pending',
                         'molecular charges are not directly transferable to cured-network atoms'])
        report['output_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (directory/'molecule.esp', directory/'stage2.q',directory/'esp_arrays.npz')}
        write(state, report)
    except Exception as exc:
        report.update(state='failed', error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())
        write(state, report)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', choices=['BADCy','DCDDS','triazine_reference'])
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    if args.smoke:
        smoke()
    elif args.name:
        calculate(args.name)
    else:
        parser.error('Choose --smoke or --name')
