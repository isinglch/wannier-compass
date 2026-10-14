# Roadmap and evidence boundary

## Current research preview

The research-preview artifact is a VASP projected-band-path decision workflow with a thin MCP adapter and a compatible skill. It records `initial`, reviewed `real_first_try`, and later trials separately. It supports interface preparation, disentanglement-only Wannier90, convergence/product inspection, and same-k full-target fit review. Synthetic data exercise the interface only; real benchmark input packages and detailed answer records stay outside this repository. The README includes an author-provided Cu illustration for explaining the workflow.

Continue matched fresh-context evaluations with different AI clients and models, using a fixed tool release and current DFT inputs. Record input/release identities, objective, model settings where available, trial count, actual runtime, failures and physical review. Cost accounting is optional for an evaluation and is not an ordinary-use requirement. A completed run and a numerical fit are not final scientific acceptance.

## Next milestones

1. **Broader evidence:** collect distinct materials, representative FM/AFM states and bulk/monolayer structures. Track family and space group for analysis without letting them reveal historical answers during blind selection. Separate technical, solver, fit, and physical outcomes.
2. **Lower agent cost:** move deterministic inspection, plotting, record checks, and accounting to tools; keep model calls for ambiguous orbital and window decisions. Measure cost and quality against the same fixed benchmark before simplifying prompts or changing model size. A local-model claim requires its own evaluation.
3. **Experience retrieval:** design a versioned, opt-in store keyed by structure, Wyckoff positions, orbital shells, and evidence provenance. Retrieval should provide precedents and uncertainty, never silently substitute a historical answer or modify a blinded test. Test retrieval with and without the store.
4. **Multi-trial policy:** keep the first physical target and basis explicit; make window-only retries preserve them. Record when a retry widens `dis`, shrinks `frozen`, needs more NBANDS, or changes the model. Evaluate whether each intervention actually improves fit and convergence.
5. **Few-band and symmetry:** treat these as separate scientific contracts, with site/orbital subspaces, symmetry representations, gauge checks, and physically motivated observables. Assess integration with existing symmetry-adapted Wannier tools before implementing duplicate methods.

No date, material count, pass rate, cost saving, or scientific acceptance is promised by this roadmap.
