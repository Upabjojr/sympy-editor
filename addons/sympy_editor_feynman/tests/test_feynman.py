"""The QED expansion: the textbook counts, factors and signs; the nodes in
a document; the panel's data."""
import sys
from pathlib import Path

import pytest
from sympy import Add, Function, I, Integral, Rational, Symbol, latex, srepr, symbols

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sympy_editor import Document  # noqa: E402
from sympy_editor_feynman import ADDON, Diagram, PathIntegral, Photon, Psi, PsiBar, amplitude, diagrams  # noqa: E402
from sympy_editor_feynman.qed import FERMION, PHOTON, diagram_json  # noqa: E402

x1, x2, x3, x4, mu, nu = symbols("x_1 x_2 x_3 x_4 mu nu")


def edges(d):
    return [tuple(str(x) for x in e) for e in d.edges]


def test_the_electron_propagator_to_order_two():
    """The free propagator, and the one-loop self-energy with factor 1; the
    vacuum bubble is dropped unless asked for, and comes with 1/2 and the
    loop's sign."""
    P = PathIntegral(Psi(x1) * PsiBar(x2))
    ds = diagrams(P, 2)
    assert [(d.order, d.factor) for d in ds] == [(0, 1), (2, 1)]
    assert edges(ds[0]) == [("F", "x_2", "x_1")]                                   # ψ̄(x₂) ... ψ(x₁)
    assert edges(ds[1]) == [("F", "z_1", "x_1"), ("F", "z_2", "z_1"), ("F", "x_2", "z_2"), ("A", "z_1", "z_2", "mu_1", "mu_2")]
    assert diagrams(P, 2, "no_bubbles") == ds
    everything = diagrams(P, 2, "all")
    assert len(everything) == 3 and Rational(-1, 2) in [d.factor for d in everything]
    assert diagrams(P, 1) == ds[:1]                                                  # odd orders: nothing to contract
    # the Feynman rules, as printed
    tex = latex(ds[1])
    assert tex.startswith(r"(-ie)^{2}\;\int d^4z_{1}\,d^4z_{2}")
    assert r"S_F(x_{1} - z_{1})\,\gamma^{\mu_{1}}\,S_F(z_{1} - z_{2})\,\gamma^{\mu_{2}}\,S_F(z_{2} - x_{2})" in tex
    assert r"D_{\mu_{1} \mu_{2}}(z_{1} - z_{2})" in tex
    assert latex(ds[0]) == "S_F(x_{1} - x_{2})"


def test_the_loop_brings_its_sign_and_the_traces():
    """Vacuum polarisation: one diagram at order two, factor -1 (the two
    contractions of the external photons over 2!, times the loop's sign),
    printed as a trace; the vacuum bubbles of Z itself."""
    ds = diagrams(PathIntegral(Photon(mu, x1) * Photon(nu, x2)), 2)
    assert [(d.order, d.factor) for d in ds] == [(0, 1), (2, -1)]
    tex = latex(ds[1])
    assert r"\mathrm{Tr}\!\left[\gamma^{\mu_{1}}\,S_F(z_{1} - z_{2})\,\gamma^{\mu_{2}}\,S_F(z_{2} - z_{1})\right]" in tex
    assert r"D_{\mu \mu_{1}}(x_{1} - z_{1})" in tex and r"D_{\nu \mu_{2}}(x_{2} - z_{2})" in tex and "(-1)" not in tex
    assert tex.startswith("-1")
    # the vacuum amplitude: its connected bubbles (log Z), or 1 with the bubbles dropped
    assert [(d.order, d.factor) for d in diagrams(PathIntegral(1), 2, "all")] == [(0, 1), (2, Rational(-1, 2))]
    assert diagrams(PathIntegral(1), 2) == diagrams(PathIntegral(1), 2, "all")
    assert [(d.order, d.factor) for d in diagrams(PathIntegral(1), 2, "no_bubbles")] == [(0, 1)]


def test_the_vertex_the_scatterings_and_their_relative_signs():
    assert [(d.order, d.factor) for d in diagrams(PathIntegral(Psi(x1) * PsiBar(x2) * Photon(mu, x3)), 1)] == [(1, 1)]
    moller = diagrams(PathIntegral(Psi(x1) * PsiBar(x2) * Psi(x3) * PsiBar(x4)), 2)
    assert sorted(d.factor for d in moller) == [-1, 1]                              # the exchange diagram, with its sign
    assert all(d.order == 2 and sum(1 for e in d.edges if e[0] == PHOTON) == 1 for d in moller)
    compton = diagrams(PathIntegral(Psi(x1) * PsiBar(x2) * Photon(mu, x3) * Photon(nu, x4)), 2)
    assert [d.factor for d in compton] == [1, 1] and all(d.order == 2 for d in compton)
    higher = diagrams(PathIntegral(Psi(x1) * PsiBar(x2)), 4)
    assert len([d for d in higher if d.order == 4]) == 5                            # the two-loop self-energy diagrams
    with pytest.raises(ValueError, match="order"):
        diagrams(PathIntegral(1), 5)
    with pytest.raises(ValueError, match="which"):
        diagrams(PathIntegral(1), 1, "some")
    with pytest.raises(ValueError, match="Not a field"):
        PathIntegral(Symbol("x"))


