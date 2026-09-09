# sympy-editor-latex

An add-on for [sympy-editor](https://github.com/Upabjojr/sympy-editor): LaTeX
in, with its ambiguities laid open.

A panel under the formula takes LaTeX, typed or pasted, and shows a first
reading of it, rendered and as SymPy source.  Two things about LaTeX make a
silent conversion a bad idea, and the panel puts both in the user's hands:

- **Ambiguity.**  `f(x)` is a function applied or a product; `\sin x \cos y`
  is `sin(x) cos(y)` or `sin(x cos(y))`; `a/bc` divides by `b` or by `bc`.
  The parser keeps every reading (an Earley parser, through
  [Lark](https://github.com/lark-parser/lark)), and each ambiguous part of
  the text becomes a menu of the readings it allows, the whole expression
  shown under each.  The first is picked by convention - a function without
  parentheses takes the product after it but stops at a sum and at another
  function, `f`, `g`, `h` and the document's own functions are applied while
  other letters multiply, parentheses end an argument - and any pick changes
  the reading on the spot.
- **Constants.**  `\pi`, `e`, `i`, `\gamma` are letters to LaTeX.  Each one
  the text uses is a switch: the constant (π, Euler's number, the imaginary
  unit...) or a plain symbol of that name.  `\pi`, `e` and `i` start as
  constants, the others as symbols.

**Replace the selection** puts the reading over what is selected in the
editor, **Replace the whole expression** (or Ctrl+Enter in the box) makes it
the formula.  Names the document already uses are reused with their
assumptions or matrix shapes.

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
