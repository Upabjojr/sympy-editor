# sympy-editor-latex

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): LaTeX
in, with its ambiguities laid open.

The LaTeX is typed **into the formula**: the **LaTeX** tool opens a field
where what is typed will land - in the selected piece's place (the piece
steps off the screen until the field goes, so that a replacement looks like
one), at the cursor, or after the whole expression.  The field is drawn as
what it is, in dashes on tinted paper: it is not part of the formula yet.
What it reads as is shown once, under the editor - as mathematics and as
SymPy source - rather than beside the LaTeX it is the reading of.  Beside the field the reading is shown as it will look; under the
editor, as SymPy would get it.  Two things about LaTeX make a silent
conversion a bad idea, and both are in the user's hands there:

- **Ambiguity.**  `f(x)` is a function applied or a product; `\sin x \cos y`
  is `sin(x) cos(y)` or `sin(x cos(y))`; `a/bc` divides by `b` or by `bc`.
  The parser keeps every reading (an Earley parser, through
  [Lark](https://github.com/lark-parser/lark)), and each ambiguous part of
  the text becomes a row of buttons, one per reading it allows, each the
  whole expression under that reading, typeset.  The first is picked by convention - a function without
  parentheses takes the product after it but stops at a sum and at another
  function, `f`, `g`, `h` and the document's own functions are applied while
  other letters multiply, parentheses end an argument - and any pick changes
  the reading on the spot.
- **Constants.**  `\pi`, `e`, `i`, `\gamma` are letters to LaTeX.  Each one
  the text uses is a switch: the constant (π, Euler's number, the imaginary
  unit...) or a plain symbol of that name.  `\pi`, `e` and `i` start as
  constants, the others as symbols.

Nothing changes in the document until **Apply to the formula** (or
<kbd>Enter</kbd>) says so - and then the strip shows the formula before and
after, what went in red and what came in green, to **Keep** or to **Undo the
change**; <kbd>Esc</kbd> closes the field and leaves the formula alone.  Where
the reading goes is the editor's own answer: the selected sub-expression, the
selected range, the cursor, or the end of the formula (where it goes in as if
typed there - multiplied, or added when it begins with + or -).  To replace
the whole expression, select it first.  Names the document already uses are
reused with their assumptions or matrix shapes - found under their LaTeX
spelling too: `x_{1}` is the document's `x_1`, `\lambda` its `lamda`,
`\hat{x}` its `xhat` (and `\lambda` is `lamda` in any case: `lambda` cannot
be typed back).

A fraction over a differential is a derivative only when its numerator is a
differential or `d^n` (`\frac{dy}{dx}`, `\frac{d}{dx} f`); anything else over
`dx` divides (`\frac{1}{dx}` is `1/(d x)`), and a numerator that is `d^n`
times something else (`\frac{d^2 y}{dx^2}`, `\frac{b d}{dt}`) is a choice
between the two - a derivative by default when the `d` comes first.

## Install

```sh
pip install -e addons/sympy_editor_latex     # from the editor's checkout
```

It needs `lark` (pure Python, MIT), nothing else; a self-contained Pyodide
page installs it with micropip on load.  Then:

```python
from sympy_editor import edit
edit(expr, addons=["latex"])
```

or switch it on in the editor's **Add-ons ▾** menu.

## The grammar

`sympy_editor_latex/static/grammar/` is a copy of SymPy's Lark grammar
(`sympy.parsing.latex.lark`, BSD) with what the add-on needs added:
`\pi`, `\sigma`, `\iota` and the uppercase Greek letters as symbols;
`\partial` reading like `d`, so `\frac{\partial f}{\partial x}` and
`\frac{d^2 y}{dx^2}` are derivatives; `\text{...}` and `\operatorname{...}`
naming multi-letter symbols; `\vec{v}`, `\hat{x}`, `\bar{x}`, `\mathbf{x}`...
as decorated symbols; a power, a decorated symbol or a function followed by
more factors (`x^2 y`, `\ln(x) y`); `a/bc` with the product as divisor;
`\sqrt x` without braces.  Ambiguities are never resolved in the grammar -
that is `parser.py`'s work, and the user's.

From Python:

```python
from sympy_editor_latex import read_latex
r = read_latex(r"\sin x \cos y + \pi")
r["src"]            # 'pi + sin(x)*cos(y)'
r["ambiguities"]    # [{'key': ..., 'fragment': '\\sin x \\cos y', 'choice': 1, 'options': [...]}]
r["choices"]        # every decision taken; pass it back with one changed to change just that one:
read_latex(r"\sin x \cos y + \pi", choices={**r["choices"], r["ambiguities"][1]["key"]: 0})["src"]
read_latex(r"\pi r^2", constants={"pi": False})["src"]   # 'pi*r**2' with pi a Symbol
```

## Tests

`pytest addons/sympy_editor_latex` - the reader (a battery of inputs, the
choices, the constants, the document methods) and, with Playwright and
Chromium, the panel in a browser.
