"""The generated example pages embed the package's JavaScript and, for the
Pyodide backend, its Python sources.  A stale page silently runs old code, so
whenever one exists it must match the current sources."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from sympy_editor.html import python_sources, read_static

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
# every generated page, the derivations included; checkpoints and other
# hidden directories are not ours
PAGES = sorted(p for p in EXAMPLES.rglob("*.html")
               if not any(part.startswith(".") for part in p.relative_to(EXAMPLES).parts))


@pytest.mark.parametrize("page", PAGES, ids=[p.name for p in PAGES])
def test_generated_page_is_up_to_date(page):
    text = page.read_text(encoding="utf-8")
    if "SympyEditor" not in text:
        pytest.skip(f"{page.name} carries no editor (an index, say)")
    hint = f"regenerate it: python examples/{page.stem}.py"
    assert read_static("editor.js").strip() in text, f"{page.name} embeds an outdated editor.js; {hint}"
    assert read_static("editor.css").strip() in text, f"{page.name} embeds an outdated editor.css; {hint}"
    # An editor page mounts an editor; a history page mounts the viewer alone.
    configs = re.findall(r"SympyEditor\.mount(?:History)?\(document\.getElementById\(\"[^\"]+\"\), (\{.*?\})\);\n", text)
    assert configs, f"no editor or history viewer found in {page.name}"
    current = python_sources()
    for raw in configs:
        cfg = json.loads(raw)
        if cfg.get("backend") == "pyodide":
            for name, src in current.items():
                assert cfg["sources"].get(name) == src, f"{page.name} embeds an outdated {name}; {hint}"


def test_no_pages_is_fine():
    # Pages are git-ignored build products; their absence is not an error.
    assert EXAMPLES.is_dir()


NOTEBOOKS = sorted(EXAMPLES.glob("*.ipynb"))


@pytest.mark.parametrize("nb", NOTEBOOKS, ids=[p.name for p in NOTEBOOKS])
def test_notebook_is_well_formed(nb):
    doc = json.loads(nb.read_text(encoding="utf-8"))
    assert doc["nbformat"] == 4 and doc["cells"]
    assert doc["metadata"]["kernelspec"]["language"] == "python"
    for cell in doc["cells"]:
        assert cell["cell_type"] in ("markdown", "code")
        assert isinstance(cell["source"], list)


def test_the_manualintegrate_example_still_runs(tmp_path, monkeypatch):
    """The notebook reaches into `sympy.integrals.manualintegrate` - a private
    rule tree that SymPy is free to change.  It is an example, not part of
    the library, but a broken example is worse than none: run its cells."""
    from sympy import Symbol, sin

    nb = EXAMPLES / "manualintegrate_steps.ipynb"
    if not nb.exists():
        pytest.skip("example not present")
    cells = [c for c in json.loads(nb.read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code"]
    monkeypatch.chdir(tmp_path)                      # the last cell writes a file
    ns = {}
    for i, cell in enumerate(cells):
        exec(compile("".join(cell["source"]), f"<cell {i}>", "exec"), ns)
    x = Symbol("x")
    hist = ns["integration_history"](x * sin(x), x)
    assert len(hist) > 2
    assert hist[0] == ns["Integral"](x * sin(x), x)
    assert hist[-1] == (x * sin(x)).integrate(x)     # the chain really ends at the antiderivative
    # the rules are named in words, with their own parameters after them
    assert hist.actions[1] == "Parts rule: u = x, dv = sin(x)"
    assert all(a is None or "rule" in a.lower() or a.startswith("Substitute back") for a in hist.actions), hist.actions
    assert (tmp_path / "integration_steps.html").is_file()


DERIVATIONS = EXAMPLES / "derivations"


def _derivations_module():
    spec = importlib.util.spec_from_file_location("derivations", DERIVATIONS / "derivations.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_derivation_is_a_history_that_can_be_shown():
    """The shelf is meant to be read: every derivation has to build, to have
    a word about each step, and to render."""
    mod = _derivations_module()
    assert len(mod.DERIVATIONS) >= 8
    slugs = [slug for slug, _ in mod.DERIVATIONS]
    assert len(set(slugs)) == len(slugs)
    for slug, make in mod.DERIVATIONS:
        history = make()
        assert len(history) >= 4, slug                    # a derivation worth the name has steps
        assert history.title, slug
        assert (make.__doc__ or "").strip(), slug         # the index shows this line
        actions = history.actions
        assert actions[0] is None, slug                   # nothing produced the first step
        assert all(a for a in actions[1:]), slug          # every other step says what it was
        from sympy.logic.boolalg import BooleanAtom

        steps = history.steps
        # An Eq whose sides SymPy can compare answers True or False the
        # moment it is built, and the reader is shown the word instead of
        # the equation.  `evaluate=False` is the fix; this is the alarm.
        decided = [i for i, step in enumerate(steps) if isinstance(step, BooleanAtom)]
        assert not decided, (slug, decided)
        repeated = [i for i in range(1, len(steps)) if steps[i] == steps[i - 1]]
        # SymPy answers some questions the moment they are built - exp(I*pi)
        # is -1, and x**3 - x**3 is gone - which turns a step into the one
        # before it and leaves the reader looking at the same formula twice.
        assert not repeated, (slug, repeated)
        payload = history.payload()
        assert len(payload["steps"]) == len(history), slug
        assert all("\\htmlData{path=/}" in step["latex"] for step in payload["steps"]), slug


def test_every_step_of_every_derivation_holds():
    """Not only the last line: each step is checked against the one before
    it, in whatever way that derivation means - the same solutions, the same
    value, the same system.  A wrong middle step is the easiest kind of
    mistake to publish and the hardest to notice."""
    from sympy import (Eq, I, Matrix, MatrixSymbol, cos, diff, exp, expand, eye, pi,
                       simplify, sin, solve, sqrt, symbols)

    mod = _derivations_module()
    by = {slug: make for slug, make in mod.DERIVATIONS}
    x, a, b, c, lam, t, omega = mod.x, mod.a, mod.b, mod.c, mod.lam, mod.t, mod.omega

    # the quadratic formula: every line has the solutions the first one had
    steps = by["quadratic-formula"]()
    wanted = set(solve(Eq(steps[0].lhs, steps[0].rhs), x))
    for i, step in enumerate(steps):
        got = set(solve(Eq(step.lhs, step.rhs), x))
        assert got == wanted or (i >= 5 and got and got <= wanted), (i, got)
    root = steps[-1].rhs
    assert simplify(a * root**2 + b * root + c) == 0

    # the Gaussian integral: the first line is the integral, the rest are its
    # square until the root is taken back at the end
    steps = by["gaussian-integral"]()
    assert steps[0].doit() == sqrt(pi) and steps[-1] == sqrt(pi)
    for i, step in enumerate(steps[1:7], start=2):
        assert simplify(step.doit() - pi) == 0, i

    # every limit of the first-principles derivation is the same derivative
    for i, step in enumerate(by["derivative-from-first-principles"]()):
        assert simplify(step.doit() - 3 * x**2) == 0, i

    # the integral is the same integral until it is done, and the last step
    # only folds two logarithms into one
    steps = by["partial-fractions"]()
    for step in steps[:3]:            # the fourth is two integrals added, not one
        assert simplify(step.function - 1 / (x**2 - 1)) == 0
    assert simplify(steps[3].doit() - steps[4]) == 0
    assert abs(complex((steps[5] - steps[4]).subs(x, 3).evalf())) < 1e-12

    # the eigenvalue derivation: the determinant, the roots, the vectors
    steps = by["eigenvalues"]()
    matrix = Matrix([[2, 1], [1, 2]])
    assert steps[1] == matrix - lam * eye(2)
    assert simplify(steps[2].arg.det() - ((2 - lam) ** 2 - 1)) == 0
    assert set(solve(steps[3].lhs, lam)) == set(matrix.eigenvals())
    assert simplify(steps[4].lhs - expand(steps[3].lhs)) == 0
    assert simplify(expand(steps[5].lhs) - steps[4].lhs) == 0
    for column, value in enumerate(steps[6]):
        vector = steps[7][:, column]
        assert (matrix * vector - value * vector).norm() == 0, value

    # every matrix of the elimination describes the same system
    steps = by["gaussian-elimination"]()
    answer = steps[0][:, :3].solve(steps[0][:, 3])
    for i, matrix in enumerate(steps[:-1]):
        assert (matrix[:, :3] * answer - matrix[:, 3]).norm() == 0, i
    assert steps[-1] == answer

    # the oscillator: the two derivatives of the Lagrangian, and a solution
    steps = by["harmonic-oscillator"]()
    v, mass = symbols("v"), mod.m
    lagrangian = mass * v**2 / 2 - mass * omega**2 * x**2 / 2
    assert simplify(diff(lagrangian, v) - steps[1].rhs) == 0
    assert simplify(diff(lagrangian, x) - steps[2].rhs) == 0
    amplitude, phase = symbols("A phi")
    motion = amplitude * cos(omega * t + phase)
    assert simplify(diff(motion, t, 2) + omega**2 * motion) == 0

    # least squares, with numbers: SymPy cannot decide these symbolically
    # (the two cross terms cancel because each is a 1x1 matrix)
    steps = by["least-squares"]()
    rows, cols = symbols("m n", positive=True, integer=True)
    A, bb, xx = MatrixSymbol("A", rows, cols), MatrixSymbol("b", rows, 1), MatrixSymbol("x", cols, 1)
    An, bn = Matrix([[1, 2], [3, 4], [5, 6]]), Matrix([7, 8, 9])
    x1, x2 = symbols("x1 x2")
    sub = {A: An, bb: bn, xx: Matrix([x1, x2])}
    quadratic = steps[1].subs(sub).doit()[0, 0]
    assert expand(steps[0].subs(sub).doit()[0, 0] - quadratic) == 0
    gradient = Matrix([diff(quadratic, v) for v in (x1, x2)])
    assert simplify(gradient - steps[2].lhs.subs(sub).doit()) == Matrix([0, 0])
    solution = steps[4].rhs.subs(sub).doit()
    assert simplify(An.T * An * solution - An.T * bn) == Matrix([0, 0])
    assert simplify(An.T * (An * solution - bn)) == Matrix([0, 0])   # the residual is orthogonal

    # Euler's identity, series and all
    steps = by["eulers-identity"]()
    assert simplify(steps[1].doit() - exp(I * x)) == 0
    assert simplify(steps[2].doit() - (cos(x) + I * sin(x))) == 0
    assert simplify(steps[5].lhs.doit() - steps[5].rhs) == 0
    assert simplify(steps[6].lhs.doit() - steps[6].rhs) == 0


def test_the_derivations_reach_the_results_they_claim():
    """Each one ends where the mathematics says it should - checked against
    SymPy itself, so a typo in a step cannot pass unnoticed."""
    from sympy import I, Rational, exp, pi, sqrt, symbols

    mod = _derivations_module()
    by_slug = {slug: make for slug, make in mod.DERIVATIONS}
    x, a, b, c = mod.x, mod.a, mod.b, mod.c

    quadratic = by_slug["quadratic-formula"]()[-1]
    root = quadratic.rhs
    assert (a * root**2 + b * root + c).simplify() == 0            # it really is a root

    assert by_slug["gaussian-integral"]()[-1] == sqrt(pi)
    assert by_slug["gaussian-integral"]()[0].doit() == sqrt(pi)    # and SymPy agrees

    assert by_slug["geometric-series"]()[-1].rhs == 1 / (1 - x)
    assert by_slug["derivative-from-first-principles"]()[-1] == (x**3).diff(x)
    fractions = by_slug["partial-fractions"]()
    assert fractions[-2] == fractions[0].doit()                    # SymPy's own antiderivative
    combined = (fractions[-1] - fractions[-2]).subs(x, 3)          # the last step only folds the two logs
    assert abs(complex(combined.evalf())) < 1e-12
    euler = by_slug["eulers-identity"]()
    assert euler[-1].lhs.doit() == 0 and euler[-1].rhs == 0        # e^(iπ) + 1 really is 0

    eigen = by_slug["eigenvalues"]()
    from sympy import Matrix
    assert Matrix([[2, 1], [1, 2]]).eigenvals() == {Rational(1): 1, Rational(3): 1}
    assert eigen[-2] == Matrix([1, 3])                             # the eigenvalues it shows

    system = by_slug["gaussian-elimination"]()
    augmented = system[0]
    assert augmented[:, :3].solve(augmented[:, 3]) == system[-1]    # the solution it ends on