def test_the_amplitude_as_an_expression():
    d = diagrams(PathIntegral(Psi(x1) * PsiBar(x2)), 2)[1]
    a = amplitude(d)
    e, z1, z2, mu1, mu2 = symbols("e z_1 z_2 mu_1 mu_2")
    S_F, D_F, gamma = Function("S_F"), Function("D_F"), Function("gamma")
    assert isinstance(a, Integral) and a.variables == [z1, z2]
    assert a.function == (-I * e) ** 2 * S_F(x1, z1) * gamma(mu1) * S_F(z1, z2) * gamma(mu2) * S_F(z2, x2) * D_F(mu1, mu2, z1, z2)
    loop = diagrams(PathIntegral(Photon(mu, x1) * Photon(nu, x2)), 2)[1]
    assert "Tr(" in str(amplitude(loop)) and str(amplitude(loop)).startswith("Integral(e**2*")   # -1 * (-ie)^2 = e^2
    assert amplitude(diagrams(PathIntegral(Psi(x1) * PsiBar(x2)), 0)[0]) == S_F(x1, x2)


def test_the_nodes_in_a_document():
    """Typed input, the kinds and their ops, the expansion as a transformation
    with its parameters, the drawing data in every snapshot, srepr back."""
    doc = Document("PathIntegral(psi(x_1)*psibar(x_2))", addons=[ADDON])
    assert isinstance(doc.expr, PathIntegral) and doc.expr.fields == [Psi(x1), PsiBar(x2)]
    snap = doc.snapshot()
    assert snap["nodes"]["/"]["kind"] == "pathintegral" and snap["nodes"]["/0/0"]["kind"] == "field"
    assert snap["feynman"] == {"diagrams": [], "integrals": 1}
    assert r"\int\!\mathcal{D}[\bar{\psi},\psi,A]" in snap["latex_plain"] and r"e^{\,i S_{\mathrm{QED}}" in snap["latex_plain"]
    op = next(o for o in snap["ops"] if o["name"] == "feynman_expand")
    assert op["kinds"] == ["pathintegral"] and [p["default"] for p in op["params"]] == ["2", "connected"]
    snap = doc.handle({"action": "apply", "path": "/", "op": "feynman_expand", "params": ["2", "connected"]})
    assert snap["error"] is None and isinstance(doc.expr, Add) and all(isinstance(t, Diagram) for t in doc.expr.args)
    assert [d["factor"] for d in snap["feynman"]["diagrams"]] == ["1", "1"]
    drawn = snap["feynman"]["diagrams"][1]
    assert {n["id"]: n["kind"] for n in drawn["nodes"]} == {"x_1": "psi", "x_2": "psibar", "z_1": "vertex", "z_2": "vertex"}
    assert {(e["kind"], e["from"], e["to"]) for e in drawn["edges"]} == {("F", "z_1", "x_1"), ("F", "z_2", "z_1"), ("F", "x_2", "z_2"), ("A", "z_1", "z_2")}
    assert drawn["order"] == 2 and drawn["src"] in {n["src"] for n in snap["nodes"].values()}      # the panel finds the term
    assert snap["nodes"]["/1"]["kind"] == "diagram"
    # the diagram's op, and srepr back into a document
    assert Document(srepr(doc.expr), addons=[ADDON]).expr == doc.expr
    snap = doc.handle({"action": "apply", "path": "/1", "op": "feynman_amplitude"})
    assert snap["error"] is None and any(isinstance(t, Integral) for t in doc.expr.args)
    assert doc.history_labels()["actions"][-2:] == ["Transform: Feynman diagrams (expand)", "Transform: Feynman rules (position space)"]
    # the panel's methods: an example, an expansion
    snap = doc.handle({"action": "addon", "addon": "feynman", "method": "example", "src": "PathIntegral(A(mu, x_1)*A(nu, x_2))"})
    assert snap["error"] is None and doc.expr == PathIntegral(Photon(mu, x1) * Photon(nu, x2))
    snap = doc.handle({"action": "addon", "addon": "feynman", "method": "expand", "path": "/", "order": "2", "which": "all"})
    assert snap["error"] is None and len(snap["feynman"]["diagrams"]) == 3
    assert doc.history_labels()["actions"][-1] == "Feynman: diagrams to order 2"
    bad = doc.handle({"action": "addon", "addon": "feynman", "method": "example", "src": "PathIntegral(1)*0"})
    assert "Not one of the examples" in bad["query"]["error"]
    # nothing to contract: a message, not a change
    doc.handle({"action": "set", "src": "PathIntegral(psi(x_1))"})
    snap = doc.handle({"action": "apply", "path": "/", "op": "feynman_expand", "params": ["2", "connected"]})
    assert "No diagram" in snap["error"] and isinstance(doc.expr, PathIntegral)
    assert ADDON.client_options()["max_order"] == 4 and len(ADDON.client_options()["examples"]) >= 5


def test_the_manifest_says_not_bundled():
    import json
    manifest = json.loads((Path(__file__).resolve().parents[1] / "addon.json").read_text())
    assert manifest["name"] == "feynman" and manifest["bundle"] is False and manifest["requires"] == []
    assert diagram_json(diagrams(PathIntegral(1), 0)[0])["edges"] == []
    assert FERMION != PHOTON
