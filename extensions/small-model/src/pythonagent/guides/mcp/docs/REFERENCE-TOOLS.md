# Evidence tools and decision records

The scientific CLI retains v0.2 contract identifiers; the Python/MCP package version is separate. Use [sealed stages](DECISION-STAGES.md) for new execution; standalone proposals remain drafts. Initial is not executed directly.

[manifold_selection](MANIFOLD-SELECTION.md) is the complete selection entry; orbital_evidence provides lower-level evidence/checks; [selection_gallery](SELECTION-GALLERY.md) aggregates review. Policy/schema are reference-v0.2.json/decision-v0.2.schema.json. There is no material-specific threshold table. The package MCP server wraps these same CLIs rather than introducing a second selection algorithm.

Repeated trials can seal/verify shared_nscf, then prepare with shared-source for basis-specific ALGO=None matrices. Window-only retries reuse matching matrices. See [interface modes](WANNIER-INTERFACE.md). Register [01/02/03 layout](MATERIAL-LAYOUT.md), keep analysis/decision under selection records, and publish relative display links without changing scientific state.

## 1. Cache adapter

Analyze reads NPZ rather than bare PROCAR, with a single charge channel:

| Field | Contract |
| --- | --- |
| metadata | JSON species/counts/columns, independent ef/ef_source, procar_sha256; optional name |
| energies | Absolute (1,nk,nb), subtract metadata EF exactly once |
| raw | Charge (1,nk,nb,nion,norb), norb=9 or 16, excluding SOC magnetization tables |
| columns | Complete resolved s/p/d, optionally plus all seven f components; see [VASP cache layouts](VASP-CACHE.md). Cache column order may vary, excluding tot |

Additional coordinates/weights/occupation are allowed. Same-state analysis does not integrate a path or track branches automatically. [VASP conversion](VASP-CACHE.md) checks original data and energy reference; renaming an incompatible cache is insufficient. Spin mode must match actual scalar/spinor calculation. Dual collinear spectra remain unsupported. Whole-shell dimensions per atom are s=1, p=3, d=5, f=7; spinor mode doubles them. The same dimension function is used by proposals, decision checks and complete-shell exclusions.

## 2. Raw evidence and same-state competition

```bash
python3 tools/orbital_evidence.py analyze \
  --cache /path/to/charge_cache.npz \
  --spin-mode spinor --sampling band_path --out evidence.json
```

Without reviewed basis B, output raw levels with competition=null; this does not mean no competition exists. Raw weight 0.10 is a visibility indicator, not automatic inclusion. Do not place all such channels in B to evade competition review. Explain each channel's independent target or necessary mixed-state role; exceeding a line is insufficient. No new fixed ratio is imposed. Each B member is compared to B with itself removed; comparison_channels records that set.

After reviewing B, specify:

```text
--comparison-basis X:p,Y:d
--basis-revision B1
--basis-origin projected_bands_review
--basis-reason <current-target-branch-evidence>
--basis-evidence-ref <projected-figure-or-state-evidence>
```

Replace placeholders with current elements/evidence. Historical fixed-basis diagnostics use historical_control and cannot be called automatic selection. Revise B identity and recompute when its membership changes.

Output includes raw channel levels, trigger-state weights, full-spectrum peaks/per-band means, and candidate-versus-B competition. Default unclassified_full_spectrum does not select from local concentration. Explicit diagnostic ranges may add labels, not choices. Preserve 0.05/0.10 support ranges without choosing whichever matches history. Zero denominators yield null and counts; weak B retains raw data for review. analysis_id binds cache, optional range, spin/sampling, ordered B/revision/evidence, policy, and results. Parameter order does not change results. Analyze returns needs_basis_review and does not track branches or finalize basis.

## 3. Decision records

Every channel receives include, omit_first_trial, or defer plus reasons and locatable evidence. trial_basis equals include groups; defer is not silently included. Schema requires nonempty references, not physical validity, and CLI does not browse them.

| Status | Meaning |
| --- | --- |
| needs_basis_review | Basis/additional channels/branches unresolved |
| basis_trial_candidate | Candidate basis, window review pending |
| needs_window_review | Boundary/reference/tail evidence needs review |
| needs_more_data | Specific missing sources/steps recorded |
| proposed_trial | Reviewed choice passes sampled necessary checks; path permits interface preparation, actual mesh pending; no fit claim |

Ambiguous finite trials use separate self-consistent records. A record cannot retain defer while claiming a final executable configuration.

Path proposed_trial requires reviewed_target_manifold and complete_band_ranges_1based from projection/objective review, not an automatic branch algorithm. all_computed_diagnostic counts alone do not establish window review. Use next_action=prepare_interface, sampling=band_path, mesh_gate_pending=true. No precomputed mesh is required. Report actually missing band/SCF sources; add_bands_nscf requires demonstrated band-space insufficiency.

## 4. Recalculation and validation

```bash
python3 tools/orbital_evidence.py check-record \
  --cache /path/to/charge_cache.npz \
  --analysis evidence.json --record decision.json
```

Check schema, analysis/source/policy identity, B revision, choices, and trial basis. Recompute declared target envelopes; outer truncation cannot be excused by sufficient state count. Recompute counts, full-shell Nwann, and common band-top margin on the supplied cache. Reject reversed/nonnested windows, stale checks, or inconsistent gate_pass.

Band-path checks may permit preparation but leave mesh_gate_pending. Actual counts come from the current interface EIGENVAL/EIG/k points. Legacy mesh_fallback/trial_wannier branches require matching physics and are not default prerequisites. This tool does not certify full BZ, projection rank, or interpolation. It supports reviewed occupied-prefix exclusion; arbitrary high-row deletion/exact-square construction are outside scope.

record_consistent=true establishes necessary consistency only. Read check_scope, mesh_gate_pending, interface_preparation_allowed, and ready_for_wannier separately. Actual preparation also requires a sealed first try/retry. No validated_model/fit_pass is assigned. MCP callers must preserve the same configuration through actual inputs/grid/jobs.

## 5. Execution boundaries

Evidence CLI does not execute DFT/W90. Interface prepare creates inputs; submit previews unless execute; run-interface executes; validate only inspects existing outputs. Do not replace execution with validation or rewrite records/manifests to certify mismatches.

W90 uses official prepare/submit/run/inspect and independent wannier_manifest/run records. Plotting-only recovery is available after actual DIS/postwann chk when output writing was interrupted: prepare-output-recovery plus submit-output-recovery uses a fresh technical directory, restart=plot, afterany dependency, stable source hashes, and no-op when the source is already complete. Recovery convergence comes from source WOUT without fabricated new DIS. Adapt the recovery site resource profile separately.

Use bundled parsing/Nwann computation and preserve VASP-generated win. Require num_iter=0 and write_hr/write_rmn/write_tb/bands_plot. Report termination, convergence, bound product completeness, and fit separately. See [comparison](WANNIER-COMPARISON.md) for official postw90 submission/plots/diagnostics; native bands are display products, not guaranteed DFT-point alignment. No ad hoc driver is required.

Development synthetic tests check denominators, weak/zero channels, states, and stale records; they are behavior tests, not material acceptance. Packaged adapter/parser tests have the same boundary. Historical replay is evaluated only after current choices are sealed and is not a new-material selector.

There is no default seed. Inspect the full computed spectrum; optionally request inspect-range. Legacy seed is an explicit alias. seed_eV=null means no local diagnostic domain, not absent EF weight. priority_window_eV defaults null; physical precision objectives must be declared separately.
