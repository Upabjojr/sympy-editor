# Derivations

A shelf of worked derivations, each one a `sympy_editor.History`: the steps
somebody would actually take to get to a result nobody believes in one line.

```
python examples/derivations/build.py --open
```

writes one page per derivation and an index over them.  Open one and press
**Play**: it runs as a slideshow, a step and the change that produced it on
each screen, with what went in red and what came in green.  **Save** keeps
any of them as a single file that works offline.

* `derivations.py` — the derivations themselves, as plain Python.  Add one by
  writing a function that returns a `History` and listing it in
  `DERIVATIONS`.
* `build.py` — writes the HTML.

The pages are build products (git-ignored); the Python is the source.  None
of them runs Python in the browser: they are the history viewer, which needs
only the steps.
