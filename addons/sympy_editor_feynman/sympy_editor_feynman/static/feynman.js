/*
 * sympy-editor add-on "feynman": the diagrams of the expression, drawn.
 *
 * Python (FeynmanAddon.contribute) puts every Diagram node of the expression
 * in the snapshot as points and propagators (snap.feynman.diagrams); this
 * panel draws each one - fermion lines with an arrow, wavy photon lines,
 * a dot per vertex, a labelled circle per external point - in a card, and
 * a click on a card selects that term in the formula.  The bar starts an
 * example and expands the selected path integral to an order.
 */
SympyEditor.registerAddon("feynman", {
  mount: function (api) {
    var h = api.h;
    var SVG = "http://www.w3.org/2000/svg";
    var W = 170, H = 110, PAD = 22;

    var examples = api.options.examples || [];
    var exampleSel = h("select", { class: "fd-examples", title: "Start from one of the usual correlators" },
      [h("option", { value: "", disabled: "", selected: "" }, ["Examples ▾"])].concat(
        examples.map(function (ex) { return h("option", { value: ex.src }, [ex.label]); })));
    var orderField = h("input", { type: "text", inputmode: "numeric", class: "fd-order", value: "2", title: "The order in the coupling e (0 to " + (api.options.max_order || 4) + ")" });
    var whichSel = h("select", { class: "fd-which", title: "Which diagrams: connected ones, all but the vacuum bubbles, or all" }, [
      h("option", { value: "connected" }, ["connected"]),
      h("option", { value: "no_bubbles" }, ["no vacuum bubbles"]),
      h("option", { value: "all" }, ["all"])
    ]);
    var expandBtn = h("button", { type: "button", class: "fd-expand", disabled: "", title: "Expand the selected path integral into its Feynman diagrams" }, ["Diagrams"]);
    var hint = h("span", { class: "fd-hint" });
    var bar = h("div", { class: "fd-bar" }, [exampleSel, h("span", {}, ["order"]), orderField, whichSel, expandBtn, hint]);
    var strip = h("div", { class: "fd-strip" });
    var element = h("div", { class: "fd-panel" }, [bar, strip]);

    var last = null;        // snap.feynman
    var cards = [];         // {src, el}

    exampleSel.addEventListener("change", function () {
      var src = exampleSel.value;
      exampleSel.selectedIndex = 0;
      if (src) api.call("example", { src: src }).catch(function (e) { api.error(String(e && e.message || e)); });
    });
    expandBtn.addEventListener("click", function () {
      var path = integralPath();
      if (path === null) return;
      api.call("expand", { path: path, order: orderField.value.trim() || "2", which: whichSel.value })
        .catch(function (e) { api.error(String(e && e.message || e)); });
    });
    orderField.addEventListener("keydown", function (ev) { ev.stopPropagation(); if (ev.key === "Enter") expandBtn.click(); });

    /** The path of the path integral to expand: the selected one, or the
     *  whole expression when it is one, or the only one in it. */
    function integralPath() {
      var st = api.state();
      if (!st) return null;
      var sel = api.selected();
      if (sel && st.nodes[sel] && st.nodes[sel].type === "PathIntegral") return sel;
      var all = Object.keys(st.nodes).filter(function (p) { return st.nodes[p].type === "PathIntegral"; });
      return all.length === 1 ? all[0] : null;
    }

    function updateBar() {
      var path = integralPath();
      expandBtn.disabled = path === null;
      var st = api.state();
      if (!st) { hint.textContent = ""; return; }
      if (path !== null) hint.textContent = "the interaction brought down n times, the fields contracted in every way";
      else if (last && last.diagrams.length) hint.textContent = last.diagrams.length + (last.diagrams.length === 1 ? " diagram" : " diagrams") + ": click one to select its term";
      else if (last && last.integrals) hint.textContent = "select a path integral to expand";
      else hint.textContent = "type PathIntegral(psi(x_1)*psibar(x_2)), or pick an example";
    }

    /* ---- drawing ---- */

    function el(tag, attrs) {
      var e = document.createElementNS(SVG, tag);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      return e;
    }

    /** Positions: external points on the sides (ψ̄ and half the photons on
     *  the left, ψ and the rest on the right), the vertices spread in the
     *  middle and relaxed a little: propagators pull, vertices push. */
    function layout(d) {
      var pos = {}, ext = d.nodes.filter(function (n) { return n.external; }), verts = d.nodes.filter(function (n) { return !n.external; });
      var left = [], right = [], nPhoton = 0;
      ext.forEach(function (n) {
        if (n.kind === "psibar") left.push(n);
        else if (n.kind === "psi") right.push(n);
        else (nPhoton++ % 2 ? right : left).push(n);
      });
      function column(list, x) {
        list.forEach(function (n, i) { pos[n.id] = { x: x, y: PAD + (H - 2 * PAD) * (list.length === 1 ? 0.5 : i / (list.length - 1)) }; });
      }
      column(left, PAD); column(right, W - PAD);
      var k = verts.length;
      verts.forEach(function (n, i) {
        var t = k === 1 ? 0.5 : i / (k - 1);
        pos[n.id] = { x: PAD + 24 + (W - 2 * PAD - 48) * t, y: H / 2 + (k > 2 ? (i % 2 ? 22 : -22) : 0) };
      });
      if (!ext.length && k) verts.forEach(function (n, i) {           // a bubble: a ring
        var a = 2 * Math.PI * i / k - Math.PI / 2;
        pos[n.id] = { x: W / 2 + 32 * Math.cos(a), y: H / 2 + 28 * Math.sin(a) };
      });
      for (var it = 0; it < 60 && k > 1; it++) {
        var force = {};
        verts.forEach(function (n) { force[n.id] = { x: 0, y: 0 }; });
        verts.forEach(function (a) {
          verts.forEach(function (b) {
            if (a === b) return;
            var dx = pos[a.id].x - pos[b.id].x, dy = pos[a.id].y - pos[b.id].y, d2 = dx * dx + dy * dy + 1;
            force[a.id].x += 900 * dx / d2; force[a.id].y += 900 * dy / d2;
          });
        });
        d.edges.forEach(function (e) {
          var p = pos[e.from], q = pos[e.to];
          if (!p || !q) return;
          var dx = q.x - p.x, dy = q.y - p.y, len = Math.sqrt(dx * dx + dy * dy) || 1, pull = (len - 50) * 0.02;
          if (force[e.from]) { force[e.from].x += pull * dx / len; force[e.from].y += pull * dy / len; }
          if (force[e.to]) { force[e.to].x -= pull * dx / len; force[e.to].y -= pull * dy / len; }
        });
        verts.forEach(function (n) {
          pos[n.id].x = Math.min(W - PAD - 16, Math.max(PAD + 16, pos[n.id].x + force[n.id].x));
          pos[n.id].y = Math.min(H - 12, Math.max(12, pos[n.id].y + force[n.id].y));
        });
      }
      return pos;
    }

    /** A wavy path along the curve p(t) (a quadratic through c). */
    function wavy(p, c, q) {
      var pts = [], n = 40, amp = 3.2, waves = Math.max(3, Math.round(Math.hypot(q.x - p.x, q.y - p.y) / 9));
      for (var i = 0; i <= n; i++) {
        var t = i / n, mt = 1 - t;
        var x = mt * mt * p.x + 2 * mt * t * c.x + t * t * q.x, y = mt * mt * p.y + 2 * mt * t * c.y + t * t * q.y;
        var dx = 2 * mt * (c.x - p.x) + 2 * t * (q.x - c.x), dy = 2 * mt * (c.y - p.y) + 2 * t * (q.y - c.y);
        var len = Math.hypot(dx, dy) || 1, off = amp * Math.sin(t * waves * 2 * Math.PI);
        pts.push((x - off * dy / len).toFixed(1) + "," + (y + off * dx / len).toFixed(1));
      }
      return "M" + pts.join(" L");
    }

    function draw(d) {
      var svg = el("svg", { class: "fd-svg", viewBox: "0 0 " + W + " " + H, width: W, height: H });
      var pos = layout(d);
      // parallel propagators between the same two points bend apart
      var seen = {};
      d.edges.forEach(function (e) {
        var p = pos[e.from], q = pos[e.to];
        if (!p || !q) return;
        var key = [e.from, e.to].sort().join("|");
        var nth = (seen[key] = (seen[key] || 0) + 1);
        var total = d.edges.filter(function (o) { return [o.from, o.to].sort().join("|") === key; }).length;
        var bend = total > 1 ? (nth - (total + 1) / 2) * 26 : (e.from === e.to ? 30 : 0);
        var mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2, dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
        var c = { x: mx - bend * dy / len, y: my + bend * dx / len };
        if (e.kind === "A") {
          svg.appendChild(el("path", { class: "fd-photon", d: wavy(p, c, q) }));
        } else {
          svg.appendChild(el("path", { class: "fd-fermion", d: "M" + p.x + "," + p.y + " Q" + c.x + "," + c.y + " " + q.x + "," + q.y }));
          // the arrow at the middle of the curve, along it: charge flows from ψ̄ to ψ
          var ax = 0.25 * p.x + 0.5 * c.x + 0.25 * q.x, ay = 0.25 * p.y + 0.5 * c.y + 0.25 * q.y;
          var tx = (c.x - p.x) + (q.x - c.x), ty = (c.y - p.y) + (q.y - c.y), tl = Math.hypot(tx, ty) || 1;
          tx /= tl; ty /= tl;
          var s = 4.5;
          svg.appendChild(el("polygon", { class: "fd-arrow", points:
            (ax + s * tx) + "," + (ay + s * ty) + " " + (ax - s * tx - s * 0.8 * ty) + "," + (ay - s * ty + s * 0.8 * tx) + " " +
            (ax - s * tx + s * 0.8 * ty) + "," + (ay - s * ty - s * 0.8 * tx) }));
        }
      });
      d.nodes.forEach(function (n) {
        var p = pos[n.id];
        if (!p) return;
        if (n.external) {
          svg.appendChild(el("circle", { class: "fd-external", cx: p.x, cy: p.y, r: 3.2 }));
          var t = el("text", { class: "fd-label", x: p.x + (p.x < W / 2 ? -6 : 6), y: p.y - 6, "text-anchor": p.x < W / 2 ? "end" : "start" });
          t.textContent = n.id.replace(/_(\d+)$/, "$1").replace(/_\{?(\w+)\}?/, "$1");
          svg.appendChild(t);
        } else {
          svg.appendChild(el("circle", { class: "fd-vertex", cx: p.x, cy: p.y, r: 2.6 }));
        }
      });
      return svg;
    }

    function render(fd) {
      strip.textContent = "";
      cards = [];
      if (!fd || !fd.diagrams.length) {
        strip.appendChild(h("div", { class: "fd-empty" }, [fd && fd.integrals ? "" : "No diagram yet."]));
        return;
      }
      fd.diagrams.forEach(function (d, i) {
        var caption = h("div", { class: "fd-caption" }, ["order e" + (d.order === 1 ? "" : "^" + d.order) + " · factor " + d.factor.replace(/\\frac\{(-?\d+)\}\{(\d+)\}/, "$1/$2").replace(/^- ?/, "−")]);
        var card = h("div", { class: "fd-card", title: "Diagram " + (i + 1) + ": click to select its term", "data-src": d.src }, [draw(d), caption]);
        card.addEventListener("click", function () {
          var st = api.state();
          for (var p in (st && st.nodes || {})) if (st.nodes[p].src === d.src) { api.select(p); return; }
        });
        strip.appendChild(card);
        cards.push({ src: d.src, el: card });
      });
      markSelected();
    }

    function markSelected() {
      var st = api.state(), sel = api.selected();
      var src = st && sel && st.nodes[sel] ? st.nodes[sel].src : null;
      cards.forEach(function (c) { c.el.classList.toggle("fd-selected", src !== null && c.src === src); });
    }

    return {
      element: element,
      title: "Feynman diagrams",
      help: "<section><h3>Feynman diagrams</h3><ul>"
        + "<li>The expression is a path integral of QED, <code>PathIntegral(psi(x_1)*psibar(x_2))</code>: the correlator of a product of fields — <code>psi(x)</code>, <code>psibar(x)</code>, <code>A(mu, x)</code>. <b>Examples ▾</b> starts from the usual ones.</li>"
        + "<li><b>Diagrams</b> (or the <i>Feynman diagrams</i> transformation) expands the selected path integral to an order in the coupling: the interaction −ie ψ̄ γ<sup>μ</sup> ψ A<sub>μ</sub> brought down n times, the fields contracted pairwise in every way (Wick), each full contraction a diagram, the ones equal up to a relabelling of the vertices counted once with the factor (their number)/n! and the sign of the fermion permutation.</li>"
        + "<li>Each diagram is a term of the sum, printed by the Feynman rules in position space (S<sub>F</sub>, D<sub>μν</sub>, γ<sup>μ</sup> at the vertices, a trace per closed loop, the vertices integrated over) and drawn here: fermion lines with the arrow of the charge, wavy photon lines, a dot per vertex. A click on a drawing selects its term; <i>Feynman rules</i> in the term's menu gives its value as an expression.</li>"
        + "<li><i>connected</i> keeps the diagrams in one piece, <i>no vacuum bubbles</i> drops the pieces with no external point (they cancel against the normalisation of the path integral), <i>all</i> keeps everything.</li>"
        + "</ul></section>",
      onState: function (snap) { if (snap.preview) return; last = snap.feynman || null; render(last); updateBar(); },
      onSelect: function () { markSelected(); updateBar(); }
    };
  }
});
