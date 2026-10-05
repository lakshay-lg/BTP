# CVL7454 – Assignment 1: FE analysis of a 1D bar (2-noded vs 3-noded elements)

## Contents
| File | Description |
|---|---|
| `FE_1D_Bar_Assignment1.ipynb` | **FE code + report**: weak-form derivation, symbolic element matrices, FE solver, static and dynamic analyses, comparison studies (executed, outputs included) |
| `FE_1D_Bar_Assignment1.html` | Same notebook rendered as HTML, for viewing without Jupyter |
| `bar_input.json` | Input data file: geometry, material, loads, meshes, time-step settings |
| `results/` | All figures (`.png`) and tables (`.csv`, `.txt`) written by the notebook |

## How to run
```bash
pip install numpy scipy matplotlib sympy jupyter
jupyter nbconvert --to notebook --execute FE_1D_Bar_Assignment1.ipynb   # or open it in Jupyter and "Run All"
```
The notebook reads `bar_input.json` from its own folder and regenerates everything in `results/`. Edit the JSON to change the problem (e.g. mesh sizes, loads, Δt).

## Problem
Steel bar, L = 1 m, A = 1 cm², E = 200 GPa, ρ = 7850 kg/m³. Fixed at x = 0, free at x = L.
* **Static:** tip load P = 10 kN plus distributed load p(x) = p₀x/L with p₀ = 20 kN/m. The exact solution is cubic.
* **Dynamic:** suddenly applied (Heaviside step) tip load P₀ = 10 kN, bar initially at rest, undamped. The exact solution is a triangle wave in tip displacement and a square wave in fixed-end stress.

## Results files
| Figure / table | Study |
|---|---|
| `static_displacement.png`, `static_stress.png` | 2-node vs 3-node static fields and errors |
| `static_error_vs_dofs.png`, `static_mesh_study.csv` | L2 / energy-norm error vs DOFs (rates 2, 3 / 1, 2) |
| `element_matrices.txt` | Numerical K_e, M_e (consistent and lumped) for both elements |
| `frequency_error.png`, `mode_shapes.png`, `natural_frequencies.csv` | Eigen-analysis: FE vs exact frequencies, consistent vs lumped |
| `dynamic_2node_vs_3node.png`, `dynamic_stress_snapshots.png` | Step-load transient: 2-node vs 3-node |
| `dynamic_consistent_vs_lumped.png`, `dynamic_error_summary.csv` | Consistent vs lumped mass, transient |
| `integrators_comparison.png`, `dt_sensitivity.png`, `dt_sensitivity.csv` | Newmark (avg./linear accel.), RK4 and forward Euler; time-step sensitivity |

## Key findings
1. The weak form gives K_e = EA/l_e[1 −1; −1 1] and M_e = ρAl_e/6[2 1; 1 2] for the 2-node element. For the 3-node element it gives K_e = EA/(3l_e)[7 −8 1; −8 16 −8; 1 −8 7] and M_e = ρAl_e/30[4 2 −1; 2 16 2; −1 2 4]. Both are checked symbolically and by Gauss quadrature.
2. **Static:** both elements give nodally exact displacements (1D superconvergence). Inside the elements the 3-node element converges one order faster (L2: h² vs h³, energy: h vs h²). At equal DOFs its error is about 5× to 70× smaller.
3. **Frequencies:** consistent mass gives upper bounds and lumped mass lowers the frequencies. The 3-node element is far more accurate on the acoustic branch (error ∝ (ωh)⁴ vs (ωh)²) but has a spurious optical branch in the upper half of the spectrum.
4. **Step-load transient:** displacements are accurate for both elements. Stresses show about 30 % Gibbs-type overshoot and dispersion ringing. The 3-node element is only modestly better here (tip error 0.39 % vs 0.87 %, stress RMS error 11.7 % vs 13.8 %).
5. **Time integration:** Newmark average acceleration is unconditionally stable and conserves energy, with O(Δt²) error. Newmark linear acceleration is O(Δt²) and conditionally stable (Δt ≤ 0.55 T_min). RK4 is O(Δt⁴), slightly dissipative and conditionally stable (ω_max Δt ≤ 2√2). Forward Euler is unstable for undamped systems.
