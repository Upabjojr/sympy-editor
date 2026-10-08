"""The Feynman panel in a real browser: an example started, the expansion,
the drawings, a click selecting a term.  Needs Playwright with Chromium and
the KaTeX CDN (skipped otherwise)."""
import sys
import threading
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

playwright = pytest.importorskip("playwright.sync_api")

from sympy_editor import Document  # noqa: E402
from sympy_editor.html import default_urls  # noqa: E402
from sympy_editor.server import EditorServer  # noqa: E402
from sympy_editor_feynman import ADDON, Diagram  # noqa: E402


def _online(url):
    try:
        with closing(urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _online(default_urls()["katexJs"]), reason="KaTeX CDN not reachable")


def test_the_panel_expands_draws_and_selects():
    doc = Document("PathIntegral(psi(x_1)*psibar(x_2))", addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(srv.url)
        page.wait_for_selector(".se-addon-feynman .fd-panel", timeout=30000)
        panel = page.locator(".se-addon-feynman")
        assert "No diagram yet" in panel.inner_text() or panel.locator(".fd-empty").count() == 1
        assert not panel.locator(".fd-expand").is_disabled()                    # the whole expression is the integral
        # expand to order 2: two cards, drawn
        panel.locator(".fd-order").fill("2")
        panel.locator(".fd-expand").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 2", timeout=15000)
        assert isinstance(doc.expr.args[1], Diagram)
        second = panel.locator(".fd-card").nth(1)
        assert second.locator("path.fd-fermion").count() == 3 and second.locator("path.fd-photon").count() == 1
        assert second.locator("circle.fd-vertex").count() == 2 and second.locator("circle.fd-external").count() == 2
        assert second.locator("polygon.fd-arrow").count() == 3
        assert "order e^2" in second.locator(".fd-caption").inner_text()
        assert "2 diagrams" in panel.locator(".fd-hint").inner_text()
        # a click on a card (its caption: the centre is a line, with a menu of its own) selects its term; the card shows it
        second.locator(".fd-caption").click(position={"x": 4, "y": 4})
        page.wait_for_function("document.querySelector('.fd-card:nth-child(2)').classList.contains('fd-selected')")
        assert page.evaluate("document.querySelector('.sympy-editor').__sympyEditor.selected") == "/1"
        assert page.locator(".se-view .katex").count() >= 1
        # an example from the menu replaces the expression
        panel.locator(".fd-examples").select_option("PathIntegral(A(mu, x_1)*A(nu, x_2))")
        page.wait_for_function("document.querySelector('.sympy-editor').__sympyEditor.state.src === 'PathIntegral(A(mu, x_1)*A(nu, x_2))'", timeout=15000)
        panel.locator(".fd-which").select_option("all")
        panel.locator(".fd-expand").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 3", timeout=15000)
        assert any("−1/2" in t or "-1/2" in t for t in panel.locator(".fd-caption").all_inner_texts())
        # -- editing the drawings edits the terms --
        panel.locator(".fd-examples").select_option("PathIntegral(psi(x_1)*psibar(x_2))")
        page.wait_for_function("document.querySelector('.sympy-editor').__sympyEditor.state.src === 'PathIntegral(psi(x_1)*psibar(x_2))'", timeout=15000)
        panel.locator(".fd-which").select_option("connected")
        panel.locator(".fd-expand").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 2", timeout=15000)
        card = panel.locator(".fd-card").nth(1)
        before = doc.expr

        def center(locator):
            b = locator.bounding_box()
            return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

        # move: a vertex dragged moves in the drawing, the expression stays
        z1 = card.locator('.fd-node[data-node="z_1"] .fd-vertex')
        x0, y0 = center(z1)
        page.mouse.move(x0, y0); page.mouse.down(); page.mouse.move(x0 + 20, y0 - 15, steps=4); page.mouse.up()
        x1_, y1_ = center(card.locator('.fd-node[data-node="z_1"] .fd-vertex'))
        assert abs(x1_ - x0 - 20) < 3 and abs(y1_ - y0 + 15) < 3 and doc.expr == before
        # a line's menu: delete the photon line -> the term loses it and is marked
        # (a point on the line itself: drawn as an arc, its box's middle is off it)
        x0, y0 = card.locator(".fd-edge:has(.fd-photon) .fd-hit").evaluate("""p => {
            const at = p.getPointAtLength(p.getTotalLength() / 2), m = p.getScreenCTM();
            return [at.x * m.a + at.y * m.c + m.e, at.x * m.b + at.y * m.d + m.f]; }""")
        page.mouse.click(x0, y0)
        menu = panel.locator(".fd-menu")
        assert menu.is_visible() and [b.strip() for b in menu.inner_text().split("\n") if b.strip()] == ["Delete line", "Make it a fermion line"]
        menu.get_by_text("Delete line").click()
        page.wait_for_function("document.querySelectorAll('.fd-card')[1].querySelectorAll('.fd-photon').length === 0", timeout=15000)
        card = panel.locator(".fd-card").nth(1)
        assert len(doc.expr.args[1].edges) == 3 and card.locator(".fd-warn").count() == 1
        assert "one photon line" in card.get_attribute("title")
        # draw it back as a photon line: drag from z_1 to z_2 in photon mode
        panel.locator(".fd-mode").select_option("photon")
        assert "photon" in panel.locator(".fd-hint").inner_text()
        xa, ya = center(card.locator('.fd-node[data-node="z_1"] .fd-vertex'))
        xb, yb = center(card.locator('.fd-node[data-node="z_2"] .fd-vertex'))
        page.mouse.move(xa, ya); page.mouse.down(); page.mouse.move(xb, yb, steps=6); page.mouse.up()
        page.wait_for_function("document.querySelectorAll('.fd-card')[1].querySelectorAll('.fd-photon').length === 1", timeout=15000)
        card = panel.locator(".fd-card").nth(1)
        assert len(doc.expr.args[1].edges) == 4 and card.locator(".fd-warn").count() == 0
        # add a vertex with a click: the order follows
        panel.locator(".fd-mode").select_option("vertex")
        b = card.locator("svg.fd-svg").bounding_box()
        page.mouse.click(b["x"] + b["width"] / 2, b["y"] + 12)
        page.wait_for_function("document.querySelectorAll('.fd-card')[1].querySelectorAll('.fd-vertex').length === 3", timeout=15000)
        assert doc.expr.args[1].order == 3 and "e^3" in panel.locator(".fd-card").nth(1).locator(".fd-caption").inner_text()
        # delete mode: the new vertex goes again
        panel.locator(".fd-mode").select_option("delete")
        card = panel.locator(".fd-card").nth(1)
        xz, yz = center(card.locator('.fd-node[data-node="z_3"] .fd-vertex'))
        page.mouse.click(xz, yz)
        page.wait_for_function("document.querySelectorAll('.fd-card')[1].querySelectorAll('.fd-vertex').length === 2", timeout=15000)
        assert doc.expr.args[1].order == 2
        panel.locator(".fd-mode").select_option("move")
        # the factor
        card = panel.locator(".fd-card").nth(1)
        card.locator(".fd-factor").click()
        card.locator(".fd-factor-field").fill("-1/2")
        card.locator(".fd-factor-field").press("Enter")
        page.wait_for_function("(b => !!b && b.textContent.includes('1/2'))(document.querySelectorAll('.fd-card')[1].querySelector('.fd-factor'))", timeout=15000)
        from sympy import Rational
        assert doc.expr.args[1].factor == Rational(-1, 2)
        # a new bare diagram, then removed with its ×
        panel.locator(".fd-new").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 3", timeout=15000)
        assert panel.locator(".fd-card").nth(2).locator(".fd-warn").count() == 1
        panel.locator(".fd-card").nth(2).locator(".fd-remove").click()
        page.wait_for_function("document.querySelectorAll('.fd-card').length === 2", timeout=15000)
        assert len(doc.expr.args) == 2
        assert "diagram edited" in doc.history_labels()["actions"][-3]
        # the guide
        page.locator(".se-addon-feynman .se-addon-help").click()
        assert "Wick" in page.locator(".se-help-view").inner_text() and "Editing a drawing" in page.locator(".se-help-view").inner_text()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()


def _expanded(page, src, order, which):
    """The cards of `src` expanded to `order` (`which` diagrams)."""
    panel = page.locator(".se-addon-feynman")
    ed = "document.querySelector('.sympy-editor').__sympyEditor"
    seq = page.evaluate(f"{ed}._stateCount || 0")
    panel.locator(".fd-examples").select_option(src)
    # (SymPy may order the fields its own way: the new state, not the text, is waited for)
    page.wait_for_function(f"({ed}._stateCount || 0) > {seq} && !{ed}.busy && {ed}.state.src.startsWith('PathIntegral')", timeout=15000)
    panel.locator(".fd-order").fill(order)
    panel.locator(".fd-which").select_option(which)
    before = panel.locator(".fd-card").count()
    panel.locator(".fd-expand").click()
    page.wait_for_function(f"document.querySelectorAll('.fd-card').length > 0 && "
                           f"[...document.querySelectorAll('.fd-card')].every(c => c.querySelector('svg'))", timeout=15000)
    page.wait_for_timeout(300)
    return panel.locator(".fd-card")


def test_the_diagrams_are_drawn_as_on_paper():
    """The layout of a textbook: the inner points follow the line through
    them (the self-energy's z joined to x₁ stands on x₁'s side), the photon
    of the self-energy arcs above a straight fermion line, a vacuum bubble
    floats clear of the propagator, and nothing is drawn off its card."""
    doc = Document("PathIntegral(psi(x_1)*psibar(x_2))", addons=[ADDON])
    srv = EditorServer(doc, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    geometry = """card => {
        const svg = card.querySelector('svg'), vb = svg.viewBox.baseVal, out = {nodes: {}, inside: true};
        card.querySelectorAll('.fd-node').forEach(g => {
            const c = g.querySelector('circle'); out.nodes[g.dataset.node] = [+c.getAttribute('cx'), +c.getAttribute('cy')]; });
        card.querySelectorAll('path.fd-fermion, path.fd-photon').forEach(p => {
            const b = p.getBBox();
            if (b.x < -1 || b.y < -1 || b.x + b.width > vb.width + 1 || b.y + b.height > vb.height + 1) out.inside = false; });
        const ph = card.querySelector('path.fd-photon');
        out.photonTop = ph ? ph.getBBox().y : null;
        return out; }"""
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium not available: {exc}")
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(srv.url)
        page.wait_for_selector(".se-addon-feynman .fd-panel", timeout=30000)
        # the electron's self-energy
        cards = _expanded(page, "PathIntegral(psi(x_1)*psibar(x_2))", "2", "connected")
        g = cards.nth(1).evaluate(geometry)
        assert g["inside"], g
        n = g["nodes"]
        # each vertex on the side of the external point it is joined to: x_2 → z_2 → z_1 → x_1, left to right
        assert n["x_2"][0] < n["z_2"][0] < n["z_1"][0] < n["x_1"][0], n
        assert abs(n["z_1"][1] - n["z_2"][1]) < 3                         # a straight fermion line...
        assert g["photonTop"] < min(n["z_1"][1], n["z_2"][1]) - 10        # ...and the photon arcs above it
        # every connected diagram up to order 3 of the vertex, and Møller's, on their cards
        for src, order in (("PathIntegral(psi(x_1)*psibar(x_2)*A(mu, x_3))", "3"),
                           ("PathIntegral(psi(x_1)*psibar(x_2)*psi(x_3)*psibar(x_4))", "2"),
                           ("PathIntegral(A(mu, x_1)*A(nu, x_2))", "2")):
            cards = _expanded(page, src, order, "connected")
            for i in range(cards.count()):
                assert cards.nth(i).evaluate(geometry)["inside"], (src, i)
        # a vacuum bubble beside the propagator: above it, and on the card
        cards = _expanded(page, "PathIntegral(psi(x_1)*psibar(x_2))", "2", "all")
        bubble = [cards.nth(i).evaluate(geometry) for i in range(cards.count())]
        bubble = [b for b in bubble if b["photonTop"] is not None and "z_1" in b["nodes"]
                  and abs(b["nodes"]["x_1"][1] - b["nodes"]["z_1"][1]) > 20]
        assert bubble and all(b["inside"] for b in bubble), bubble
        assert all(b["nodes"]["z_1"][1] < b["nodes"]["x_1"][1] for b in bubble)
        assert errors == []
        browser.close()
    srv.shutdown()
    srv.server_close()
