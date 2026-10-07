# sympy-editor-steps

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): a
**step-by-step solution** for the selection, or the whole formula when
nothing is selected.  SymPy and the standard library only.

| the selection | what the panel shows |
|---|---|
| an **integral** (`Integral(x*sin(x), x)`, definite or not) | the rules of SymPy's `manualintegrate`, one per step: by parts (`u = x, dv = sin x`), substitution (`u = cos x, du = -sin x dx`, then back), sum, constant multiple, power, known integrals, rewrites; the integrals still to do stay unevaluated until a later step does them; `+ C` at the end, or the antiderivative evaluated between the limits |
| a **derivative** (`Derivative(x**2*sin(x), x)`, any order) | sum, constant multiple, product, quotient, power, exponential, logarithmic differentiation, chain rule, known derivatives - one rule applied to one `d/dx` per step - and a final simplification when it is shorter |
| an **equation** (`Eq(...)`) of degree one or two in one unknown | expand, collect, divide; for a quadratic the coefficients, the discriminant, the formula and the solutions (`x = 2 ∨ x = 3`), or a factor `x` / a square root when `b` or `c` is zero |
| anything else | says so, and offers **Differentiate**, **Integrate** or **Solve** (the expression = 0) |

Each step has its rule in words, the rule's parameters, and the whole
expression it leaves, drawn by KaTeX.  **Apply** puts that expression in the
formula in place of the selection: one step of the history, labelled
`Steps: <rule>`, which Undo takes back.  Remarks (the discriminant, the
antiderivative of a definite integral, the `+ C`) have nothing to apply, and
neither has an equation where the selection is a piece of a larger
expression.  What cannot be explained is said in words: an integral
`manualintegrate` has no rule for (SymPy's `integrate` result is given
without steps when there is one), a double integral, an equation of degree
three, an unknown in a denominator, an inequality.

## Use

```python
from sympy_editor import edit, serve
edit(expr, addons=["steps"])          # once installed: pip install -e addons/sympy_editor_steps
serve(expr, addons=["sympy_editor_steps"])   # or by module name, with the folder on the path
```

The engine works without the editor:

```python
from sympy import Integral, sin, symbols
from sympy_editor_steps.engine import explain
x = symbols("x")
for step in explain(Integral(x * sin(x), x)).steps:
    print(step.text, "|", step.expr)
```

## How it talks to the editor

Two methods, through the editor's one add-on message:

- `steps` (a query): `{"path", "children", "task", "var"}` - the target as
  a view path (or a range of its children), `task` one of `auto`,
  `integrate`, `differentiate`, `solve`.  The answer: `task`, `start`
  (LaTeX), `steps` (`text`, `latex`, `detail`, `applicable`), `message`,
  `offers`, `vars`, `var`, `src`.
- `apply` (a change): the same plus `index`, `src` (the target as the panel
  saw it - a target that changed since is refused) and `label`.  The steps
  are worked out again in Python rather than sent back as text: nothing is
  parsed.

The panel follows the selection, asks nothing while it is folded, and asks
again after every committed change.

## Tests

`pytest addons/sympy_editor_steps` (with `PYTHONPATH=src` from the editor's
checkout): the engines rule by rule, the methods through a `Document`
(undo, a session saved and opened again), and the panel in Chromium with
Playwright (skipped without it).
