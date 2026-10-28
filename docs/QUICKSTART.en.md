**[简体中文](QUICKSTART.md) · [English →](QUICKSTART.en.md)**

# WannierCompass: quick start

**Ask your agent to read the toolkit, provide your material and computing environment, and start automated Wannier construction.**

> Use WannierCompass to construct the Wannier TB Hamiltonian for material XX.

[Install](#install) · [Material and environment](#input) · [Start construction](#start) · [Results](#output) · [MCP / CLI](#other)

<a id="install"></a>

## 1. Download and install

Install the toolkit in the environment where it will run, using Python 3.10+:

```bash
git clone https://github.com/isinglch/wannier-compass.git
cd wannier-compass

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

You can also give the repository URL to your agent and ask it to download and install the toolkit.

<a id="input"></a>

## 2. Provide the material and computing environment

Give your agent the following information:

| Information | What to provide |
|---|---|
| Material inputs | POSCAR; for existing DFT calculations, the SCF, projected-band and reusable wavefunction directories |
| Computing environment | Paths to VASP, Wannier90 and the pseudopotential library, plus available resources and how jobs are run |
| DFT settings | Functional, SOC, magnetism, cutoff energy, k-point sampling and other material-specific parameters |
| Construction objective | Priority energy range, bands or intended research application |
| Run settings | Working directory, available resources and maximum number of trials |

Reuse existing DFT data when available. When starting from a structure, the agent prepares the SCF and projected-band calculations.

<a id="start"></a>

## 3. Ask the agent to start construction

Ask an agent with file and command access to read [SKILL.md](../SKILL.md), then give it a task such as:

> Use the toolkit in `/path/to/wannier-compass` to construct a Wannier TB Hamiltonian for this material.
>
> Read SKILL.md and the construction overview, then use the first-trial card to analyze the current projected bands and select a basis and windows. Reuse existing DFT data; use the parameters and executable paths I provide for additional calculations.
>
> Run in the specified working directory and computing resources for up to three trials. Save each plan and band comparison, inspect convergence and orbital spreads, and refine the construction using the actual results.

You can follow up with a more specific request:

> Focus on improving the fit near the Fermi level. Preserve the current target and try one more iteration.

<a id="output"></a>

## 4. View the results

Results are saved in the task's working directory and include:

| Result | Contents |
|---|---|
| Basis and window plans | Selected orbitals, Wannier count, disentanglement window and frozen window |
| DFT / Wannier comparisons | Same-k band plots and error records |
| Calculation and refinement records | Convergence, spreads, changes and next-step decisions for each trial |
| TB Hamiltonian | `wannier90_hr.dat` and other Wannier output files |

<a id="other"></a>

## Other entrypoints: MCP / CLI

<details>
<summary>Connect an agent through MCP</summary>

Add the following to the client's MCP configuration, using the actual toolkit and working-directory paths:

```json
{
  "mcpServers": {
    "wannier-compass": {
      "command": "/path/to/wannier-compass/.venv/bin/wannier-compass",
      "env": {
        "WAN_WORKSPACE_ROOT": "/path/to/workspace",
        "WAN_ALLOW_SUBMIT": "1"
      }
    }
  }
}
```

Once connected, ask the agent to call `workflow_guide` and read the construction and runtime instructions. Use `execute=true` when submitting calculations.

</details>

<details>
<summary>Call the CLI tools directly</summary>

Use `wannier-compass-run` to invoke tools after installation. See the [runtime card](../wannier_mcp/workflow/docs/RUNTIME.md) and [input/output interface](../wannier_mcp/workflow/docs/INTERFACES.md) for commands and input formats.

</details>

[← Project homepage](../README.en.md) · [DFT input preparation](../wannier_mcp/workflow/docs/DFT-PREPARATION.md) · [Construction methodology](../wannier_mcp/workflow/docs/CONSTRUCTION-OVERVIEW.md)
