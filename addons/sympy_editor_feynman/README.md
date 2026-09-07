# sympy-editor-feynman

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor):
path integrals of quantum electrodynamics turned into Feynman diagrams,
the way it is done on paper.

The expression is a path integral - the correlator of a product of fields,
normalised by the vacuum integral:

```
PathIntegral(psi(x_1)*psibar(x_2))            ∫ D[ψ̄, ψ, A] ψ(x₁) ψ̄(x₂) e^{iS_QED}
PathIntegral(A(mu, x_1)*A(nu, x_2))
PathIntegral(psi(x_1)*psibar(x_2)*A(mu, x_3))
```

**Feynman diagrams** (the transformation in the path integral's menu, or
the panel's *Diagrams* button) expands it to an order in the coupling:
the interaction `-ie ψ̄ γ^μ ψ A_μ` is brought down n times, Wick's theorem
contracts the fields pairwise in every way (ψ with ψ̄ - never at the same
vertex, the interaction being normal-ordered - and A with A), each full
contraction is a diagram, and the contractions that are the same diagram
up to a relabelling of the vertices are one diagram with the factor
(their number)/n! and the sign of the fermion permutation - so a closed
fermion loop brings its −1, and the two electron-scattering diagrams come
with opposite signs.  The result is the sum of the diagrams, each a term
printed by the Feynman rules in position space:

```
(-ie)² ∫ d⁴z₁ d⁴z₂  S_F(x₁ - z₁) γ^{μ₁} S_F(z₁ - z₂) γ^{μ₂} S_F(z₂ - x₂)  D_{μ₁μ₂}(z₁ - z₂)
```

The panel draws them - fermion lines with the arrow of the charge, wavy
photon lines, a dot per vertex, the external points labelled - and a
click on a drawing selects its term.  *Feynman rules* in a diagram's menu
gives its value as an expression (`S_F`, `D_F`, `gamma`, `Tr`, the
vertices integrated over).  *connected* keeps the diagrams in one piece,
*no vacuum bubbles* drops the pieces without an external point (they
cancel against the normalisation), *all* keeps everything.  Orders up to
4 (every contraction is enumerated).

## Install

This add-on is **not bundled** with the apps: it is the example of an
add-on installed from the editor's **Add-ons ▾** menu -

- from this repository: paste `https://github.com/Upabjojr/sympy-editor`
  (or the folder's URL,
  `https://github.com/Upabjojr/sympy-editor/tree/master/addons/sympy_editor_feynman`),
  *Look up*, tick *Feynman diagrams*, *Install*;
- from a file: `python addons/pack.py sympy_editor_feynman` writes the
  .zip that *From a file…* takes.

On a desktop, `pip install -e addons/sympy_editor_feynman` works too, then
`edit(expr, addons=["feynman"])`.

## From Python

```python
from sympy import symbols
from sympy_editor_feynman import PathIntegral, Psi, PsiBar, Photon, diagrams, amplitude
x1, x2 = symbols("x_1 x_2")
ds = diagrams(PathIntegral(Psi(x1) * PsiBar(x2)), order=2)     # [the free propagator, the self-energy]
ds[1].factor, ds[1].order, ds[1].edges                          # 1, 2, (F, z_1, x_1), (F, z_2, z_1), ...
amplitude(ds[1])                                                # Integral(-e**2*D_F(...)*S_F(...)..., z_1, z_2)
```

## Tests

`pytest addons/sympy_editor_feynman`: the contractions (counts, factors
and signs of the textbook cases), the nodes in a document, and, with
Playwright and Chromium, the panel.
