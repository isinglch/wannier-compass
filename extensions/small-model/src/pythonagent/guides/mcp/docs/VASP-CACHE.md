# VASP output to evidence cache

`tools/vasp_to_cache.py` converts explicitly specified current static outputs to NPZ for review, plotting, and sampling checks. It parses data without selecting materials/orbitals/windows, searching other directories, or submitting jobs. Requires Python and NumPy.

## Projected band path

```bash
python tools/vasp_to_cache.py \
  --poscar run/band/POSCAR \
  --eigenval run/band/EIGENVAL \
  --procar run/band/PROCAR \
  --scf-outcar run/scf/OUTCAR \
  --spin-mode spinor --sampling band_path \
  --kpoints run/band/KPOINTS \
  --fit-path-out run/band/fit_path.json \
  --out run/band/charge.npz
```

Spin mode is an expectation checked against PROCAR, not a relabeling option. Without it, one charge table per band identifies scalar; four tables identify spinor. Read only charge; check, but do not sum, the three magnetization tables. The supported single-ion scalar dialect can omit a redundant tot row; parser tests cover its occupation convention.

Support Reciprocal Line-mode with uniform points per segment including endpoints; adjacent shared endpoints repeat. Compare actual EIGENVAL coordinates to KPOINTS before recording geometry. Reciprocal vectors include 2π and have units Å⁻¹. Preserve jumps, repetitions, and order; do not guess symmetry points. path_geometry serves plotting and fit_path serves exact-k comparison without resampling.

## Optional existing uniform-grid cache

Selection defaults to band evidence, followed by configured interface generation and actual-grid checking. No preliminary mesh PROCAR/WAVECAR is required. This example applies only to existing compatibility data or explicitly authorized analysis:

```bash
python tools/vasp_to_cache.py \
  --poscar run/mesh/POSCAR \
  --eigenval run/mesh/EIGENVAL \
  --procar run/mesh/PROCAR \
  --scf-outcar run/scf/OUTCAR \
  --spin-mode scalar --sampling wannier_mesh \
  --out run/mesh/charge.npz
```

NKPTS/NBANDS/energies/coordinates/weights come from EIGENVAL and are checked against PROCAR. No grid dimensions are inferred from NKPTS alone. wannier_mesh declares use; the execution layer verifies complete required sampling. Undeclared non-line data uses sampled_kpoints. A path cannot be relabeled as an interface mesh.

## Energy reference

Default requires explicit scf-outcar with NSW=0, ICHARG<10, matching scalar/spinor, and normal timing termination, then reads the final E-fermi. Parsing does not establish electronic convergence, structure, or Hamiltonian consistency; those are execution checks. Never use the line-path EF or directory-name guessing.

An explicitly reviewed consistent reference may use ef-json instead:

```json
{
  "kind": "reviewed_scf_reference",
  "ef_eV": 0.0,
  "source_path": "scf/OUTCAR",
  "source_sha256": "complete-source-SHA256",
  "spin_mode": "spinor",
  "reason": "Current structure/SOC/settings consistency and EF extraction evidence"
}
```

ef_eV has no default; relative source paths resolve from the JSON directory. Require readability/hash agreement. A reasoned reference is not automatic physical validation or scalar-to-SOC energy reuse.

## Cache contract

| Field | Representation |
| --- | --- |
| energies, occupations | (1,nk,nb); absolute DFT energies in eV |
| raw | (1,nk,nb,ni,norb), norb=9 for s/p/d or 16 for s/p/d/f; original per-ion charge, no self-normalization or division by tot |
| kcoords, kweights | (1,nk,3) fractional reciprocal coordinates, (1,nk) original weights including zero-weight path points |
| printed_total | (1,nk,nb) original charge sum and retained rounding diagnostics |
| metadata | Species/counts/columns, EF source, actual dimensions, spin/sampling, source hashes, maximum alignment errors |
| metadata.source_files | POSCAR/EIGENVAL/PROCAR and optional KPOINTS absolute paths, SHA256, bytes |
| path_geometry | Optional verified line-mode geometry only |

The supported ordered PROCAR columns are `s py pz px dxy dyz dz2 dxz x2-y2`, optionally followed by all seven `fy3x2 fxyz fyz2 fz3 fxz2 fzx2 fx3`. Known `dx2`/`dx2-y2` aliases are normalized to `x2-y2`. The printed header determines the orbital axis and `metadata.columns`; every charge/magnetization table must use the same complete layout. `tot` is retained separately and never used to renormalize weights. The v1 cache format remains readable for existing 9-column data. No f column in a source means f evidence is unavailable, not zero: do not fabricate an f group. Other f label dialects need an explicit validated adapter rather than dropping columns.

Require matching static outputs, complete ordered k/band/ion indices, consistent atom counts, supported tables/headers, finite values, and nonnegative charge weights. Default print-alignment tolerances are energy 2×10⁻⁵ eV, fractional k 2×10⁻⁷, occupation 10⁻⁵, and k weight 10⁻⁷. Explicit energy/k adjustments cap at 10⁻³ eV/10⁻⁴.

PROCAR has ion indices, not element names. POSCAR supplies atom-order labels; these files alone cannot prove an equal-count/equal-spectrum POSCAR was not swapped. Freeze POSCAR/POTCAR before execution and use them afterward. Preserve hashes/order/positions for audit.

Reject changed size/mtime during conversion, existing output, mismatched/truncated/reordered data, dual collinear spectra, partial/coarse/unsupported complex tables, unknown f label layouts, and unsupported layouts. Supported scope is ordinary static scalar and noncollinear/SOC charge-plus-three-magnetization outputs, not MD predicted eigenvalues, KPOINTS_OPT mixed grids, collapsed/shared-endpoint paths, or dynamic structures.

File interpretation follows [PROCAR](https://vasp.at/wiki/PROCAR), [EIGENVAL](https://vasp.at/wiki/EIGENVAL), [KPOINTS](https://vasp.at/wiki/KPOINTS), and [LNONCOLLINEAR](https://vasp.at/wiki/LNONCOLLINEAR), which provide no automatic orbital/window selection rules. Legacy development selftests are separate from the distributable package; packaged adapter/parser tests do not establish material-fit accuracy.
