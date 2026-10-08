/*
 * sympy-editor add-on "feynman": the diagrams of the expression, drawn and
 * edited.
 *
 * Python (FeynmanAddon.contribute) puts every Diagram node of the expression
 * in the snapshot as points and propagators (snap.feynman.diagrams); this
 * panel draws each one in a card - fermion lines with an arrow, wavy photon
 * lines, a dot per vertex, a labelled circle per external point - and lets
 * it be edited there: drag a point, add a vertex or an external leg, draw a
 * propagator from a point to another, delete or flip one, edit the factor,
 * remove the term.  Every change goes to Python ("edit"), which rebuilds
 * the Diagram and puts it in the term's place, so the sum follows.  A click
 * on a card selects its term; the bar starts an example and expands the
 * selected path integral to an order.
 */
SympyEditor.registerAddon("feynman", {
  mount: function (api) {
    var h = api.h;
    var SVG = "http://www.w3.org/2000/svg";
    var W = 190, H = 120, PAD = 22;

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
    var newBtn = h("button", { type: "button", class: "fd-new", disabled: "", title: "A bare diagram with the external points, to draw by hand" }, ["New diagram"]);
    // The drawing tools: what a click or a drag on a card does.
    var modeSel = h("select", { class: "fd-mode", title: "What a click or a drag on a drawing does" }, [
      h("option", { value: "move" }, ["move / select"]),
      h("option", { value: "fermion" }, ["draw fermion line (drag ψ̄ → ψ)"]),
      h("option", { value: "photon" }, ["draw photon line (drag)"]),
      h("option", { value: "vertex" }, ["add vertex (click)"]),
      h("option", { value: "psi" }, ["add external ψ (click)"]),
      h("option", { value: "psibar" }, ["add external ψ̄ (click)"]),
      h("option", { value: "A" }, ["add external photon (click)"]),
      h("option", { value: "delete" }, ["delete (click a point or a line)"])
    ]);
    var hint = h("span", { class: "fd-hint" });
    var bar = h("div", { class: "fd-bar" }, [exampleSel, h("span", {}, ["order"]), orderField, whichSel, expandBtn, newBtn, modeSel, hint]);
    var strip = h("div", { class: "fd-strip" });
    var menu = h("div", { class: "fd-menu", hidden: "", role: "menu" });
    var element = h("div", { class: "fd-panel" }, [bar, strip, menu]);

    var last = null;        // snap.feynman
    var cards = [];         // {src, el, data, pos}
    var memory = [];        // positions by card index: {id: {x, y}} - kept across edits
    var busy = false;

    function mode() { return modeSel.value; }
    function fail(e) { api.error(String(e && e.message || e)); }

    exampleSel.addEventListener("change", function () {
      var src = exampleSel.value;
      exampleSel.selectedIndex = 0;
      if (src) { memory = []; api.call("example", { src: src }).catch(fail); }
    });
    expandBtn.addEventListener("click", function () {
      var path = integralPath();
      if (path === null) return;
      memory = [];
      api.call("expand", { path: path, order: orderField.value.trim() || "2", which: whichSel.value }).catch(fail);
    });
    newBtn.addEventListener("click", function () { api.call("new_diagram", {}).catch(fail); });
    orderField.addEventListener("keydown", function (ev) { ev.stopPropagation(); if (ev.key === "Enter") expandBtn.click(); });
    modeSel.addEventListener("change", function () { element.setAttribute("data-mode", mode()); updateBar(); });
    element.setAttribute("data-mode", "move");

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

    /** The term a card stands for: the node whose source is the diagram's. */
    function pathOf(src) {
      var st = api.state();
      for (var p in (st && st.nodes || {})) if (st.nodes[p].src === src) return p;
      return null;
    }

    var MODE_HINTS = {
      move: "drag a point to move it · click a line for its menu · click a card to select its term",
      fermion: "drag from the ψ̄ end to the ψ end of the new fermion line (the arrow follows the charge)",
      photon: "drag from one point to another to join them with a photon line",
      vertex: "click an empty spot of a card to add a vertex there",
      psi: "click an empty spot of a card to add an external ψ there",
      psibar: "click an empty spot of a card to add an external ψ̄ there",
      A: "click an empty spot of a card to add an external photon there",
      delete: "click a point (and its lines) or a line to delete it"
    };

    function updateBar() {
      var path = integralPath();
      expandBtn.disabled = path === null;
      var st = api.state();
      newBtn.disabled = !st || !(path !== null || (last && last.diagrams.length));
      if (!st) { hint.textContent = ""; return; }
      if (mode() !== "move") hint.textContent = MODE_HINTS[mode()];
      else if (path !== null && !(last && last.diagrams.length)) hint.textContent = "the interaction brought down n times, the fields contracted in every way";
      else if (last && last.diagrams.length) hint.textContent = last.diagrams.length + (last.diagrams.length === 1 ? " diagram · " : " diagrams · ") + MODE_HINTS.move;
      else if (last && last.integrals) hint.textContent = "select a path integral to expand";
      else hint.textContent = "type PathIntegral(psi(x_1)*psibar(x_2)), or pick an example";
    }

    /* ---- editing: the drawing goes back to Python as the term ---- */

    function commit(card, nodes, edges, what, factor) {
      var path = pathOf(card.src);
      if (path === null) { api.error("The diagram is no longer in the expression"); return Promise.resolve(); }
      busy = true;
      var payload = { path: path, nodes: nodes, edges: edges, what: what };
      if (factor !== undefined) payload.factor = factor;
      return api.call("edit", payload).then(function () { busy = false; }, function (e) { busy = false; fail(e); });
    }

    function freshId(card, prefix) {
      var taken = {};
      cards.forEach(function (c) { c.data.nodes.forEach(function (n) { taken[n.id] = true; }); });
      for (var i = 1; ; i++) if (!taken[prefix + "_" + i]) return prefix + "_" + i;
    }

    function addNode(card, kind, x, y) {
      var external = kind !== "vertex";
      var id = freshId(card, external ? "x" : "z");
      var node = { id: id, kind: kind, external: external };
      if (kind === "A") node.index = freshId(card, "mu").replace("_", "");
      card.pos[id] = { x: x, y: y };
      memory[card.index] = card.pos;
      return commit(card, card.data.nodes.concat([node]), card.data.edges, (external ? "external " : "") + kind + " added");
    }

    function removeNode(card, id) {
      var nodes = card.data.nodes.filter(function (n) { return n.id !== id; });
      var edges = card.data.edges.filter(function (e) { return e.from !== id && e.to !== id; });
      return commit(card, nodes, edges, id + " removed");
    }

    function addEdge(card, kind, from, to) {
      if (from === to) return Promise.resolve();
      return commit(card, card.data.nodes, card.data.edges.concat([{ kind: kind, from: from, to: to }]), (kind === "A" ? "photon" : "fermion") + " line added");
    }

    function editEdge(card, i, change) {
      var edges = card.data.edges.slice();
      var what;
      if (change === "delete") { edges.splice(i, 1); what = "line removed"; }
      else if (change === "flip") { edges[i] = { kind: edges[i].kind, from: edges[i].to, to: edges[i].from }; what = "arrow flipped"; }
      else { edges[i] = { kind: change, from: edges[i].from, to: edges[i].to }; what = change === "A" ? "made a photon line" : "made a fermion line"; }
      return commit(card, card.data.nodes, edges, what);
    }

    /* ---- the line menu ---- */

    function showMenu(card, i, x, y) {
      var e = card.data.edges[i];
      menu.textContent = "";
      var items = [["Delete line", "delete"]];
      if (e.kind === "F") items.push(["Flip the arrow", "flip"], ["Make it a photon line", "A"]);
      else items.push(["Make it a fermion line", "F"]);
      items.forEach(function (it) {
        var b = h("button", { type: "button", role: "menuitem" }, [it[0]]);
        b.addEventListener("click", function (ev) { ev.stopPropagation(); hideMenu(); editEdge(card, i, it[1]); });
        menu.appendChild(b);
      });
      menu.hidden = false;
      var r = element.getBoundingClientRect();
      menu.style.left = (x - r.left) + "px";
      menu.style.top = (y - r.top + 6) + "px";
    }
    function hideMenu() { menu.hidden = true; }
    document.addEventListener("pointerdown", function (ev) { if (!menu.hidden && !menu.contains(ev.target)) hideMenu(); }, true);

    /* ---- layout ---- */

    function el(tag, attrs) {
      var e = document.createElementNS(SVG, tag);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      return e;
    }

    /** Positions: the remembered ones for this card (a drag, an earlier
     *  layout), else external points on the sides (ψ̄ and half the photons
     *  on the left, ψ and the rest on the right), the vertices spread in the
     *  middle and relaxed a little: propagators pull, vertices push. */
    function layout(d, known) {
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
      var fixed = {};
      d.nodes.forEach(function (n) { if (known && known[n.id]) { pos[n.id] = { x: known[n.id].x, y: known[n.id].y }; fixed[n.id] = true; } });
      var free = verts.filter(function (n) { return !fixed[n.id]; });
      for (var it = 0; it < 60 && free.length && k > 1; it++) {
        var force = {};
        free.forEach(function (n) { force[n.id] = { x: 0, y: 0 }; });
        free.forEach(function (a) {
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
        free.forEach(function (n) {
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

    function control(card, e, pos) {
      var p = pos[e.from], q = pos[e.to];
      var key = [e.from, e.to].sort().join("|");
      var same = card.data.edges.filter(function (o) { return [o.from, o.to].sort().join("|") === key; });
      var nth = same.indexOf(e) + 1, total = same.length;
      var bend = total > 1 ? (nth - (total + 1) / 2) * 26 : (e.from === e.to ? 30 : 0);
      var mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2, dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
      return { x: mx - bend * dy / len, y: my + bend * dx / len };
    }

    /* ---- drawing, with the interactions ---- */

    function draw(card) {
      var d = card.data, pos = card.pos;
      var svg = el("svg", { class: "fd-svg", viewBox: "0 0 " + W + " " + H, width: W, height: H });
      d.edges.forEach(function (e, i) {
        var p = pos[e.from], q = pos[e.to];
        if (!p || !q) return;
        var c = control(card, e, pos);
        var curve = "M" + p.x + "," + p.y + " Q" + c.x + "," + c.y + " " + q.x + "," + q.y;
        var g = el("g", { class: "fd-edge", "data-edge": String(i) });
        if (e.kind === "A") {
          g.appendChild(el("path", { class: "fd-photon", d: wavy(p, c, q) }));
        } else {
          g.appendChild(el("path", { class: "fd-fermion", d: curve }));
          // the arrow at the middle of the curve, along it: charge flows from ψ̄ to ψ
          var ax = 0.25 * p.x + 0.5 * c.x + 0.25 * q.x, ay = 0.25 * p.y + 0.5 * c.y + 0.25 * q.y;
          var tx = (c.x - p.x) + (q.x - c.x), ty = (c.y - p.y) + (q.y - c.y), tl = Math.hypot(tx, ty) || 1;
          tx /= tl; ty /= tl;
          var s = 4.5;
          g.appendChild(el("polygon", { class: "fd-arrow", points:
            (ax + s * tx) + "," + (ay + s * ty) + " " + (ax - s * tx - s * 0.8 * ty) + "," + (ay - s * ty + s * 0.8 * tx) + " " +
            (ax - s * tx + s * 0.8 * ty) + "," + (ay - s * ty - s * 0.8 * tx) }));
        }
        g.appendChild(el("path", { class: "fd-hit", d: curve }));       // a wide, invisible stroke to click on
        g.addEventListener("pointerdown", function (ev) {
          ev.stopPropagation();
          if (busy) return;
          if (mode() === "delete") { editEdge(card, i, "delete"); return; }
          if (mode() === "move") showMenu(card, i, ev.clientX, ev.clientY);
        });
        svg.appendChild(g);
      });
      d.nodes.forEach(function (n) {
        var p = pos[n.id];
        if (!p) return;
        var g = el("g", { class: "fd-node fd-node-" + n.kind, "data-node": n.id });
        if (n.external) {
          g.appendChild(el("circle", { class: "fd-external", cx: p.x, cy: p.y, r: 3.4 }));
          var t = el("text", { class: "fd-label", x: p.x + (p.x < W / 2 ? -6 : 6), y: p.y - 6, "text-anchor": p.x < W / 2 ? "end" : "start" });
          t.textContent = n.id.replace(/_\{?(\w+)\}?$/, "$1");
          g.appendChild(t);
        } else {
          g.appendChild(el("circle", { class: "fd-vertex", cx: p.x, cy: p.y, r: 2.8 }));
        }
        g.appendChild(el("circle", { class: "fd-hit-node", cx: p.x, cy: p.y, r: 9 }));   // the handle
        g.addEventListener("pointerdown", function (ev) { onNodeDown(ev, card, n, svg); });
        svg.appendChild(g);
      });
      svg.addEventListener("pointerdown", function (ev) {
        if (busy || ev.target !== svg && !ev.target.classList.contains("fd-svg")) return;
        var m = mode();
        if (m === "vertex" || m === "psi" || m === "psibar" || m === "A") {
          ev.stopPropagation();
          var pt = toSvg(svg, ev);
          addNode(card, m, Math.round(pt.x), Math.round(pt.y));
        }
      });
      return svg;
    }

    function toSvg(svg, ev) {
      var r = svg.getBoundingClientRect();
      return { x: (ev.clientX - r.left) * W / r.width, y: (ev.clientY - r.top) * H / r.height };
    }

    /** A point pressed: delete it, start a line from it, or drag it. */
    function onNodeDown(ev, card, n, svg) {
      ev.stopPropagation();
      if (busy) return;
      var m = mode();
      if (m === "delete") { removeNode(card, n.id); return; }
      if (m === "vertex" || m === "psi" || m === "psibar" || m === "A") return;
      ev.preventDefault();
      var connect = (m === "fermion" || m === "photon");
      var start = toSvg(svg, ev), origin = { x: card.pos[n.id].x, y: card.pos[n.id].y };
      var rubber = connect ? el("path", { class: m === "photon" ? "fd-photon fd-rubber" : "fd-fermion fd-rubber", d: "" }) : null;
      if (rubber) svg.appendChild(rubber);
      var moved = false;
      function move(e2) {
        var pt = toSvg(svg, e2);
        if (Math.hypot(pt.x - start.x, pt.y - start.y) > 2) moved = true;
        if (connect) {
          rubber.setAttribute("d", "M" + origin.x + "," + origin.y + " L" + pt.x.toFixed(1) + "," + pt.y.toFixed(1));
        } else {
          card.pos[n.id] = { x: Math.min(W - 6, Math.max(6, origin.x + pt.x - start.x)), y: Math.min(H - 6, Math.max(6, origin.y + pt.y - start.y)) };
          memory[card.index] = card.pos;
          redraw(card);
          svg = card.svg;
        }
      }
      function up(e2) {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        if (rubber && rubber.parentNode) rubber.parentNode.removeChild(rubber);
        if (connect) {
          var target = document.elementFromPoint(e2.clientX, e2.clientY);
          var g = target && target.closest ? target.closest(".fd-node") : null;
          if (g && card.el.contains(g)) addEdge(card, m === "photon" ? "A" : "F", n.id, g.getAttribute("data-node"));
        } else if (!moved) {
          selectCard(card);
        }
      }
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }

    function redraw(card) {
      var fresh = draw(card);
      card.el.replaceChild(fresh, card.svg);
      card.svg = fresh;
    }

    function selectCard(card) {
      var path = pathOf(card.src);
      if (path !== null) api.select(path);
    }

    function factorText(d) {
      return d.factor.replace(/\\frac\{(-?\d+)\}\{(\d+)\}/, "$1/$2").replace(/^- ?/, "−");
    }

    function render(fd) {
      hideMenu();
      strip.textContent = "";
      var old = cards;
      cards = [];
      if (!fd || !fd.diagrams.length) {
        memory = [];
        strip.appendChild(h("div", { class: "fd-empty" }, [fd && fd.integrals ? "" : "No diagram yet."]));
        return;
      }
      fd.diagrams.forEach(function (d, i) {
        var card = { src: d.src, data: d, index: i, pos: null, el: null, svg: null };
        card.pos = layout(d, memory[i] || (old[i] && old[i].pos) || null);
        memory[i] = card.pos;
        var problems = d.problems || [];
        var factorBtn = h("button", { type: "button", class: "fd-factor", title: "The factor in front: click to change it" }, ["factor " + factorText(d)]);
        factorBtn.addEventListener("click", function (ev) {
          ev.stopPropagation();
          var field = h("input", { type: "text", class: "fd-factor-field", value: d.factor_src || "1", title: "Enter applies, Esc cancels" });
          factorBtn.replaceWith(field);
          field.focus(); field.select();
          field.addEventListener("keydown", function (ke) {
            ke.stopPropagation();
            if (ke.key === "Enter") { ke.preventDefault(); commit(card, d.nodes, d.edges, "factor " + field.value, field.value); }
            if (ke.key === "Escape") { ke.preventDefault(); field.replaceWith(factorBtn); }
          });
          field.addEventListener("blur", function () { if (field.parentNode) field.replaceWith(factorBtn); });
        });
        var caption = h("div", { class: "fd-caption" }, ["order e" + (d.order === 1 ? "" : "^" + d.order) + " · ", factorBtn]);
        if (problems.length) caption.appendChild(h("span", { class: "fd-warn", title: problems.join("\n") }, [" ⚠"]));
        var remove = h("button", { type: "button", class: "fd-remove", title: "Remove this diagram from the sum" }, ["×"]);
        remove.addEventListener("click", function (ev) {
          ev.stopPropagation();
          var path = pathOf(card.src);
          if (path !== null) api.call("delete_term", { path: path }).catch(fail);
        });
        card.svg = draw(card);
        card.el = h("div", { class: "fd-card" + (problems.length ? " fd-invalid" : ""), title: problems.length ? problems.join("\n") : "Diagram " + (i + 1), "data-src": d.src }, [remove, card.svg, caption]);
        card.el.addEventListener("click", function (ev) {
          // anywhere but the controls, the lines and the points (those have their own meaning)
          if (!ev.target.closest || !ev.target.closest(".fd-remove, .fd-factor, .fd-factor-field, .fd-edge, .fd-node")) selectCard(card);
        });
        strip.appendChild(card.el);
        cards.push(card);
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
        + "<li><b>Diagrams</b> (or the <i>Feynman diagrams</i> transformation) expands the selected path integral to an order in the coupling: the interaction −ie ψ̄ γ<sup>μ</sup> ψ A<sub>μ</sub> brought down n times, the fields contracted pairwise in every way (Wick), each full contraction a diagram, the ones equal up to a relabelling of the vertices counted once with the factor (their number)/n! and the sign of the fermion permutation.</li>"
        + "<li>Each diagram is a term of the sum, printed by the Feynman rules in position space (S<sub>F</sub>, D<sub>μν</sub>, γ<sup>μ</sup> at the vertices, a trace per closed loop, the vertices integrated over) and drawn in a card: fermion lines with the arrow of the charge, wavy photon lines, a dot per vertex. A click on a card selects its term; <i>Feynman rules</i> in the term's menu gives its value as an expression.</li>"
        + "<li><b>Editing a drawing edits the term.</b> The tool menu says what a click or a drag does: <i>move</i> drags a point (and a click on a line opens its menu: delete, flip the arrow, change its kind); <i>draw fermion line</i> and <i>draw photon line</i> drag a new propagator from one point to another (a fermion line from its ψ̄ end to its ψ end); <i>add vertex</i> and <i>add external …</i> put a new point where you click; <i>delete</i> removes what you click. The factor is edited by clicking it; × removes the diagram from the sum; <b>New diagram</b> adds a bare one with the external points, to draw by hand. Every change rebuilds the term from the drawing — its order is its number of vertices — and a ⚠ marks a drawing that is not a QED diagram (a vertex without one fermion line in, one out and one photon line; an external point without its one line).</li>"
        + "<li><i>connected</i> keeps the diagrams in one piece, <i>no vacuum bubbles</i> drops the pieces with no external point (they cancel against the normalisation of the path integral), <i>all</i> keeps everything.</li>"
        + "</ul></section>",
      onState: function (snap) { if (snap.preview) return; last = snap.feynman || null; render(last); updateBar(); },
      onSelect: function () { markSelected(); updateBar(); },
      destroy: function () { hideMenu(); }
    };
  }
});
