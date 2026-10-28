# Independent evaluation and experience accumulation

The skill supplies general decision order, numerical indicators, and required checks. An agent chooses candidates from current evidence, explains changes, and arranges authorized trials. Success concerns the actual fit and physical checks for the current objective, not agreement with historical windows or basis.

## 1. Run modes

| Mode | Accessible evidence | Purpose |
| --- | --- | --- |
| Development replay | Development materials and declared historical sources/results | Debug implementation and calibrate general rules; not independent generalization evidence |
| Independent blind evaluation | Frozen toolkit, authorized structure/settings, current DFT projections, and results generated within this run | Evaluate independent construction with experience retrieval disabled |
| Experience-assisted | Current evidence plus authorized retrieved cases | Measure changes in failure count, time, and cost; report separately |

Elements, structure, SOC, current DFT bands, and projections are legitimate inputs. Isolate historical final bases/windows, manual tuning, successful models, acceptance plots, golden files, and documents/tool outputs/logs exposing them. Do not copy Wannier settings from successful inputs into initial INCAR/templates.

Audit metadata: names, original directories, and energy-source paths can expose history. Give the evaluated agent necessary numeric data and neutral case IDs; retain the source mapping privately. OUTCAR may echo Wannier settings and is not automatically answer-free. The blind runner must reject `historical_control` as a primary-group source.

Materials used to modify rules belong to development. A new agent replaying them checks implementation/reproducibility, not unseen-material performance. Freeze rules before held-out material/family evaluation. Renamed directories, repeat calculations, and near-duplicate structures are not independent materials.

## 2. Distribution and execution boundaries

Private development workspaces may contain case data; the distributable repository contains allowlisted tools/docs only. Do not give an evaluated agent an entire historical workspace or this development conversation. Distribution manifests identify reviewed files by hash. A new directory is not access control: an agent with account-wide filesystem/SSH access can read history elsewhere.

Provide fresh context and an environment exposing only the current workspace, authorized inputs, and required tools. Enforce boundaries with permissions, a separate account/container, or tool-side confinement. Prompts alone cannot establish isolation. Historical directory search, logs, retrieval, and cached answers cannot serve as hidden fallbacks. Blind runs receive no experience-store credentials or endpoint.

At start, freeze case ID, model objective, allowed inputs/hashes, package hash, model/prompt, budget, acceptance scope, and experience mode. The agent may inspect its own current-run results for retries. Evaluators retain historical references privately, seal submissions and access logs, then compare. Do not return references to an agent still under the same evaluation.

Local consistency/packaging checks cannot prove external context, filesystem, or knowledge isolation; validate deployment boundaries separately.

## 3. Proposed experience-store structure

This is a future design, not an implemented vector database. Use a queryable case store rather than requiring the agent to search project directories:

1. Structured facts: structure/deduplication identifiers, chemistry/structure features, objective, XC/pseudopotential identity, SOC/magnetism, energy reference, sampling, NBANDS, basis/windows, and rule/tool versions.
2. Trial trajectories and evidence: each choice, revision reason, failure, figure, metric, cost, and acceptance state. Retain successes and failures. Large artifacts use immutable content-hash references rather than text vectors.
3. A vector index of evidence-supported summaries for similarity retrieval. It does not replace original facts or convert similarity into physical reliability.

Filter objective/settings comparability and held-out evaluation groups before ranking structure/semantic similarity. Return applicability, differences, provenance, failure boundaries, and validation evidence. Similar formulas alone cannot justify copying bases/windows. Historical energy values retain their own references and are only starting candidates for current-projection review.

Exclude blind materials and held-out near-duplicate/family groups at the retrieval-tool boundary. Experience-assisted runs record snapshot, filters, query, and hits. Keep evaluation results isolated until the round ends; decide later whether to ingest them. Do not add held-out answers to an index accessible during that same round.

## 4. Measuring experience benefits

Report retrieval-off and assisted success rates, errors over preregistered precision and complete-target scopes, missing branches, trial counts, and costs separately. Declare access to same-material history. Faster reproduction of known materials and unseen-material generalization are different evaluation outcomes.

Separate numerical feasibility, band fit, target-observable checks, and human review in each experience record. Preserve unchecked fields. Similar-looking figures, existing files, or an earlier success label cannot set all reliability fields to passing.

## General reasoning versus material answers

Reviewing deeper safe non-target prefixes when an upper boundary is unsafe, retaining mixed background for disentanglement, covering the target bottom with outer, and avoiding background in frozen are transferable physical guidance. Exact material band numbers, windows, and successful bases are case answers and remain outside blind packages. Current composition, structure, and DFT numbers are legitimate evidence.

A clean export is neither the whole development repository nor operating-system isolation. Keep historical cases, diagnostics, and retrieval stores outside the evaluated environment; disable retrieval for blind runs. Fresh-context answer-withheld replay of development materials remains development regression. Unseen generalization requires held-out materials or families. The vector knowledge store remains planned.

## Available interfaces

[Decision stages](DECISION-STAGES.md) support separately sealed initial/first-try choices, scientific trials, technical-restart directories, result snapshots, and trajectory export. Initial uses current DFT/general guidance; AI review and experience input are distinct records. Retrieval defaults off and has no real backend; there is no automatic ingestion or claimed learning benefit. Baseline ablations explicitly select unchanged initial, while AI-only arms use separate cases. Evaluate a retrieval arm after implementing its backend.
