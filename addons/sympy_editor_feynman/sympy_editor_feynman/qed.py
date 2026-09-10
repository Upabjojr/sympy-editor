"""Quantum electrodynamics, the perturbative way: the path integral of a
product of fields, the interaction expanded to a given order, Wick's theorem
contracting the fields pairwise, every full contraction a Feynman diagram.

The pieces are SymPy expressions, so the editor shows and edits them:

* :class:`Psi`, :class:`PsiBar`, :class:`Photon` - the fields at a point
  (``psi(x_1)``, ``psibar(x_2)``, ``A(mu, x_3)``);
* :class:`PathIntegral` - ``∫ D[ψ̄, ψ, A] ψ(x_1) ψ̄(x_2) ... e^{iS_QED}``, the
  correlator of its insertion (normalised by the vacuum-to-vacuum integral,
  which is why vacuum bubbles are dropped);
* :class:`Diagram` - one Feynman diagram: the external points, the
  vertices, the propagators between them, the symmetry factor with the
  fermion sign, and the order in the coupling.  It prints as its value by
  the Feynman rules in position space.

:func:`diagrams` does the expansion: at order n the interaction
``-ie ∫ d⁴z ψ̄(z) γ^μ ψ(z) A_μ(z)`` appears n times, the fields are contracted
in every way (ψ with ψ̄ - never at the same vertex, the interaction being
normal-ordered - and A with A), the sign of each contraction is the parity
of the permutation that brings the pairs together, and the contractions that
are the same diagram up to a relabelling of the vertices are one diagram with
the factor (their number)/n!.
"""

from __future__ import annotations

import itertools
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple as TTuple

import sympy
from sympy import Expr, Function, Integer, Rational, Symbol, Tuple
from sympy.core.symbol import Str

__all__ = ["Psi", "PsiBar", "Photon", "PathIntegral", "Diagram", "diagrams", "diagram_json", "diagram_from_json",
           "problems", "MAX_ORDER", "FERMION", "PHOTON", "amplitude", "EXAMPLES"]

#: Orders beyond this take too long to enumerate (every contraction is listed).
MAX_ORDER = 4

#: The edge kinds - a fermion propagator ψ̄(from) ... ψ(to), a photon
#: propagator - as Str atoms: not symbols, so that they never shadow the
#: names typed in the context of the expression (``A`` is the photon field).
FERMION = Str("F")
PHOTON = Str("A")


# -- the fields -----------------------------------------------------------------


class Psi(Function):
    """The electron field ψ(x)."""
    nargs = 1

    def _latex(self, printer):
        return r"\psi{\left(%s\right)}" % printer._print(self.args[0])

    def _sympystr(self, printer):
        return "psi(%s)" % printer._print(self.args[0])


class PsiBar(Function):
    """The conjugate electron field ψ̄(x)."""
    nargs = 1

    def _latex(self, printer):
        return r"\bar{\psi}{\left(%s\right)}" % printer._print(self.args[0])

    def _sympystr(self, printer):
        return "psibar(%s)" % printer._print(self.args[0])


class Photon(Function):
    """The photon field A_μ(x): ``A(mu, x)``."""
    nargs = 2

    def _latex(self, printer):
        return r"A_{%s}{\left(%s\right)}" % (printer._print(self.args[0]), printer._print(self.args[1]))

    def _sympystr(self, printer):
        return "A(%s, %s)" % (printer._print(self.args[0]), printer._print(self.args[1]))


FIELD_TYPES = (Psi, PsiBar, Photon)


