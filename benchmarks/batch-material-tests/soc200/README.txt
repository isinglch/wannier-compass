SOC200 benchmark

results.csv
200 materials, each represented by its best valid model. Of the 199 materials with valid scores, 158 pass; the pass rate over the full cohort is 79.0%. SOC-092 was terminated before completion and is counted as a nonpass, with numerical fields left blank.

outer_eV / frozen_eV
[minimum, maximum] in eV relative to the original SCF Fermi energy (EF = 0), extracted from the actual input of each retained model and verified against its recorded input hash. Both fields are blank for SOC-092, which has no valid retained model.

comparison.csv
Material count, attempt count, pass count, pass rate, and mean WF spread across passing materials. Each passing material has equal weight in the spread average.

Attempt accounting
610 attempts in total: 461 non-localization attempts and 149 localization attempts, averaging 3.05 per material (200 initial attempts plus 410 additional attempts).
For the same material and source model, localization runs capped at 20, 50 or 100 iterations count together as one attempt. Localization of a different source model counts separately.
results.csv gives each material's total. attempt_counts.csv gives the non-localization and localization breakdown. summary.json and attempt_accounting.json use the same counts.

plots/
PNG figures only. The eta2 histogram spans 0-30 meV with 1 meV bins. Its mean and median use all 199 valid scores; the 32 values above the displayed range remain in results.csv.

Evaluation
Fixed original-target, same-k Hungarian band matching; energy range up to EF + 2 eV for metals or sampled CBM + 2 eV for insulators; sigma = 0.1 eV; pass threshold eta2 <= 20 meV. Results are limited to the original target states and finite sampling.

Structure-source and publication links are listed in SOURCES.txt. Structural files are not included.
