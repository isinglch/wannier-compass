This dataset contains 100 materials, including magnetic compounds, computed using spinor DFT with spin-orbit coupling (SOC).

Deduplication: reduced elemental composition and final-SCF BNS magnetic space group.
magnetic_moments_muB: final SCF [mx,my,mz] per atom in POSCAR order, in mu_B per PAW sphere.
eta2_meV: selected-target weighted RMS band error in meV; pass <=20 meV.
outer_eV/frozen_eV: [min,max] in eV relative to the original SCF Fermi energy.
attempts: number of attempts per material.
mean_spread_A2: arithmetic mean of WF spreads within each material, in angstrom^2.
passed_mean_spread_A2: arithmetic mean of mean_spread_A2 over passed materials, with equal weight per material.