def _fields_of(insertion: Expr) -> List[Expr]:
    """The fields of an insertion (a product of fields, one field, or 1),
    in the order written; anything else is refused."""
    if insertion == 1:
        return []
    factors = list(insertion.args) if isinstance(insertion, sympy.Mul) else [insertion]
    out = []
    for f in factors:
        if isinstance(f, sympy.Pow) and isinstance(f.base, FIELD_TYPES) and f.exp.is_Integer and f.exp > 0:
            out.extend([f.base] * int(f.exp))               # psi(x)**2: the same field twice (it vanishes, but is legal)
        elif isinstance(f, FIELD_TYPES):
            out.append(f)
        else:
            raise ValueError(f"Not a field: {f} (the insertion is a product of psi(x), psibar(x) and A(mu, x))")
    return out


# -- the nodes ------------------------------------------------------------------


class PathIntegral(Expr):
    """``∫ D[ψ̄, ψ, A] <insertion> e^{iS_QED}``: the correlator of the fields
    of ``insertion`` (a product of fields; 1 for the vacuum amplitude),
    normalised by the vacuum integral."""

    def __new__(cls, insertion=1):
        insertion = sympy.sympify(insertion)
        _fields_of(insertion)                                # refuse anything but fields now
        return Expr.__new__(cls, insertion)

    @property
    def insertion(self) -> Expr:
        return self.args[0]

    @property
    def fields(self) -> List[Expr]:
        return _fields_of(self.insertion)

    def _latex(self, printer):
        inner = "" if self.insertion == 1 else r"\;" + printer._print(self.insertion)
        return r"\int\!\mathcal{D}[\bar{\psi},\psi,A]%s\; e^{\,i S_{\mathrm{QED}}[\bar{\psi},\psi,A]}" % inner

    def _sympystr(self, printer):
        return "PathIntegral(%s)" % printer._print(self.insertion)

    def _eval_is_commutative(self):
        return True


class Diagram(Expr):
    """One Feynman diagram.  ``Diagram(externals, vertices, edges, factor,
    order)``: the external fields (a Tuple), the vertex points (a Tuple of
    symbols), the propagators (a Tuple of ``(F, from, to)`` for a fermion
    line ψ̄(from)…ψ(to), and ``(A, p, q, mu_p, mu_q)`` for a photon line),
    the symmetry factor with the sign (a Rational), and the order in e.

    It prints as its value by the Feynman rules in position space - the
    spinor lines read from each external ψ back to an external ψ̄, closed
    loops as traces (their −1 is in the factor, with the sign of the
    contraction) - and is drawn by the add-on's panel."""

    def __new__(cls, externals, vertices, edges, factor=1, order=0):
        externals = Tuple(*externals)
        vertices = Tuple(*vertices)
        edges = Tuple(*[Tuple(*e) for e in edges])
        return Expr.__new__(cls, externals, vertices, edges, sympy.sympify(factor), Integer(order))

    externals = property(lambda self: self.args[0])
    vertices = property(lambda self: self.args[1])
    edges = property(lambda self: self.args[2])
    factor = property(lambda self: self.args[3])
    order = property(lambda self: int(self.args[4]))

    def _eval_is_commutative(self):
        return True

    # -- the Feynman rules, as text --

    def _index(self, point) -> str:
        """The Lorentz index carried at a point: μ_i at vertex i, the
        external photon's own index at its point."""
        for i, v in enumerate(self.vertices):
            if v == point:
                return r"\mu_{%d}" % (i + 1)
        for f in self.externals:
            if isinstance(f, Photon) and f.args[1] == point:
                return sympy.latex(f.args[0])
        return r"\mu"

    def lines(self) -> TTuple[List[List[Any]], List[List[Any]]]:
        """The fermion lines: the open ones as lists of points from an
        external ψ to an external ψ̄ (following the propagators against
        the arrows), and the closed ones as lists of vertices."""
        by_to = {}
        for e in self.edges:
            if e[0] == FERMION:
                by_to[e[2]] = e[1]
        open_lines, used = [], set()
        for f in self.externals:
            if isinstance(f, Psi):
                point = f.args[0]
                line = [point]
                while point in by_to and point not in used:
                    used.add(point)
                    point = by_to[point]
                    line.append(point)
                open_lines.append(line)
        loops = []
        for start in self.vertices:
            if start in used or start not in by_to:
                continue
            loop, point = [], start
            while point not in used:
                used.add(point)
                loop.append(point)
                point = by_to[point]
            loops.append(loop)
        return open_lines, loops

    def _latex(self, printer):
        lat = sympy.latex
        n = self.order
        parts = []
        if self.factor != 1:
            parts.append(lat(self.factor))
        if n:
            parts.append(r"(-ie)^{%d}" % n if n > 1 else "(-ie)")
            parts.append(r"\int " + r"\,".join(r"d^4%s" % lat(z) for z in self.vertices))
        open_lines, loops = self.lines()

        def prop(a, b):
            return r"S_F(%s - %s)" % (lat(a), lat(b))

        for line in open_lines:
            chain = []
            for i in range(len(line) - 1):
                chain.append(prop(line[i], line[i + 1]))
                if i + 1 < len(line) - 1:
                    chain.append(r"\gamma^{%s}" % self._index(line[i + 1]))
            parts.append(r"\,".join(chain))
        for loop in loops:
            chain = []
            for i, z in enumerate(loop):
                chain.append(r"\gamma^{%s}" % self._index(z))
                chain.append(prop(z, loop[(i + 1) % len(loop)]))
            parts.append(r"\mathrm{Tr}\!\left[%s\right]" % r"\,".join(chain))      # its -1 is in the factor
        for e in self.edges:
            if e[0] == PHOTON:
                parts.append(r"D_{%s %s}(%s - %s)" % (self._index(e[1]), self._index(e[2]), lat(e[1]), lat(e[2])))
        if not parts:
            return "1"
        return r"\;".join(parts)

    def _sympystr(self, printer):
        return "Diagram(order=%d, factor=%s, edges=%s)" % (self.order, self.factor, [tuple(str(x) for x in e) for e in self.edges])


