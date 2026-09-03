# Required validated MD inputs

- `interface.data`: equilibrated topology with masses, charges, bonds, and box dimensions.
- `forcefield.production`: validated LAMMPS styles and all coefficients for that topology.
- `in.production`: generated temperature protocol; review run length and ensemble before approval.

- `md_approval.json`: approval record pointing to evidence for the exact hashed topology and force field.

The campaign will not submit this task until all files are present and the MD production evidence is approved.
