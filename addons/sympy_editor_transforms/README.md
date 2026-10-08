# sympy-editor-transforms

A [sympy-editor](https://github.com/Upabjojr/sympy-editor) add-on: integral
transforms of the selection - Laplace, Fourier, sine, cosine, Mellin,
Hankel and their inverses, which are SymPy's, and the one-sided
z-transform and its inverse, which SymPy does not have and this add-on
adds.  No dependency beyond the editor and SymPy.

```python
from sympy_editor import edit
edit(expr, addons=["transforms"])          # or switch it on in the ≡ drawer's Add-ons
```

## The panel

Select a piece of the formula (nothing selected: the whole of it), then

| control | what it does |
|---|---|
| **transform** | which one: Laplace, inverse Laplace, Fourier, … , z-transform, inverse z-transform |
| **of** | the variable of the selection - one of its free symbols; the usual one is chosen for you (`t` for Laplace, `s` for its inverse, `n` for the z-transform) |
| **new variable** | the name of the result's variable (`s`, `k`, `z`, `n`… by default) |
| **order** / **strip** | Hankel's order ν; the strip `(a, b)` an inverse Mellin transform is taken in |
| **Compute** | shows the result, typeset, and where it holds in words - *converges for Re(s) > -2*, *converges for \|z\| > 1/2*, *provided k is real and positive* - and the convention used; the formula is not changed |
| **Apply** | replaces the selection (or the range) by its transform: one step of the history, which Undo takes back.  With the editor's **unevaluated** toggle on, the transform itself goes in - `LaplaceTransform(f, t, s)`, `FourierTransform(...)`, and for the z-transform `Sum(f*z**(-n), (n, 0, oo))` - which *Evaluate (doit)* computes later |

The panel asks Python nothing by itself: a new selection only refills the
variable list from the snapshot.

## The Transform menu

*Laplace transform…*, *Inverse Laplace transform…*, *Fourier transform…*,
*Inverse Fourier transform…*, *z-transform…* and *Inverse z-transform…* are
ops: the editor's parameter form asks for the variable (a list of the
selection's symbols) and the new variable (optional, with its default), as
it does for *Differentiate…*.  The conditions go to the status line.  All
but the inverse z-transform have an unevaluated form.

## The z-transform

`sympy_editor_transforms.z_transform(f, n, z)` returns `(F, R, nonzero)`:
`F(z) = Σ_{n≥0} f(n) z^(-n)`, converging for `|z| > R`.  It works term by
term from the table

| f(n) | F(z) | region |
|---|---|---|
| `b**n` (any power or exponential with an exponent linear in n) | `z/(z - b)` | `\|z\| > \|b\|` |
| `b**n sin(w n + p)`, `b**n cos(w n + p)` | `b z sin(w)/(z² − 2bz cos(w) + b²)`, `z(z − b cos(w))/(…)` combined by `p` | `\|z\| > \|b\|` |
| `n * x(n)` | `−z d/dz X(z)` | the same |
| `KroneckerDelta(n, m)` | `z**(-m)` | `z ≠ 0` |

and leaves anything else to SymPy's `summation`, whose condition gives the
region.  `inverse_z_transform(F, z, n)` takes a rational function of `z`
whose numerator's degree is at most its denominator's (a causal sequence),
splits `F(z)/z` into partial fractions (`apart`) and reads each term back:
a pole `p` of order `m` gives `binomial(n, m − 1) p**(n − m + 1)` (a
Kronecker delta for a pole at 0), a pair of complex poles `r e^(±iθ)` gives
`r**n cos(θn)` and `r**n sin(θn)`.  The result is the sequence for `n ≥ 0`.

## Conventions

SymPy's, said under each result: Fourier `F(k) = ∫ f(x) e^(−2πixk) dx`, sine
and cosine `√(2/π) ∫₀^∞`, Laplace one-sided, Hankel `∫₀^∞ f(r) J_ν(kr) r dr`.

## Limitations

- What SymPy cannot transform is reported ("SymPy could not find the Laplace
  transform of …") and nothing changes; some transforms are slow - the
  editor's Interrupt stops them.
- The inverse z-transform takes rational functions only, with poles SymPy's
  `roots` can find in closed form.
- SymPy's `MellinTransform(...).doit()` answers with a tuple (result, strip,
  condition), which then lands in the formula as such: apply the Mellin
  transform evaluated rather than *Evaluate* an unevaluated one.
- An unevaluated inverse Laplace transform is built with the abscissa `-oo`
  (SymPy wants one; it does not change what `doit` computes).

## Tests

```sh
PYTHONPATH=src python -m pytest addons/sympy_editor_transforms -q
```

`tests/test_transforms.py` (the z-transform table both ways, SymPy's
transforms with their conditions, the panel's methods, the ops, undo,
sessions) and `tests/test_transforms_browser.py` (the panel and the
Transform menu in Chromium, skipped without Playwright or the KaTeX CDN).