# -- Wick's theorem ---------------------------------------------------------------


def _parity(perm: Sequence[int]) -> int:
    """+1 for an even permutation of 0..n-1, -1 for an odd one."""
    seen, sign = [False] * len(perm), 1
    for i in range(len(perm)):
        if seen[i]:
            continue
        j, length = i, 0
        while not seen[j]:
            seen[j] = True
            j = perm[j]
            length += 1
        if length % 2 == 0:
            sign = -sign
    return sign


def _matchings(items: List[int]) -> Iterable[List[TTuple[int, int]]]:
    """Every way to pair up ``items`` (an even number of them)."""
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for i, other in enumerate(rest):
        for more in _matchings(rest[:i] + rest[i + 1:]):
            yield [(first, other)] + more


def _components(points: List[Any], edges: List[TTuple[Any, Any]]) -> List[set]:
    parent = {p: p for p in points}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: Dict[Any, set] = {}
    for p in points:
        groups.setdefault(find(p), set()).add(p)
    return list(groups.values())


def diagrams(integral: PathIntegral, order: int = 2, which: str = "connected") -> List[Diagram]:
    """The Feynman diagrams of ``integral`` at every order up to ``order``
    in the coupling: the contractions of the external fields with the
    vertices' ψ̄ γ ψ A, grouped up to a relabelling of the vertices.
    ``which``: ``"connected"`` (one piece), ``"no_bubbles"`` (pieces without
    an external point dropped - they cancel against the normalisation; of
    the vacuum amplitude ``PathIntegral(1)`` only 1 is left) or ``"all"``."""
    if which not in ("connected", "no_bubbles", "all"):
        raise ValueError("which is 'connected', 'no_bubbles' or 'all'")
    order = int(order)
    if order < 0 or order > MAX_ORDER:
        raise ValueError(f"The order is 0 to {MAX_ORDER}")
    externals = integral.fields
    out: List[Diagram] = []
    for n in range(order + 1):
        out.extend(_diagrams_at(externals, n, which))
    return out


