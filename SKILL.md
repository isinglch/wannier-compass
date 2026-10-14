---
name: wannier-compass
description: Use VASP projected band evidence to select a complete orbital manifold and Wannier windows, run disentanglement-only trials, and review same-k band fits through MCP or CLI. Applies to prepared VASP materials; does not certify physical or symmetry acceptance.
---

# WannierCompass

At the start of a fresh context, read the [construction overview](wannier_mcp/workflow/docs/CONSTRUCTION-OVERVIEW.md) to understand the intended model, window tradeoffs, good-result features and diagnostic order. Retain that picture for subsequent cases; after a context reset, restore it before choosing orbitals or windows. Then use the [first-trial card](wannier_mcp/workflow/docs/FIRST-TRIAL-CARD.md) to make evidence-based decisions. English is operational; the [Chinese overview](wannier_mcp/workflow/docs/zh-CN/CONSTRUCTION-OVERVIEW.md) supports human review.

With MCP, call `workflow_guide`, then use `read_text` on the returned overview, decision, runtime and interface paths. With a shell/CLI client, read these bundled documents directly; MCP is not required. Use the [runtime card](wannier_mcp/workflow/docs/RUNTIME.md) and [interface contract](wannier_mcp/workflow/docs/INTERFACES.md) for commands. The bundled [scientific workflow](wannier_mcp/workflow/SKILL.md) is the source for selection logic; the MCP server only invokes its CLI. Do not infer orbital/window choices from a material name or historical fit.

Declare the task and concentrated deep-block roles before basis or target rows. The default is the main task-relevant orbital manifold and related low conduction. Extra channels require independent task evidence; fitting every deep bonding shell is an explicitly broader task. No global gap or residual mixing alone requires adding s. See the v3 scope contract in the first-trial card.

For each case, keep the projected-band evidence, `initial`, reviewed `real_first_try`, each retry, selection figures, actual-interface mesh checks, disentanglement convergence, full-target same-k comparison, and physical review as distinct records. Submit commands require explicit execution and site permission. Use `num_iter=0`; never silently change basis or target during a window-only retry. Compare alternative bases on the same task states and report additional deep-state fit separately.

After an unaccepted actual trial, read the short [window retry card](wannier_mcp/workflow/docs/WINDOW-RETRIES.md) before selecting the next experiment. It separates review of unprotected low-conduction states from release of already protected high-conduction states.

Read the detailed reference documents only when that branch is needed. The current release has no experience database, automatic few-band construction, or final symmetry-adapted acceptance.

English is canonical for execution; Chinese reading translations are separate. See the [documentation policy](wannier_mcp/workflow/docs/DOCUMENTATION.md). With bounded MCP reads, continue at `next_offset` until the required reference is complete.

Evidence and selection are file-based and deployment-independent. Slurm execution is an optional reference adapter, not a prerequisite for using scientific tools. Use compact evidence/proposal helpers and incremental draft patches; load full records only for the current decision. See the [optional Slurm example](wannier_mcp/workflow/docs/SLURM-EXAMPLE.md) when that execution environment applies.
