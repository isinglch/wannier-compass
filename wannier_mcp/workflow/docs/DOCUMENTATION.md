# Documentation, language, and reproducibility conventions

## Language layout

English is the canonical language for MCP guidance, executable skills, CLI reference documents, and default generated reports. `docs/*.md` holds canonical references. `docs/zh-CN/*.md` holds corresponding Chinese reading translations; filenames match, and each translation links to its English source. The root README.md is a human-facing homepage with bilingual introduction/background paragraphs and Chinese main content. README.en.md is the complete English project page and package description. README.zh-CN.md preserves the older Chinese entry link. This homepage layout does not change the language or authority of operational instructions. Translation notices identify their role. Do not combine translated paragraphs with the executable English instruction body.

Human-facing display examples under the repository's root `docs/` directory are separate from the bundled operational `wannier_mcp/workflow/docs/` references. README illustrations are not policy, material-selection priors or inputs to a blinded evaluation. When staging a fresh selection test, provide current inputs and operational tools within the declared access scope; withhold historical examples and answers. Do not infer strict isolation from sharing an account.

Read CONSTRUCTION-OVERVIEW first for the physical model, window tradeoffs, expected result and diagnostic order, then the first-trial card for operational decisions. Use the retry card after a trial. Load further references only for the current branch. MCP `read_text` is bounded: if `next_offset < total_chars`, continue reading at next_offset before claiming to have read the complete reference. The guide's documentation paths point to English by default. Explicitly named locale metadata, such as legacy name_zh, is a display translation rather than another decision rule. Preserve user-submitted rationale and source text in their original language; language normalization does not rewrite sealed records.

## Scientific authority and terminology

- CONSTRUCTION-OVERVIEW supplies the physical picture; WORKFLOW-DECISIONS defines decision order and criterion scope; FIRST-TRIAL-CARD and WINDOW-RETRIES provide executable summaries.
- Tool implementations and policy/schema files define reproducible numerical checks, field names, and defaults. Documentation cannot waive their gates.
- State whether a quantity is a raw projection, reference-conditioned coverage, candidate-generation heuristic, necessary feasibility check, or externally reviewed acceptance condition.
- Use outer/disentanglement window and frozen/inner window consistently. Distinguish declared target, tail-inspection reference, precision objective, and plotting range.
- Report energies in eV relative to the declared SCF EF unless explicitly absolute. Use fractional reciprocal coordinates with stated basis, reciprocal distance in Å⁻¹ including 2π, and the documented 1-based band or 0-based half-open segment conventions.
- Label band-path evidence separately from actual-interface mesh checks. Path-projected spectra are not BZ DOS. Product completeness and DIS convergence are not fit or symmetry acceptance.

## Language-only revisions

Preserve criterion IDs, allowed effects, thresholds, schema keys, numerical examples, sampling/energy conventions, iteration controls, and scientific-trial accounting during translation. Changes to scientific requirements need a separately documented revision rather than being hidden in a language edit. Verify links, packaged document availability, manifests, numeric policy hashes, and executable behavior. Reproduction records bind the actual revised package hash, not just a reused version label.

Unchanged numerical code/settings preserve deterministic calculations for identical inputs. An agent reading a different instruction language can nevertheless make different choices. Cross-model decision quality therefore requires matched evaluation: fixed case/input hashes, model/settings, objective, trial budget, and acceptance scope; fresh equivalent contexts; and separate choices, fits, costs, and uncertainty. Do not infer equal scientific success rates from a translation or passing parser/adapter tests.

Chinese translations support human review. If wording differs, review against the canonical English contract and implementation, then correct the translation; do not merge both texts into a new rule. Keep historical benchmark snapshots immutable while evaluating a new documentation revision.