def _diagrams_at(externals: List[Expr], n: int, which: str) -> List[Diagram]:
    vertices = [Symbol("z_%d" % (i + 1)) for i in range(n)]
    # the fields in order: the insertion, then ψ̄(z_i) ψ(z_i) A(z_i) per vertex
    fields: List[TTuple[str, Any, Optional[int]]] = []      # (kind, point, vertex index or None)
    for f in externals:
        if isinstance(f, Psi):
            fields.append(("psi", f.args[0], None))
        elif isinstance(f, PsiBar):
            fields.append(("psibar", f.args[0], None))
        else:
            fields.append(("A", f.args[1], None))
    for i, z in enumerate(vertices):
        fields.extend([("psibar", z, i), ("psi", z, i), ("A", z, i)])
    psis = [i for i, f in enumerate(fields) if f[0] == "psi"]
    psibars = [i for i, f in enumerate(fields) if f[0] == "psibar"]
    photons = [i for i, f in enumerate(fields) if f[0] == "A"]
    if len(psis) != len(psibars) or len(photons) % 2:
        return []
    fermionic = [i for i, f in enumerate(fields) if f[0] != "A"]
    position = {idx: k for k, idx in enumerate(fermionic)}
    ext_points = [f[1] for f in fields if f[2] is None]
    photon_index = {}
    for f in externals:
        if isinstance(f, Photon):
            photon_index[f.args[1]] = f.args[0]
    for i, z in enumerate(vertices):
        photon_index[z] = Symbol("mu_%d" % (i + 1))

    classes: Dict[Any, List[Any]] = {}
    for perm in itertools.permutations(psibars):
        pairs = list(zip(psis, perm))
        if any(fields[a][2] is not None and fields[a][2] == fields[b][2] for a, b in pairs):
            continue                                        # ψ̄ψ at one vertex: normal-ordered away
        # the sign: the fermionic fields brought into the order ψ ψ̄ ψ ψ̄ ...
        target = [position[i] for pair in pairs for i in pair]
        sign = _parity(target)
        fermion_edges = [(fields[b][1], fields[a][1]) for a, b in pairs]     # ψ̄(from) ... ψ(to)
        for matching in _matchings(photons):
            if any(fields[a][2] is not None and fields[a][2] == fields[b][2] for a, b in matching):
                continue
            photon_edges = [(fields[a][1], fields[b][1]) for a, b in matching]
            points = ext_points + vertices
            comps = _components(points, fermion_edges + photon_edges)
            if which != "all":
                if ext_points:
                    if any(not any(p in c for p in ext_points) for c in comps):
                        continue                            # a vacuum bubble: cancels against the normalisation
                elif which == "no_bubbles" and n:
                    continue                                # the vacuum amplitude: everything is a bubble
                if which == "connected" and len(comps) > 1:
                    continue
            key = _canonical(vertices, fermion_edges, photon_edges)
            entry = classes.setdefault(key, [0, sign, fermion_edges, photon_edges])
            entry[0] += 1
            if entry[1] != sign:                            # the same diagram, two signs: cannot be
                raise AssertionError("inconsistent fermion sign")
    out = []
    for count, sign, fermion_edges, photon_edges in classes.values():
        factor = Rational(sign * count, sympy.factorial(n))
        edges = [Tuple(FERMION, a, b) for a, b in fermion_edges] + \
                [Tuple(PHOTON, a, b, photon_index[a], photon_index[b]) for a, b in photon_edges]
        out.append(Diagram(externals, vertices, edges, factor, n))
    out.sort(key=lambda d: (len(d.edges), str(d)))
    return out


def _canonical(vertices, fermion_edges, photon_edges):
    """The edges under the relabelling of the vertices that sorts them first:
    contractions that differ by a permutation of the vertices are one
    diagram."""
    best = None
    for perm in itertools.permutations(range(len(vertices))):
        relabel = {v: ("v", perm[i]) for i, v in enumerate(vertices)}

        def name(p):
            return relabel.get(p, ("x", str(p)))

        key = (tuple(sorted((name(a), name(b)) for a, b in fermion_edges)),
               tuple(sorted(tuple(sorted((name(a), name(b)))) for a, b in photon_edges)))
        if best is None or key < best:
            best = key
    return best


