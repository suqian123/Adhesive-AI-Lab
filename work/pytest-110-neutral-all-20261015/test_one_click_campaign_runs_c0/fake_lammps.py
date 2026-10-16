from pathlib import Path
Path('log.lammps').write_text('Step Temp Pe Vol\n0 298 -10 1000\n100 299 -12 1001\n', encoding='utf-8')