# -- the value, as SymPy --------------------------------------------------------


def amplitude(diagram: Diagram) -> Expr:
    """The diagram's value by the Feynman rules in position space, as an
    unevaluated SymPy expression: ``S_F(x, y)`` for a fermion propagator,
    ``D_F(mu, nu, x, y)`` for a photon one, ``gamma(mu)`` at each vertex,
    ``Tr(...)`` for a closed loop, the vertices integrated over.  (SymPy's
    product is commutative: the order along a spinor line is the printed
    diagram's, not this expression's.)"""
    S_F, D_F, gamma, Tr = Function("S_F"), Function("D_F"), Function("gamma"), Function("Tr")
    e = Symbol("e")
    open_lines, loops = diagram.lines()
    index = {}
    for i, z in enumerate(diagram.vertices):
        index[z] = Symbol("mu_%d" % (i + 1))
    for f in diagram.externals:
        if isinstance(f, Photon):
            index[f.args[1]] = f.args[0]
    factors: List[Expr] = [diagram.factor, (-sympy.I * e) ** diagram.order]
    for line in open_lines:
        for i in range(len(line) - 1):
            factors.append(S_F(line[i], line[i + 1]))
            if i + 1 < len(line) - 1:
                factors.append(gamma(index[line[i + 1]]))
    for loop in loops:
        inside = []
        for i, z in enumerate(loop):
            inside.append(gamma(index[z]))
            inside.append(S_F(z, loop[(i + 1) % len(loop)]))
        factors.append(Tr(sympy.Mul(*inside)))                  # the loop's -1 is in the factor
    for edge in diagram.edges:
        if edge[0] == PHOTON:
            factors.append(D_F(edge[3], edge[4], edge[1], edge[2]))
    value = sympy.Mul(*factors)
    if diagram.vertices:
        value = sympy.Integral(value, *diagram.vertices)
    return value


# -- for the panel ----------------------------------------------------------------


def problems(diagram: Diagram) -> List[str]:
    """What keeps the graph from being a QED diagram: a vertex without
    exactly one fermion line in, one out and one photon line; an external
    point with more or less than one line; a propagator to itself."""
    out = []
    legs: Dict[Any, List[str]] = {p: [] for p in list(diagram.vertices) + [_point(f) for f in diagram.externals]}
    for e in diagram.edges:
        if e[1] == e[2]:
            out.append("%s is joined to itself" % e[1])
        if e[0] == FERMION:
            legs.setdefault(e[1], []).append("out")
            legs.setdefault(e[2], []).append("in")
        else:
            legs.setdefault(e[1], []).append("A")
            legs.setdefault(e[2], []).append("A")
    for z in diagram.vertices:
        got = sorted(legs[z])
        if got != ["A", "in", "out"]:
            out.append("%s needs one fermion line in, one out and one photon line" % z)
    for f in diagram.externals:
        p = _point(f)
        want = "A" if isinstance(f, Photon) else "in" if isinstance(f, Psi) else "out"
        if legs[p] != [want]:
            out.append("%s should have one %s line" % (f, "photon" if want == "A" else "fermion"))
    return out


def _point(field) -> Any:
    return field.args[1] if isinstance(field, Photon) else field.args[0]


def diagram_json(diagram: Diagram) -> Dict[str, Any]:
    """What the panel draws: the points (external ones with their field),
    the propagators, the caption, and what is wrong with the graph if
    anything (the panel lets it be edited)."""
    nodes = []
    for f in diagram.externals:
        kind = "psi" if isinstance(f, Psi) else "psibar" if isinstance(f, PsiBar) else "A"
        point = _point(f)
        node = {"id": str(point), "label": sympy.latex(point), "kind": kind, "external": True, "field": sympy.latex(f)}
        if kind == "A":
            node["index"] = str(f.args[0])
        nodes.append(node)
    for z in diagram.vertices:
        nodes.append({"id": str(z), "label": sympy.latex(z), "kind": "vertex", "external": False})
    edges = [{"kind": "F" if e[0] == FERMION else "A", "from": str(e[1]), "to": str(e[2])} for e in diagram.edges]
    return {"src": str(diagram), "nodes": nodes, "edges": edges, "order": diagram.order,
            "factor": sympy.latex(diagram.factor), "factor_src": str(diagram.factor), "latex": sympy.latex(diagram),
            "problems": problems(diagram)}


def diagram_from_json(data: Dict[str, Any], parse=sympy.sympify, factor=None, template: Optional[Diagram] = None) -> Diagram:
    """The Diagram a panel's edit describes: ``{"nodes": [{"id", "kind",
    "external", "index"?}], "edges": [{"kind", "from", "to"}]}`` (the shape
    :func:`diagram_json` gives), the ids parsed back with ``parse``.  The
    order is the number of vertices; the factor is ``factor`` (parsed), else
    the template's, else 1.  A photon's index is the one given, the
    template's for that point, or a new one."""
    points: Dict[str, Any] = {}

    def point(name: str):
        if name not in points:
            points[name] = parse(name)
        return points[name]

    old_index = {}
    if template is not None:
        for f in template.externals:
            if isinstance(f, Photon):
                old_index[str(f.args[1])] = f.args[0]
    externals, vertices, photon_index = [], [], {}
    for n in data.get("nodes") or []:
        kind, p = n.get("kind"), point(str(n["id"]))
        if kind == "psi":
            externals.append(Psi(p))
        elif kind == "psibar":
            externals.append(PsiBar(p))
        elif kind == "A":
            idx = parse(str(n["index"])) if n.get("index") else old_index.get(str(p), Symbol("mu_" + str(p).replace("_", "")))
            externals.append(Photon(idx, p))
            photon_index[p] = idx
        else:
            vertices.append(p)
    for i, z in enumerate(vertices):
        photon_index[z] = Symbol("mu_%d" % (i + 1))
    edges = []
    for e in data.get("edges") or []:
        a, b = point(str(e["from"])), point(str(e["to"]))
        if e.get("kind") == "A":
            edges.append(Tuple(PHOTON, a, b, photon_index.get(a, Symbol("mu")), photon_index.get(b, Symbol("nu"))))
        else:
            edges.append(Tuple(FERMION, a, b))
    if factor is not None and str(factor).strip() != "":
        fac = parse(str(factor))
    elif template is not None:
        fac = template.factor
    else:
        fac = Integer(1)
    return Diagram(externals, vertices, edges, fac, len(vertices))


#: What the panel offers to start from: name -> the insertion's source.
EXAMPLES = [
    ("Electron propagator ⟨ψ(x₁) ψ̄(x₂)⟩", "PathIntegral(psi(x_1)*psibar(x_2))"),
    ("Photon propagator ⟨A(x₁) A(x₂)⟩", "PathIntegral(A(mu, x_1)*A(nu, x_2))"),
    ("Vertex ⟨ψ ψ̄ A⟩", "PathIntegral(psi(x_1)*psibar(x_2)*A(mu, x_3))"),
    ("Electron scattering ⟨ψ ψ̄ ψ ψ̄⟩", "PathIntegral(psi(x_1)*psibar(x_2)*psi(x_3)*psibar(x_4))"),
    ("Compton scattering ⟨ψ ψ̄ A A⟩", "PathIntegral(psi(x_1)*psibar(x_2)*A(mu, x_3)*A(nu, x_4))"),
    ("Vacuum bubbles ⟨1⟩", "PathIntegral(1)"),
]
